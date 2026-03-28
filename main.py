"""
main.py
~~~~~~~~
Chiko Trading Monitor v2.0.0 – Production Entry Point

Run:
    python main.py
    python main.py --symbol ETHUSDT --granularity 900 --check-interval 60

Environment variables (.env):
    BINANCE_API_KEY, BINANCE_API_SECRET
    EMAIL_SENDER, EMAIL_APP_PASSWORD, RECEIVER_EMAIL
    TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_CHAT_ID
    SYMBOL (default BTCUSDT), GRANULARITY (default 300), CHECK_INTERVAL (default 60)

Analysis timing:
    The loop sleeps until `buffer_seconds` AFTER the expected candle close.
    This guarantees the new closed candle is available on Binance before we
    fetch it – eliminating stale-data false signals.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import queue
import signal
import ssl
import smtplib
import sys
import threading
import time
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import backoff
from dotenv import load_dotenv
from rich.console import Console

# ── Internal ──────────────────────────────────────────────────────────────────
from binance_usage.market import get_lastest_400
from binance_usage.account import get_balances
from strategy import StrategyEngine, RiskManager, CandleData
from tgbot.bot import TelegramBot
from alert_manager import AlertManager, ConfigurationError
from chiko_email import ChikoEmail
from paper_trader import PaperTrader

# ─────────────────────────────────────────────────────────────────────────────
load_dotenv()

console = Console()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-28s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.FileHandler("trading_monitor.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
LOCAL_TZ               = ZoneInfo("Africa/Johannesburg")
APP_VERSION            = "2.0.0"

DEFAULT_GRANULARITY    = int(os.getenv("GRANULARITY",     300))
DEFAULT_SYMBOL         = os.getenv("SYMBOL",              "BTCUSDT")
DEFAULT_CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL",  60))

CANDLE_FETCH_SIZE      = 400     # number of closed candles fed into StrategyEngine
CANDLE_BUFFER_SECONDS  = 5      # wait this many seconds after candle close before fetching
STATUS_INTERVAL        = 3600   # hourly heartbeat
SMTP_HOST              = "smtp.gmail.com"
SMTP_PORT              = 465
MAX_RETRIES            = 5
TIMEOUT                = 30

TELEGRAM_ADMIN_CHAT_ID = int(os.getenv("TELEGRAM_ADMIN_CHAT_ID", 0))


# ═════════════════════════════════════════════════════════════════════════════
# Email sender (unchanged from v1, kept inline for zero external deps here)
# ═════════════════════════════════════════════════════════════════════════════
class EmailSender:
    """Thread-safe SMTP sender with exponential-backoff retry."""

    def __init__(self) -> None:
        self.sender       = self._require("EMAIL_SENDER").strip()
        self.app_password = self._require("EMAIL_APP_PASSWORD").strip()
        self.context      = ssl.create_default_context()
        self._check(self.sender, "EMAIL_SENDER")

    @staticmethod
    def _require(key: str) -> str:
        v = os.getenv(key)
        if not v:
            raise ConfigurationError(f"Missing: {key}")
        return v

    @staticmethod
    def _check(addr: str, label: str) -> None:
        if "@" not in addr or "." not in addr.split("@")[-1]:
            raise ConfigurationError(f"Invalid email in {label}: {addr!r}")

    @backoff.on_exception(
        backoff.expo,
        (smtplib.SMTPException, ConnectionError),
        max_tries=MAX_RETRIES,
        max_time=60,
    )
    def send_email(
        self,
        receiver: str,
        subject: str,
        html_content: str,
        text_fallback: str = "Your client does not support HTML.",
    ) -> bool:
        try:
            self._check(receiver, "receiver")
        except ConfigurationError as exc:
            logger.error(str(exc))
            return False

        from email.message import EmailMessage
        subject = "".join(c for c in subject if c.isprintable())[:200]
        msg = EmailMessage()
        msg["From"]    = self.sender
        msg["To"]      = receiver
        msg["Subject"] = subject
        msg.set_content(text_fallback)
        msg.add_alternative(html_content, subtype="html")

        try:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=self.context, timeout=TIMEOUT) as smtp:
                smtp.login(self.sender, self.app_password)
                smtp.send_message(msg)
            logger.info("Email → %s", receiver)
            return True
        except smtplib.SMTPAuthenticationError:
            raise ConfigurationError("SMTP auth failed – check EMAIL_APP_PASSWORD")
        except smtplib.SMTPConnectError as exc:
            raise ConnectionError(f"SMTP connect failed: {exc}") from exc
        except Exception as exc:
            logger.error("Email error: %s", exc, exc_info=True)
            return False


# ═════════════════════════════════════════════════════════════════════════════
# Candle Monitor
# ═════════════════════════════════════════════════════════════════════════════
class CandleMonitor:
    """
    Fetches `CANDLE_FETCH_SIZE` closed candles from Binance REST API and
    tracks when the next candle is expected to close.

    Thread-safe via asyncio.Lock.
    """

    _VALID_GRANULARITIES = {60, 180, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 86400}

    def __init__(
        self,
        symbol: str,
        granularity: int,
        alert_queue: Optional[queue.Queue] = None,
    ) -> None:
        if not symbol:
            raise ValueError("symbol must be non-empty")
        if granularity not in self._VALID_GRANULARITIES:
            raise ValueError(f"granularity {granularity} not in {sorted(self._VALID_GRANULARITIES)}")

        self.symbol      = symbol
        self.granularity = granularity
        self.alert_queue = alert_queue

        self._candles: list[CandleData]           = []
        self._forming: Optional[CandleData]        = None
        self._next_close: int                      = 0
        self._lock                                 = asyncio.Lock()
        self.running                               = False

    # ------------------------------------------------------------------ #
    async def fetch_and_refresh(self) -> bool:
        """Pull fresh candles from Binance and update state. Returns True on success."""
        try:
            loop = asyncio.get_event_loop()
            raw  = await loop.run_in_executor(
                None,
                lambda: get_lastest_400(
                    self.symbol,
                    limit=CANDLE_FETCH_SIZE,
                    granularity=self.granularity,
                ),
            )
            if not raw:
                logger.warning("Empty candle payload from Binance")
                return False

            parsed = self._parse(raw)
            parsed.sort(key=lambda c: c.open_time)

            now = int(time.time())

            async with self._lock:
                # Detect forming candle: the last candle whose period hasn't ended yet
                # (get_lastest_400 already strips it, but we double-check)
                if parsed and parsed[-1].open_time + self.granularity > now:
                    self._forming           = parsed.pop()
                    self._forming.is_closed = False
                    self._next_close        = self._forming.open_time + self.granularity
                else:
                    self._forming    = None
                    self._next_close = (parsed[-1].open_time + self.granularity) if parsed else now

                self._candles = parsed

            ttc = max(0, self._next_close - now)
            logger.debug(
                "Fetched %d closed candles | next close in %dm %ds",
                len(self._candles), ttc // 60, ttc % 60,
            )
            return True

        except Exception as exc:
            logger.error("fetch_and_refresh: %s", exc, exc_info=True)
            return False

    async def get_candles(self) -> list[CandleData]:
        """Return a thread-safe copy of all closed candles (oldest → newest)."""
        async with self._lock:
            return self._candles.copy()

    async def seconds_until_next_close(self) -> int:
        """Seconds remaining until the current forming candle closes."""
        async with self._lock:
            return max(0, self._next_close - int(time.time()))

    # ------------------------------------------------------------------ #
    def _parse(self, raw: list[dict]) -> list[CandleData]:
        result: list[CandleData] = []
        for c in raw:
            try:
                epoch = c["open_time"]
                ts    = (
                    datetime.fromtimestamp(epoch, tz=ZoneInfo("UTC"))
                    .astimezone(LOCAL_TZ)
                    .strftime("%Y-%m-%d %H:%M")
                )
                result.append(CandleData(
                    open_time = epoch,
                    time_str  = ts,
                    open      = float(c["open"]),
                    high      = float(c["high"]),
                    low       = float(c["low"]),
                    close     = float(c["close"]),
                    volume    = float(c.get("volume", 0)),
                    is_closed = True,
                ))
            except (KeyError, ValueError) as exc:
                logger.warning("Skipping bad candle: %s", exc)
        return result

    def stop(self) -> None:
        self.running = False


# ═════════════════════════════════════════════════════════════════════════════
# Main Application
# ═════════════════════════════════════════════════════════════════════════════
class TradingMonitorApp:
    """
    Orchestrates:
      - CandleMonitor  (data fetching)
      - StrategyEngine (multi-signal analysis over 400 candles)
      - RiskManager    (confidence gate + ATR levels + cooldown)
      - AlertManager   (email + Telegram notifications)

    Analysis timing
    ---------------
    We sleep until CANDLE_BUFFER_SECONDS after the expected candle close.
    This ensures Binance has committed the new closed candle before we fetch.

    Example for 5-minute candles:
        Candle closes at 12:05:00
        We wake at    12:05:05  (+5s buffer)
        Fetch 400 candles  → last closed candle is 12:05:00
        Run StrategyEngine  → decision
        Sleep until         12:10:05
    """

    def __init__(
        self,
        args: argparse.Namespace,
        telegram_bot: Optional[TelegramBot] = None,
        alert_queue: Optional[queue.Queue] = None,
    ) -> None:
        self.symbol         = args.symbol
        self.granularity    = args.granularity
        self.check_interval = args.check_interval
        self.running        = True
        self.alert_queue    = alert_queue

        self._setup_signal_handlers()

        self.email_sender    = EmailSender()
        self.risk_manager    = RiskManager(
            min_confidence    = 0.60,
            atr_period        = 14,
            atr_sl_multiplier = 1.5,
            atr_tp_multiplier = 2.5,
            cooldown_seconds  = self.granularity * 2,  # 2 candles cooldown
        )
        self.engine          = StrategyEngine(
            risk_manager     = self.risk_manager,
            require_all_agree= False,
            min_votes        = 2,
        )
        self.candle_monitor  = CandleMonitor(
            symbol           = self.symbol,
            granularity      = self.granularity,
            alert_queue      = alert_queue,
        )
        self.alert_manager   = AlertManager(
            email_sender     = self.email_sender,
            telegram_bot     = telegram_bot,
        )

        # Paper trader – wired to alert_queue so every trade notifies via Telegram
        self.paper_trader = PaperTrader(
            balance          = float(os.getenv("PAPER_BALANCE", 1000.0)),
            risk_pct         = float(os.getenv("PAPER_RISK_PCT", 0.01)),
            fee_pct          = 0.00075,
            max_hold_candles = 12,
            one_position     = True,
            notify           = lambda msg: alert_queue.put(msg) if alert_queue else None,
        )

        self._last_status = 0.0

    # ------------------------------------------------------------------ #
    def _setup_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT,  self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame) -> None:
        logger.info("Shutdown signal received")
        self.running = False
        self.candle_monitor.stop()

    # ------------------------------------------------------------------ #
    async def run(self) -> None:
        logger.info("=" * 64)
        logger.info("  Chiko Trading Monitor v%s", APP_VERSION)
        logger.info("  Symbol:      %s", self.symbol)
        logger.info("  Timeframe:   %dm (%ds)", self.granularity // 60, self.granularity)
        logger.info("  Candles:     %d", CANDLE_FETCH_SIZE)
        logger.info("  Min votes:   %d / 4 detectors", self.engine.min_votes)
        logger.info("  Min conf:    %.0f%%", self.risk_manager.min_confidence * 100)
        logger.info("  Cooldown:    %ds", self.risk_manager.cooldown_seconds)
        logger.info("=" * 64)

        # ── Initial fetch ────────────────────────────────────────────────
        console.print("[cyan]Fetching initial candles…[/cyan]")
        ok = await self.candle_monitor.fetch_and_refresh()
        if not ok:
            logger.error("Initial candle fetch failed – aborting")
            return

        ttc = await self.candle_monitor.seconds_until_next_close()
        console.print(
            f"[green]Ready.[/green] Next candle closes in "
            f"[bold]{ttc // 60}m {ttc % 60}s[/bold]"
        )

        # ── Analysis loop ────────────────────────────────────────────────
        while self.running:
            try:
                await self._analysis_cycle()
                await self._sleep_until_next_candle()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Analysis loop error: %s", exc, exc_info=True)
                await asyncio.sleep(15)

        logger.info("=" * 64)
        logger.info("  Chiko Trading Monitor stopped")
        logger.info("=" * 64)

    # ------------------------------------------------------------------ #
    async def _analysis_cycle(self) -> None:
        """
        One full analysis cycle:
          1. Refresh candle data from Binance.
          2. Run StrategyEngine over all 400 candles.
          3. If actionable → send alerts.
          4. Hourly heartbeat.
        """
        # ── Fetch fresh candles ──────────────────────────────────────────
        ok = await self.candle_monitor.fetch_and_refresh()
        if not ok:
            logger.warning("Candle fetch failed – skipping this cycle")
            return

        candles = await self.candle_monitor.get_candles()
        if len(candles) < 30:
            logger.warning("Only %d candles available – waiting for more data", len(candles))
            return

        # ── Run strategy engine ──────────────────────────────────────────
        decision = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.engine.analyse(candles),
        )

        signal_dir  = decision["signal"]
        confidence  = decision["confidence"]
        reason      = decision["reason"]
        levels      = decision.get("levels")
        filters_log = decision.get("filters", [])

        # ── Log decision ─────────────────────────────────────────────────
        if signal_dir:
            colour = "green" if signal_dir == "bullish" else "red"
            console.print(
                f"[bold {colour}]▶ {signal_dir.upper()} signal[/bold {colour}] "
                f"conf={confidence:.0%} | {reason[:80]}"
            )
            if levels:
                console.print(
                    f"  Entry={levels['entry']:.4f}  SL={levels['stop_loss']:.4f}  "
                    f"TP={levels['take_profit']:.4f}  R:R={levels['risk_reward']:.2f}"
                )
        else:
            logger.debug("No signal: %s", reason)

        # ── Log individual detector results ──────────────────────────────
        for sr in decision.get("signals", []):
            icon = "" if sr.signal else "–"
            logger.debug(
                "  %s [%s] → %s (conf=%.0f%%) %s",
                icon, sr.name, sr.signal or "none", sr.confidence * 100, sr.reason
            )

        # ── Send alert ───────────────────────────────────────────────────
        if signal_dir:
            sent = await self.alert_manager.send_signal_alert(
                decision=decision,
                symbol=self.symbol,
                candles=candles,
            )
            if sent:
                self.risk_manager.record_alert(signal_dir)
                logger.info("Alert dispatched for %s signal", signal_dir)
            else:
                logger.warning("Alert dispatch failed")

        # ── Paper trader: open position on signal ─────────────────────
        if signal_dir and candles:
            self.paper_trader.on_signal(decision, candles[-1])

        # ── Paper trader: process candle close ────────────────────────
        if candles:
            closed = self.paper_trader.on_candle_close(candles[-1])
            for ct in closed:
                logger.info(
                    "Paper trade #%d closed: %s P&L=%.2f (%s) | balance=%.2f",
                    ct.id, ct.exit_reason, ct.net_pnl, ct.direction,
                    ct.balance_after,
                )

        # ── Hourly heartbeat ─────────────────────────────────────────────
        now = time.time()
        if now - self._last_status >= STATUS_INTERVAL:
            ttc      = await self.candle_monitor.seconds_until_next_close()
            n_candles = len(candles)
            heartbeat = (
                f" *Chiko Heartbeat*\n\n"
                f"Symbol:       `{self.symbol}`\n"
                f"Uptime:       `{_uptime()}`\n"
                f"Candles:      `{n_candles}`\n"
                f"Next close:   `{ttc // 60}m {ttc % 60}s`\n"
                f"Last signal:  `{signal_dir or 'none'}`\n\n"
                + self.paper_trader.status_message()
            )
            await self.alert_manager.send_telegram_text(heartbeat)
            self._last_status = now

    # ------------------------------------------------------------------ #
    async def _sleep_until_next_candle(self) -> None:
        """
        Sleep until CANDLE_BUFFER_SECONDS after the next expected candle close.
        Falls back to check_interval if the timing is unknown.
        """
        ttc = await self.candle_monitor.seconds_until_next_close()

        if ttc > 0:
            sleep_for = ttc + CANDLE_BUFFER_SECONDS
        else:
            # Already past close – use configured check interval as fallback
            sleep_for = self.check_interval

        logger.debug("Sleeping %.0fs until next candle (+%ds buffer)", sleep_for, CANDLE_BUFFER_SECONDS)
        console.print(
            f"[dim]Next analysis in {sleep_for // 60:.0f}m {sleep_for % 60:.0f}s[/dim]"
        )
        await asyncio.sleep(sleep_for)


# ═════════════════════════════════════════════════════════════════════════════
# Supporting coroutines
# ═════════════════════════════════════════════════════════════════════════════

def _handle_email(email_data: dict, alert_queue: queue.Queue) -> None:
    """Called on each new email. Logs it and pushes a Telegram notification."""
    subject = email_data.get("subject", "(no subject)")
    sender  = email_data.get("from",    "(unknown)")
    body    = (email_data.get("body") or "")[:300]
    logger.info("New email | from=%s | subject=%s", sender, subject)
    alert_queue.put(
        f" *New Email*\n"
        f"From: `{sender}`\n"
        f"Subject: _{subject}_\n\n"
        f"{body}…"
    )


def _start_email_listener(alert_queue: queue.Queue) -> None:
    """Entry point for the email daemon thread."""
    email_addr = os.getenv("EMAIL_SENDER", "")
    app_pw     = os.getenv("EMAIL_APP_PASSWORD", "")

    if not email_addr or not app_pw:
        logger.warning("Email listener disabled – EMAIL_SENDER / EMAIL_APP_PASSWORD not set")
        return

    chiko = ChikoEmail(
        email       = email_addr,
        app_password= app_pw,
        alert_queue = alert_queue,
    )
    try:
        chiko.connect()
        chiko.listen(callback=lambda ed: _handle_email(ed, alert_queue))
    except Exception as exc:
        logger.error("Email listener crashed: %s", exc, exc_info=True)


async def _alert_dispatcher(
    alert_q: queue.Queue,
    alert_manager: AlertManager,
) -> None:
    """
    Drains the thread-safe `alert_queue` and sends each message via Telegram.
    Runs as an asyncio task alongside the main trading loop.
    """
    loop = asyncio.get_event_loop()
    while True:
        try:
            # Non-blocking get with a short timeout so we yield to the event loop
            try:
                message = alert_q.get_nowait()
            except queue.Empty:
                await asyncio.sleep(1)
                continue

            if message:
                await alert_manager.send_telegram_text(str(message))

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Dispatcher error: %s", exc)
            await asyncio.sleep(2)


def _uptime() -> str:
    from system.status import uptime
    return uptime()


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Chiko Trading Monitor",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--symbol",         default=DEFAULT_SYMBOL,
                        help="Binance trading pair (e.g. BTCUSDT)")
    parser.add_argument("--granularity",    default=DEFAULT_GRANULARITY, type=int,
                        help="Candle size in seconds (300 = 5m, 900 = 15m, etc.)")
    parser.add_argument("--check-interval", default=DEFAULT_CHECK_INTERVAL, type=int,
                        help="Fallback polling interval in seconds (used only when "
                             "candle-close timing is unavailable)")
    return parser.parse_args()


# ═════════════════════════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════════════════════════
async def main_async() -> None:
    alert_queue = queue.Queue()

    bot  = TelegramBot()
    args = parse_args()
    app  = TradingMonitorApp(args, telegram_bot=bot, alert_queue=alert_queue)

    # ── Email listener thread ────────────────────────────────────────────
    email_thread = threading.Thread(
        target=_start_email_listener,
        args=(alert_queue,),
        daemon=True,
        name="EmailListener",
    )
    email_thread.start()

    # ── Alert dispatcher task ────────────────────────────────────────────
    dispatcher = asyncio.create_task(
        _alert_dispatcher(alert_queue, app.alert_manager),
        name="AlertDispatcher",
    )

    # ── Run bot + trading monitor concurrently ───────────────────────────
    try:
        await asyncio.gather(
            bot.start(),   # telegram polling (non-blocking after start)
            app.run(),     # analysis loop
        )
    except Exception as exc:
        logger.error("Fatal error in main: %s", exc, exc_info=True)
    finally:
        dispatcher.cancel()
        try:
            await dispatcher
        except asyncio.CancelledError:
            pass
        await bot.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except ConfigurationError as exc:
        logger.error("Configuration error: %s", exc)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as exc:
        logger.error("Unhandled error: %s", exc, exc_info=True)
        sys.exit(1)
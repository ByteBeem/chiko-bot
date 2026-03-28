"""
alert_manager.py
~~~~~~~~~~~~~~~~~
AlertManager: sends trading alerts via Email and Telegram.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from strategy import CandleData

logger = logging.getLogger(__name__)
LOCAL_TZ = ZoneInfo("Africa/Johannesburg")


class ConfigurationError(Exception):
    pass


class AlertManager:
    """
    Sends trading alerts via Email and Telegram.

    Receives a fully-formed `decision` dict from StrategyEngine and converts
    it into rich notifications for email and Telegram.
    """

    def __init__(
        self,
        email_sender,
        telegram_bot=None,
        template_dir: str = "templates",
    ) -> None:
        self.email_sender = email_sender
        self.telegram_bot = telegram_bot
        self.template_dir = Path(template_dir)
        self.receiver     = self._require("RECEIVER_EMAIL").strip()
        self.username     = os.getenv("USERNAME", "Trader")
        self._lock        = asyncio.Lock()

        self.template_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _require(key: str) -> str:
        val = os.getenv(key)
        if not val:
            raise ConfigurationError(f"Missing required env var: {key}")
        return val

    # ------------------------------------------------------------------ #
    async def send_signal_alert(
        self,
        decision: dict,
        symbol: str,
        candles: list[CandleData],
    ) -> bool:
        """
        Send full alert for an actionable StrategyEngine decision.
        Sends both email and Telegram notifications.
        """
        async with self._lock:
            signal     = decision["signal"]
            confidence = decision["confidence"]
            reason     = decision["reason"]
            levels     = decision.get("levels")
            timestamp  = decision.get("timestamp", datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S"))
            action     = "BUY 📈" if signal == "bullish" else "SELL 📉"

            # ── Build Telegram message ──────────────────────────────────
            tg_lines = [
                f"🚨 *TRADING SIGNAL – {symbol}*",
                f"",
                f"Direction:  *{signal.upper()}*",
                f"Action:     *{action}*",
                f"Confidence: *{confidence:.0%}*",
                f"Time:       `{timestamp}`",
            ]
            if levels:
                tg_lines += [
                    f"",
                    f"📊 *Levels (ATR-based)*",
                    f"Entry:       `{levels['entry']:.4f}`",
                    f"Stop Loss:   `{levels['stop_loss']:.4f}`",
                    f"Take Profit: `{levels['take_profit']:.4f}`",
                    f"Risk/Reward: `{levels['risk_reward']:.2f}R`",
                ]
            tg_lines += [
                f"",
                f"📋 *Reason*",
                f"_{reason}_",
                f"",
                f"📉 *Last 3 Candles*",
            ]
            for i, c in enumerate(candles[-3:], 1):
                tg_lines.append(
                    f"  {i}. {c.time_str} | O={c.open:.4f} H={c.high:.4f} "
                    f"L={c.low:.4f} C={c.close:.4f} | {c.candle_type().upper()}"
                )
            tg_message = "\n".join(tg_lines)

            # ── Build Email HTML ────────────────────────────────────────
            html = self._build_email_html(
                symbol=symbol,
                signal=signal,
                action=action,
                confidence=confidence,
                reason=reason,
                timestamp=timestamp,
                levels=levels,
                candles=candles[-3:],
            )
            text_fallback = tg_message.replace("*", "").replace("_", "").replace("`", "")
            subject = (
                f"{'🟢' if signal == 'bullish' else '🔴'} {signal.capitalize()} Signal – "
                f"{symbol} | {confidence:.0%} confidence | {timestamp}"
            )

            # ── Send concurrently ───────────────────────────────────────
            email_ok = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.email_sender.send_email(
                    receiver=self.receiver,
                    subject=subject,
                    html_content=html,
                    text_fallback=text_fallback,
                ),
            )

            tg_ok = await self._send_telegram(tg_message, symbol=symbol, direction=signal)

            if email_ok or tg_ok:
                logger.info(
                    "Alert sent: %s %s (conf=%.0f%%) | email=%s tg=%s",
                    signal, symbol, confidence * 100, email_ok, tg_ok,
                )
            return email_ok or tg_ok

    async def send_telegram_text(self, text: str) -> bool:
        """Send a plain Telegram message (for heartbeats, errors, etc.)."""
        return await self._send_telegram(text)

    # ------------------------------------------------------------------ #
    async def _send_telegram(
        self,
        text: str,
        symbol: str | None = None,
        direction: str | None = None,
    ) -> bool:
        from tgbot.listeners import build_signal_keyboard
        ADMIN_CHAT_ID = int(os.getenv("TELEGRAM_ADMIN_CHAT_ID", 0))
        if not self.telegram_bot or not ADMIN_CHAT_ID:
            return False
        try:
            markup = None
            if symbol and direction:
                markup = build_signal_keyboard(symbol, direction)
            return await self.telegram_bot.send_signal_alert(
                chat_id=ADMIN_CHAT_ID,
                text=text,
                reply_markup=markup,
            )
        except Exception as exc:
            logger.error("Telegram send error: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    def _build_email_html(
        self,
        symbol: str,
        signal: str,
        action: str,
        confidence: float,
        reason: str,
        timestamp: str,
        levels: dict | None,
        candles: list[CandleData],
    ) -> str:
        template_path = self.template_dir / "signal_alert.html"
        if template_path.exists():
            try:
                tmpl = template_path.read_text(encoding="utf-8")
                # Simple token substitution — expand as needed
                tmpl = tmpl.replace("{{USERNAME}}", self.username)
                tmpl = tmpl.replace("{{SYMBOL}}", symbol)
                tmpl = tmpl.replace("{{SIGNAL}}", signal.upper())
                tmpl = tmpl.replace("{{ACTION}}", action)
                tmpl = tmpl.replace("{{CONFIDENCE}}", f"{confidence:.0%}")
                tmpl = tmpl.replace("{{TIMESTAMP}}", timestamp)
                tmpl = tmpl.replace("{{REASON}}", reason)
                return tmpl
            except Exception as exc:
                logger.warning("Template load failed (%s), using fallback", exc)

        # ── Built-in fallback template ─────────────────────────────────
        colour   = "#27ae60" if signal == "bullish" else "#e74c3c"
        candle_rows = "".join(
            f"<tr><td>{c.time_str}</td><td>{c.open:.4f}</td><td>{c.high:.4f}</td>"
            f"<td>{c.low:.4f}</td><td>{c.close:.4f}</td>"
            f"<td style='color:{colour}'>{c.candle_type().upper()}</td></tr>"
            for c in candles
        )
        levels_html = ""
        if levels:
            levels_html = f"""
            <table style="width:100%;border-collapse:collapse;margin:12px 0">
              <tr><th style="text-align:left;padding:6px;background:#f0f0f0">Level</th>
                  <th style="text-align:right;padding:6px;background:#f0f0f0">Price</th></tr>
              <tr><td style="padding:6px">Entry</td>       <td style="text-align:right;padding:6px">{levels['entry']:.4f}</td></tr>
              <tr><td style="padding:6px">Stop Loss</td>   <td style="text-align:right;padding:6px;color:#e74c3c">{levels['stop_loss']:.4f}</td></tr>
              <tr><td style="padding:6px">Take Profit</td> <td style="text-align:right;padding:6px;color:#27ae60">{levels['take_profit']:.4f}</td></tr>
              <tr><td style="padding:6px">Risk/Reward</td> <td style="text-align:right;padding:6px"><strong>{levels['risk_reward']:.2f}R</strong></td></tr>
            </table>"""

        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body{{font-family:'Segoe UI',Arial,sans-serif;background:#f4f6f8;margin:0;padding:20px}}
  .card{{background:#fff;border-radius:10px;box-shadow:0 2px 12px rgba(0,0,0,.08);max-width:640px;margin:0 auto;overflow:hidden}}
  .header{{background:{colour};color:#fff;padding:24px 28px}}
  .header h1{{margin:0;font-size:1.6rem}}
  .header p{{margin:4px 0 0;opacity:.85;font-size:.95rem}}
  .body{{padding:24px 28px}}
  .badge{{display:inline-block;background:{colour};color:#fff;border-radius:4px;padding:4px 12px;font-weight:700;font-size:1.1rem;margin-bottom:16px}}
  .meta{{color:#555;font-size:.9rem;line-height:1.8}}
  table{{width:100%;border-collapse:collapse;margin-top:16px}}
  th,td{{padding:8px 10px;border-bottom:1px solid #eee;font-size:.88rem}}
  th{{background:#f8f9fa;font-weight:600;text-align:left}}
  .footer{{background:#f8f9fa;color:#888;font-size:.8rem;padding:14px 28px;border-top:1px solid #eee}}
</style></head>
<body>
<div class="card">
  <div class="header">
    <h1>{"📈" if signal == "bullish" else "📉"} {signal.upper()} Signal – {symbol}</h1>
    <p>{timestamp}</p>
  </div>
  <div class="body">
    <p>Hello {self.username},</p>
    <p>Chiko has detected a high-confidence trading signal on <strong>{symbol}</strong>.</p>
    <div class="badge">{action}</div>
    <div class="meta">
      <strong>Confidence:</strong> {confidence:.0%}<br>
      <strong>Reason:</strong> {reason}
    </div>
    {levels_html}
    <h3 style="margin-top:20px;font-size:1rem">Last 3 Candles</h3>
    <table>
      <tr><th>Time</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>Type</th></tr>
      {candle_rows}
    </table>
  </div>
  <div class="footer">Chiko Trading Monitor v2.0.0 – automated alert, not financial advice.</div>
</div>
</body></html>"""
"""
paper_trader.py
~~~~~~~~~~~~~~~~
Paper Trading Engine – runs alongside the live CandleMonitor and
StrategyEngine, simulating trade execution with a virtual balance.

Every signal that would be sent as an alert is also "executed" here,
tracking P&L, open positions, and trade history in real time.

Telegram notifications show both the signal AND the paper trade result
(entry, current P&L, exit when SL/TP is hit).

Usage (in main.py)
------------------
    paper = PaperTrader(balance=1000.0, risk_pct=0.01)
    ...
    # Inside analysis cycle, after engine.analyse():
    if decision["signal"]:
        paper.on_signal(decision, current_candle)

    # On each new candle close:
    paper.on_candle_close(latest_closed_candle)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Callable
from zoneinfo import ZoneInfo

from strategy.models import CandleData

logger = logging.getLogger(__name__)
LOCAL_TZ = ZoneInfo("Africa/Johannesburg")


# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class PaperPosition:
    """An open paper trade."""
    id:           int
    direction:    str            # "bullish" | "bearish"
    entry_price:  float
    stop_loss:    float
    take_profit:  float
    quantity:     float
    entry_time:   str
    entry_bar:    int            # candle index at entry
    confidence:   float
    signal_names: str
    fee_pct:      float = 0.00075

    @property
    def entry_fee(self) -> float:
        return self.quantity * self.entry_price * self.fee_pct

    def unrealised_pnl(self, current_price: float) -> float:
        if self.direction == "bullish":
            return (current_price - self.entry_price) * self.quantity
        return (self.entry_price - current_price) * self.quantity

    def check_exit(self, candle: CandleData) -> Optional[tuple[str, float]]:
        """
        Check if this candle hits SL or TP.
        Returns ("sl"|"tp", exit_price) or None.
        """
        if self.direction == "bullish":
            if candle.low <= self.stop_loss:
                return "sl", self.stop_loss
            if candle.high >= self.take_profit:
                return "tp", self.take_profit
        else:
            if candle.high >= self.stop_loss:
                return "sl", self.stop_loss
            if candle.low <= self.take_profit:
                return "tp", self.take_profit
        return None

    def to_summary(self, current_price: float) -> str:
        pnl = self.unrealised_pnl(current_price)
        sign = "+" if pnl >= 0 else ""
        return (
            f"{'' if self.direction == 'bullish' else ''} "
            f"*{self.direction.upper()}* open since {self.entry_time}\n"
            f"Entry: `{self.entry_price:.4f}` | Now: `{current_price:.4f}`\n"
            f"SL: `{self.stop_loss:.4f}` | TP: `{self.take_profit:.4f}`\n"
            f"Unrealised P&L: `{sign}{pnl:.2f} USDT`"
        )


@dataclass
class ClosedPaperTrade:
    id:           int
    direction:    str
    entry_price:  float
    exit_price:   float
    stop_loss:    float
    take_profit:  float
    quantity:     float
    entry_time:   str
    exit_time:    str
    net_pnl:      float
    exit_reason:  str
    confidence:   float
    signal_names: str
    balance_after: float


# ─────────────────────────────────────────────────────────────────────────────
class PaperTrader:
    """
    Simulates live trading with a virtual balance.

    Parameters
    ----------
    balance          : float – starting virtual balance (USDT)
    risk_pct         : float – % of balance risked per trade (0.01 = 1%)
    fee_pct          : float – simulated Binance taker fee
    max_hold_candles : int   – force-close after N candles
    one_position     : bool  – if True, only one open position at a time
    notify           : Callable[[str], None] – called with a Markdown string
                       on every trade open/close (wire up to Telegram)
    """

    def __init__(
        self,
        balance:          float = 1_000.0,
        risk_pct:         float = 0.01,
        fee_pct:          float = 0.00075,
        max_hold_candles: int   = 12,
        one_position:     bool  = True,
        notify:           Optional[Callable[[str], None]] = None,
    ) -> None:
        self.initial_balance  = balance
        self.balance          = balance
        self.risk_pct         = risk_pct
        self.fee_pct          = fee_pct
        self.max_hold_candles = max_hold_candles
        self.one_position     = one_position
        self.notify           = notify

        self._open_positions: list[PaperPosition] = []
        self._closed_trades:  list[ClosedPaperTrade] = []
        self._trade_counter   = 0
        self._candle_counter  = 0

    # ------------------------------------------------------------------ #
    # Signal ingestion
    # ------------------------------------------------------------------ #
    def on_signal(self, decision: dict, candle: CandleData) -> Optional[PaperPosition]:
        """
        Called when StrategyEngine fires an actionable decision.
        Opens a paper position if conditions allow.
        """
        direction = decision.get("signal")
        levels    = decision.get("levels")
        if not direction or not levels:
            return None

        # One-position gate
        if self.one_position and self._open_positions:
            logger.debug("Paper trader: skipping signal – position already open")
            return None

        entry = candle.close   # simulate entry at current close

        # Apply ATR levels from engine (recalculate from entry)
        sl = levels["stop_loss"]
        tp = levels["take_profit"]

        # Recalculate from actual entry price (engine used close of signal bar)
        sl_dist = abs(levels["entry"] - sl)
        tp_dist = abs(levels["entry"] - tp)
        if direction == "bullish":
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            sl = entry + sl_dist
            tp = entry - tp_dist

        # Position size
        risk_amount   = self.balance * self.risk_pct
        risk_per_unit = abs(entry - sl)
        if risk_per_unit == 0:
            return None
        quantity = risk_amount / risk_per_unit

        self._trade_counter += 1
        signal_names = ", ".join(
            sr.name for sr in decision.get("signals", []) if sr.signal == direction
        )

        pos = PaperPosition(
            id           = self._trade_counter,
            direction    = direction,
            entry_price  = entry,
            stop_loss    = sl,
            take_profit  = tp,
            quantity     = quantity,
            entry_time   = candle.time_str,
            entry_bar    = self._candle_counter,
            confidence   = decision.get("confidence", 0),
            signal_names = signal_names,
            fee_pct      = self.fee_pct,
        )
        self._open_positions.append(pos)

        action = "BUY" if direction == "bullish" else "SELL"
        msg = (
            f" *PAPER TRADE #{pos.id} OPENED*\n\n"
            f"Action:     *{action}*\n"
            f"Entry:      `{entry:.4f}`\n"
            f"Stop Loss:  `{sl:.4f}`\n"
            f"Take Profit:`{tp:.4f}`\n"
            f"Qty:        `{quantity:.6f}`\n"
            f"R:R:        `{tp_dist / sl_dist:.2f}`\n"
            f"Confidence: `{pos.confidence:.0%}`\n"
            f"Signals:    _{signal_names}_\n"
            f"Balance:    `${self.balance:,.2f}`"
        )
        self._notify(msg)
        logger.info("Paper trade #%d opened: %s @ %.4f", pos.id, direction, entry)
        return pos

    # ------------------------------------------------------------------ #
    # Candle close processing
    # ------------------------------------------------------------------ #
    def on_candle_close(self, candle: CandleData) -> list[ClosedPaperTrade]:
        """
        Called on each new closed candle.
        Checks all open positions for SL/TP or time exit.
        Returns list of trades closed this bar.
        """
        self._candle_counter += 1
        closed_this_bar: list[ClosedPaperTrade] = []
        remaining: list[PaperPosition] = []

        for pos in self._open_positions:
            exit_info = pos.check_exit(candle)
            bars_held = self._candle_counter - pos.entry_bar

            if exit_info:
                reason, exit_price = exit_info
            elif bars_held >= self.max_hold_candles:
                exit_price = candle.close
                reason     = "time"
            else:
                remaining.append(pos)
                continue

            # ── Close the position ──────────────────────────────────
            exit_fee  = pos.quantity * exit_price * self.fee_pct
            total_fee = pos.entry_fee + exit_fee

            if pos.direction == "bullish":
                gross_pnl = (exit_price - pos.entry_price) * pos.quantity
            else:
                gross_pnl = (pos.entry_price - exit_price) * pos.quantity
            net_pnl = gross_pnl - total_fee

            self.balance += net_pnl
            self.balance  = max(self.balance, 0.0)

            trade = ClosedPaperTrade(
                id            = pos.id,
                direction     = pos.direction,
                entry_price   = pos.entry_price,
                exit_price    = exit_price,
                stop_loss     = pos.stop_loss,
                take_profit   = pos.take_profit,
                quantity      = pos.quantity,
                entry_time    = pos.entry_time,
                exit_time     = candle.time_str,
                net_pnl       = round(net_pnl, 4),
                exit_reason   = reason,
                confidence    = pos.confidence,
                signal_names  = pos.signal_names,
                balance_after = round(self.balance, 2),
            )
            self._closed_trades.append(trade)
            closed_this_bar.append(trade)

            icon   = "" if net_pnl > 0 else ""
            action = "BUY" if pos.direction == "bullish" else "SELL"
            msg = (
                f"{icon} *PAPER TRADE #{pos.id} CLOSED*\n\n"
                f"Action:    *{action}*\n"
                f"Entry:     `{pos.entry_price:.4f}`\n"
                f"Exit:      `{exit_price:.4f}` ({reason.upper()})\n"
                f"Net P&L:   `{'+'if net_pnl>=0 else ''}{net_pnl:.2f} USDT`\n"
                f"Balance:   `${self.balance:,.2f}`\n"
                f"Win rate:  `{self.win_rate:.1%}` ({self.n_winners}W / {self.n_losers}L)"
            )
            self._notify(msg)
            logger.info(
                "Paper trade #%d closed: %s @ %.4f (%s) P&L=%.2f | balance=%.2f",
                pos.id, reason, exit_price, pos.direction, net_pnl, self.balance,
            )

        self._open_positions = remaining
        return closed_this_bar

    # ------------------------------------------------------------------ #
    # Stats
    # ------------------------------------------------------------------ #
    @property
    def n_trades(self) -> int:
        return len(self._closed_trades)

    @property
    def n_winners(self) -> int:
        return sum(1 for t in self._closed_trades if t.net_pnl > 0)

    @property
    def n_losers(self) -> int:
        return self.n_trades - self.n_winners

    @property
    def win_rate(self) -> float:
        return self.n_winners / self.n_trades if self.n_trades else 0.0

    @property
    def total_pnl(self) -> float:
        return sum(t.net_pnl for t in self._closed_trades)

    @property
    def return_pct(self) -> float:
        return (self.balance - self.initial_balance) / self.initial_balance * 100

    def status_message(self) -> str:
        open_count = len(self._open_positions)
        open_info  = ""
        if self._open_positions:
            for p in self._open_positions:
                # We don't have the current price here – caller can enrich
                open_info += f"\n  • #{p.id} {p.direction} from {p.entry_time}"

        return (
            f" *Paper Trader Status*\n\n"
            f"Balance:   `${self.balance:,.2f}` ({self.return_pct:+.2f}%)\n"
            f"Trades:    `{self.n_trades}` closed "
            f"({self.n_winners}W / {self.n_losers}L | {self.win_rate:.1%})\n"
            f"Total P&L: `${self.total_pnl:+,.2f}`\n"
            f"Open pos:  `{open_count}`{open_info}"
        )

    def get_closed_trades(self) -> list[ClosedPaperTrade]:
        return self._closed_trades.copy()

    # ------------------------------------------------------------------ #
    def _notify(self, msg: str) -> None:
        if self.notify:
            try:
                self.notify(msg)
            except Exception as exc:
                logger.error("Paper trader notify error: %s", exc)
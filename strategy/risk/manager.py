"""
strategy/risk/manager.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Risk management layer.

Responsibilities:
  - Compute suggested stop-loss and take-profit levels using ATR.
  - Validate that a signal's confidence meets the minimum threshold.
  - Prevent alert spam by enforcing a per-signal cooldown.
  - Calculate position size using fixed fractional risk.
"""
from __future__ import annotations
import time
import logging
from typing import Optional
from ..models import CandleData, SignalResult
from ..indicators import atr

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Stateful risk manager – keeps track of the last alert time to
    enforce the cooldown window.

    Parameters
    ----------
    min_confidence    : float  – reject signals below this (0-1, default 0.6)
    atr_period        : int    – ATR period for SL/TP calculation
    atr_sl_multiplier : float  – stop-loss = ATR * multiplier (default 1.5)
    atr_tp_multiplier : float  – take-profit = ATR * multiplier (default 2.5)
    cooldown_seconds  : int    – minimum seconds between alerts for the same
                                 signal direction (default 900 = 15 min)
    """

    def __init__(
        self,
        min_confidence: float = 0.6,
        atr_period: int = 14,
        atr_sl_multiplier: float = 1.5,
        atr_tp_multiplier: float = 2.5,
        cooldown_seconds: int = 900,
    ) -> None:
        self.min_confidence = min_confidence
        self.atr_period = atr_period
        self.atr_sl_multiplier = atr_sl_multiplier
        self.atr_tp_multiplier = atr_tp_multiplier
        self.cooldown_seconds = cooldown_seconds

        self._last_alert: dict[str, float] = {}   # direction → last epoch

    # ---------------------------------------------------------------- #

    def validate(self, result: SignalResult) -> tuple[bool, str]:
        """
        Check whether `result` should be acted upon.

        Returns (approved: bool, reason: str)
        """
        if not result.is_actionable:
            return False, "Signal is not actionable (no direction or zero confidence)"

        if result.confidence < self.min_confidence:
            return False, (
                f"Confidence {result.confidence:.0%} below minimum "
                f"{self.min_confidence:.0%}"
            )

        direction = result.signal  # "bullish" | "bearish"
        last_time = self._last_alert.get(direction, 0)
        elapsed = time.time() - last_time

        if elapsed < self.cooldown_seconds:
            remaining = int(self.cooldown_seconds - elapsed)
            return False, (
                f"Cooldown active – {remaining}s remaining for {direction} alerts"
            )

        return True, "Signal approved"

    def record_alert(self, direction: str) -> None:
        """Call this after successfully sending an alert."""
        self._last_alert[direction] = time.time()
        logger.debug("Alert recorded for direction '%s'", direction)

    # ---------------------------------------------------------------- #

    def compute_levels(
        self,
        candles: list[CandleData],
        signal: str,
        entry_price: Optional[float] = None,
    ) -> Optional[dict[str, float]]:
        """
        Compute ATR-based stop-loss and take-profit levels.

        Returns dict with: entry, stop_loss, take_profit, atr, risk_reward
        """
        atr_val = atr(candles, self.atr_period)
        if atr_val is None:
            return None

        price = entry_price or candles[-1].close

        if signal == "bullish":
            stop_loss   = price - atr_val * self.atr_sl_multiplier
            take_profit = price + atr_val * self.atr_tp_multiplier
        else:
            stop_loss   = price + atr_val * self.atr_sl_multiplier
            take_profit = price - atr_val * self.atr_tp_multiplier

        risk        = abs(price - stop_loss)
        reward      = abs(price - take_profit)
        risk_reward = round(reward / risk, 2) if risk else 0

        return {
            "entry":       round(price, 4),
            "stop_loss":   round(stop_loss, 4),
            "take_profit": round(take_profit, 4),
            "atr":         round(atr_val, 4),
            "risk_reward": risk_reward,
        }

    def position_size(
        self,
        account_balance: float,
        risk_pct: float,
        entry: float,
        stop_loss: float,
    ) -> float:
        """
        Fixed-fractional position sizing.

        position_size = (balance * risk_pct) / |entry - stop_loss|

        Returns units (e.g. BTC quantity).
        """
        risk_amount = account_balance * risk_pct
        risk_per_unit = abs(entry - stop_loss)
        if risk_per_unit == 0:
            return 0.0
        return round(risk_amount / risk_per_unit, 6)
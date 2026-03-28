"""
strategy/signals/ema_cross.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Detects EMA fast/slow crossovers using the last 400 candles.
"""
from __future__ import annotations
from ..models import CandleData, SignalResult
from ..indicators import ema_series
from .base import BaseSignal
import math


class EmaCrossSignal(BaseSignal):
    """
    Fires on golden cross (fast EMA crosses above slow EMA → bullish)
    or death cross (fast EMA crosses below slow EMA → bearish).

    Parameters
    ----------
    fast : int  – fast EMA period (default 9)
    slow : int  – slow EMA period (default 21)
    """

    name = "ema_cross"

    def __init__(self, fast: int = 9, slow: int = 21) -> None:
        if fast >= slow:
            raise ValueError("fast period must be less than slow period")
        self.fast = fast
        self.slow = slow

    def detect(self, candles: list[CandleData]) -> SignalResult:
        if len(candles) < self.slow + 2:
            return self._no_signal(
                f"Not enough candles for EMA{self.slow} (need {self.slow + 2})"
            )

        fast_series = ema_series(candles, self.fast)
        slow_series = ema_series(candles, self.slow)

        # Only look at the last 2 valid (non-NaN) pairs
        valid_pairs = [
            (f, s)
            for f, s in zip(fast_series, slow_series)
            if not (math.isnan(f) or math.isnan(s))
        ]

        if len(valid_pairs) < 2:
            return self._no_signal("Insufficient valid EMA values")

        prev_fast, prev_slow = valid_pairs[-2]
        curr_fast, curr_slow = valid_pairs[-1]

        metadata = {
            f"ema{self.fast}": round(curr_fast, 4),
            f"ema{self.slow}": round(curr_slow, 4),
        }

        # Golden cross: fast crossed above slow
        if prev_fast <= prev_slow and curr_fast > curr_slow:
            gap_pct = (curr_fast - curr_slow) / curr_slow
            confidence = min(0.5 + gap_pct * 100, 1.0)
            return self._bullish(
                f"EMA{self.fast} crossed above EMA{self.slow} (golden cross)",
                candles[-3:],
                confidence=round(confidence, 2),
                metadata=metadata,
            )

        # Death cross: fast crossed below slow
        if prev_fast >= prev_slow and curr_fast < curr_slow:
            gap_pct = (curr_slow - curr_fast) / curr_slow
            confidence = min(0.5 + gap_pct * 100, 1.0)
            return self._bearish(
                f"EMA{self.fast} crossed below EMA{self.slow} (death cross)",
                candles[-3:],
                confidence=round(confidence, 2),
                metadata=metadata,
            )

        return self._no_signal("No EMA crossover", candles[-3:])
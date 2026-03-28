"""
strategy/signals/macd_signal.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
MACD histogram flip signal (momentum confirmation).
"""
from __future__ import annotations
from ..models import CandleData, SignalResult
from ..indicators import macd
from .base import BaseSignal


class MacdSignal(BaseSignal):
    """
    Fires when the MACD histogram flips sign:
      - Negative → Positive : bullish momentum
      - Positive → Negative : bearish momentum

    Parameters
    ----------
    fast, slow, signal_period: standard MACD settings
    """

    name = "macd_histogram_flip"

    def __init__(
        self,
        fast: int = 12,
        slow: int = 26,
        signal_period: int = 9,
    ) -> None:
        self.fast = fast
        self.slow = slow
        self.signal_period = signal_period

    def detect(self, candles: list[CandleData]) -> SignalResult:
        if len(candles) < self.slow + self.signal_period + 1:
            return self._no_signal("Not enough candles for MACD")

        curr = macd(candles,       self.fast, self.slow, self.signal_period)
        prev = macd(candles[:-1],  self.fast, self.slow, self.signal_period)

        if curr is None or prev is None:
            return self._no_signal("MACD calculation failed")

        metadata = {
            "macd":      round(curr["macd"], 6),
            "signal":    round(curr["signal"], 6),
            "histogram": round(curr["histogram"], 6),
        }

        prev_hist = prev["histogram"]
        curr_hist = curr["histogram"]

        if prev_hist < 0 and curr_hist >= 0:
            confidence = min(0.5 + abs(curr_hist) * 10_000, 1.0)
            return self._bullish(
                f"MACD histogram flipped positive ({prev_hist:.6f} → {curr_hist:.6f})",
                candles[-3:],
                confidence=round(confidence, 2),
                metadata=metadata,
            )

        if prev_hist > 0 and curr_hist <= 0:
            confidence = min(0.5 + abs(curr_hist) * 10_000, 1.0)
            return self._bearish(
                f"MACD histogram flipped negative ({prev_hist:.6f} → {curr_hist:.6f})",
                candles[-3:],
                confidence=round(confidence, 2),
                metadata=metadata,
            )

        return self._no_signal(
            f"MACD no flip (hist={curr_hist:.6f})",
            candles[-3:],
        )
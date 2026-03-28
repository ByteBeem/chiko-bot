"""
strategy/signals/rsi_signal.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
RSI-based oversold / overbought signal with divergence awareness.
"""
from __future__ import annotations
from ..models import CandleData, SignalResult
from ..indicators import rsi
from .base import BaseSignal


class RsiSignal(BaseSignal):
    """
    Fires when RSI exits the overbought/oversold zone:
      - RSI crosses back above `oversold` from below  → bullish
      - RSI crosses back below `overbought` from above → bearish

    Parameters
    ----------
    period      : RSI period (default 14)
    oversold    : Lower threshold (default 30)
    overbought  : Upper threshold (default 70)
    """

    name = "rsi_reversal"

    def __init__(
        self,
        period: int = 14,
        oversold: float = 30.0,
        overbought: float = 70.0,
    ) -> None:
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def detect(self, candles: list[CandleData]) -> SignalResult:
        if len(candles) < self.period + 2:
            return self._no_signal("Not enough candles for RSI")

        # We need at least 2 RSI values to detect a cross
        rsi_now  = rsi(candles,       self.period)
        rsi_prev = rsi(candles[:-1],  self.period)

        if rsi_now is None or rsi_prev is None:
            return self._no_signal("RSI calculation failed")

        metadata = {"rsi": round(rsi_now, 2), "rsi_prev": round(rsi_prev, 2)}

        # Crossed out of oversold → bullish
        if rsi_prev < self.oversold and rsi_now >= self.oversold:
            confidence = min((self.oversold - rsi_prev) / self.oversold + 0.5, 1.0)
            return self._bullish(
                f"RSI exited oversold zone ({rsi_prev:.1f} → {rsi_now:.1f})",
                candles[-3:],
                confidence=round(confidence, 2),
                metadata=metadata,
            )

        # Crossed out of overbought → bearish
        if rsi_prev > self.overbought and rsi_now <= self.overbought:
            confidence = min((rsi_prev - self.overbought) / (100 - self.overbought) + 0.5, 1.0)
            return self._bearish(
                f"RSI exited overbought zone ({rsi_prev:.1f} → {rsi_now:.1f})",
                candles[-3:],
                confidence=round(confidence, 2),
                metadata=metadata,
            )

        return self._no_signal(
            f"RSI neutral at {rsi_now:.1f}",
            candles[-3:],
        )
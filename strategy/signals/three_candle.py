"""
strategy/signals/three_candle.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Detects 3 consecutive bullish or bearish candles (original logic,
now with configurable minimum body ratio and volume confirmation).
"""
from __future__ import annotations
from ..models import CandleData, SignalResult
from ..indicators import volume_ratio
from .base import BaseSignal


class ThreeCandleSignal(BaseSignal):
    """
    Fires when the last N candles are all the same direction.

    Parameters
    ----------
    count : int
        How many consecutive candles to require (default 3).
    min_body_ratio : float
        Minimum body/range ratio to exclude doji candles (default 0.3).
    require_volume_confirm : bool
        If True, also requires latest volume >= avg volume (default False).
    """

    name = "three_candle_consecutive"

    def __init__(
        self,
        count: int = 3,
        min_body_ratio: float = 0.3,
        require_volume_confirm: bool = False,
    ) -> None:
        self.count = count
        self.min_body_ratio = min_body_ratio
        self.require_volume_confirm = require_volume_confirm

    def detect(self, candles: list[CandleData]) -> SignalResult:
        if len(candles) < self.count:
            return self._no_signal(
                f"Not enough candles (need {self.count}, got {len(candles)})",
                candles,
            )

        window = candles[-self.count:]

        # Each candle must have a meaningful body (no doji)
        for c in window:
            if c.range > 0 and (c.body / c.range) < self.min_body_ratio:
                return self._no_signal(
                    f"Doji/indecision candle at {c.time_str} – signal ignored",
                    window,
                )

        types = [c.candle_type() for c in window]

        if all(t == "bullish" for t in types):
            direction = "bullish"
        elif all(t == "bearish" for t in types):
            direction = "bearish"
        else:
            return self._no_signal("Mixed candle direction", window)

        # Optional: volume confirmation
        if self.require_volume_confirm:
            ratio = volume_ratio(candles, period=20)
            if ratio is not None and ratio < 1.0:
                return self._no_signal(
                    f"Volume below average (ratio={ratio:.2f}) – signal rejected",
                    window,
                )

        confidence = self._score_confidence(window, direction)
        reason = (
            f"{self.count} consecutive {direction} candles detected "
            f"(confidence {confidence:.0%})"
        )

        if direction == "bullish":
            return self._bullish(reason, window, confidence)
        return self._bearish(reason, window, confidence)

    # ---------------------------------------------------------------- #

    def _score_confidence(self, window: list[CandleData], direction: str) -> float:
        """
        Heuristic confidence score.
        Considers: body ratios, momentum (each close > prev close for bullish),
        and wick symmetry.
        """
        scores: list[float] = []

        for i, c in enumerate(window):
            body_score = (c.body / c.range) if c.range else 0
            scores.append(body_score)

        # Reward increasing closes (bullish) or decreasing closes (bearish)
        if len(window) >= 2:
            momentum_ok = all(
                window[i].close > window[i - 1].close
                for i in range(1, len(window))
            ) if direction == "bullish" else all(
                window[i].close < window[i - 1].close
                for i in range(1, len(window))
            )
            if momentum_ok:
                scores.append(1.0)
            else:
                scores.append(0.5)

        return round(min(sum(scores) / len(scores), 1.0), 2)
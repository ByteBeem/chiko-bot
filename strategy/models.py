"""
strategy/models.py
~~~~~~~~~~~~~~~~~~
Shared data models used throughout the Strategy package.
These are pure-Python dataclasses with no external dependencies so they
can be imported safely anywhere.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class CandleData:
    """Immutable snapshot of a single OHLCV candle."""

    open_time: int          # Unix epoch seconds (candle open)
    time_str: str           # Human-readable local time string
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    is_closed: bool = True

    # ------------------------------------------------------------------ #
    # Mutators (only used on the forming candle)
    # ------------------------------------------------------------------ #
    def update(self, high: float, low: float, close: float, volume: float = 0.0) -> None:
        """Merge a tick update into the forming candle."""
        self.high = max(self.high, high)
        self.low = min(self.low, low)
        self.close = close
        if volume:
            self.volume = volume

    # ------------------------------------------------------------------ #
    # Derived properties
    # ------------------------------------------------------------------ #
    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def is_doji(self) -> bool:
        return self.range > 0 and (self.body / self.range) < 0.1

    def candle_type(self) -> str:
        if self.is_doji:
            return "doji"
        return "bullish" if self.is_bullish else "bearish"

    # Backwards-compat alias used in legacy code
    def get_type(self) -> str:
        return self.candle_type()

    # ------------------------------------------------------------------ #
    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"CandleData({self.time_str} | "
            f"O={self.open:.4f} H={self.high:.4f} "
            f"L={self.low:.4f} C={self.close:.4f} | "
            f"{self.candle_type().upper()})"
        )


@dataclass
class SignalResult:
    """
    Output produced by any signal detector.

    Attributes:
        signal:      "bullish" | "bearish" | None
        name:        Identifier of the strategy that raised the signal
        confidence:  0.0 – 1.0 (how strong the signal is)
        reason:      Human-readable explanation
        candles:     The candles that triggered the signal
        metadata:    Arbitrary extra data (indicator values, etc.)
    """
    signal: Optional[str]          # "bullish" | "bearish" | None
    name: str
    confidence: float = 1.0        # 0.0 – 1.0
    reason: str = ""
    candles: list[CandleData] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        return self.signal in ("bullish", "bearish") and self.confidence > 0.0

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"SignalResult({self.name!r} → {self.signal} "
            f"conf={self.confidence:.0%})"
        )
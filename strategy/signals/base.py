"""
strategy/signals/base.py
~~~~~~~~~~~~~~~~~~~~~~~~
Abstract base class for all signal detectors.
Every concrete detector must implement `detect()`.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from ..models import CandleData, SignalResult


class BaseSignal(ABC):
    """
    All signal detectors inherit from this class.

    Subclasses MUST implement:
        detect(candles: list[CandleData]) -> SignalResult
    """

    # Override in subclass to give the strategy a unique name
    name: str = "unnamed"

    @abstractmethod
    def detect(self, candles: list[CandleData]) -> SignalResult:
        """
        Analyse `candles` and return a SignalResult.

        Args:
            candles: Full list of closed candles (oldest → newest).

        Returns:
            SignalResult with signal="bullish"/"bearish" or signal=None
            when no actionable pattern is found.
        """
        ...

    # -------------------------------------------------------------- #
    # Convenience helpers available to all subclasses
    # -------------------------------------------------------------- #

    def _no_signal(self, reason: str = "No pattern", candles: list[CandleData] | None = None) -> SignalResult:
        return SignalResult(
            signal=None,
            name=self.name,
            confidence=0.0,
            reason=reason,
            candles=candles or [],
        )

    def _bullish(
        self,
        reason: str,
        candles: list[CandleData],
        confidence: float = 1.0,
        metadata: dict | None = None,
    ) -> SignalResult:
        return SignalResult(
            signal="bullish",
            name=self.name,
            confidence=confidence,
            reason=reason,
            candles=candles,
            metadata=metadata or {},
        )

    def _bearish(
        self,
        reason: str,
        candles: list[CandleData],
        confidence: float = 1.0,
        metadata: dict | None = None,
    ) -> SignalResult:
        return SignalResult(
            signal="bearish",
            name=self.name,
            confidence=confidence,
            reason=reason,
            candles=candles,
            metadata=metadata or {},
        )
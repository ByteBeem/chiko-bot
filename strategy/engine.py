"""
strategy/engine.py
~~~~~~~~~~~~~~~~~~~
StrategyEngine – the single entry-point for analysis.

Usage
-----
    engine = StrategyEngine()
    decision = engine.analyse(candles)   # candles = list[CandleData] (400 items)

Decision shape
--------------
    {
        "signal":      "bullish" | "bearish" | None,
        "confidence":  float,           # 0-1 weighted average
        "reason":      str,
        "signals":     list[SignalResult],
        "levels":      dict | None,     # entry/SL/TP/R:R
        "filters":     list[str],       # filter verdicts
        "timestamp":   str,
    }
"""
from __future__ import annotations
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

from .models import CandleData, SignalResult
from .signals import ThreeCandleSignal, EmaCrossSignal, RsiSignal, MacdSignal
from .risk import RiskManager
from .filters import trend_filter, volatility_filter, volume_filter

logger = logging.getLogger(__name__)

LOCAL_TZ = ZoneInfo("Africa/Johannesburg")


class StrategyEngine:
    """
    Orchestrates all signal detectors, pre-filters, and the risk manager.

    Signal voting
    -------------
    Every enabled detector casts a vote. A final signal fires only when:
      1. All pre-filters pass for that direction.
      2. The weighted vote majority agrees on the direction.
      3. The aggregate confidence >= risk_manager.min_confidence.

    Parameters
    ----------
    risk_manager       : RiskManager instance (or None → default created)
    require_all_agree  : bool – if True, ALL detectors must agree (strict mode)
    min_votes          : int  – minimum detectors that must agree (default 2)
    """

    def __init__(
        self,
        risk_manager: Optional[RiskManager] = None,
        require_all_agree: bool = False,
        min_votes: int = 2,
    ) -> None:
        self.risk_manager = risk_manager or RiskManager()
        self.require_all_agree = require_all_agree
        self.min_votes = min_votes

        # All active signal detectors
        self._detectors: list[tuple[str, object, float]] = [
            # (label, instance, weight)
            ("three_candle",  ThreeCandleSignal(count=3, min_body_ratio=0.25, require_volume_confirm=False), 1.5),
            ("ema_cross",     EmaCrossSignal(fast=9, slow=21),                                               1.2),
            ("rsi_reversal",  RsiSignal(period=14, oversold=30, overbought=70),                              1.0),
            ("macd_flip",     MacdSignal(fast=12, slow=26, signal_period=9),                                 1.0),
        ]

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def analyse(self, candles: list[CandleData]) -> dict:
        """
        Run full analysis on `candles` (should be ~400 closed candles).

        Returns a decision dict (see module docstring).
        """
        if not candles:
            return self._empty_decision("No candle data provided")

        # ---- 1. Run all detectors ----
        results: list[SignalResult] = []
        for label, detector, weight in self._detectors:
            try:
                result = detector.detect(candles)
                result.metadata["weight"] = weight
                results.append(result)
                logger.debug(
                    "[%s] → %s (conf=%.0f%%): %s",
                    label, result.signal, result.confidence * 100, result.reason
                )
            except Exception as exc:
                logger.error("Detector '%s' raised: %s", label, exc, exc_info=True)

        # ---- 2. Vote ----
        bullish_votes = [r for r in results if r.signal == "bullish"]
        bearish_votes = [r for r in results if r.signal == "bearish"]

        if self.require_all_agree:
            n = len(self._detectors)
            if len(bullish_votes) == n:
                direction, votes = "bullish", bullish_votes
            elif len(bearish_votes) == n:
                direction, votes = "bearish", bearish_votes
            else:
                direction, votes = None, []
        else:
            if len(bullish_votes) >= self.min_votes and len(bullish_votes) > len(bearish_votes):
                direction, votes = "bullish", bullish_votes
            elif len(bearish_votes) >= self.min_votes and len(bearish_votes) > len(bullish_votes):
                direction, votes = "bearish", bearish_votes
            else:
                direction, votes = None, []

        if direction is None:
            reasons = "; ".join(r.reason for r in results if not r.is_actionable) or "No consensus"
            return self._empty_decision(reasons, signals=results)

        # ---- 3. Run pre-filters ----
        filter_log: list[str] = []

        vol_ok, vol_reason = volatility_filter(candles)
        filter_log.append(f"volatility: {vol_reason}")
        if not vol_ok:
            return self._empty_decision(f"Volatility filter blocked: {vol_reason}", signals=results, filters=filter_log)

        vol_qty_ok, vol_qty_reason = volume_filter(candles)
        filter_log.append(f"volume: {vol_qty_reason}")
        if not vol_qty_ok:
            return self._empty_decision(f"Volume filter blocked: {vol_qty_reason}", signals=results, filters=filter_log)

        trend_ok, trend_reason = trend_filter(candles)
        filter_log.append(f"trend: {trend_reason}")
        # Trend filter is directional: bullish needs uptrend, bearish needs downtrend
        # We soft-warn rather than hard-block (you can make this a hard block if desired)
        if not trend_ok and direction == "bullish":
            filter_log.append("⚠ Bullish signal against downtrend – confidence reduced")
            for v in votes:
                v.confidence *= 0.6
        elif trend_ok and direction == "bearish":
            # trend_ok means uptrend – bearish signal against uptrend
            filter_log.append("⚠ Bearish signal against uptrend – confidence reduced")
            for v in votes:
                v.confidence *= 0.6

        # ---- 4. Aggregate confidence (weighted average) ----
        total_weight = sum(v.metadata.get("weight", 1.0) for v in votes)
        agg_confidence = sum(
            v.confidence * v.metadata.get("weight", 1.0) for v in votes
        ) / total_weight if total_weight else 0.0
        agg_confidence = round(agg_confidence, 3)

        # ---- 5. Risk validation ----
        # Build a synthetic SignalResult for the risk gate
        composite = SignalResult(
            signal=direction,
            name="composite",
            confidence=agg_confidence,
            reason=f"{len(votes)} detectors agreed",
            candles=candles[-3:],
        )
        approved, risk_reason = self.risk_manager.validate(composite)
        filter_log.append(f"risk: {risk_reason}")
        if not approved:
            return self._empty_decision(
                f"Risk manager blocked: {risk_reason}",
                signals=results,
                filters=filter_log,
            )

        # ---- 6. Compute levels ----
        levels = self.risk_manager.compute_levels(candles, direction)

        # ---- 7. Build decision ----
        summary_reason = (
            f"{direction.upper()} confirmed by {len(votes)}/{len(self._detectors)} detectors "
            f"(conf={agg_confidence:.0%}). "
            + " | ".join(v.reason for v in votes)
        )

        return {
            "signal":     direction,
            "confidence": agg_confidence,
            "reason":     summary_reason,
            "signals":    results,
            "levels":     levels,
            "filters":    filter_log,
            "timestamp":  datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        }

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _empty_decision(
        reason: str,
        signals: list[SignalResult] | None = None,
        filters: list[str] | None = None,
    ) -> dict:
        return {
            "signal":     None,
            "confidence": 0.0,
            "reason":     reason,
            "signals":    signals or [],
            "levels":     None,
            "filters":    filters or [],
            "timestamp":  datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        }
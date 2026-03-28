"""
strategy/filters/__init__.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Pre-signal filters that gate whether a strategy is allowed to fire.
Each filter is a callable: filter(candles) -> (allowed: bool, reason: str)
"""
from __future__ import annotations
from ..models import CandleData
from ..indicators import atr, average_volume, ema


def trend_filter(
    candles: list[CandleData],
    fast_ema: int = 20,
    slow_ema: int = 50,
) -> tuple[bool, str]:
    """
    Only allow bullish signals when fast EMA > slow EMA (uptrend),
    and bearish signals when fast EMA < slow EMA (downtrend).

    Returns (allowed, reason). The caller checks against signal direction.
    """
    f = ema(candles, fast_ema)
    s = ema(candles, slow_ema)
    if f is None or s is None:
        return True, "Trend filter skipped (insufficient data)"
    if f > s:
        return True, f"Uptrend confirmed (EMA{fast_ema}={f:.2f} > EMA{slow_ema}={s:.2f})"
    return False, f"Downtrend (EMA{fast_ema}={f:.2f} < EMA{slow_ema}={s:.2f})"


def volatility_filter(
    candles: list[CandleData],
    atr_period: int = 14,
    min_atr_pct: float = 0.001,   # 0.1% of price minimum
) -> tuple[bool, str]:
    """
    Reject signals when the market is too quiet (ATR too low).
    Prevents false signals in consolidating markets.
    """
    atr_val = atr(candles, atr_period)
    if atr_val is None:
        return True, "Volatility filter skipped (insufficient data)"
    price = candles[-1].close
    pct = atr_val / price if price else 0
    if pct >= min_atr_pct:
        return True, f"Sufficient volatility (ATR={atr_val:.4f}, {pct:.3%})"
    return False, f"Market too quiet (ATR={atr_val:.4f}, {pct:.3%} < {min_atr_pct:.3%})"


def volume_filter(
    candles: list[CandleData],
    period: int = 20,
    min_ratio: float = 0.8,
) -> tuple[bool, str]:
    """
    Reject signals when the most recent candle's volume is significantly
    below average – low-volume moves are less reliable.
    """
    avg = average_volume(candles[:-1], period)
    if avg is None or avg == 0:
        return True, "Volume filter skipped (insufficient data)"
    ratio = candles[-1].volume / avg
    if ratio >= min_ratio:
        return True, f"Volume OK (ratio={ratio:.2f})"
    return False, f"Low volume (ratio={ratio:.2f} < {min_ratio:.2f})"
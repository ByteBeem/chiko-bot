"""
strategy/indicators.py
~~~~~~~~~~~~~~~~~~~~~~
Pure-function technical indicators.
All functions accept a list[CandleData] and return numeric values or
lists of numerics.  No side-effects, fully testable.
"""
from __future__ import annotations
import math
from typing import Optional
from .models import CandleData


# ------------------------------------------------------------------ #
# Moving averages
# ------------------------------------------------------------------ #

def sma(candles: list[CandleData], period: int, source: str = "close") -> Optional[float]:
    """Simple moving average of the last `period` candles."""
    if len(candles) < period:
        return None
    values = [getattr(c, source) for c in candles[-period:]]
    return sum(values) / period


def ema_series(candles: list[CandleData], period: int, source: str = "close") -> list[float]:
    """
    Exponential moving average – returns a full series aligned with `candles`.
    The first `period-1` values are seeded with SMA.
    """
    if not candles:
        return []
    values = [getattr(c, source) for c in candles]
    k = 2 / (period + 1)
    result: list[float] = []
    for i, v in enumerate(values):
        if i < period - 1:
            result.append(float("nan"))
        elif i == period - 1:
            result.append(sum(values[:period]) / period)
        else:
            result.append(v * k + result[-1] * (1 - k))
    return result


def ema(candles: list[CandleData], period: int, source: str = "close") -> Optional[float]:
    """Return the most-recent EMA value."""
    series = ema_series(candles, period, source)
    if not series or math.isnan(series[-1]):
        return None
    return series[-1]


# ------------------------------------------------------------------ #
# RSI
# ------------------------------------------------------------------ #

def rsi(candles: list[CandleData], period: int = 14) -> Optional[float]:
    """Wilder's RSI.  Returns None when there are not enough candles."""
    if len(candles) < period + 1:
        return None
    closes = [c.close for c in candles[-(period + 1):]]
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# ------------------------------------------------------------------ #
# MACD
# ------------------------------------------------------------------ #

def macd(
    candles: list[CandleData],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> Optional[dict[str, float]]:
    """
    Returns dict with keys: macd, signal, histogram.
    Returns None if there are not enough candles.
    """
    if len(candles) < slow + signal_period:
        return None
    fast_series = ema_series(candles, fast)
    slow_series = ema_series(candles, slow)
    macd_line = [
        f - s
        for f, s in zip(fast_series, slow_series)
        if not (math.isnan(f) or math.isnan(s))
    ]
    if len(macd_line) < signal_period:
        return None
    k = 2 / (signal_period + 1)
    sig_val = sum(macd_line[:signal_period]) / signal_period
    for v in macd_line[signal_period:]:
        sig_val = v * k + sig_val * (1 - k)
    macd_val = macd_line[-1]
    return {
        "macd": macd_val,
        "signal": sig_val,
        "histogram": macd_val - sig_val,
    }


# ------------------------------------------------------------------ #
# Bollinger Bands
# ------------------------------------------------------------------ #

def bollinger_bands(
    candles: list[CandleData],
    period: int = 20,
    std_dev: float = 2.0,
    source: str = "close",
) -> Optional[dict[str, float]]:
    """Returns dict with upper, middle, lower bands."""
    if len(candles) < period:
        return None
    values = [getattr(c, source) for c in candles[-period:]]
    middle = sum(values) / period
    variance = sum((v - middle) ** 2 for v in values) / period
    deviation = math.sqrt(variance) * std_dev
    return {
        "upper": middle + deviation,
        "middle": middle,
        "lower": middle - deviation,
        "bandwidth": (2 * deviation) / middle if middle else 0,
    }


# ------------------------------------------------------------------ #
# ATR – Average True Range
# ------------------------------------------------------------------ #

def atr(candles: list[CandleData], period: int = 14) -> Optional[float]:
    """Wilder's ATR."""
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        prev_close = candles[i - 1].close
        c = candles[i]
        tr = max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close))
        trs.append(tr)
    return sum(trs[-period:]) / period


# ------------------------------------------------------------------ #
# Volume helpers
# ------------------------------------------------------------------ #

def average_volume(candles: list[CandleData], period: int = 20) -> Optional[float]:
    if len(candles) < period:
        return None
    return sum(c.volume for c in candles[-period:]) / period


def volume_ratio(candles: list[CandleData], period: int = 20) -> Optional[float]:
    """Latest volume / average volume over `period` candles."""
    avg = average_volume(candles[:-1], period)
    if not avg or not candles:
        return None
    return candles[-1].volume / avg
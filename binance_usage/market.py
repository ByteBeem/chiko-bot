from __future__ import annotations
import logging
from typing import Optional
from binance.client import Client as BinanceClient
from .client import client

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Candle dict shape:
#   open_time (int epoch ms), open, high, low, close, volume (floats),
#   close_time (int epoch ms)
# ------------------------------------------------------------------

_INTERVAL_MAP: dict[int, str] = {
    60:    BinanceClient.KLINE_INTERVAL_1MINUTE,
    180:   BinanceClient.KLINE_INTERVAL_3MINUTE,
    300:   BinanceClient.KLINE_INTERVAL_5MINUTE,
    #600:   BinanceClient.KLINE_INTERVAL_10MINUTE,  # not standard – falls back
    900:   BinanceClient.KLINE_INTERVAL_15MINUTE,
    1800:  BinanceClient.KLINE_INTERVAL_30MINUTE,
    3600:  BinanceClient.KLINE_INTERVAL_1HOUR,
    7200:  BinanceClient.KLINE_INTERVAL_2HOUR,
    14400: BinanceClient.KLINE_INTERVAL_4HOUR,
    28800: BinanceClient.KLINE_INTERVAL_8HOUR,
    86400: BinanceClient.KLINE_INTERVAL_1DAY,
}


def _granularity_to_interval(granularity: int) -> str:
    interval = _INTERVAL_MAP.get(granularity)
    if interval is None:
        raise ValueError(
            f"Unsupported granularity {granularity}s. "
            f"Valid values: {sorted(_INTERVAL_MAP.keys())}"
        )
    return interval


def _parse_kline(k: list) -> dict:
    return {
        "open_time":  int(k[0]) // 1000,   # convert ms → epoch seconds
        "open":       float(k[1]),
        "high":       float(k[2]),
        "low":        float(k[3]),
        "close":      float(k[4]),
        "volume":     float(k[5]),
        "close_time": int(k[6]) // 1000,
    }


def get_lastest_400(
    symbol: str,
    limit: int = 400,
    granularity: int = 300,          # default 5-minute
) -> list[dict]:
    """
    Fetch the last `limit` *closed* candles for `symbol`.

    Binance always returns the currently-forming candle as the final entry,
    so we request limit+1 klines and drop the last one.

    Args:
        symbol:      Trading pair, e.g. "BTCUSDT"
        limit:       Number of closed candles to return (max 1000).
        granularity: Candle size in seconds (must be in _INTERVAL_MAP).

    Returns:
        List of candle dicts ordered oldest → newest (all closed).
    """
    interval = _granularity_to_interval(granularity)
    raw = client.get_klines(symbol=symbol, interval=interval, limit=limit + 1)

    candles = [_parse_kline(k) for k in raw[:-1]]   # drop forming candle
    logger.debug("Fetched %d closed candles for %s (%ds)", len(candles), symbol, granularity)
    return candles


def get_last_closed_candle(
    symbol: str,
    granularity: int = 300,
) -> Optional[dict]:
    """Return only the most-recently closed candle."""
    candles = get_lastest_400(symbol, limit=1, granularity=granularity)
    return candles[-1] if candles else None
"""
backtest/data.py
~~~~~~~~~~~~~~~~~
Historical candle loader for backtesting.

Fetches via Binance REST in chunks (max 1000 candles per request),
stitching them together to cover any date range you need.

Usage
-----
    candles = load_candles("BTCUSDT", granularity=300, days=90)
    candles = load_candles("BTCUSDT", granularity=900, days=180)
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
LOCAL_TZ = ZoneInfo("Africa/Johannesburg")

_INTERVAL_MAP = {
    60:    "1m",
    180:   "3m",
    300:   "5m",
    900:   "15m",
    1800:  "30m",
    3600:  "1h",
    7200:  "2h",
    14400: "4h",
    28800: "8h",
    86400: "1d",
}

BINANCE_MAX_LIMIT = 1000  # max candles per Binance request


def load_candles(
    symbol:      str,
    granularity: int  = 300,
    days:        int  = 90,
    end_time:    Optional[datetime] = None,
) -> list:
    """
    Fetch `days` worth of closed candles for `symbol` at `granularity` seconds.

    Args:
        symbol:      e.g. "BTCUSDT"
        granularity: candle size in seconds (must be in _INTERVAL_MAP)
        days:        how many days of history to fetch
        end_time:    end of the window (default: now)

    Returns:
        list[CandleData] sorted oldest → newest (all closed candles)
    """
    from strategy.models import CandleData
    from binance_usage.client import client
    from binance.client import Client as BinanceClient

    interval = _INTERVAL_MAP.get(granularity)
    if not interval:
        raise ValueError(f"Unsupported granularity {granularity}. Valid: {sorted(_INTERVAL_MAP)}")

    end_dt   = end_time or datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)

    start_ms = int(start_dt.timestamp() * 1000)
    end_ms   = int(end_dt.timestamp() * 1000)

    candles_per_day = 86400 // granularity
    total_needed    = days * candles_per_day
    logger.info(
        "Fetching %d candles (%s, %s) for %s…",
        total_needed, start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"), symbol,
    )

    raw_all: list[list] = []
    cursor = start_ms

    while cursor < end_ms:
        try:
            chunk = client.get_klines(
                symbol    = symbol,
                interval  = interval,
                startTime = cursor,
                endTime   = end_ms,
                limit     = BINANCE_MAX_LIMIT,
            )
        except Exception as exc:
            logger.error("Binance fetch error at cursor=%d: %s", cursor, exc)
            raise

        if not chunk:
            break

        raw_all.extend(chunk)
        last_open_ms = int(chunk[-1][0])

        if len(chunk) < BINANCE_MAX_LIMIT:
            break

        cursor = last_open_ms + granularity * 1000
        time.sleep(0.2)   # respect Binance rate limit

    if not raw_all:
        raise RuntimeError(f"No candle data returned for {symbol}")

    # Deduplicate (can happen at page boundaries)
    seen:   set[int]        = set()
    unique: list[list]      = []
    for k in raw_all:
        ts = int(k[0])
        if ts not in seen:
            seen.add(ts)
            unique.append(k)
    unique.sort(key=lambda k: int(k[0]))

    # Drop the currently-forming candle (open_time + granularity > now)
    now_ms = int(time.time() * 1000)
    unique = [k for k in unique if int(k[0]) + granularity * 1000 <= now_ms]

    # Parse into CandleData
    result: list[CandleData] = []
    for k in unique:
        epoch = int(k[0]) // 1000   # ms → seconds
        ts    = (
            datetime.fromtimestamp(epoch, tz=timezone.utc)
            .astimezone(LOCAL_TZ)
            .strftime("%Y-%m-%d %H:%M")
        )
        result.append(CandleData(
            open_time = epoch,
            time_str  = ts,
            open      = float(k[1]),
            high      = float(k[2]),
            low       = float(k[3]),
            close     = float(k[4]),
            volume    = float(k[5]),
            is_closed = True,
        ))

    logger.info("Loaded %d closed candles for %s", len(result), symbol)
    return result
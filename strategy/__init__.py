"""
strategy/
~~~~~~~~~
Public API for the Strategy package.

    from strategy import StrategyEngine, CandleData, SignalResult, RiskManager
"""
from .models import CandleData, SignalResult
from .engine import StrategyEngine
from .risk import RiskManager
from .indicators import rsi, ema, macd, atr, bollinger_bands
from .signals import (
    BaseSignal,
    ThreeCandleSignal,
    EmaCrossSignal,
    RsiSignal,
    MacdSignal,
)

__all__ = [
    # Models
    "CandleData",
    "SignalResult",
    # Engine
    "StrategyEngine",
    # Risk
    "RiskManager",
    # Indicators (convenience re-exports)
    "rsi",
    "ema",
    "macd",
    "atr",
    "bollinger_bands",
    # Signal detectors
    "BaseSignal",
    "ThreeCandleSignal",
    "EmaCrossSignal",
    "RsiSignal",
    "MacdSignal",
]
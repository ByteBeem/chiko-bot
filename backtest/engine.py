"""
backtest/engine.py
~~~~~~~~~~~~~~~~~~~
Walk-forward backtester that feeds your real StrategyEngine candle-by-candle,
simulating EXACTLY what would happen in live trading.

No look-ahead bias: at bar N, the engine only sees candles[0:N].
Exit logic mirrors live trading: SL/TP hit on the NEXT candle's H/L,
or time-based exit after `max_hold_candles`.

Usage
-----
    from backtest.engine import Backtester
    from backtest.data   import load_candles

    candles = load_candles("BTCUSDT", granularity=300, days=90)
    bt      = Backtester(initial_balance=1000.0, risk_pct=0.01, fee_pct=0.00075)
    report  = bt.run(candles)
    report.print_summary()
    report.to_csv("results/btcusdt_5m_90d.csv")
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from strategy import StrategyEngine, RiskManager, CandleData

logger = logging.getLogger(__name__)
LOCAL_TZ = ZoneInfo("Africa/Johannesburg")

# Minimum candles needed before the engine can produce meaningful signals
# (covers EMA-26 + MACD signal-9 = 35 + buffer)
MIN_WARMUP_CANDLES = 60


# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Trade:
    """Single completed trade record."""
    index:        int            # bar index where entry occurred
    entry_time:   str
    exit_time:    str
    direction:    str            # "bullish" (long) | "bearish" (short)
    entry_price:  float
    exit_price:   float
    stop_loss:    float
    take_profit:  float
    quantity:     float          # position size in base asset
    fee_paid:     float          # total fees (entry + exit)
    pnl:          float          # net P&L in quote currency
    pnl_pct:      float          # % return on the risked capital
    exit_reason:  str            # "tp" | "sl" | "time" | "signal_flip"
    confidence:   float
    signal_name:  str            # which detectors fired

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0


@dataclass
class BacktestReport:
    """Aggregated results from a completed backtest run."""
    symbol:           str
    granularity:      int
    days_tested:      int
    total_candles:    int
    initial_balance:  float
    final_balance:    float
    trades:           list[Trade] = field(default_factory=list)

    # ── Computed metrics ─────────────────────────────────────────────── #
    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def n_winners(self) -> int:
        return sum(1 for t in self.trades if t.is_winner)

    @property
    def n_losers(self) -> int:
        return self.n_trades - self.n_winners

    @property
    def win_rate(self) -> float:
        return self.n_winners / self.n_trades if self.n_trades else 0.0

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def total_return_pct(self) -> float:
        return (self.final_balance - self.initial_balance) / self.initial_balance * 100

    @property
    def avg_win(self) -> float:
        wins = [t.pnl for t in self.trades if t.is_winner]
        return sum(wins) / len(wins) if wins else 0.0

    @property
    def avg_loss(self) -> float:
        losses = [t.pnl for t in self.trades if not t.is_winner]
        return sum(losses) / len(losses) if losses else 0.0

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t.pnl for t in self.trades if t.is_winner)
        gross_loss   = abs(sum(t.pnl for t in self.trades if not t.is_winner))
        return gross_profit / gross_loss if gross_loss else float("inf")

    @property
    def expectancy(self) -> float:
        """Average P&L per trade (positive = edge exists)."""
        return self.total_pnl / self.n_trades if self.n_trades else 0.0

    @property
    def max_drawdown(self) -> float:
        """Maximum peak-to-trough drawdown as a percentage."""
        if not self.trades:
            return 0.0
        balance    = self.initial_balance
        peak       = balance
        max_dd     = 0.0
        for t in self.trades:
            balance += t.pnl
            peak     = max(peak, balance)
            dd       = (peak - balance) / peak * 100
            max_dd   = max(max_dd, dd)
        return max_dd

    @property
    def sharpe_ratio(self) -> float:
        """Simplified Sharpe (assumes 0% risk-free rate, daily returns)."""
        import math
        if len(self.trades) < 2:
            return 0.0
        returns  = [t.pnl_pct for t in self.trades]
        mean_r   = sum(returns) / len(returns)
        variance = sum((r - mean_r) ** 2 for r in returns) / len(returns)
        std_r    = math.sqrt(variance)
        return (mean_r / std_r) * math.sqrt(252) if std_r else 0.0

    @property
    def avg_rr(self) -> float:
        rrs = []
        for t in self.trades:
            risk   = abs(t.entry_price - t.stop_loss)
            reward = abs(t.entry_price - t.take_profit)
            if risk:
                rrs.append(reward / risk)
        return sum(rrs) / len(rrs) if rrs else 0.0

    # ── Output ───────────────────────────────────────────────────────── #
    def print_summary(self) -> None:
        sep = "─" * 52
        print(f"\n{'═' * 52}")
        print(f"  BACKTEST REPORT  –  {self.symbol}  {self.granularity // 60}m")
        print(f"{'═' * 52}")
        print(f"  Period tested:    {self.days_tested}d  ({self.total_candles} candles)")
        print(f"  Initial balance:  ${self.initial_balance:,.2f}")
        print(f"  Final balance:    ${self.final_balance:,.2f}")
        print(f"  Total return:     {self.total_return_pct:+.2f}%")
        print(sep)
        print(f"  Total trades:     {self.n_trades}")
        print(f"  Winners:          {self.n_winners}  ({self.win_rate:.1%})")
        print(f"  Losers:           {self.n_losers}")
        print(f"  Avg win:          ${self.avg_win:+.2f}")
        print(f"  Avg loss:         ${self.avg_loss:+.2f}")
        print(f"  Profit factor:    {self.profit_factor:.2f}")
        print(f"  Expectancy:       ${self.expectancy:+.2f}/trade")
        print(sep)
        print(f"  Max drawdown:     {self.max_drawdown:.2f}%")
        print(f"  Sharpe ratio:     {self.sharpe_ratio:.2f}")
        print(f"  Avg R:R:          {self.avg_rr:.2f}")
        print(f"{'═' * 52}\n")

        if self.trades:
            print("  Recent trades (last 10):")
            print(f"  {'Time':<17} {'Dir':<8} {'Entry':>10} {'Exit':>10} {'P&L':>10} {'Reason':<12}")
            print(f"  {'-'*17} {'-'*8} {'-'*10} {'-'*10} {'-'*10} {'-'*12}")
            for t in self.trades[-10:]:
                print(
                    f"  {t.entry_time:<17} {t.direction:<8} "
                    f"{t.entry_price:>10.4f} {t.exit_price:>10.4f} "
                    f"{t.pnl:>+10.2f} {t.exit_reason:<12}"
                )
        print()

    def to_csv(self, path: str) -> None:
        import csv, os
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            if not self.trades:
                f.write("No trades generated\n")
                return
            writer = csv.DictWriter(f, fieldnames=self.trades[0].__dataclass_fields__.keys())
            writer.writeheader()
            writer.writerows(t.__dict__ for t in self.trades)
        logger.info("CSV saved → %s", path)

    def to_dict(self) -> dict:
        return {
            "symbol":          self.symbol,
            "granularity":     self.granularity,
            "days_tested":     self.days_tested,
            "total_candles":   self.total_candles,
            "initial_balance": self.initial_balance,
            "final_balance":   round(self.final_balance, 2),
            "total_return_pct":round(self.total_return_pct, 2),
            "n_trades":        self.n_trades,
            "win_rate":        round(self.win_rate, 4),
            "profit_factor":   round(self.profit_factor, 4),
            "expectancy":      round(self.expectancy, 2),
            "max_drawdown":    round(self.max_drawdown, 2),
            "sharpe_ratio":    round(self.sharpe_ratio, 4),
            "avg_rr":          round(self.avg_rr, 4),
        }


# ─────────────────────────────────────────────────────────────────────────────
class Backtester:
    """
    Walk-forward backtester.

    Parameters
    ----------
    initial_balance  : float  – starting capital in quote currency (e.g. USDT)
    risk_pct         : float  – fraction of balance risked per trade (e.g. 0.01 = 1%)
    fee_pct          : float  – taker fee per side (Binance default 0.00075 = 0.075%)
    max_hold_candles : int    – force-close after this many candles (time exit)
    slippage_pct     : float  – simulated slippage on entry/exit (0.0005 = 0.05%)
    min_votes        : int    – passed to StrategyEngine
    min_confidence   : float  – passed to RiskManager
    cooldown_candles : int    – bars to skip after a trade (mirrors live cooldown)
    """

    def __init__(
        self,
        initial_balance:  float = 1_000.0,
        risk_pct:         float = 0.01,
        fee_pct:          float = 0.00075,
        max_hold_candles: int   = 12,
        slippage_pct:     float = 0.0005,
        min_votes:        int   = 2,
        min_confidence:   float = 0.60,
        cooldown_candles: int   = 3,
    ) -> None:
        self.initial_balance  = initial_balance
        self.risk_pct         = risk_pct
        self.fee_pct          = fee_pct
        self.max_hold_candles = max_hold_candles
        self.slippage_pct     = slippage_pct
        self.cooldown_candles = cooldown_candles

        # Build a cooldown-disabled RiskManager for backtest
        # (we handle cooldown manually via cooldown_candles)
        self._risk_manager = RiskManager(
            min_confidence    = min_confidence,
            atr_period        = 14,
            atr_sl_multiplier = 1.5,
            atr_tp_multiplier = 2.5,
            cooldown_seconds  = 0,   # disabled – managed per-bar below
        )
        self._engine = StrategyEngine(
            risk_manager     = self._risk_manager,
            require_all_agree= False,
            min_votes        = min_votes,
        )

    # ------------------------------------------------------------------ #
    def run(
        self,
        candles: list[CandleData],
        symbol:  str = "UNKNOWN",
        granularity: int = 300,
    ) -> BacktestReport:
        """
        Run the full walk-forward backtest.

        For each bar N (starting at MIN_WARMUP_CANDLES):
          - Feed candles[0:N] into StrategyEngine.
          - If signal → open trade at open of bar N+1.
          - Check exit: SL/TP hit intra-bar on bars N+1 … N+max_hold.
          - Close at bar N+max_hold if neither SL nor TP hit.
        """
        if len(candles) < MIN_WARMUP_CANDLES + 10:
            raise ValueError(
                f"Need at least {MIN_WARMUP_CANDLES + 10} candles, got {len(candles)}"
            )

        balance      = self.initial_balance
        trades: list[Trade] = []
        cooldown_left = 0

        total        = len(candles)
        days_tested  = (candles[-1].open_time - candles[MIN_WARMUP_CANDLES].open_time) // 86400

        print(f"\n Backtesting {symbol} {granularity // 60}m over {len(candles)} candles…\n")

        # Walk forward – last bar can't enter (no next bar to fill on)
        for i in range(MIN_WARMUP_CANDLES, total - 1):
            self._print_progress(i - MIN_WARMUP_CANDLES, total - MIN_WARMUP_CANDLES - 1)

            # ── Cooldown gate ──────────────────────────────────────────
            if cooldown_left > 0:
                cooldown_left -= 1
                continue

            # ── Feed history UP TO (not including) current bar ─────────
            history = candles[:i]          # no look-ahead
            decision = self._engine.analyse(history)

            if not decision["signal"]:
                continue

            direction = decision["signal"]
            levels    = decision.get("levels")
            if not levels:
                continue

            # ── Entry: open of next bar (bar i+1) ─────────────────────
            next_bar   = candles[i + 1]
            entry_raw  = next_bar.open

            # Apply slippage
            if direction == "bullish":
                entry = entry_raw * (1 + self.slippage_pct)
            else:
                entry = entry_raw * (1 - self.slippage_pct)

            sl = levels["stop_loss"]
            tp = levels["take_profit"]

            # ── Position sizing ────────────────────────────────────────
            risk_amount  = balance * self.risk_pct
            risk_per_unit = abs(entry - sl)
            if risk_per_unit == 0:
                continue
            quantity = risk_amount / risk_per_unit

            entry_fee = quantity * entry * self.fee_pct

            # ── Walk exit bars ─────────────────────────────────────────
            exit_price  = None
            exit_reason = "time"
            exit_bar    = None

            max_exit = min(i + 1 + self.max_hold_candles, total - 1)
            for j in range(i + 1, max_exit + 1):
                bar = candles[j]

                if direction == "bullish":
                    # SL hit?
                    if bar.low <= sl:
                        exit_price  = sl * (1 - self.slippage_pct)
                        exit_reason = "sl"
                        exit_bar    = bar
                        break
                    # TP hit?
                    if bar.high >= tp:
                        exit_price  = tp * (1 - self.slippage_pct)
                        exit_reason = "tp"
                        exit_bar    = bar
                        break
                else:  # bearish (short)
                    if bar.high >= sl:
                        exit_price  = sl * (1 + self.slippage_pct)
                        exit_reason = "sl"
                        exit_bar    = bar
                        break
                    if bar.low <= tp:
                        exit_price  = tp * (1 + self.slippage_pct)
                        exit_reason = "tp"
                        exit_bar    = bar
                        break

            # Time exit: close at close of last bar in hold window
            if exit_price is None:
                exit_bar    = candles[max_exit]
                exit_price  = exit_bar.close * (
                    (1 - self.slippage_pct) if direction == "bullish" else (1 + self.slippage_pct)
                )
                exit_reason = "time"

            exit_fee = quantity * exit_price * self.fee_pct
            total_fee = entry_fee + exit_fee

            # ── P&L ────────────────────────────────────────────────────
            if direction == "bullish":
                gross_pnl = (exit_price - entry) * quantity
            else:
                gross_pnl = (entry - exit_price) * quantity
            net_pnl = gross_pnl - total_fee

            # % return on capital at risk
            pnl_pct = net_pnl / (quantity * entry * self.risk_pct) if (quantity * entry * self.risk_pct) else 0

            balance += net_pnl
            balance  = max(balance, 0.0)   # never go negative

            # ── Record trade ───────────────────────────────────────────
            signal_names = ", ".join(
                sr.name for sr in decision.get("signals", []) if sr.signal == direction
            )
            trade = Trade(
                index       = i,
                entry_time  = next_bar.time_str,
                exit_time   = exit_bar.time_str if exit_bar else next_bar.time_str,
                direction   = direction,
                entry_price = round(entry, 6),
                exit_price  = round(exit_price, 6),
                stop_loss   = round(sl, 6),
                take_profit = round(tp, 6),
                quantity    = round(quantity, 6),
                fee_paid    = round(total_fee, 4),
                pnl         = round(net_pnl, 4),
                pnl_pct     = round(pnl_pct * 100, 4),
                exit_reason = exit_reason,
                confidence  = round(decision["confidence"], 4),
                signal_name = signal_names,
            )
            trades.append(trade)

            cooldown_left = self.cooldown_candles
            logger.debug(
                "Trade %d: %s %s @ %.4f → %.4f (%s) P&L=%.2f",
                len(trades), direction, symbol, entry, exit_price, exit_reason, net_pnl,
            )

        print()  # newline after progress bar

        return BacktestReport(
            symbol          = symbol,
            granularity     = granularity,
            days_tested     = days_tested,
            total_candles   = total,
            initial_balance = self.initial_balance,
            final_balance   = round(balance, 4),
            trades          = trades,
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _print_progress(current: int, total: int, width: int = 40) -> None:
        pct   = current / total if total else 0
        filled= int(width * pct)
        bar   = "█" * filled + "░" * (width - filled)
        print(f"\r  [{bar}] {pct:.1%}  ({current}/{total})", end="", flush=True)
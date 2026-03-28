"""
backtest/optimizer.py
~~~~~~~~~~~~~~~~~~~~~~
Grid-search optimizer that finds the best parameter combination
by running the Backtester across a parameter grid.

Usage
-----
    from backtest.optimizer import Optimizer
    from backtest.data      import load_candles

    candles = load_candles("BTCUSDT", granularity=300, days=90)

    best = Optimizer(candles, symbol="BTCUSDT", granularity=300).run()
    best.report.print_summary()
"""
from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass

from .engine import Backtester, BacktestReport

logger = logging.getLogger(__name__)


@dataclass
class OptResult:
    params: dict
    report: BacktestReport

    @property
    def score(self) -> float:
        """
        Composite score used for ranking.
        Balances return, win rate, and drawdown.
        """
        r = self.report
        if r.n_trades < 5:          # not enough trades to trust
            return -999.0
        return (
            r.total_return_pct * 0.4
            + r.win_rate        * 100 * 0.3
            + r.profit_factor   * 10  * 0.2
            - r.max_drawdown         * 0.1
        )


class Optimizer:
    """
    Exhaustive grid-search over Backtester parameters.

    Parameters grid (override defaults via constructor):
        risk_pct, min_votes, min_confidence, max_hold_candles, cooldown_candles
    """

    DEFAULT_GRID = {
        "risk_pct":          [0.005, 0.01, 0.02],
        "min_votes":         [2, 3],
        "min_confidence":    [0.55, 0.65, 0.75],
        "max_hold_candles":  [6, 12, 24],
        "cooldown_candles":  [2, 4],
    }

    def __init__(
        self,
        candles:     list,
        symbol:      str = "UNKNOWN",
        granularity: int = 300,
        grid:        dict | None = None,
    ) -> None:
        self.candles     = candles
        self.symbol      = symbol
        self.granularity = granularity
        self.grid        = grid or self.DEFAULT_GRID

    def run(self, top_n: int = 5) -> list[OptResult]:
        """
        Run all parameter combinations. Returns the top_n results by score.
        """
        keys   = list(self.grid.keys())
        combos = list(itertools.product(*[self.grid[k] for k in keys]))
        total  = len(combos)
        logger.info("Optimizer: testing %d combinations…", total)
        print(f"\n Optimizer: testing {total} parameter combinations…\n")

        results: list[OptResult] = []

        for idx, combo in enumerate(combos):
            params = dict(zip(keys, combo))
            print(f"  [{idx + 1:>4}/{total}] {params}", end=" ")

            try:
                bt = Backtester(
                    initial_balance  = 1_000.0,
                    risk_pct         = params["risk_pct"],
                    min_votes        = params["min_votes"],
                    min_confidence   = params["min_confidence"],
                    max_hold_candles = params["max_hold_candles"],
                    cooldown_candles = params["cooldown_candles"],
                )
                report = bt.run(self.candles, symbol=self.symbol, granularity=self.granularity)
                result = OptResult(params=params, report=report)
                print(
                    f"→ trades={report.n_trades:>3}  "
                    f"wr={report.win_rate:.0%}  "
                    f"ret={report.total_return_pct:+.1f}%  "
                    f"dd={report.max_drawdown:.1f}%  "
                    f"score={result.score:.1f}"
                )
                results.append(result)
            except Exception as exc:
                print(f"→ ERROR: {exc}")
                logger.warning("Combo %s failed: %s", params, exc)

        results.sort(key=lambda r: r.score, reverse=True)

        print(f"\n{'═' * 60}")
        print(f"  TOP {min(top_n, len(results))} PARAMETER SETS")
        print(f"{'═' * 60}")
        for rank, r in enumerate(results[:top_n], 1):
            print(f"\n  #{rank}  score={r.score:.1f}")
            for k, v in r.params.items():
                print(f"       {k:<22} = {v}")
            rpt = r.report
            print(
                f"       → trades={rpt.n_trades}  wr={rpt.win_rate:.1%}  "
                f"ret={rpt.total_return_pct:+.2f}%  dd={rpt.max_drawdown:.2f}%"
            )
        print()

        return results[:top_n]
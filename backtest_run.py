"""
backtest_run.py
~~~~~~~~~~~~~~~~
CLI entry point for backtesting and optimization.

Examples
--------
# Quick 30-day backtest on BTCUSDT 5m:
    python backtest_run.py --symbol BTCUSDT --granularity 300 --days 30

# 90-day backtest with custom risk:
    python backtest_run.py --days 90 --risk 0.02 --min-confidence 0.65

# Run optimizer (grid search):
    python backtest_run.py --days 60 --optimize

# Save results to CSV:
    python backtest_run.py --days 90 --out results/btc_5m.csv
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.WARNING,   # suppress debug noise during backtest
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Chiko Backtester",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--symbol",         default=os.getenv("SYMBOL", "BTCUSDT"), help="Trading pair")
    p.add_argument("--granularity",    default=300,   type=int, help="Candle size in seconds")
    p.add_argument("--days",           default=60,    type=int, help="Days of history to test")
    p.add_argument("--balance",        default=1000.0,type=float, help="Starting balance (USDT)")
    p.add_argument("--risk",           default=0.01,  type=float, help="Risk per trade (0.01 = 1%%)")
    p.add_argument("--min-confidence", default=0.60,  type=float, help="Min signal confidence")
    p.add_argument("--min-votes",      default=2,     type=int,   help="Min detector votes")
    p.add_argument("--max-hold",       default=12,    type=int,   help="Max hold candles (time exit)")
    p.add_argument("--cooldown",       default=3,     type=int,   help="Cooldown bars after trade")
    p.add_argument("--fee",            default=0.00075, type=float, help="Taker fee per side")
    p.add_argument("--slippage",       default=0.0005, type=float, help="Simulated slippage")
    p.add_argument("--out",            default=None,  help="CSV output path (optional)")
    p.add_argument("--optimize",       action="store_true", help="Run grid-search optimizer")
    p.add_argument("--json",           action="store_true", help="Print summary as JSON")
    return p.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    args = parse_args()

    print(f"\n{'═' * 60}")
    print(f"  Chiko Backtester")
    print(f"  Symbol:      {args.symbol}")
    print(f"  Timeframe:   {args.granularity // 60}m")
    print(f"  Days:        {args.days}")
    print(f"  Balance:     ${args.balance:,.2f}")
    print(f"  Risk/trade:  {args.risk:.1%}")
    print(f"{'═' * 60}")

    # ── Load historical candles ───────────────────────────────────────
    print("\n Loading historical data from Binance…")
    from backtest.data import load_candles
    try:
        candles = load_candles(
            symbol      = args.symbol,
            granularity = args.granularity,
            days        = args.days,
        )
    except Exception as exc:
        print(f"\n Failed to load candles: {exc}")
        sys.exit(1)

    print(f" Loaded {len(candles)} candles "
          f"({candles[0].time_str} → {candles[-1].time_str})\n")

    # ── Optimize or single run ────────────────────────────────────────
    if args.optimize:
        from backtest.optimizer import Optimizer
        top = Optimizer(
            candles     = candles,
            symbol      = args.symbol,
            granularity = args.granularity,
        ).run(top_n=5)

        # Run full backtest with best params
        best = top[0]
        print(f"\n▶ Running full backtest with best params: {best.params}\n")
        from backtest.engine import Backtester
        bt = Backtester(
            initial_balance  = args.balance,
            **best.params,
        )
        report = bt.run(candles, symbol=args.symbol, granularity=args.granularity)

    else:
        from backtest.engine import Backtester
        bt = Backtester(
            initial_balance  = args.balance,
            risk_pct         = args.risk,
            fee_pct          = args.fee,
            max_hold_candles = args.max_hold,
            slippage_pct     = args.slippage,
            min_votes        = args.min_votes,
            min_confidence   = args.min_confidence,
            cooldown_candles = args.cooldown,
        )
        report = bt.run(candles, symbol=args.symbol, granularity=args.granularity)

    # ── Output ────────────────────────────────────────────────────────
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        report.print_summary()

    if args.out:
        report.to_csv(args.out)
        print(f" Results saved → {args.out}")

    # ── Verdict ───────────────────────────────────────────────────────
    print("─" * 52)
    if report.n_trades == 0:
        print("  No trades generated. Try reducing --min-confidence or --min-votes.")
    elif report.profit_factor >= 1.5 and report.win_rate >= 0.5:
        print(" Strategy looks PROMISING on this data.")
        print("   Run on a different period to validate before going live.")
    elif report.profit_factor >= 1.0:
        print("  Strategy is marginally profitable. Needs refinement.")
    else:
        print(" Strategy lost money on this period.")
        print("   Adjust parameters or try a different timeframe.")
    print("─" * 52)
    print("\n Tip: run with --optimize to find best parameter settings.\n")


if __name__ == "__main__":
    main()
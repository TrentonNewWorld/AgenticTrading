#!/usr/bin/env python3
"""Run one Alpaca paper-trading decision cycle from the command line, for
either a deterministic leaderboard strategy or an LLM-driven Marketplace
template.

This is the manual on-ramp to
``dashboard.backend.execution.alpaca_paper_service``. Pass exactly one of
``--strategy`` (no LLM call -- the decision comes from one of the
deterministic classes under ``dashboard/backend/domain/leaderboard/strategies/``)
or ``--template`` (a real LLM call using a Marketplace template's own trading
instruction as the prompt -- costs a small amount of real API spend per
cycle, unlike ``--strategy``). Both are **review-only by default**: pass
``--execute`` AND set ``ALPACA_PAPER_EXECUTE=true`` in ``dashboard/.env`` to
let orders actually reach the paper account (the same two-gate pattern the
live-agent script uses, kept in a fully separate env-var namespace so paper
and live can never share a kill switch).

Examples
--------
Review what a deterministic strategy would do, no risk at all:
    python dashboard/scripts/run_alpaca_paper_strategy.py --strategy momentum_effect

Review what an LLM-driven Marketplace template would do (makes one real LLM
call, but places no orders without --execute):
    python dashboard/scripts/run_alpaca_paper_strategy.py --template momentum-scout

Actually place paper orders (after you've watched a dry run and set
ALPACA_PAPER_EXECUTE=true in dashboard/.env):
    python dashboard/scripts/run_alpaca_paper_strategy.py --strategy momentum_effect --execute

List every strategy key / template id that supports live/paper decisions:
    python dashboard/scripts/run_alpaca_paper_strategy.py --list
"""

import argparse
import asyncio
import json
import sys

from _bootstrap import ensure_repo_root

ensure_repo_root()

from dotenv import load_dotenv  # noqa: E402

from dashboard.backend.paths import DASHBOARD_DIR  # noqa: E402

# app.py loads .env from dashboard/.env when the full app boots; a standalone
# script needs the same explicit load, or ALPACA_API_KEY etc. are never set.
load_dotenv(DASHBOARD_DIR / ".env")

from dashboard.backend.domain.leaderboard.strategies import available_strategies  # noqa: E402
from dashboard.backend.execution import alpaca_paper_service as svc  # noqa: E402


def _decidable_strategy_keys():
    """Every registered strategy key whose class defines its own `decide()`
    (BaselineStrategy itself declares none, so plain attribute lookup can't
    tell "supports live trading" from "doesn't")."""
    return sorted(key for key, cls in available_strategies().items() if "decide" in cls.__dict__)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--strategy", default=None, help="Registered leaderboard strategy key, e.g. momentum_effect.")
    parser.add_argument("--template", default=None, help="Marketplace template id, e.g. momentum-scout (makes a real LLM call).")
    parser.add_argument("--model", default=None, help="Override the model used for --template (defaults to the app's default).")
    parser.add_argument(
        "--symbols", nargs="*", default=None, help="Symbol universe override (defaults to the strategy's/DJIA-30's own)."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Allow real paper-order placement for this run. Still requires "
            "ALPACA_PAPER_EXECUTE=true in dashboard/.env — without it, this "
            "run stays review-only regardless of this flag."
        ),
    )
    parser.add_argument("--list", action="store_true", help="List every strategy key / template id that supports live/paper decisions, then exit.")
    args = parser.parse_args()

    if args.list:
        print("-- deterministic strategies (--strategy, no LLM call) --")
        for key in _decidable_strategy_keys():
            print(key)
        print("\n-- Marketplace templates (--template, real LLM call) --")
        for key in sorted(svc.SUPPORTED_MARKETPLACE_TEMPLATES):
            print(key)
        return 0

    if bool(args.strategy) == bool(args.template):
        parser.error("pass exactly one of --strategy or --template (or --list to see available keys)")

    if args.execute and not svc.execute_enabled():
        print(
            "--execute was passed, but ALPACA_PAPER_EXECUTE is not 'true' in the environment.\n"
            "This run will stay in review-only mode. Set ALPACA_PAPER_EXECUTE=true in "
            "dashboard/.env once you're ready for real paper orders.\n",
            file=sys.stderr,
        )

    if args.strategy:
        result = asyncio.run(
            svc.run_paper_for_strategy(strategy_key=args.strategy, symbols=args.symbols, dry_run=not args.execute)
        )
    else:
        result = asyncio.run(
            svc.run_paper_for_marketplace_agent(
                template_id=args.template, model_name=args.model, symbols=args.symbols, dry_run=not args.execute,
            )
        )

    print(json.dumps(result, indent=2, default=str))

    if result.get("action") == "none":
        print(f"\n[no action] {result.get('reason')}", file=sys.stderr)
    elif result.get("dry_run"):
        print("\n[dry run] No orders were sent to the broker.", file=sys.stderr)
    else:
        print("\n[PAPER] Orders were sent to your Alpaca paper account.", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

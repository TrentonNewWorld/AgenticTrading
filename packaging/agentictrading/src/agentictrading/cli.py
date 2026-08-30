"""Command-line interface for the ``agentictrading`` package.

Run ``agentictrading`` for project info, or a subcommand to hit a live API:

    agentictrading                       # show project info + links
    agentictrading health --api URL
    agentictrading leaderboard --api URL
    agentictrading ticker AAPL,NVDA --api URL
"""

from __future__ import annotations

import argparse
import json
import sys

from . import DOCS_URL, LIVE_DEMO_URL, SOURCE_URL, __version__, info
from .client import DEFAULT_BASE_URL, AgenticTradingClient, ApiError


def _print_info() -> int:
    data = info()
    print(f"agentictrading {data['version']}")
    print(data["summary"])
    print()
    print(f"  Live demo : {LIVE_DEMO_URL}")
    print(f"  Docs      : {DOCS_URL}")
    print(f"  Source    : {SOURCE_URL}")
    print()
    print("Quickstart:")
    print("  from agentictrading import AgenticTradingClient")
    print(f"  client = AgenticTradingClient({DEFAULT_BASE_URL!r})")
    print("  print(client.health())")
    return 0


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentictrading",
        description="Lightweight client for the NewWorldTrading REST API.",
    )
    parser.add_argument("--version", action="version", version=f"agentictrading {__version__}")

    # Shared option so each subcommand accepts --api after the command name.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--api", default=DEFAULT_BASE_URL, help="API base URL")

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("info", help="Show project info and links (default)", parents=[common])
    sub.add_parser("health", help="Check API server health", parents=[common])
    sub.add_parser("leaderboard", help="Show the agent leaderboard", parents=[common])
    p_ticker = sub.add_parser("ticker", help="Show latest quotes", parents=[common])
    p_ticker.add_argument("symbols", nargs="?", default="AAPL,NVDA,MSFT,BTC")

    args = parser.parse_args(argv)

    if args.command in (None, "info"):
        return _print_info()

    client = AgenticTradingClient(args.api)
    try:
        if args.command == "health":
            result = client.health()
        elif args.command == "leaderboard":
            result = client.leaderboard()
        elif args.command == "ticker":
            result = client.ticker(args.symbols)
        else:  # pragma: no cover - argparse guards this
            parser.error(f"unknown command: {args.command}")
            return 2
    except ApiError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

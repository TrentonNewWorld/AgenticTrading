"""Sub-phase 1 of the Options-dashboard plan: a throwaway data-availability
spike, run once against real Alpaca paper credentials to answer three
questions before any Options code depends on the answers:

1. Does get_option_chain() return live contracts for a real underlying?
2. How far back do get_option_bars() actually have data for an EXPIRED
   contract (there is no "as of a past date" query -- see the module docstring
   in domain/options/contracts.py once it exists -- so this is the only way
   to find out)?
3. Does this account tier support submitting a real multi-leg (MLEG) order in
   paper mode?

Not part of the shipped app -- run manually, read the printed findings, then
this file's job is done (kept for the record / to re-run if the account tier
changes). Run from the repo root:  python -m dashboard.backend.scripts.spike_options_data
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
if _ENV_PATH.exists():
    from dotenv import load_dotenv
    load_dotenv(_ENV_PATH)

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")


def _section(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def main() -> None:
    if not API_KEY or not SECRET_KEY:
        print("ALPACA_API_KEY / ALPACA_SECRET_KEY not set (dashboard/.env) -- cannot run the spike.")
        sys.exit(1)

    from alpaca.data.historical.option import OptionHistoricalDataClient
    from alpaca.data.requests import OptionBarsRequest, OptionChainRequest, OptionLatestQuoteRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import OptionsFeed
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        GetOptionContractsRequest, OptionLegRequest, MarketOrderRequest,
    )
    from alpaca.trading.enums import OrderClass, OrderSide, ContractType, PositionIntent, TimeInForce

    data_client = OptionHistoricalDataClient(API_KEY, SECRET_KEY)
    trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)

    underlying = "AAPL"

    # -------------------------------------------------------------------
    # Question 1: live option chain
    # -------------------------------------------------------------------
    _section(f"1. Live option chain for {underlying}")
    try:
        t0 = datetime.now()
        chain = data_client.get_option_chain(
            OptionChainRequest(underlying_symbol=underlying, feed=OptionsFeed.INDICATIVE)
        )
        elapsed = (datetime.now() - t0).total_seconds()
        contracts = list(chain.keys()) if hasattr(chain, "keys") else []
        print(f"OK -- {len(contracts)} contracts returned in {elapsed:.2f}s")
        if contracts:
            sample = contracts[0]
            print(f"Sample contract symbol: {sample}")
            print(f"Sample snapshot: {chain[sample]}")
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")

    # -------------------------------------------------------------------
    # Question 2: historical bar depth for EXPIRED contracts
    # -------------------------------------------------------------------
    _section(f"2. Historical bar depth for expired {underlying} contracts")

    # Need real expired OCC symbols to probe. Pull current contracts (any
    # expiration) from GetOptionContractsRequest, then also hand-construct a
    # few plausible expired-contract symbols (standard monthly = 3rd Friday)
    # at round strikes, since we cannot list *expired* contracts directly.
    def third_friday(year: int, month: int) -> date:
        d = date(year, month, 1)
        first_friday_offset = (4 - d.weekday()) % 7
        first_friday = d + timedelta(days=first_friday_offset)
        return first_friday + timedelta(days=14)

    def occ_symbol(underlying: str, expiration: date, right: str, strike: float) -> str:
        strike_int = round(strike * 1000)
        return f"{underlying}{expiration.strftime('%y%m%d')}{right}{strike_int:08d}"

    today = date.today()
    months_back_targets = [6, 12, 18]
    probe_symbols = []
    for months_back in months_back_targets:
        year = today.year
        month = today.month - months_back
        while month <= 0:
            month += 12
            year -= 1
        expiration = third_friday(year, month)
        for right in ("C", "P"):
            for strike in (150.0, 175.0, 200.0):
                probe_symbols.append((months_back, occ_symbol(underlying, expiration, right, strike)))

    print(f"Probing {len(probe_symbols)} candidate symbols across {months_back_targets} months back...")
    found_by_months_back: dict = {}
    for months_back, symbol in probe_symbols:
        try:
            bars_resp = data_client.get_option_bars(
                OptionBarsRequest(
                    symbol_or_symbols=[symbol],
                    timeframe=TimeFrame.Day,
                    start=today - timedelta(days=365 * 2),
                    end=today,
                )
            )
            bars = bars_resp.data.get(symbol, []) if hasattr(bars_resp, "data") else []
            if bars:
                found_by_months_back.setdefault(months_back, []).append((symbol, len(bars)))
        except Exception as e:
            print(f"  probe {symbol} raised {type(e).__name__}: {e}")

    if found_by_months_back:
        for months_back in sorted(found_by_months_back):
            hits = found_by_months_back[months_back]
            print(f"~{months_back} months back: {len(hits)} candidate(s) with data, e.g. {hits[0]}")
    else:
        print("No historical bars found for any synthesized candidate symbol at any depth tested.")
        print("This does NOT necessarily mean no data exists -- the synthesized strikes/expirations")
        print("may simply not match real listed contracts. Re-run with real symbols from a recent")
        print("chain snapshot (see question 1's `contracts` list) once some have aged into the past.")

    # -------------------------------------------------------------------
    # Question 3: multi-leg (MLEG) order support in paper mode
    # -------------------------------------------------------------------
    _section("3. Multi-leg (MLEG) order support in paper mode")
    try:
        contracts_resp = trading_client.get_option_contracts(
            GetOptionContractsRequest(
                underlying_symbols=[underlying],
                status="active",
                type=ContractType.CALL,
                expiration_date_gte=today + timedelta(days=14),
                expiration_date_lte=today + timedelta(days=60),
                limit=10,
            )
        )
        candidate_contracts = getattr(contracts_resp, "option_contracts", None) or contracts_resp
        if not candidate_contracts or len(candidate_contracts) < 2:
            print("Fewer than 2 live contracts returned to build a test vertical-spread MLEG order -- skipping.")
        else:
            # A real 2-leg order that needs no pre-existing position: a call
            # vertical spread (buy the lower strike, sell the higher one).
            sorted_contracts = sorted(candidate_contracts, key=lambda c: float(c.strike_price))
            long_leg, short_leg = sorted_contracts[0], sorted_contracts[-1]
            print(f"Attempting a 2-leg MLEG vertical spread: buy {long_leg.symbol}, sell {short_leg.symbol}...")
            order_data = MarketOrderRequest(
                qty=1,
                order_class=OrderClass.MLEG,
                time_in_force=TimeInForce.DAY,
                legs=[
                    OptionLegRequest(
                        symbol=long_leg.symbol, ratio_qty=1, side=OrderSide.BUY,
                        position_intent=PositionIntent.BUY_TO_OPEN,
                    ),
                    OptionLegRequest(
                        symbol=short_leg.symbol, ratio_qty=1, side=OrderSide.SELL,
                        position_intent=PositionIntent.SELL_TO_OPEN,
                    ),
                ],
            )
            order = trading_client.submit_order(order_data)
            print(f"OK -- order submitted, id={order.id}, status={order.status}")
            try:
                trading_client.cancel_order_by_id(order.id)
                print("Cancelled the test order.")
            except Exception as cancel_err:
                print(f"(cancel attempt raised {type(cancel_err).__name__}: {cancel_err} -- may have filled)")
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")

    _section("Findings summary -- fill in after reading the output above")
    print("(a) Chain endpoint working: see section 1")
    print("(b) Historical bar depth: see section 2")
    print("(c) Multi-leg order support: see section 3")


if __name__ == "__main__":
    main()

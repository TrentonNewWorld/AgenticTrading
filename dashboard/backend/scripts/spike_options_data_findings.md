# Options data-availability spike — findings

Run 2026-08-22 against real Alpaca paper credentials (`dashboard/.env`), via
`python -m dashboard.backend.scripts.spike_options_data`. This gates
Sub-phase 6 (the options backtester) of the Options-dashboard plan.

## (a) Live option chain

`get_option_chain(OptionChainRequest(underlying_symbol="AAPL", feed=OptionsFeed.INDICATIVE))`
returned **3,348 contracts in 1.6s**, each with real bid/ask/last-trade
quotes. The free `INDICATIVE` feed is sufficient for building a live chain
snapshot — no need for the paid `OPRA` feed for this.

## (b) Historical bar depth for expired contracts

Synthesized OCC symbols (standard monthly expirations, round strikes) at
6/12/18 months back **all found real historical daily bars** via
`get_option_bars()` on the same free `INDICATIVE` feed:

| Months back | Contracts with data | Example |
|---|---|---|
| ~6 | 6/6 probed | `AAPL260220C00150000`, 61 daily bars |
| ~12 | 6/6 probed | `AAPL250815C00150000`, 112 daily bars |
| ~18 | 6/6 probed | `AAPL250221C00150000`, 50 daily bars |

**Conclusion: a full-year contract-level backtest is feasible on the free
feed.** The bar counts (50-112) are lower than 252 trading days because a
single option contract doesn't trade the *whole* year — it only exists from
listing to expiration, so its own history is naturally shorter than the
underlying's. This is expected and matches how a real option position's
lifetime works; the backtester (Sub-phase 6) rolls to a new contract at
expiration rather than expecting one contract to span the full window.

The contract-symbol-synthesis approach itself (deterministic OCC symbol from
underlying + expiration + right + strike, no "list expired contracts" API
needed) works: every synthesized symbol that should exist did.

## (c) Multi-leg (MLEG) order support in paper mode

Confirmed working. A 2-leg MLEG **limit** order (vertical call spread — buy
low strike / sell high strike, `$0.01` limit, both legs `DAY`) was **accepted
(`OrderStatus.ACCEPTED`) and successfully cancelled**.

A first attempt using a **market** order class outside trading hours failed
with `options market orders are only allowed during market hours` (Alpaca
error 42210000) — this is an ordinary market-hours gate on the *market* order
type, identical in spirit to equity market orders, not an account-tier
restriction on multi-leg trading itself. The limit-order retry (which Alpaca
does accept outside market hours, queued for the next session) proved the
account and the MLEG order class both work; it was purely the order type
that was time-gated.

**Implication for Sub-phase 5's engine**: submit multi-leg entries as limit
orders (not market orders) so the engine isn't blocked by market-hours
timing when it wants to open a position — consistent with how a real trader
would price a spread anyway (a market order on a multi-leg combo risks a
much worse fill than a limit at the net debit/credit target).

## Bottom line for Sub-phase 6

Proceed with a **full-year backtest window** as originally scoped — the data
depth supports it. No need to scope down to a shorter window.

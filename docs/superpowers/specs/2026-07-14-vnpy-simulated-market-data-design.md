# vn.py Simulated Market Data Integration - Design Spec

- **Date:** 2026-07-14
- **Status:** Approved design, awaiting written-spec review
- **Project:** NewWorldTrading
- **Iteration:** Loop 1 of the vn.py market-data integration

## 1. Context

NewWorldTrading currently loads hourly US-equity bars directly through
`AlpacaDataLoader`. The main backtest path is coupled to the DJIA-30 universe,
US Eastern market hours, and Alpaca credentials. The project intends to use
vn.py for market data and, later, trading through Interactive Brokers (IB).

No IB paper account or TWS/IB Gateway installation is currently available.
Loop 1 therefore validates the vn.py data contract offline before attempting a
real broker connection.

## 2. Goal

Add a development-only `vn.py Simulation` data source to the existing Dashboard
backtest workflow. A user can select it, run a deterministic DJIA-30 backtest,
and view the existing chart, metrics, baselines, and trade log without IB,
Alpaca, LLM credentials, or network access.

This iteration proves this contract:

```text
vn.py BarData -> normalized OHLCV DataFrame -> existing NewWorldTrading backtest
```

It does not prove that an IB account can connect or that a strategy is
profitable on real market data.

## 3. Non-goals

- Connecting TWS, IB Gateway, or the `vnpy_ib` gateway.
- Subscribing to real-time ticks.
- Querying real historical data from IB.
- Sending, cancelling, or reconciling real or paper orders.
- Building a long-running vn.py worker service.
- Changing the supported universe beyond the existing DJIA-30.
- Enabling LLM decisions in simulation mode.
- Replacing or removing Alpaca.
- Treating synthetic performance as evidence of real trading performance.

## 4. Resolved Decisions

| Decision | Choice | Reason |
|---|---|---|
| Initial market | US equities | Matches the current NewWorldTrading product and future `vnpy_ib` path. |
| First data mode | Historical hourly bars | Matches the current backtest input and provides the shortest end-to-end loop. |
| Credentials | None in Loop 1 | IB access is not currently available. |
| Delivery surface | Existing Dashboard backtest page | Produces a user-visible, end-to-end result. |
| Data-source choice | Explicit selector | Makes provenance visible and creates a place for future IB data. |
| Simulation availability | Development-only feature flag | Prevents public users from mistaking synthetic data for real data. |
| Decisions | Existing rule-based agent | Keeps the run offline and free of model cost. |
| Universe | DJIA-30 | Avoids an unrelated universe refactor. |
| Architecture | Provider boundary now, separate vn.py process later | Keeps Loop 1 small without coupling the backtest to vn.py internals. |
| Baselines | Keep Buy and Hold and DJIA comparisons | The Dashboard should remain feature-complete in simulation mode. |

## 5. Alternatives Considered

### 5.1 Embed vn.py logic directly in the backtest engine

This is the smallest initial change, but it couples generation, conversion, and
strategy execution. A real IB integration would require extracting the same
logic later. Rejected because it creates deliberate rework.

### 5.2 Build a separate vn.py service immediately

This gives strong process isolation and resembles the eventual production
shape. It also adds process management, transport, health checks, and deployment
work before any broker connection exists. Rejected for Loop 1 as premature.

### 5.3 Establish a provider boundary, then extract the real runtime

Loop 1 keeps a simulation provider in the backend behind a stable market-data
contract. Loop 2 can implement an IB-backed provider via a separate vn.py worker
without changing consumers. This is the selected approach.

## 6. Architecture

```text
Dashboard
  |
  | POST /backtest/run {data_source: "vnpy_simulation"}
  v
Backtest API
  |
  v
MarketDataProvider factory
  |-- alpaca          -> existing AlpacaDataLoader
  `-- vnpy_simulation -> VnpySimulationProvider
                              |
                              | creates real vn.py BarData
                              v
                        VnpyBarAdapter
                              |
                              | Dict[str, pd.DataFrame]
                              v
Existing technical indicators, rule-based decisions, portfolio, baselines,
database persistence, and Dashboard charts
```

The backtest domain consumes only the normalized provider output. It does not
import `IbGateway`, understand broker credentials, or branch on vn.py fields.

### 6.1 Provider contract

The provider boundary preserves the current loader shape:

```python
class MarketDataProvider(Protocol):
    def fetch_bars(
        self,
        symbols: list[str],
        start: str,
        end: str,
    ) -> dict[str, pd.DataFrame]: ...
```

Returned frames use a timezone-aware `DatetimeIndex` named `timestamp` and
exactly these required columns:

```text
open, high, low, close, volume
```

`AlpacaDataLoader` remains the default provider. Existing requests that omit
`data_source` retain current behavior.

### 6.2 vn.py dependency boundary

Simulation uses the real `vnpy.trader.object.BarData` type with:

- `exchange=Exchange.SMART`
- `interval=Interval.HOUR`
- `gateway_name="VNPY_SIM"`

vn.py is a development-only optional dependency. A separate
`requirements-vnpy.txt` pins `vnpy==4.4.0` and repeats the project's
`numpy==2.2.6` pin so vn.py's broad NumPy constraint cannot upgrade the runtime
past the versions supported by the existing Numba/SciPy stack. vn.py is imported
lazily only after `vnpy_simulation` is selected. The production Alpaca path must
import and run without vn.py installed. `vnpy_ib` is not installed in Loop 1.

Loop 1 does not instantiate `EventEngine`, `MainEngine`, or `IbGateway`.

## 7. User Experience

### 7.1 Feature flag

The option is enabled only when:

```text
ENABLE_VNPY_SIMULATION=true
```

When disabled, the selector omits `vn.py Simulation`, and direct requests for
that source are rejected by the backend.

The frontend reads this state from a new non-secret endpoint:

```text
GET /config/features
-> {"vnpy_simulation_enabled": true | false}
```

The browser does not infer feature availability from local configuration.

### 7.2 Backtest setup

The existing Backtest Setup panel gains a `Market Data Source` select control:

```text
Alpaca
vn.py Simulation
```

Alpaca remains selected by default. Selecting simulation displays a persistent
notice: `Synthetic vn.py data for integration testing; not real market data.`
It also disables the model selector and displays `Rule-based (offline)` so the
page does not imply that the selected paid model is being used.

The request sends one canonical value:

```json
{"data_source": "vnpy_simulation"}
```

Allowed API values are `alpaca` and `vnpy_simulation`.

### 7.3 Result provenance

The selected source is stored on the agent run and both generated baseline runs
in the existing run `metadata` JSON as `data_source`; no new database column is
required. Run metadata returned to the Dashboard exposes an optional
`data_source` field. Historical rows without the field are interpreted as
`alpaca` for backward compatibility.

Simulation provenance remains visible while a run is active and after a saved
run is reloaded.

## 8. Simulated Data

### 8.1 Coverage

The provider generates hourly bars for every symbol in the current DJIA-30
constant over the requested window. It generates timestamps only on weekdays
and only at decision points accepted by the current US/Eastern market-hours
filter.

The provider freezes the existing Alpaca-loader boundary convention: `start` is
inclusive and `end` is exclusive. Switching providers therefore does not change
the meaning of the form's start and end dates.

### 8.2 Determinism

The price path is derived from a stable SHA-256 seed over canonical inputs such
as symbol and requested date. Python's process-randomized `hash()` is not used.

The same symbols and date range must produce byte-for-byte equivalent normalized
OHLCV values across processes and test runs.

### 8.3 Market scenarios

Synthetic paths contain controlled downtrend, recovery/uptrend, and sideways
segments. The canonical Dashboard demo window is `2026-04-01` through
`2026-04-23` (`end` exclusive). It must provide enough history for RSI, SMA20,
and SMA50 and must produce at least one rule-based trade. Very short valid
windows may complete with no trades and must not fabricate one by bypassing the
strategy rules.

The simulation validates the integration, not investment performance.

### 8.4 Bar invariants

Every generated and converted row must satisfy:

- `open`, `high`, `low`, and `close` are finite and greater than zero.
- `high >= max(open, close)`.
- `low <= min(open, close)`.
- `volume` is finite and non-negative.
- timestamps are timezone-aware in `US/Eastern`.
- timestamps are strictly increasing after normalization.
- duplicate timestamps are rejected.

## 9. vn.py Conversion

The adapter performs this explicit mapping:

| vn.py `BarData` | NewWorldTrading frame |
|---|---|
| `datetime` | `timestamp` index |
| `open_price` | `open` |
| `high_price` | `high` |
| `low_price` | `low` |
| `close_price` | `close` |
| `volume` | `volume` |

The adapter is responsible only for conversion and validation. Generation and
provider selection remain separate so conversion tests can use hand-built
`BarData` fixtures.

## 10. Backtest Execution

`data_source` is carried through the existing API, background runner, CLI
subprocess, and `HourlyBacktester` construction. Provider creation happens in
the backtest process.

For `vnpy_simulation`:

- `use_llm` is forced to false regardless of model controls in the page.
- the subprocess must not receive `--use-llm`.
- no Alpaca, IB, Yahoo, or hosted-model request is permitted.
- existing technical indicators and rule-based portfolio decisions are reused.
- agent, Buy and Hold, and DJIA curves are persisted and shown normally.

### 10.1 Offline baseline correction

The current `BaselineGenerator` loads Alpaca credentials in its constructor even
when the caller already provides all bar frames. That coupling makes the current
baseline path incompatible with a truly offline run.

Loop 1 changes credential loading to be lazy: operations that calculate a
baseline from supplied `bars_by_symbol` do not initialize an Alpaca client or
read credentials. Only methods that actually fetch Alpaca data require Alpaca
credentials. This is a targeted correction; baseline algorithms remain
unchanged.

## 11. Error Handling

| Condition | Required behavior |
|---|---|
| Unknown `data_source` | Reject with HTTP 422 before scheduling. |
| Simulation requested while feature flag is off | Reject with HTTP 403 before scheduling. |
| vn.py optional dependency missing | Return HTTP 503 with the install instruction; do not start a run. |
| Invalid or reversed dates | Preserve current request validation behavior. |
| No valid market timestamps | Fail with an explicit no-trading-hours message. |
| Invalid generated or converted bar | Fail with symbol, timestamp, and violated invariant. |
| Simulation provider failure | Mark the run failed; never fall back to Alpaca. |
| Alpaca provider failure | Preserve current error; never fall back to simulation. |
| Background exception | Publish the error and always clear the global running state. |

Provider provenance must be explicit. Silent fallback between real and synthetic
data is prohibited.

## 12. Testing

### 12.1 Unit tests

- Map a real vn.py `BarData` fixture field by field.
- Reject non-finite or non-positive prices, inconsistent high/low, negative
  volume, naive timestamps, and duplicate timestamps.
- Verify sorted, timezone-aware frame output.
- Verify stable generator output across repeated instances.
- Verify DJIA-30 coverage and weekend exclusion.
- Verify the canonical demo window creates indicator-ready data.
- Verify provider-factory defaults and allow-list behavior.
- Verify feature-flag and missing-dependency errors.
- Verify baseline calculation from supplied frames never reads Alpaca
  credentials or creates an Alpaca client.

### 12.2 API and runner tests

- An omitted `data_source` selects Alpaca.
- `/config/features` exposes only the boolean feature state.
- `vnpy_simulation` is propagated through the request, background runner, CLI,
  and engine.
- Simulation forces rule-based execution even if the request names an LLM model.
- Simulation persists `metadata.data_source` and returns it in run metadata.
- Run failure clears the running flag.
- Unsupported or disabled sources fail before a thread is started.

### 12.3 Integration tests

- Run the canonical simulation window with network clients replaced by
  fail-on-call fakes.
- Assert no Alpaca, IB, Yahoo, or LLM client is called.
- Assert the run completes with a non-empty agent equity curve.
- Assert Buy and Hold and DJIA curves are non-empty.
- Assert the canonical demo produces at least one recorded trade.
- Assert all stored runs are labelled with the correct source provenance.

### 12.4 Frontend tests and manual QA

- The selector is hidden when the feature flag is disabled.
- The selector sends the canonical source value when enabled.
- Simulation disables the model selector and labels the run rule-based.
- The simulation disclaimer remains visible during and after the run.
- Existing progress, chart, metric, run-history, and trade-log views still work.
- Alpaca remains the default and its request shape remains backward-compatible.

## 13. Acceptance Flow

```text
1. Install the optional vn.py development dependency.
2. Set ENABLE_VNPY_SIMULATION=true.
3. Start NewWorldTrading.
4. Open Dashboard -> Playground -> Backtest.
5. Select vn.py Simulation.
6. Run the canonical DJIA-30 demo window (`2026-04-01` to `2026-04-23`).
7. Wait for completion.
8. Confirm the agent, Buy and Hold, and DJIA curves render.
9. Confirm metrics and at least one trade render.
10. Confirm the page labels the run as synthetic vn.py data.
11. Confirm no external credentials or network requests were required.
12. Disable the feature flag and confirm the option disappears while Alpaca
    remains unchanged.
```

## 14. Loop 2 Direction

After an IB paper account and TWS or IB Gateway are available, create a separate
vn.py worker that owns `EventEngine`, `MainEngine`, and `IbGateway`. It will
subscribe to or query IB data and expose the same normalized provider contract.

Loop 2 replaces the simulation producer, not the Dashboard, normalized frame
contract, technical-indicator code, or backtest consumers. Real-time ticks and
trading orders remain separate later loops with their own designs and safety
reviews.

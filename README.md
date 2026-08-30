
<div align="center">
  <img src="./dashboard/frontend/images/atltransparent.png" alt="NewWorldTrading" width="140">
  <h1>NewWorldTrading</h1>
</div>

<p align="center">
  <a href="https://discord.gg/9HnQ6XDG98">
    <img src="https://img.shields.io/badge/Discord-Join%20Community-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord">
  </a>
</p>

**NewWorldTrading is a platform for LLM-powered trading agents.**
Turn trading ideas into traceable experiments: prototype agents, run backtests and paper-trading simulations, inspect reasoning and decision logs, benchmark against market baselines, and — when you're ready — trade live under per-order risk caps.

<div align="center">
  <img src="./dashboard/frontend/images/snapshot.png" alt="Website Snapshot" width="720">
</div>

## 📥 Download

**[⬇ Download the bot (latest release)](https://github.com/TrentonNewWorld/AgenticTrading/releases/latest)** — one zip containing the full bot, the **[Setup Guide](SETUP-GUIDE.md)**, the **[Disclaimer](DISCLAIMER.md)**, and the **[License](LICENSE)**.

Or clone this repository — it is the same code. Read [DISCLAIMER.md](DISCLAIMER.md) before trading anything.

## How It Works

**Make it → prove it → trade it.**

1. **Create or upload a strategy.** Write your own in the built-in editor (a
   simple Python `decide()` function — docs and examples are on the Manual
   page in-app), build an AI agent from a plain-English trading instruction on
   My Agents, or upload a `.strategy.json` file.
2. **It gets vetted automatically.** Every strategy is safety-scanned,
   validated, and backtested over a full year of real market data before it
   can touch even simulated money. (Prediction-market strategies instead run
   a 5-day live forward paper-test — a deliberate risk control.)
3. **Paper-trade it.** Allocate simulated capital on the Strategy page and
   watch real-time performance with zero risk, on any of the six dashboards.
4. **Go live only if you choose.** Live trading sits behind a per-strategy
   activation AND a master switch, with per-order risk caps. It is off by
   default. Your broker keys, your risk.

Everything runs locally on your machine (`127.0.0.1` only) — your broker keys
never leave it, and uploaded strategies execute in a locked-down sandbox.

## Key Features

- **Create trading agents your way** — choose a model, data source, and trading prompt, or connect your own agent through the API.
- **Six dashboards** — Stocks, Options, Futures, Forex, Crypto, and Prediction markets, each with its own strategy catalog and manual-trading page.
- **Test before using real capital** — move from historical backtests to live-market paper trading in one workflow.
- **Go live when you're ready** — connect a broker account and let a strategy trade it under per-order risk caps, gated behind a master switch. See [Live Trading](docs/source/lab/live_trading.rst).
- **See every run, decision, and reason** — monitor positions, trades, portfolio changes, and the reasoning behind each action.
- **Measure more than returns** — performance, risk, drawdown, and trading behavior under standardized metrics.
- **Compare strategies in the open** — benchmark LLM models, baseline strategies, and market indices on a leaderboard under the same market window.

## File Structure

```
NewWorldTrading/
├── dashboard/                 # The shipping product
│   ├── backend/               # FastAPI package (dashboard.backend.*)
│   │   ├── api/               # /api routers + Agent API v2 (/api/v2)
│   │   ├── domain/            # Business logic (runs, backtesting, leaderboard, trading, …)
│   │   ├── execution/         # v2 execution backends (backtest live; paper stub)
│   │   ├── infrastructure/    # LLM validator, market data, broker adapters
│   │   └── integrations/      # Discord bot, etc.
│   ├── frontend/              # Static assets: landing (/) + dashboard (/app)
│   ├── landing/               # Vite/React landing source (builds into frontend/)
│   ├── scripts/               # CLI backtests (backtest_hourly_agent.py, …)
│   ├── config/                # defaults.json, leaderboard*.json, marketplace.json
│   └── storage/               # data/backtest.db + backups/
├── packaging/agentictrading/  # PyPI SDK (AgentRunner + HTTP client)
├── credentials/               # Local only — not in git (see alpaca.json.example)
├── docs/                      # Sphinx docs + architecture notes
├── requirements.txt           # Dashboard deps
└── Dockerfile / render.yaml   # Backend deploy
```

## Full Setup Guide (Windows & Linux)

### 1. Prerequisites

| Requirement | Windows | Linux (Debian/Ubuntu shown) |
|---|---|---|
| Python 3.11+ (3.13 recommended) | [python.org](https://www.python.org/downloads/) — check **"Add python.exe to PATH"** during install | `sudo apt install python3 python3-venv python3-pip curl` |
| (Optional) Node.js 20+ | Only needed to rebuild the landing page | Same |
| (Optional) Docker | Alternative one-command run | Same |

### 2. Unpack and create a virtual environment

Unzip the bot anywhere you like, open a terminal **in that folder**, then:

**Windows (PowerShell or cmd):**
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

**Linux/macOS:**
```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure credentials

```
copy .env.example dashboard\.env     (Windows)
cp .env.example dashboard/.env       (Linux)
```

Open `dashboard/.env` in a text editor and fill in what you plan to use:

- `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` — **paper**-trading keys from
  [alpaca.markets](https://alpaca.markets). Required for market data and
  backtests. Start with paper keys; live keys are a separate, deliberate step.
- `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY` / etc. — only if you want
  LLM-driven agents. Everything rule-based works without them.
- Leave everything else at its default to start. Every variable is documented
  inline in the file.

The app reads `dashboard/.env` (not a repo-root `.env`).

### 4. Run it

**Either platform, by hand (from the repo root, venv active):**
```
uvicorn dashboard.backend.app:app --host 127.0.0.1 --port 8000
```

**Or use the launchers** (they also write logs to `ops/logs/`):

| | Windows | Linux |
|---|---|---|
| Start server | `ops\run-server.cmd` | `bash ops/run-server.sh` |
| Watchdog (restart if down) | `ops\keep-bot-online.ps1` | `bash ops/keep-bot-online.sh` |
| Install auto-restart schedule | `ops\install-keepalive-task.ps1` (Scheduled Task, every 4h) | `bash ops/install-keepalive-cron.sh` (cron, every 4h + on reboot) |

**Or Docker (identical on any OS):**
```
docker compose up --build
```

Then open **http://127.0.0.1:8000** — the app signs you in automatically on
localhost. The server binds `127.0.0.1` only, on purpose: the local auto-login
means a network-reachable bind would hand anyone on your network an
admin session. Do not change it to `0.0.0.0` unless you have disabled
auto-login and added real authentication.

### 5. Load strategies

This build ships with **empty strategy catalogs** — strategies are distributed
separately as zip files, one per dashboard plus performance-tier bundles.

To install a strategy:
1. Unzip the strategy pack you want.
2. In the app, pick the matching dashboard (Stocks, Options, Futures, Forex,
   Crypto, or Prediction) from the Home screen.
3. Go to **Testing** (or the Prediction dashboard's **Strategy** page) and
   upload the `.strategy.json` file. It is scanned, validated, and backtested
   (Prediction strategies instead run a 5-day live forward paper-test — that
   delay is a deliberate risk control, not a bug).
4. When it finishes, click **Add to Strategy** to place it in that dashboard's
   catalog, where you can allocate simulated capital and activate it for
   paper trading.

Stock strategy files marked `strategy-reference-v1` refer to built-in engine
strategies and register through the same upload page.

### 6. Going live (only when you're ready)

Live trading is **double-gated**: each strategy must be individually activated
for Live on the Strategy page, **and** the master Live Trading switch in the
header must be on. Orders are further limited by per-order risk caps. Connect
broker credentials on the **API Connections** page. Start with paper trading;
verify behavior for at least several sessions before arming anything live.

### 7. Troubleshooting

- **`ModuleNotFoundError: dashboard`** — you ran a file directly. Always start
  via `uvicorn dashboard.backend.app:app` from the repo root.
- **Port already in use** — another instance is running; the watchdog scripts
  detect and manage this safely.
- **Garbled characters on Windows** — harmless; the app forces UTF-8 output
  itself. If you see it in your own terminal, run `chcp 65001` first.
- **Empty leaderboard/market data** — check your Alpaca keys in
  `dashboard/.env` and the server log at `ops/logs/server.log`.
- **Tests** — `pip install pytest`, then `pytest dashboard/backend/tests/ -q`
  (the suite never touches your live database).

## Strategies

This repository ships with **empty strategy catalogs** — a blank-slate platform.
Write your own strategies in the built-in editor (see the Manual page in-app),
build an AI agent from a plain-English instruction, or upload strategy files.

## License

OpenMDW-1.0 — See [LICENSE](LICENSE)

---

Built with Alpaca API, FastAPI, Chart.js, and SQLite

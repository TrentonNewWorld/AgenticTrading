
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

## Getting Started

**Read [SETUP-GUIDE.md](SETUP-GUIDE.md) for full Windows & Linux setup instructions, and [DISCLAIMER.md](DISCLAIMER.md) before trading anything.**

One codebase, three ways to run — identical behavior on Windows and Linux:

```bash
# Any platform, by hand
pip install -r requirements.txt
uvicorn dashboard.backend.app:app --reload
# open http://localhost:8000
```

```bash
# Docker (identical everywhere)
docker compose up --build
```

**Always-on with a watchdog** (probes `/health` every 4h from midnight, restarts if down):

| | Windows | Linux/macOS |
|---|---|---|
| Start server | `ops\run-server.cmd` | `ops/run-server.sh` |
| Watchdog once | `ops\keep-bot-online.ps1` | `ops/keep-bot-online.sh` |
| Install schedule | `ops\install-keepalive-task.ps1` | `ops/install-keepalive-cron.sh` |
| Discord bot | `ops\run-discord-bot.cmd` + `keep-discord-bot-online.ps1` | `ops/run-discord-bot.sh` + `keep-discord-bot-online.sh` |

Both watchdog sets share the same semantics: idempotent when healthy, bind `127.0.0.1` only, and never kill a port listener that isn't this repo's own venv Python.

## Strategies

This repository ships with **empty strategy catalogs** — a blank-slate platform.
Write your own strategies in the built-in editor (see the Manual page in-app),
build an AI agent from a plain-English instruction, or upload strategy files.

## License

OpenMDW-1.0 — See [LICENSE](LICENSE)

---

Built with Alpaca API, FastAPI, Chart.js, and SQLite

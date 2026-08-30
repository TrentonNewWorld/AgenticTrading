# Discord Bot Instructions

Talk to the NewWorldTrading bot in Discord. Chat about strategies, save a prompt, and run a real backtest (Alpaca bars + hosted model) without leaving Discord.

Invite: [discord.gg/9HnQ6XDG98](https://discord.gg/9HnQ6XDG98)

---

## Link your website account (required for `/agent`)

`/agent` shows **your** agents from the lab website — not everyone else's.

1. Sign in on the website. (No account yet? Click **Sign in** in the header, then **Need an account? Sign up**.)
2. Click **Open Discord** (header or My Agents). The first time, Discord asks you to authorize once.
3. After linking, run `/agent` in Discord to pick one of your built-in agents.

Until you link, `/backtest prompt:…` still works (results stay on a Discord session, not an agent card).

---

## Fastest path: one command, no extra clicks

```
/backtest prompt: Buy the Magnificent 7 equally when the market is calm; cut exposure when volatility spikes.
```

Optional dates: `start:YYYY-MM-DD` `end:YYYY-MM-DD`.

---

## Full workflow

1. `/agent` — pick a linked website agent (after Open Discord / OAuth)
2. `/ask` — brainstorm rules (or type in the bot channel)
3. `/strategy` — save a reusable prompt (+ optional **Run backtest** button)
4. `/backtest` — run on real Alpaca data (`prompt:` or `code:`)

When a backtest finishes for a selected agent, the bot includes a **Dashboard** link that opens Playground → Backtest for that agent and run (`/app?view=backtest&agent_id=…&run_id=…`).

---

## Commands

- **`/agent`** — list *your* linked agents and select one
- **`/ask`** — chat with the selected (or default) agent
- **`/strategy`** — synthesize and save a strategy prompt
- **`/prompt`** — show a saved strategy by share code
- **`/backtest`** — start a backtest (`prompt` or `code`, optional dates)
- **`/reset`** — clear temporary chat memory

---

## Tips

- Only one backtest runs on the server at a time.
- Prefer **built-in** agents for Discord backtests (results land on that agent's website card).
- If `/agent` says you are not linked, use **Open Discord** on the site while signed in.
- `/backtest` returns immediately ("queued"). When the job finishes, the bot **posts results + chart in the channel** and @mentions you — you do not need to keep the ephemeral "thinking" message open. Long runs (10–30 min) still deliver.

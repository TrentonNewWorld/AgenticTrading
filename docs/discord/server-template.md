# NewWorldTrading — Discord Server Template

Complete setup guide: channels, roles, bots, ready-to-paste messages, and how
to get the custom **NewWorldSupport** bot online.

---

## 0. Server identity

**Server name:** `NewWorldTrading`
**Server icon:** the planet logo (`dashboard/frontend/images/atltransparent.png`)

**Server description** (Server Settings → Overview — shows on invite previews
and, if Community is enabled, in discovery):

> The official home of NewWorldTrading — a free, open-source trading bot.
> Build and backtest your own strategies, paper trade, go live when you're
> ready. 18+ only. Not financial advice.

Shorter variant if you prefer punchy:

> Free open-source trading bot. Build strategies, backtest on real data,
> trade paper or live. 18+ only. Not financial advice.

---

## 1. Roles

Create in this order (Discord role hierarchy matters — higher role = more power):

| Role | Color suggestion | Key permissions |
|---|---|---|
| **Owner** | Gold `#F1C40F` | Administrator. That's it — Administrator covers everything. |
| **Mod** | Red `#E74C3C` | Manage Messages, Kick Members, Ban Members, Timeout Members, Manage Threads, View Audit Log, Mention @everyone |
| **Verified 18+** | Green `#2ECC71` | No extra permissions — this role only unlocks channel visibility (see §2) |
| **Member** | Blurple `#5865F2` | Default @everyone-level permissions. Assigned automatically on join (use a bot's autorole, §3) |

**Server-wide settings:**
- Verification Level: **Medium** (registered >5 min) or **High** (member >10 min) — cuts raid spam
- Explicit content filter: **All members**
- Enable **Community** (Server Settings → Enable Community) — required for announcement-type channels and the server rules screen

---

## 2. Channel layout

### ── 📌 START HERE ──

**`#welcome`** (read-only for everyone)
> Channel topic: `Start here — what this server is and where to go next.`

**`#rules`** (read-only; use Discord's Community "Rules" screening too)
> Channel topic: `Read before participating. Breaking these = timeout or ban.`

**`#verify-18`** (visible to Member; the ONLY gate to trading channels)
> Channel topic: `This server discusses real-money trading. Verify you are 18+ to unlock the rest of the server.`

Permission setup for the age gate:
- `@everyone` / `Member`: can **view** only `#welcome`, `#rules`, `#verify-18`
- `Verified 18+`: can view everything else
- The verification bot (§3) grants `Verified 18+` on successful verification

### ── 🤖 THE BOT ──

**`#get-the-bot`** (read-only; Verified 18+ can view)
> Channel topic: `Download NewWorldTrading free + learn how to build your own strategies.`
> Pinned messages: see §4.2 — post the "build your own strategies" message FIRST, then the "get the bot" message, so the strategy explanation sits above it.

**`#ai-support`** (Verified 18+ can view and send)
> Channel topic: `Ask NewWorldSupport anything about the bot — type /support followed by your question.`
> This is where the custom bot lives (§5). Set `DISCORD_SUPPORT_CHANNEL_ID` to this channel's ID so the 24-hour reminder posts here.

**`#announcements`** (Announcement-type channel; read-only; Owner/Mod post)
> Channel topic: `New strategy releases, new Discord updates, and new version releases.`

### ── 💬 COMMUNITY ──

**`#general`** (Verified 18+)
> Channel topic: `Talk trading, markets, and the bot. Not financial advice — ever.`

**`#strategy-showcase`** (Verified 18+)
> Channel topic: `Show off strategies you built in Testing — screenshots of equity curves, backtest results, catalog entries.`

**`#wins-and-losses`** (Verified 18+)
> Channel topic: `Paper or live, green or red — post your results. Honesty culture: losses welcome.`

**`#bugs-and-feedback`** (Verified 18+)
> Channel topic: `Something broken? Feature idea? Post it here so it doesn't get lost in general.`

### ── 🔒 STAFF ──

**`#mod-chat`** (Mod + Owner only)
> Channel topic: `Staff coordination.`

**`#mod-log`** (Mod + Owner only; bot-written)
> Channel topic: `Automated moderation log — joins, leaves, deletes, bans.`

---

## 3. Bots to add

### Age verification — the honest picture

You asked for verification bots that **pay you** to use them. I looked: **no
legitimate verification bot pays server owners per verification.** That
business model doesn't exist in the documented ecosystem — anything offering
it would be monetizing your members' identity data, which is a serious red
flag (and a liability for you). Real options:

| Option | Cost | How it verifies |
|---|---|---|
| **Discord's native Age-Restricted channels** | Free | Self-attestation; Discord's own Age Assurance program (2026) adds face/ID estimation at the platform level |
| **Carl-bot / MEE6 reaction gate** | Free | User clicks "I am 18+" → gets `Verified 18+` role. Self-attestation only, zero friction |
| **[Ageify](https://top.gg/bot/975133985137123339)** | Freemium | ML-based age estimation; mods never see IDs |
| **[VerifyMe](https://github.com/PhillipDiCarlo/VerifyMe-A-Discord-Age-Verifier/)** (self-hosted) | Stripe Identity fees | Real government-ID verification via Stripe Identity; you never handle personal data |
| **[VibeBot](https://www.vibebot.gg/features/verification)** | Freemium | DOB confirmation + optional ID upload to a private staff channel |

**Recommendation:** Carl-bot reaction gate for launch (free, instant), upgrade
to Ageify or VerifyMe if you ever need verification with actual teeth. Also
mark trading channels as Age-Restricted in Discord's own channel settings —
that layers Discord's platform-level gate on top for free.

### The full bot lineup

| Bot | Purpose |
|---|---|
| **NewWorldSupport** (custom, §5) | `/support` Q&A grounded in the platform knowledge base + 24h reminder. This is its ONLY command. |
| **Carl-bot** | Reaction-role age gate, autorole (`Member` on join), mod-log, automod |
| **MEE6** or **Dyno** | Backup automod/leveling if you want XP roles (optional — skip if Carl-bot covers you) |
| **Ticket Tool** | Private ticket threads for account-specific issues that shouldn't be public (optional) |

Keep the bot count low. Every bot is an account with permissions in a server
about money.

---

## 4. Ready-to-paste messages

### 4.0 `#rules` (also paste these into Community → Rules Screening, one per rule)

```
**Server Rules** 📜

**1. 18+ only.** This server discusses real-money trading. If you are under
18, leave now. Lying in verification = permanent ban.

**2. Nothing here is financial advice.** Not from mods, not from members,
not from bots. "Buy X" / "sell Y" posts presented as advice get removed.
Share what YOU did, not what others should do.

**3. Never share API keys, passwords, or account credentials.** Not in
channels, not in DMs — no mod or bot will ever ask for them. Anyone asking
is a scammer; report them.

**4. No pumping, shilling, or promotion.** No coin/stock pumping, no
referral links, no "signal group" ads, no DM solicitation. Instant ban for
crypto-scam behavior.

**5. Be honest about results.** Post real numbers or don't post. Faked
screenshots and cherry-picked flexes poison the community.

**6. Be respectful.** No harassment, slurs, or personal attacks. Argue
about strategies, not people.

**7. No spam.** No flooding, no mass mentions, no repeated self-posting.
Use the right channel for your topic.

**8. Mods have final say.** Timeout → kick → ban, at their discretion.
Ban evasion (alt accounts) = permanent ban for all accounts.

React ✅ if you agree, then head to #verify-18.
```

### 4.1 `#welcome`

```
**Welcome to NewWorldTrading** 🌍

This is the community for the free NewWorldTrading bot — build trading
strategies, backtest them on real market data, run them in paper or live.

**Get started:**
1. Read #rules
2. Verify you're 18+ in #verify-18
3. Grab the bot in #get-the-bot
4. Questions? Ask the support bot in #ai-support — type `/support` and your question

Trading involves risk of loss. Past performance does not guarantee future
results. Nothing in this server is financial advice.
```

### 4.2 `#get-the-bot` — post these two in order

**Message 1 (the strategy explanation — sits ABOVE the download message):**

```
**Build your own strategies** 🛠️

NewWorldTrading isn't just prebuilt strategies — you can make your own:

1. Open the dashboard and go to **Testing**
2. Paste or upload your strategy code
3. It's automatically scanned for safety, then backtested over the most
   recent completed year with a $1,000 starting wallet
4. Like the results? Add it to the **Strategy** catalog
5. From the catalog, activate it for **Paper** (simulated) or **Live** (real
   money — requires the separate Live Trading switch to be on too)

No code? Describe your idea in the dashboard's **Testing** page, then
turns the conversation into a backtestable strategy prompt.
```

**Message 2 (the download message):**

```
**Get the bot — free** ⬇️

NewWorldTrading is completely free. Grab it here:
👉 https://whop.com/YOUR-STORE-HERE

**What it is:** an open-source trading platform that runs on your own
machine. Backtest strategies against real market data, paper trade with
simulated money, and — when you're ready and explicitly turn it on — trade
live through your own broker account. Your keys, your machine, your money.

Setup takes ~5 minutes: install, add your broker keys, open the dashboard
in your browser. Full steps come with the download.
```

### 4.3 `#verify-18` (adapt to whichever verification bot you pick)

```
**Age verification required** 🔞

This server discusses real-money trading, so it's 18+ only.

React with ✅ below to confirm you are 18 or older. This unlocks the rest
of the server.

By verifying you confirm you're 18+, you understand trading involves risk
of loss, and nothing here is financial advice.
```

### 4.4 `#ai-support` (pin this)

```
**Meet NewWorldSupport** 🤝

Your first stop for every question about the bot — setup, building
strategies, paper vs. live, broker connections, all of it.

**How to use it:** type `/support` followed by your question. Any phrasing
works.

Examples:
• `/support how do I make my own strategy`
• `/support is my strategy trading real money right now`
• `/support where do I get the bot`

If it can't answer something, it'll flag a mod. For account-specific issues,
open a ticket instead of posting keys or personal info here. **Never share
your API keys with anyone — no mod or bot will ever ask for them.**
```

### 4.5 `#announcements` templates

```
**📦 New version — v_._._**
What changed:
• …
• …
Update: grab the latest from #get-the-bot. Questions → #ai-support.
```

```
**📈 New strategy release — [Strategy Name]**
Now in the Strategy catalog:
• What it does: …
• Backtest (last completed year, $1k wallet): …% return, …% max drawdown
Try it in Paper first. Past performance ≠ future results.
```

```
**🛠️ Discord update**
• …
```

---

## 5. The custom NewWorldSupport bot — what it is and how to get it online

### What was built (already in the codebase)

The existing Discord bot (`dashboard/backend/integrations/discord_bot.py`)
gained the NewWorldSupport features:

- **`/support <question>`** — answers ANY question about the platform using an
  **OpenRouter free model**, grounded in a curated knowledge base
  (`SUPPORT_KNOWLEDGE_BASE` in `dashboard/backend/domain/chat/service.py`).
  It covers: getting the bot, every dashboard page, building strategies,
  paper vs. live semantics, the risk gate, broker connections, Discord
  commands, and common issues. The system prompt pins it to that KB — it
  says "I don't know, ask a mod" rather than guessing, and never gives
  personalized financial advice.

  Three tiers, cheapest first:
  1. **Curated keyword FAQ** (`_SUPPORT_FAQ` in `discord_bot.py`) — instant,
     reviewed answers for the common questions. Costs nothing and never
     touches the API, which is what keeps the daily free quota available for
     the questions that actually need it.
  2. **OpenRouter free model** — for anything the FAQ misses.
  3. **Human escalation** — pings the support role if both miss, or if the
     free quota is spent. It never falls back onto a paid model.
- **24-hour reminder** — posts "NewWorldSupport is here — ask a question
  with `/support`" into the support channel every 24 hours.
- **`/support` is the bot's only command.** `/ask`, `/agent`, `/strategy`,
  `/backtest`, `/reset`, `/prompt` and the free-form DM/@mention chat were
  removed, along with the agent-selection and backtest-watcher machinery
  behind them (~950 lines). Keep it that way unless you deliberately want
  the bot doing more than support.

**Before launch:** edit `SUPPORT_WHOP_URL` in
`dashboard/backend/domain/chat/service.py` to your real Whop listing — it
feeds both the KB and the fallback FAQ.

### Getting it online

**Step 1 — Discord Developer Portal** (https://discord.com/developers/applications)
1. New Application → name it **NewWorldSupport**
2. General Information → **Description** (max 400 chars; this is the bot's
   "About Me", shown when someone clicks its profile). Paste:

   > NewWorldSupport — the help desk for NewWorldTrading. Ask me about
   > setting up the bot, building and backtesting your own strategies, or
   > the difference between paper and live trading. Type `/support`
   > followed by your question. Not financial advice.

   Shorter variant:

   > Help desk for NewWorldTrading. Ask setup, strategy, and paper-vs-live
   > questions with `/support`. Not financial advice.

3. General Information → **Tags** (up to 5, max 20 chars each — used for
   App Directory search). Suggested:

   `Trading` · `Support` · `Finance` · `Stocks` · `Backtesting`

   Swap `Backtesting` → `Automation` if you'd rather optimize for broader
   discovery than accuracy.

4. Bot tab → Reset Token → copy it (this is `DISCORD_BOT_TOKEN`)
5. Bot tab → Privileged Gateway Intents → enable **Message Content Intent**
6. Set the bot's avatar to the planet logo
   (`dashboard/frontend/images/atltransparent.png`)
7. OAuth2 → URL Generator → scopes `bot` + `applications.commands`;
   bot permissions: Send Messages, Embed Links, Read Message History,
   Use Slash Commands → open the generated URL and invite it to your server

> The **slash-command** description (what Discord shows in the `/` autocomplete
> dropdown) is separate and already set in code — see the `/support` command in
> `dashboard/backend/integrations/discord_bot.py`:
> *"Ask NewWorldSupport a question about the bot (setup, strategies, paper vs. live)."*

**Step 2 — configure** (in `dashboard/.env`)
```
DISCORD_BOT_TOKEN=<token from step 1>
DISCORD_GUILD_ID=<your server ID — right-click server icon → Copy Server ID>
DISCORD_SUPPORT_CHANNEL_ID=<#ai-support channel ID — right-click channel → Copy Channel ID>
DISCORD_SUPPORT_ROLE_ID=<Mod role ID, optional — pinged when the bot can't answer>
OPENROUTER_SUPPORT_API_KEY=<dedicated OpenRouter key, $0 credit limit — see below>
```
(Developer Mode must be on to see Copy ID: User Settings → Advanced.)

**Step 2a — the OpenRouter free-tier key (do not skip the $0 limit)**

`/support` answers run on OpenRouter **free models only**. Two layers keep it
that way, and you need both:

1. **In code** — the bot refuses any model id that doesn't end in `:free`.
   A typo'd or "upgraded" model fails closed to the human-escalation reply
   instead of quietly billing you.
2. **At OpenRouter** — this is the actual guarantee. Create a **dedicated**
   key at [openrouter.ai/keys](https://openrouter.ai/keys), then **Edit →
   Credit limit → `0`**. Free models cost $0 so they keep working; a paid
   model becomes *impossible* rather than merely discouraged.

Use a separate key from `OPENROUTER_API_KEY` — that one powers trading /
leaderboard models and may legitimately need paid credits, so it can't carry
a $0 limit. If `OPENROUTER_SUPPORT_API_KEY` is unset the bot falls back to the
shared key (layer 1 still applies, layer 2 does not).

**Free-tier limits:** OpenRouter caps free-model requests per day (the cap
scales with lifetime credits purchased — currently ~50/day under $10, ~1000/day
at $10+). When the quota runs out the API returns 429 and the bot falls back
to "ask a mod" — it **never** retries onto a paid model.

Two things stretch the quota: the curated FAQ is checked **first** (common
questions never touch the API at all), and the model is pinned to
`reasoning_effort=none`.

Optional — pick a different free model (verify current ids at
[openrouter.ai/models?q=free](https://openrouter.ai/models?q=free), the roster
changes):
```
OPENROUTER_SUPPORT_MODEL=deepseek/deepseek-r1:free
```

**Step 3 — run it locally**
```
pip install -r requirements-discord.txt
python -m dashboard.backend.integrations.discord_bot
```
Console prints `Discord bot connected as NewWorldSupport#…` and syncs the
slash commands to your server (guild-level sync = they appear immediately).

**Step 4 — keep it online 24/7.** Two options:
- **Same machine as the trading bot:** add a second Windows Scheduled Task
  mirroring `ops/keep-bot-online.ps1`'s pattern for
  `python -m dashboard.backend.integrations.discord_bot`.
- **Cloud (Render):** the repo ships `render-discord-bot.yaml` — a worker
  blueprint (~$7/mo). Render Dashboard → New → Blueprint → connect the repo
  → set the blueprint path to `render-discord-bot.yaml` → fill in the env
  vars (including the two new `DISCORD_SUPPORT_*` ones, already listed in
  the blueprint).

### Testing checklist

- [ ] `/support how do I get the bot` → answers with the Whop link
- [ ] `/support how do I make a strategy` → walks through Testing → catalog
- [ ] `/support what stock should I buy` → declines to advise, explains the platform instead
- [ ] Nonsense question → says it doesn't know, pings the Mod role
- [ ] Reminder posts in #ai-support (loop fires on startup, then every 24h)
- [ ] Commands refused outside allowed channels if `DISCORD_CHANNEL_ID` is set

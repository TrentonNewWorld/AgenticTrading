from __future__ import annotations

import json
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from dashboard.backend.domain.chat.service import SUPPORT_WHOP_URL, support_answer


_env_path = Path(__file__).resolve().parents[2] / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
load_dotenv()


def require_env(name: str) -> str:
    value = os.getenv(name)

    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")

    return value


# ---------------------------------------------------------------------------
# /support keyword FAQ -- tried BEFORE the model, for two reasons: these are
# exact, reviewed answers to the common questions, and they cost nothing,
# which keeps OpenRouter's daily free quota available for questions that
# actually need it. Also the OFFLINE fallback when the model-backed
# support_answer() (domain/chat/service.py, grounded in its knowledge base)
# is unavailable: no key configured, provider outage, quota spent.
# Keyed by keywords checked against the lowercased question; first match
# wins. The Whop URL is owned by the chat service so the model KB and this
# fallback can never drift apart.
_SUPPORT_FAQ: list[tuple[tuple[str, ...], str]] = [
    (
        ("get the bot", "download", "where do i get", "how do i get", "whop"),
        f"The bot is free — grab it here: {SUPPORT_WHOP_URL}\n"
        "See #get-the-bot for setup steps and how to build your own strategy.",
    ),
    (
        ("make my own strategy", "build a strategy", "create a strategy", "write a strategy", "upload a strategy"),
        "Go to **Testing** in the dashboard, paste or upload your strategy code. "
        "It gets scanned for safety, then backtested over the most recent completed "
        "year with a $1,000 starting wallet. If it clears the bar, you can add it to "
        "the **Strategy** catalog from there.",
    ),
    # ORDER MATTERS from here down: first match wins, so the more specific
    # entry must come first. "leaderboard" sits above the paper/live entry
    # because "what is the live trading leaderboard" contains "live trading"
    # and was being answered with the paper-vs-live explanation instead.
    (
        ("leaderboard",),
        "The **Live Trading Leaderboard** (Overview tab) shows real results from orders "
        "that were actually placed — not a simulation. If a strategy hasn't traded yet, "
        "it won't have a row there.",
    ),
    (
        ("paper", "live trading", "real money", "is it trading real money", "is my strategy live"),
        "A strategy can run in **Paper** (simulated) or **Live** (real money) — they're "
        "independent switches on the Strategy Catalog page. Live additionally needs the "
        "account-level **Live Trading** switch turned on (account menu) — both have to be "
        "on for real orders to place. Check the Strategy page to see which mode a given "
        "strategy is actually activated in.",
    ),
    (
        ("safe", "risk", "lose money", "how much can i lose", "guardrails"),
        "Live orders go through a risk gate: a per-order dollar cap, no shorting, sells "
        "capped to what's actually held, and sizing capped to the strategy's own allocated "
        "capital. That limits how much a single bad order can do, but it doesn't make "
        "trading risk-free — you can still lose money. Trading involves risk of loss; "
        "past performance doesn't guarantee future results.",
    ),
]


def _support_answer(question: str) -> Optional[str]:
    q = question.lower()
    for keywords, answer in _SUPPORT_FAQ:
        if any(k in q for k in keywords):
            return answer
    return None


def _parse_id_list(raw: Optional[str]) -> list[int]:
    """Parse a comma/space/semicolon-separated list of integer IDs."""
    if not raw:
        return []
    ids: list[int] = []
    for part in raw.replace(";", ",").replace(" ", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            print(f"Discord: ignoring invalid ID '{part}'")
    return ids


def guild_ids() -> list[int]:
    """Guilds (servers) to sync slash commands to. Supports a comma-separated list."""
    ids = _parse_id_list(require_env("DISCORD_GUILD_ID"))
    if not ids:
        raise RuntimeError("DISCORD_GUILD_ID must contain at least one guild id")
    return ids


def allowed_channel_ids() -> set[int]:
    """Optional channel allowlist. When non-empty, the bot only responds in these channels."""
    return set(_parse_id_list(os.getenv("DISCORD_CHANNEL_ID")))


def support_channel_id() -> Optional[int]:
    """Channel the 24h ``/support`` reminder posts into. ``None`` disables the
    reminder loop entirely rather than guessing a channel -- posting into the
    wrong channel is worse than not posting."""
    ids = _parse_id_list(os.getenv("DISCORD_SUPPORT_CHANNEL_ID"))
    return ids[0] if ids else None


def support_role_id() -> Optional[int]:
    """Optional role pinged when ``/support`` has no curated answer for a
    question. Unset means the fallback reply just links the channels instead
    of pinging anyone."""
    ids = _parse_id_list(os.getenv("DISCORD_SUPPORT_ROLE_ID"))
    return ids[0] if ids else None


def snark_channel_id() -> Optional[int]:
    """Channel the 4-hour snark rotation posts into (meant for #general, NOT
    the support channel). ``None`` disables the rotation entirely rather than
    guessing a channel."""
    ids = _parse_id_list(os.getenv("DISCORD_SNARK_CHANNEL_ID"))
    return ids[0] if ids else None


class RestrictedCommandTree(app_commands.CommandTree):
    """Command tree that optionally restricts commands to an allowlisted channel.

    When ``DISCORD_CHANNEL_ID`` is set (one or more ids), slash commands only run
    in those channels; elsewhere the user gets a short ephemeral notice. When it
    is unset, the bot responds in any channel.
    """

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        allowed = allowed_channel_ids()
        if allowed and interaction.channel_id not in allowed:
            try:
                await interaction.response.send_message(
                    "This bot only responds in its designated channel here. "
                    "Please use the configured channel.",
                    ephemeral=True,
                )
            except Exception:
                pass
            return False
        return True


class NewWorldTradingDiscordBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            tree_cls=RestrictedCommandTree,
        )

    async def setup_hook(self) -> None:
        """
        Sync commands to each configured guild (server).

        Guild-level synchronization makes commands appear quickly. Supports a
        comma-separated ``DISCORD_GUILD_ID`` so the bot can serve multiple
        servers at once.
        """
        for guild_id in guild_ids():
            guild = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild)
            synced_commands = await self.tree.sync(guild=guild)
            print(
                f"Synced {len(synced_commands)} Discord command(s) "
                f"to guild {guild_id}."
            )

        allowed = allowed_channel_ids()
        if allowed:
            print(f"/support is restricted to channel(s): {sorted(allowed)}")
        else:
            print(
                "/support works in any channel. "
                "Set DISCORD_CHANNEL_ID to restrict it."
            )

        if support_channel_id() is not None and not _support_reminder_loop.is_running():
            _support_reminder_loop.start()

        if snark_channel_id() is not None and not _snark_loop.is_running():
            _snark_loop.start()


bot = NewWorldTradingDiscordBot()


SUPPORT_REMINDER_TEXT = (
    "**NewWorldSupport** is here — ask a question with `/support` "
    "and I'll do my best to answer it."
)

#: Wall-clock spacing between reminders. Enforced against a persisted
#: timestamp, NOT just by the loop interval -- see _reminder_is_due.
SUPPORT_REMINDER_INTERVAL = timedelta(hours=24)

#: Survives restarts. Without it the reminder posts on every boot: the loop's
#: first iteration fires immediately, so a watchdog restart (or a laptop
#: reboot) re-posts and resets the 24h clock. That is "every 24h OR whenever
#: the process starts", which is not what was asked for and reads as spam if
#: anything ever restart-loops.
_REMINDER_STATE_PATH = (
    Path(__file__).resolve().parents[2] / "storage" / "data" / "discord_support_reminder.json"
)


def _reminder_last_sent() -> Optional[datetime]:
    """When the reminder last went out, or ``None`` if never / unreadable.
    Unreadable state means "send" rather than "never send" -- a corrupt file
    must not silently disable the reminder forever."""
    try:
        raw = json.loads(_REMINDER_STATE_PATH.read_text(encoding="utf-8"))
        return datetime.fromisoformat(raw["last_sent"])
    except Exception:
        return None


def _record_reminder_sent(when: datetime) -> None:
    try:
        _REMINDER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _REMINDER_STATE_PATH.write_text(
            json.dumps({"last_sent": when.isoformat()}), encoding="utf-8"
        )
    except Exception as exc:
        # Non-fatal: worst case the next boot re-posts early. Never let a
        # read-only disk stop the reminder itself.
        print(f"Discord: could not record support-reminder timestamp: {exc}")


def _reminder_is_due(now: datetime, last_sent: Optional[datetime]) -> bool:
    """Pure, so the 24h spacing is testable without waiting a day."""
    if last_sent is None:
        return True
    return (now - last_sent) >= SUPPORT_REMINDER_INTERVAL


# Polls hourly; the 24h spacing is enforced by _reminder_is_due against the
# persisted timestamp. A short poll with a real due-check is what makes the
# reminder survive restarts AND laptop sleep: tasks.loop(hours=24) alone would
# simply not fire while the machine was suspended, silently skipping a day.
@tasks.loop(hours=1)
async def _support_reminder_loop() -> None:
    """Posts a reminder into the configured support channel every 24h. Only
    starts when DISCORD_SUPPORT_CHANNEL_ID is set (see setup_hook) -- no
    channel configured means no reminder, rather than guessing one."""
    channel_id = support_channel_id()
    if channel_id is None:
        return

    now = datetime.now(timezone.utc)
    if not _reminder_is_due(now, _reminder_last_sent()):
        return

    # get_channel reads the local cache; fetch_channel asks the API. The cache
    # can legitimately miss (a channel the bot hasn't seen traffic in yet), so
    # a miss must not be treated as "no such channel" -- that silently skipped
    # a whole 24h cycle.
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception as exc:
            print(f"Discord: support reminder skipped, channel {channel_id} unreachable: {exc}")
            return
    try:
        await channel.send(SUPPORT_REMINDER_TEXT)
    except Exception as exc:
        # Deliberately do NOT record a timestamp on failure -- the next hourly
        # poll should retry, not wait another full day.
        print(f"Discord: support reminder failed to send: {exc}")
        return
    _record_reminder_sent(now)
    print(f"Discord: support reminder posted to channel {channel_id}.")


@_support_reminder_loop.before_loop
async def _before_support_reminder() -> None:
    """tasks.loop fires its first iteration immediately, but setup_hook (where
    the loop is started) runs BEFORE the gateway READY event -- so the channel
    cache is still empty and the first reminder was being skipped on every
    single boot. Waiting for ready makes that first poll actually able to
    send (whether it *does* send is then decided by _reminder_is_due)."""
    await bot.wait_until_ready()


# ---------------------------------------------------------------------------
# Snark rotation -- a random message from the 200-strong pool every 4 hours,
# aimed at #general (DISCORD_SNARK_CHANNEL_ID). Same persisted-timestamp
# design as the support reminder, and for the same two reasons: a bare
# tasks.loop(hours=4) re-posts on every restart (the first iteration fires
# immediately) AND silently skips beats while the laptop is asleep. Hourly-ish
# polling plus a wall-clock due-check gives real 4-hour spacing through both.

SNARK_INTERVAL = timedelta(hours=4)

#: How many recently-posted message indices to remember (and avoid). 50 out
#: of a 200 pool means a message can't reappear within ~8 days at the 4h
#: cadence, which is what makes the rotation feel random instead of like the
#: same five roasts on shuffle.
_SNARK_RECENT_WINDOW = 50

_SNARK_STATE_PATH = (
    Path(__file__).resolve().parents[2] / "storage" / "data" / "discord_snark.json"
)


def _snark_state() -> dict:
    """``{"last_sent": iso|None, "recent": [int, ...]}``. Unreadable state
    means "post now, remember nothing" -- a corrupt file must not silently
    kill the rotation."""
    try:
        raw = json.loads(_SNARK_STATE_PATH.read_text(encoding="utf-8"))
        recent = [i for i in raw.get("recent", []) if isinstance(i, int)]
        return {"last_sent": raw.get("last_sent"), "recent": recent}
    except Exception:
        return {"last_sent": None, "recent": []}


def _snark_last_sent(state: dict) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(state["last_sent"]) if state["last_sent"] else None
    except Exception:
        return None


def _record_snark_sent(when: datetime, index: int, state: dict) -> None:
    recent = (state.get("recent", []) + [index])[-_SNARK_RECENT_WINDOW:]
    try:
        _SNARK_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SNARK_STATE_PATH.write_text(
            json.dumps({"last_sent": when.isoformat(), "recent": recent}),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"Discord: could not record snark state: {exc}")


def _pick_snark(recent: list[int], rng: random.Random | None = None) -> int:
    """Random index from the pool, excluding recently-used ones. Pure given
    an ``rng``, so the no-repeat behavior is testable. If recent somehow
    covers the whole pool, fall back to fully random rather than never
    posting again."""
    from dashboard.backend.integrations.snark_messages import SNARK_MESSAGES

    rng = rng or random
    candidates = [i for i in range(len(SNARK_MESSAGES)) if i not in set(recent)]
    if not candidates:
        candidates = list(range(len(SNARK_MESSAGES)))
    return rng.choice(candidates)


@tasks.loop(minutes=30)
async def _snark_loop() -> None:
    channel_id = snark_channel_id()
    if channel_id is None:
        return

    state = _snark_state()
    now = datetime.now(timezone.utc)
    last = _snark_last_sent(state)
    if last is not None and (now - last) < SNARK_INTERVAL:
        return

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception as exc:
            print(f"Discord: snark skipped, channel {channel_id} unreachable: {exc}")
            return

    from dashboard.backend.integrations.snark_messages import SNARK_MESSAGES

    index = _pick_snark(state["recent"])
    try:
        await channel.send(SNARK_MESSAGES[index])
    except Exception as exc:
        # No timestamp on failure: the next half-hour poll retries instead of
        # waiting out a full 4h beat.
        print(f"Discord: snark failed to send: {exc}")
        return
    _record_snark_sent(now, index, state)
    print(f"Discord: snark #{index} posted to channel {channel_id}.")


@_snark_loop.before_loop
async def _before_snark() -> None:
    await bot.wait_until_ready()


@bot.event
async def on_ready() -> None:
    if bot.user is not None:
        print(f"Discord bot connected as {bot.user}.")
    print(
        "Reminder: server-channel free chat needs Message Content Intent enabled "
        "in the Discord Developer Portal (Bot → Privileged Gateway Intents). "
        "DMs work without it."
    )


@bot.tree.command(
    name="support",
    description="Ask NewWorldSupport a question about the bot (setup, strategies, paper vs. live).",
)
@app_commands.describe(question="What do you need help with?")
async def support(interaction: discord.Interaction, question: str) -> None:
    # Public, not ephemeral: answers double as a visible FAQ for the channel,
    # and the escalation ping below only reaches the support role if the
    # message is actually visible (a role mention inside an ephemeral reply
    # notifies no one).
    # Curated FAQ first, model second. That ordering is cost control as much
    # as accuracy: the common questions get an exact, reviewed answer AND
    # never spend a request from OpenRouter's daily free quota.
    canned = _support_answer(question)
    if canned is not None:
        await interaction.response.send_message(f"**NewWorldSupport**\n{canned}"[:2000])
        return

    await interaction.response.defer(thinking=True)
    try:
        llm_answer = await support_answer(question)
    except Exception as exc:
        # Includes a 429 once OpenRouter's daily free quota is spent, and a
        # RuntimeError if the configured model isn't a ":free" id. Never
        # retries onto a paid model -- escalating to a human is the correct,
        # free behavior.
        print(f"Discord /support: model answer unavailable ({type(exc).__name__}): {exc}")
        llm_answer = None

    if llm_answer:
        await interaction.edit_original_response(
            content=(
                f"**NewWorldSupport**\n{llm_answer}\n\n"
                "-# AI-generated · not financial advice · ask a mod if this looks wrong"
            )[:2000]
        )
        return

    role_id = support_role_id()
    ping = f"<@&{role_id}> " if role_id else ""
    await interaction.edit_original_response(
        content=(
            f"**NewWorldSupport**\nI couldn't answer that one right now. "
            f"{ping}can someone take a look? In the meantime: #get-the-bot has "
            "setup steps and #announcements has the latest releases."
        )
    )


def main() -> None:
    bot.run(require_env("DISCORD_BOT_TOKEN"))


if __name__ == "__main__":
    main()

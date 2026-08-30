"""Focused tests for the NewWorldSupport Discord bot.

The bot was reduced to a single command (``/support``) — every other command
(/ask, /agent, /strategy, /backtest, /reset, /prompt) and the free-form chat
handler were removed, along with the agent-selection and backtest-watcher
machinery they needed. The tests that pinned those features were removed with
them; what is left covers the surviving surface.

These require the optional ``discord`` dependency; when it is absent the whole
module is skipped so the suite stays green on minimal interpreters. No real
Discord connection or network call is made.
"""

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

discord = pytest.importorskip("discord")

import dashboard.backend.integrations.discord_bot as bot_mod

_BACKEND = Path(__file__).resolve().parents[3]
_REPO_ROOT = _BACKEND.parents[1]


# ---------------------------------------------------------------------------
# Command surface
# ---------------------------------------------------------------------------

def test_support_is_the_only_registered_command():
    """The whole point of the reduction: one command, not seven. A regression
    here means a removed command was reintroduced (or /support was lost)."""
    names = {cmd.name for cmd in bot_mod.bot.tree.get_commands()}
    assert names == {"support"}


def test_removed_commands_are_really_gone():
    """Belt-and-braces against the tree check above: the *functions* backing
    the removed commands must not linger as dead module attributes either."""
    for gone in ("ask", "agent", "strategy", "backtest", "reset", "prompt_cmd"):
        assert not hasattr(bot_mod, gone), f"{gone} should have been removed"


def test_bot_prefix_and_message_content_intent():
    assert bot_mod.bot.command_prefix == "!"
    expected = discord.Intents.default()
    expected.message_content = True
    assert bot_mod.bot.intents.value == expected.value


# ---------------------------------------------------------------------------
# Keyword FAQ — the free, offline tier that runs before the model
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "question, expected_snippet",
    [
        ("Where do I get the bot?", "free"),
        ("how do i make my own strategy", "Testing"),
        ("is this trading real money", "Paper"),
        ("how much can i lose", "risk gate"),
        ("what is the live trading leaderboard", "actually placed"),
    ],
)
def test_faq_matches_common_questions(question, expected_snippet):
    answer = bot_mod._support_answer(question)
    assert answer is not None, f"no FAQ match for {question!r}"
    assert expected_snippet in answer


def test_faq_returns_none_for_an_unknown_question():
    """A miss must return None so the caller falls through to the model —
    never a wrong canned answer."""
    assert bot_mod._support_answer("what is the airspeed velocity of a swallow") is None


def test_faq_is_case_insensitive():
    assert bot_mod._support_answer("WHERE DO I GET the bot") is not None


# ---------------------------------------------------------------------------
# Channel / role resolution
# ---------------------------------------------------------------------------

def test_support_channel_id_none_when_unset(monkeypatch):
    """None disables the 24h reminder loop rather than guessing a channel."""
    monkeypatch.delenv("DISCORD_SUPPORT_CHANNEL_ID", raising=False)
    assert bot_mod.support_channel_id() is None


def test_support_channel_and_role_ids_parse(monkeypatch):
    monkeypatch.setenv("DISCORD_SUPPORT_CHANNEL_ID", "1542357459489001502")
    monkeypatch.setenv("DISCORD_SUPPORT_ROLE_ID", "1542355879737827418")
    assert bot_mod.support_channel_id() == 1542357459489001502
    assert bot_mod.support_role_id() == 1542355879737827418


def test_junk_channel_id_does_not_raise(monkeypatch):
    """A typo'd id must degrade to "unset", not crash the bot at startup."""
    monkeypatch.setenv("DISCORD_SUPPORT_CHANNEL_ID", "not-an-id")
    assert bot_mod.support_channel_id() is None


# ---------------------------------------------------------------------------
# 24h reminder loop
# ---------------------------------------------------------------------------

def test_reminder_spacing_is_24_hours():
    assert bot_mod.SUPPORT_REMINDER_INTERVAL == timedelta(hours=24)


def test_reminder_polls_more_often_than_it_sends():
    """The loop polls hourly; the 24h spacing comes from the due-check against
    a persisted timestamp. A bare tasks.loop(hours=24) would simply not fire
    while the laptop was asleep, silently skipping a day."""
    assert bot_mod._support_reminder_loop.hours == 1
    assert bot_mod.SUPPORT_REMINDER_INTERVAL > timedelta(hours=1)


@pytest.mark.parametrize(
    "age, expected_due",
    [
        (None, True),                      # never sent
        (timedelta(hours=1), False),
        (timedelta(hours=23, minutes=59), False),
        (timedelta(hours=24), True),       # exactly due
        (timedelta(days=3), True),         # missed days -> send once, now
    ],
)
def test_reminder_due_check(age, expected_due):
    now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    last = None if age is None else now - age
    assert bot_mod._reminder_is_due(now, last) is expected_due


def test_reminder_survives_restart_without_reposting(tmp_path, monkeypatch):
    """The reason the timestamp is persisted at all: the loop's first
    iteration fires immediately, so every watchdog restart / laptop reboot
    would otherwise re-post and reset the clock."""
    state = tmp_path / "reminder.json"
    monkeypatch.setattr(bot_mod, "_REMINDER_STATE_PATH", state)

    sent_at = datetime.now(timezone.utc)
    bot_mod._record_reminder_sent(sent_at)
    # Simulates a fresh process reading the state written by the previous one.
    assert bot_mod._reminder_last_sent() is not None
    assert not bot_mod._reminder_is_due(sent_at + timedelta(hours=2), bot_mod._reminder_last_sent())


def test_unreadable_reminder_state_does_not_disable_the_reminder(tmp_path, monkeypatch):
    """Corrupt state must mean "send", not "never send again"."""
    state = tmp_path / "reminder.json"
    state.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(bot_mod, "_REMINDER_STATE_PATH", state)
    assert bot_mod._reminder_last_sent() is None
    assert bot_mod._reminder_is_due(datetime.now(timezone.utc), None) is True


def test_reminder_loop_waits_for_ready_before_first_run():
    """tasks.loop fires immediately, but setup_hook (where it starts) runs
    BEFORE the gateway READY event — without a before_loop the channel cache
    is empty and the first reminder is skipped on every single boot."""
    assert bot_mod._support_reminder_loop._before_loop is not None


# ---------------------------------------------------------------------------
# Snark rotation
# ---------------------------------------------------------------------------

def test_snark_pool_is_exactly_200_unique_nonempty_messages():
    from dashboard.backend.integrations.snark_messages import SNARK_MESSAGES

    assert len(SNARK_MESSAGES) == 200
    assert len(set(SNARK_MESSAGES)) == 200, "duplicate snark messages"
    assert all(m.strip() for m in SNARK_MESSAGES)
    # Discord's hard cap is 2000; ours should read as chat, not essays.
    assert max(len(m) for m in SNARK_MESSAGES) <= 300


def test_snark_pool_never_mentions_anyone():
    """Roast behaviors, never people: no @mentions of any kind may ship in
    the pool. An @everyone in a 4-hour automated loop would be a pager."""
    from dashboard.backend.integrations.snark_messages import SNARK_MESSAGES

    for m in SNARK_MESSAGES:
        assert "@" not in m, f"mention-ish content in: {m!r}"


def test_snark_spacing_is_4_hours_with_subhour_polling():
    assert bot_mod.SNARK_INTERVAL == timedelta(hours=4)
    assert bot_mod._snark_loop.minutes == 30
    assert bot_mod._snark_loop._before_loop is not None


def test_snark_pick_avoids_recent_window():
    import random

    rng = random.Random(7)
    recent: list[int] = []
    for _ in range(300):
        i = bot_mod._pick_snark(recent, rng)
        assert i not in recent[-bot_mod._SNARK_RECENT_WINDOW:]
        recent = (recent + [i])[-bot_mod._SNARK_RECENT_WINDOW:]


def test_snark_pick_survives_recent_covering_whole_pool():
    """If state claims everything is recent, fall back to fully random —
    never deadlock into silence."""
    import random

    i = bot_mod._pick_snark(list(range(200)), random.Random(1))
    assert 0 <= i < 200


def test_snark_state_roundtrip_and_corruption(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    state_file = tmp_path / "snark.json"
    monkeypatch.setattr(bot_mod, "_SNARK_STATE_PATH", state_file)

    now = datetime.now(timezone.utc)
    bot_mod._record_snark_sent(now, 42, {"recent": [1, 2, 3]})
    state = bot_mod._snark_state()
    assert state["recent"][-1] == 42
    assert bot_mod._snark_last_sent(state) is not None

    # Corrupt state must mean "post now", not "never post again".
    state_file.write_text("{broken", encoding="utf-8")
    state = bot_mod._snark_state()
    assert state == {"last_sent": None, "recent": []}


def test_snark_recent_window_is_bounded(tmp_path, monkeypatch):
    """The recent list must not grow without bound across years of 4h posts."""
    from datetime import datetime, timezone

    state_file = tmp_path / "snark.json"
    monkeypatch.setattr(bot_mod, "_SNARK_STATE_PATH", state_file)
    state = {"recent": list(range(500))}
    bot_mod._record_snark_sent(datetime.now(timezone.utc), 999, state)
    assert len(bot_mod._snark_state()["recent"]) == bot_mod._SNARK_RECENT_WINDOW


# ---------------------------------------------------------------------------
# Import safety
# ---------------------------------------------------------------------------

def test_discord_bot_imports_without_secrets():
    """Importing the module must not require DISCORD_BOT_TOKEN et al — only
    main() should. Otherwise the test suite (and any tooling that imports it)
    breaks on a machine with no Discord config."""
    code = (
        "import dashboard.backend.integrations.discord_bot as m; "
        "print(sorted(c.name for c in m.bot.tree.get_commands()))"
    )
    env_clear = (
        "import os; "
        "[os.environ.pop(k, None) for k in "
        "('DISCORD_BOT_TOKEN','DISCORD_GUILD_ID','DISCORD_CHANNEL_ID')]; "
    )
    proc = subprocess.run(
        [sys.executable, "-c", env_clear + code],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "support" in proc.stdout

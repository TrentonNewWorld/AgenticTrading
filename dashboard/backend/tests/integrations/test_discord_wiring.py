"""Source-level wiring checks for the NewWorldSupport Discord bot.

``discord`` is an undeclared optional dep, so importing ``discord_bot`` isn't
possible in the base test env. These checks read the source directly (no
import) so the wiring is locked even where discord is absent — the behavioral
tests live in tests/domain/chat/test_discord_bot.py under
``importorskip('discord')``.

The bot was reduced to a single ``/support`` command; the checks that pinned
/ask, /agent, /strategy and /backtest wiring were removed along with those
features.
"""

import re
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
_DISCORD_BOT = _BACKEND / "integrations" / "discord_bot.py"
_CHAT_SERVICE = _BACKEND / "domain" / "chat" / "service.py"
_REPO_ROOT = _BACKEND.parents[1]


def _source() -> str:
    return _DISCORD_BOT.read_text(encoding="utf-8")


def _chat_source() -> str:
    return _CHAT_SERVICE.read_text(encoding="utf-8")


def test_discord_dependency_is_declared():
    """discord.py is an optional dep pinned in its own requirements file."""
    req = (_REPO_ROOT / "requirements-discord.txt").read_text(encoding="utf-8")
    assert re.search(r"^discord\.py>=", req, re.M)


def test_support_is_the_only_slash_command_in_source():
    src = _source()
    names = re.findall(r'@bot\.tree\.command\(\s*\n\s*name="([^"]+)"', src)
    assert names == ["support"], f"expected only /support, found {names}"


def test_removed_machinery_is_gone_from_source():
    """The agent-selection / backtest-watcher / free-chat code existed only to
    serve the removed commands. Leaving it behind would be ~900 lines of dead
    code that still imports and still looks live to a reader."""
    src = _source()
    for gone in (
        "async def deliver_agent_chat",
        "async def watch_and_deliver_backtest",
        "async def execute_backtest",
        "class AgentSelectView",
        "def should_handle_free_chat",
        "async def on_message",
    ):
        assert gone not in src, f"dead machinery still present: {gone}"


def test_support_tries_faq_before_the_model():
    """Ordering is cost control: the curated FAQ answers the common questions
    for free, keeping OpenRouter's daily free quota for the ones that need it.
    A regression that calls the model first silently burns the quota."""
    src = _source()
    faq_call = src.index("_support_answer(question)")
    model_call = src.index("await support_answer(question)")
    assert faq_call < model_call, "the keyword FAQ must be consulted before the model"


def test_support_model_is_restricted_to_openrouter_free_tier():
    """Two-layer cost control, layer 1: any model id not ending ':free' is
    refused in code rather than called. Layer 2 (a $0 credit limit on the
    OpenRouter key) lives outside the repo and cannot be asserted here."""
    src = _chat_source()
    assert '_SUPPORT_FREE_SUFFIX = ":free"' in src
    assert "endswith(_SUPPORT_FREE_SUFFIX)" in src
    # Every shipped default must itself be a free id.
    block = re.search(r"DEFAULT_SUPPORT_MODELS:.*?\)", src, re.S)
    assert block, "DEFAULT_SUPPORT_MODELS not found"
    for model in re.findall(r'"([^"]+)"', block.group(0)):
        assert model.endswith(":free"), f"default model {model!r} is not a free id"


def test_support_never_falls_back_to_a_paid_model():
    """On a per-model failure the loop tries the NEXT FREE candidate. It must
    not reach for the trading/leaderboard client, which holds paid credits."""
    src = _chat_source()
    start = src.index("async def support_answer")
    # Bound the slice to this function only. Slicing to end-of-file would also
    # sweep in chat_with_agent, which uses the paid client entirely legitimately.
    nxt = re.search(r"\n(?:async )?def ", src[start + 1:])
    support_block = src[start:start + 1 + nxt.start()] if nxt else src[start:]
    assert "get_support_client()" in support_block
    assert "get_claude_client()" not in support_block

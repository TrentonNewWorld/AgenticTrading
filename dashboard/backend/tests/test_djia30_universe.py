"""Dow-30 single-source-of-truth guard (FinSearch↔ATL reconcile 2026-07-10;
copies collapsed 2026-07-11, §PR-C.1).

validator.DJIA_30 is the one canonical Dow-30 for ATL. Every other Dow-ish
list must import it (Python) or mirror it exactly (frontend, enforced
textually below): the backtest scripts, the v2 API contract, the
paper-trading baselines, and the app.js `djia` universe preset. The
Index verified 2026-07-10 (S&P DJI, effective 2026-06-29).
"""
import ast
import re
from pathlib import Path

from dashboard.backend.infrastructure.llm.validator import DJIA_30

_REPO = Path(__file__).resolve().parents[3]          # .../agent-trading-lab
_SCRIPTS = _REPO / "dashboard" / "scripts"
_BHA = _SCRIPTS / "backtest_hourly_agent.py"
_BACKTEST = _SCRIPTS / "backtest.py"
_PAPER = (_REPO / "dashboard" / "backend" / "domain" / "backtesting"
          / "baselines" / "paper.py")

EXPECTED = {
    "AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
    "GOOGL", "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM",
    "MRK", "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "WMT",
}
FORBIDDEN = {"AMEX", "DOW", "INTC", "MA", "PFE", "WBA", "XOM", "VZ", "NFLX", "TSLA"}

# Names under which Dow-ish copies have historically appeared.
_DOW_NAMES = {"DJIA_30", "DJIA_SYMBOLS", "SYMBOLS"}


def _module_dow_literal(path, names=frozenset(_DOW_NAMES)):
    """The set in a top-level `<name> = [...]` list literal for any Dow-ish
    name, or None if the module has no such literal (i.e. it imports or
    derives the constant instead — `SYMBOLS = list(DJIA_30)` is not a
    literal and passes)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        else:
            continue
        for tgt in targets:
            if (isinstance(tgt, ast.Name) and tgt.id in names
                    and isinstance(node.value, (ast.List, ast.Tuple, ast.Set))):
                return {ast.literal_eval(e) for e in node.value.elts}
    return None


def _imports_canonical(path):
    """True if the module has `from ...infrastructure.llm.validator import
    DJIA_30` (or TOP_10_STOCKS)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.ImportFrom) and node.module
                and node.module.endswith("infrastructure.llm.validator")):
            if any(a.name in {"DJIA_30", "TOP_10_STOCKS"} for a in node.names):
                return True
    return False


def test_validator_is_the_current_index():
    assert set(DJIA_30) == EXPECTED
    assert len(DJIA_30) == 30
    assert len(set(DJIA_30)) == 30
    assert set(DJIA_30) & FORBIDDEN == set()


def test_backtest_script_imports_not_hardcodes():
    # No script carries its own Dow list literal — each imports the canonical.
    for path in (_BHA, _BACKTEST):
        assert _module_dow_literal(path) is None, path.name
        assert _imports_canonical(path), path.name


def test_api_universe_tracks_validator():
    from dashboard.backend.api.v2.models import UNIVERSE
    assert set(UNIVERSE) == EXPECTED


def test_top10_is_subset_of_djia30():
    from dashboard.backend.infrastructure.llm.validator import TOP_10_STOCKS
    assert set(TOP_10_STOCKS) <= set(DJIA_30)
    assert len(TOP_10_STOCKS) == 10


def test_paper_baselines_track_canonical():
    from dashboard.backend.domain.backtesting.baselines.paper import DJIA_SYMBOLS
    assert list(DJIA_SYMBOLS) == list(DJIA_30)
    assert _module_dow_literal(_PAPER) is None


_APP_JS = _REPO / "dashboard" / "frontend" / "app.js"


def test_frontend_djia_preset_matches_canonical():
    src = _APP_JS.read_text(encoding="utf-8")
    m = re.search(r"djia:\s*\{[^{}]*?assets:\s*\[([^\]]*)\]", src, re.S)
    assert m, "djia preset not found in ASSET_UNIVERSES in app.js"
    assets = re.findall(r"'([A-Z.]+)'", m.group(1))
    assert len(assets) == len(set(assets)), "duplicate tickers in djia preset"
    assert set(assets) == EXPECTED

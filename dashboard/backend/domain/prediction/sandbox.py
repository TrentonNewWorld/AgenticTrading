"""Executes a prediction-market strategy safely enough to auto-run
unattended. Mirrors domain/crypto/sandbox.py's three-layer security model
and contract shape (AST allowlist, subprocess isolation, restricted
builtins), adapted for a market shape that spans two platforms with
different vocabularies:

    def decide_prediction(as_of, positions, markets, account) -> [order_intent, ...]

``as_of``: "YYYY-MM-DD" -- one call per real calendar day (see
domain/prediction/engine.py's module docstring for why this is forward-only,
never a historical backtest).
``positions``: currently-held outcome positions, each
  {platform, market_id, outcome, side, qty}.
``markets``: currently-active markets across both platforms, each
  {platform: "kalshi"|"polymarket", market_id, title,
   outcomes: [{name, price}], close_time}. ``price`` is 0-1 (a settled
  contract/share pays $1 or $0).
``account``: {cash, equity}.

Returns order intents: {action: "open"|"close", platform, market_id,
outcome, side: "buy"|"sell", qty (positive)}. ``qty`` is a whole number of
contracts (Kalshi) or a share count (Polymarket) -- both platforms trade in
whole units at the price granularity in ``markets``, so this module treats
qty as an integer, matching Kalshi's contract-count convention and rounding
a fractional Polymarket share count down at the fill step (engine.py), not
here.
"""

from __future__ import annotations

import ast
import base64
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

ALLOWED_IMPORTS = {"math", "statistics", "json", "datetime"}

_FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "__import__", "open", "input", "globals", "locals",
    "vars", "getattr", "setattr", "delattr", "exit", "quit", "breakpoint",
    "os", "sys", "subprocess", "socket", "shutil", "pathlib", "importlib",
    "ctypes", "multiprocessing", "threading", "requests", "urllib", "http",
}

DEFAULT_TIMEOUT_SECONDS = 10
_MEMORY_LIMIT_BYTES = 256 * 1024 * 1024

_VALID_ACTIONS = {"open", "close"}
_VALID_SIDES = {"buy", "sell"}
_VALID_PLATFORMS = {"kalshi", "polymarket"}


def _limit_resources() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (_MEMORY_LIMIT_BYTES, _MEMORY_LIMIT_BYTES))


class PredictionStrategyCodeError(ValueError):
    """Raised by validate_code -- the API layer surfaces this as a 400 with
    the specific reasons, so an upload gets rejected loudly, not silently."""


def validate_code(code: str) -> None:
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise PredictionStrategyCodeError(f"syntax error: {exc}") from exc

    issues: List[str] = []
    has_decide_prediction = False

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "decide_prediction":
            has_decide_prediction = True

        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [n.name.split(".")[0] for n in node.names] if isinstance(node, ast.Import) else (
                [node.module.split(".")[0]] if node.module else []
            )
            for name in names:
                if name not in ALLOWED_IMPORTS:
                    issues.append(f"import of '{name}' is not allowed (allowed: {sorted(ALLOWED_IMPORTS)})")

        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            issues.append(f"use of '{node.id}' is not allowed")

        if isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                issues.append(f"access to dunder attribute '{node.attr}' is not allowed")
            elif node.attr in _FORBIDDEN_NAMES:
                issues.append(f"access to '.{node.attr}' is not allowed")

    if not has_decide_prediction:
        issues.append(
            "code must define a top-level function named "
            "decide_prediction(as_of, positions, markets, account)"
        )

    if issues:
        raise PredictionStrategyCodeError("; ".join(sorted(set(issues))))


_RUNNER_SOURCE = '''
import base64
import json
import sys

_SAFE_BUILTINS = {
    "abs": abs, "min": min, "max": max, "sum": sum, "len": len, "range": range,
    "enumerate": enumerate, "zip": zip, "sorted": sorted, "round": round, "next": next,
    "float": float, "int": int, "str": str, "dict": dict, "list": list,
    "tuple": tuple, "set": set, "bool": bool, "True": True, "False": False,
    "None": None, "print": lambda *a, **k: print(*a, **k, file=sys.stderr),
    "ValueError": ValueError, "TypeError": TypeError, "KeyError": KeyError,
    "IndexError": IndexError, "ZeroDivisionError": ZeroDivisionError,
}


def main():
    code = base64.b64decode(sys.stdin.readline()).decode("utf-8")
    payload = json.loads(base64.b64decode(sys.stdin.readline()).decode("utf-8"))
    namespace = {"__builtins__": _SAFE_BUILTINS}
    exec(compile(code, "<prediction_strategy>", "exec"), namespace)
    intents = namespace["decide_prediction"](
        payload["as_of"], payload["positions"], payload["markets"], payload["account"],
    )
    print(json.dumps(intents))


if __name__ == "__main__":
    main()
'''


def _clean_intents(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    cleaned: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).lower()
        side = str(item.get("side", "")).lower()
        platform = str(item.get("platform", "")).lower()
        market_id = item.get("market_id")
        outcome = item.get("outcome")
        try:
            qty = float(item.get("qty"))
        except (TypeError, ValueError):
            continue
        if action not in _VALID_ACTIONS or side not in _VALID_SIDES or platform not in _VALID_PLATFORMS:
            continue
        if not isinstance(market_id, str) or not market_id.strip():
            continue
        if not isinstance(outcome, str) or not outcome.strip():
            continue
        if qty <= 0:
            continue
        cleaned.append({
            "action": action, "platform": platform, "market_id": market_id.strip(),
            "outcome": outcome.strip(), "side": side, "qty": qty,
        })
    return cleaned


def run_decide_prediction(
    code: str,
    *,
    as_of: str,
    positions: List[Dict[str, Any]],
    markets: List[Dict[str, Any]],
    account: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> List[Dict[str, Any]]:
    """Any failure -- timeout, runtime exception, malformed output -- is
    treated as "no orders this cycle" rather than propagated, so a broken or
    hostile upload can only ever cost the cycle's trading, never crash the
    engine or silently execute something unintended."""
    with tempfile.TemporaryDirectory(prefix="prediction_sandbox_") as tmp:
        runner_path = Path(tmp) / "runner.py"
        runner_path.write_text(_RUNNER_SOURCE, encoding="utf-8")
        code_b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
        payload = {"as_of": as_of, "positions": positions, "markets": markets, "account": account}
        payload_b64 = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        stdin_payload = code_b64 + "\n" + payload_b64 + "\n"
        try:
            result = subprocess.run(
                [sys.executable, str(runner_path)],
                input=stdin_payload,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={},
                preexec_fn=_limit_resources if sys.platform != "win32" else None,
            )
        except subprocess.TimeoutExpired:
            print("prediction sandbox: strategy timed out")
            return []

        if result.returncode != 0:
            print(f"prediction sandbox: strategy exited {result.returncode}: {result.stderr[-500:]}")
            return []

        try:
            raw = json.loads(result.stdout.strip().splitlines()[-1]) if result.stdout.strip() else []
        except (json.JSONDecodeError, IndexError):
            print(f"prediction sandbox: strategy produced non-JSON output: {result.stdout[:200]!r}")
            return []

        return _clean_intents(raw)

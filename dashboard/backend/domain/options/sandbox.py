"""Executes uploaded **options** strategy code safely enough to auto-run
unattended.

Sub-phase 5 of the Options-dashboard plan. Clones ``domain/manual10/
sandbox.py``'s three-layer security model verbatim in structure (AST
allowlist -> subprocess isolation with a stripped environment -> restricted
builtins) -- options strategy logic needs no new capability over an equities
one; it is still pure arithmetic over data handed in, never given a broker
client or credentials. Only the **entrypoint and I/O protocol** differ, per
the decision that Options strategies are full contract-level (pick exact
contracts/strikes/expirations themselves) rather than the simplified
weight-based ``decide(price_history) -> {symbol: weight}`` contract every
other dashboard uses -- weights don't express "sell this specific 30-delta
call," so this needed a genuinely different, richer shape:

    def decide_options(as_of, positions, chain, account) -> [order_intent, ...]

``as_of``: "YYYY-MM-DD" -- the current backtest/live day.
``positions``: currently-held legs, each {symbol, underlying, strike,
  expiration, right, qty, leg_role}.
``chain``: {underlying: [{symbol, strike, expiration, right, bid, ask, last,
  open_interest}, ...]} -- the live or historically-reconstructed option
  chain for whatever underlyings the strategy cares about.
``account``: {cash, equity}.

Returns a list of order intents: {action: "open"|"close", symbol, side:
"buy"|"sell", qty (positive int), leg_role: "option"|"stock"|"single"}. A
covered call returns two intents sharing an implicit pairing (both
referencing the same underlying, submitted together) -- the engine groups
them by underlying + cycle, not the strategy author.
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

#: Identical to manual10/sandbox.py's allowlist -- options logic needs no new
#: capability, it is still pure arithmetic over data handed in.
ALLOWED_IMPORTS = {"math", "statistics", "json", "datetime"}

_FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "__import__", "open", "input", "globals", "locals",
    "vars", "getattr", "setattr", "delattr", "exit", "quit", "breakpoint",
    "os", "sys", "subprocess", "socket", "shutil", "pathlib", "importlib",
    "ctypes", "multiprocessing", "threading", "requests", "urllib", "http",
}

DEFAULT_TIMEOUT_SECONDS = 10

#: Same 256MB address-space cap as manual10/sandbox.py, same POSIX-only
#: caveat (Render runs Linux; no equivalent on Windows local dev).
_MEMORY_LIMIT_BYTES = 256 * 1024 * 1024

_VALID_ACTIONS = {"open", "close"}
_VALID_SIDES = {"buy", "sell"}
_VALID_LEG_ROLES = {"option", "stock", "single"}


def _limit_resources() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (_MEMORY_LIMIT_BYTES, _MEMORY_LIMIT_BYTES))


class OptionsStrategyCodeError(ValueError):
    """Raised by validate_code -- the API layer surfaces this as a 400 with
    the specific reasons, so an upload gets rejected loudly, not silently."""


def validate_code(code: str) -> None:
    """Raise OptionsStrategyCodeError with every violation found, or return
    normally if the code is safe to move on to the LLM review + sandboxed
    execution. Never executes anything -- pure AST inspection. Identical to
    manual10/sandbox.py's validate_code except for the required entrypoint
    name (decide_options, not decide)."""
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise OptionsStrategyCodeError(f"syntax error: {exc}") from exc

    issues: List[str] = []
    has_decide_options = False

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "decide_options":
            has_decide_options = True

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

    if not has_decide_options:
        issues.append(
            "code must define a top-level function named "
            "decide_options(as_of, positions, chain, account)"
        )

    if issues:
        raise OptionsStrategyCodeError("; ".join(sorted(set(issues))))


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
    # Base64 -- avoids any escaping ambiguity between the uploaded code's own
    # newlines/quotes and this line-oriented protocol.
    code = base64.b64decode(sys.stdin.readline()).decode("utf-8")
    payload = json.loads(base64.b64decode(sys.stdin.readline()).decode("utf-8"))
    namespace = {"__builtins__": _SAFE_BUILTINS}
    exec(compile(code, "<uploaded_options_strategy>", "exec"), namespace)
    intents = namespace["decide_options"](
        payload["as_of"], payload["positions"], payload["chain"], payload["account"],
    )
    print(json.dumps(intents))


if __name__ == "__main__":
    main()
'''


def _clean_intents(raw: Any) -> List[Dict[str, Any]]:
    """Validate and normalize the sandbox's raw output. Anything malformed is
    dropped, not raised -- a broken or hostile upload's bad entries cost
    only themselves, never abort the whole cycle's otherwise-valid intents."""
    if not isinstance(raw, list):
        return []
    cleaned: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).lower()
        side = str(item.get("side", "")).lower()
        symbol = item.get("symbol")
        leg_role = str(item.get("leg_role", "single")).lower()
        try:
            qty = int(item.get("qty"))
        except (TypeError, ValueError):
            continue
        if action not in _VALID_ACTIONS or side not in _VALID_SIDES:
            continue
        if not isinstance(symbol, str) or not symbol.strip():
            continue
        if qty <= 0:
            continue
        if leg_role not in _VALID_LEG_ROLES:
            leg_role = "single"
        cleaned.append({
            "action": action, "symbol": symbol.strip().upper(),
            "side": side, "qty": qty, "leg_role": leg_role,
        })
    return cleaned


def run_decide_options(
    code: str,
    *,
    as_of: str,
    positions: List[Dict[str, Any]],
    chain: Dict[str, List[Dict[str, Any]]],
    account: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> List[Dict[str, Any]]:
    """Run this (already ``validate_code``-checked) strategy's
    decide_options() in an isolated subprocess and return its cleaned order
    intents. Any failure -- a timeout, a runtime exception inside the
    sandbox, malformed output -- is treated as "no orders this cycle" rather
    than propagated, so a broken or hostile upload can only ever cost the
    cycle's trading, never crash the engine or silently execute something
    unintended."""
    with tempfile.TemporaryDirectory(prefix="options_sandbox_") as tmp:
        runner_path = Path(tmp) / "runner.py"
        runner_path.write_text(_RUNNER_SOURCE, encoding="utf-8")
        code_b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
        payload = {"as_of": as_of, "positions": positions, "chain": chain, "account": account}
        payload_b64 = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        stdin_payload = code_b64 + "\n" + payload_b64 + "\n"
        try:
            result = subprocess.run(
                [sys.executable, str(runner_path)],
                input=stdin_payload,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={},  # no ALPACA_*/ANTHROPIC_*/PATH/anything -- a fully stripped child environment
                preexec_fn=_limit_resources if sys.platform != "win32" else None,
            )
        except subprocess.TimeoutExpired:
            print("options sandbox: strategy timed out")
            return []

        if result.returncode != 0:
            print(f"options sandbox: strategy exited {result.returncode}: {result.stderr[-500:]}")
            return []

        try:
            raw = json.loads(result.stdout.strip().splitlines()[-1]) if result.stdout.strip() else []
        except (json.JSONDecodeError, IndexError):
            print(f"options sandbox: strategy produced non-JSON output: {result.stdout[:200]!r}")
            return []

        return _clean_intents(raw)

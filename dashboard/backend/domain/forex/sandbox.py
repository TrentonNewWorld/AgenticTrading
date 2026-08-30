"""Executes uploaded **forex** strategy code safely enough to auto-run
unattended. Mirrors domain/futures/sandbox.py's three-layer security model
and contract shape exactly (single-instrument, no legs) -- same reasoning:
strategy logic is pure arithmetic over data handed in, never given a broker
client or credentials.

    def decide_forex(as_of, positions, quotes, account) -> [order_intent, ...]

``as_of``: "YYYY-MM-DD".
``positions``: currently-held forex positions, each {symbol, qty}.
``quotes``: {symbol: {price, prev_close}} for the USD-quote pair universe
  (see infrastructure/market_data/yahoo_forex.py's docstring for why the
  universe is restricted that way).
``account``: {cash, equity}.

Returns order intents: {action: "open"|"close", symbol, side: "buy"|"sell",
qty (positive int)}.
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


def _limit_resources() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (_MEMORY_LIMIT_BYTES, _MEMORY_LIMIT_BYTES))


class ForexStrategyCodeError(ValueError):
    """Raised by validate_code -- the API layer surfaces this as a 400 with
    the specific reasons, so an upload gets rejected loudly, not silently."""


def validate_code(code: str) -> None:
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise ForexStrategyCodeError(f"syntax error: {exc}") from exc

    issues: List[str] = []
    has_decide_forex = False

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "decide_forex":
            has_decide_forex = True

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

    if not has_decide_forex:
        issues.append(
            "code must define a top-level function named "
            "decide_forex(as_of, positions, quotes, account)"
        )

    if issues:
        raise ForexStrategyCodeError("; ".join(sorted(set(issues))))


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
    exec(compile(code, "<uploaded_forex_strategy>", "exec"), namespace)
    intents = namespace["decide_forex"](
        payload["as_of"], payload["positions"], payload["quotes"], payload["account"],
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
        symbol = item.get("symbol")
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
        cleaned.append({"action": action, "symbol": symbol.strip().upper(), "side": side, "qty": qty})
    return cleaned


def run_decide_forex(
    code: str,
    *,
    as_of: str,
    positions: List[Dict[str, Any]],
    quotes: Dict[str, Dict[str, Any]],
    account: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> List[Dict[str, Any]]:
    """Any failure -- timeout, runtime exception, malformed output -- is
    treated as "no orders this cycle" rather than propagated, so a broken or
    hostile upload can only ever cost the cycle's trading, never crash the
    engine or silently execute something unintended."""
    with tempfile.TemporaryDirectory(prefix="forex_sandbox_") as tmp:
        runner_path = Path(tmp) / "runner.py"
        runner_path.write_text(_RUNNER_SOURCE, encoding="utf-8")
        code_b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
        payload = {"as_of": as_of, "positions": positions, "quotes": quotes, "account": account}
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
            print("forex sandbox: strategy timed out")
            return []

        if result.returncode != 0:
            print(f"forex sandbox: strategy exited {result.returncode}: {result.stderr[-500:]}")
            return []

        try:
            raw = json.loads(result.stdout.strip().splitlines()[-1]) if result.stdout.strip() else []
        except (json.JSONDecodeError, IndexError):
            print(f"forex sandbox: strategy produced non-JSON output: {result.stdout[:200]!r}")
            return []

        return _clean_intents(raw)

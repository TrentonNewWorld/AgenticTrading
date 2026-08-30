"""Executes uploaded strategy code safely enough to auto-run unattended.

This is the actual security boundary for uploaded strategies -- not the LLM
risk review in ``uploads.py``, which is a heuristic on top of this, not a
substitute for it. Three independent layers, all required:

1. **Static analysis** (``validate_code``): an AST walk that rejects the code
   outright -- before it is ever executed, stored, or shown to the LLM
   reviewer -- if it imports anything outside a tiny allowlist, calls
   anything that can reach the filesystem/network/process/interpreter
   internals, or touches a dunder attribute (the classic
   ``().__class__.__bases__[0].__subclasses__()``-style sandbox escape).
2. **Process isolation** (``run_decide``): even code that passed static
   analysis runs in a brand-new Python subprocess, not `exec()`'d in this
   process -- with its environment stripped of every credential
   (ALPACA_*/ANTHROPIC_*/etc), a hard wall-clock timeout, and no access to
   any broker client or API key. It receives plain JSON price history on
   stdin and must print plain JSON weights on stdout; it never sees a
   network socket, a file handle, or this process's memory.
3. **Restricted builtins** inside that subprocess (``_RUNNER_SOURCE``): even
   if layer 1 missed something, the exec() namespace the uploaded code runs
   in only exposes a small set of harmless builtins -- no `open`, `eval`,
   `exec`, `compile`, `__import__`, `input`, `getattr`/`setattr`/`delattr`.

An uploaded strategy's ``decide()`` gets *only* the data an ordinary
BaselineStrategy.decide() gets (price history), and returns *only*
``{symbol: weight}`` -- the same contract every built-in strategy uses. It
never receives a broker client, so even a strategy that fully escaped every
layer above still cannot place an order directly; only engine.py's own
trusted code (see ``uploads.py::tick_uploaded_strategy``) ever calls
``submit_market_order``.
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

#: Modules an uploaded strategy may import. Nothing here can touch the
#: filesystem, network, process table, or interpreter internals.
ALLOWED_IMPORTS = {"math", "statistics", "json", "datetime"}

#: Names that must never appear as a Call target, an attribute, or an import
#: -- reaching the OS, the network, the interpreter, or another process by any
#: route.
_FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "__import__", "open", "input", "globals", "locals",
    "vars", "getattr", "setattr", "delattr", "exit", "quit", "breakpoint",
    "os", "sys", "subprocess", "socket", "shutil", "pathlib", "importlib",
    "ctypes", "multiprocessing", "threading", "requests", "urllib", "http",
}

DEFAULT_TIMEOUT_SECONDS = 10

#: 256MB address-space cap in the sandboxed subprocess -- POSIX only (Render
#: runs Linux); Windows has no equivalent to `resource.setrlimit`, so the
#: local-dev fallback is the wall-clock timeout alone. Defense-in-depth
#: against a resource-exhaustion upload, not the primary security boundary
#: (that's the AST allowlist + stripped environment above).
_MEMORY_LIMIT_BYTES = 256 * 1024 * 1024


def _limit_resources() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (_MEMORY_LIMIT_BYTES, _MEMORY_LIMIT_BYTES))


class StrategyCodeError(ValueError):
    """Raised by validate_code -- the API layer surfaces this as a 400 with
    the specific reasons, so an upload gets rejected loudly, not silently."""


def validate_code(code: str) -> None:
    """Raise StrategyCodeError with every violation found, or return
    normally if the code is safe to move on to the LLM review + sandboxed
    execution. Never executes anything -- pure AST inspection."""
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise StrategyCodeError(f"syntax error: {exc}") from exc

    issues: List[str] = []
    has_decide = False

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "decide":
            has_decide = True

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

    if not has_decide:
        issues.append("code must define a top-level function named decide(price_history)")

    if issues:
        raise StrategyCodeError("; ".join(sorted(set(issues))))


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
    # Both lines are base64 -- avoids any escaping ambiguity between the
    # uploaded code's own newlines/quotes and this line-oriented protocol.
    code = base64.b64decode(sys.stdin.readline()).decode("utf-8")
    price_history = json.loads(base64.b64decode(sys.stdin.readline()).decode("utf-8"))
    namespace = {"__builtins__": _SAFE_BUILTINS}
    exec(compile(code, "<uploaded_strategy>", "exec"), namespace)
    weights = namespace["decide"](price_history)
    print(json.dumps(weights))


if __name__ == "__main__":
    main()
'''


def run_decide(
    code: str, price_history: Dict[str, Any], *, timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Dict[str, float]:
    """Run this (already ``validate_code``-checked) strategy's decide() in an
    isolated subprocess and return its {symbol: weight} result. Any failure
    -- a timeout, a runtime exception inside the sandbox, malformed output --
    is treated as "no positions this cycle" rather than propagated, so a
    broken or hostile upload can only ever cost the day's trading, never
    crash the scheduler or silently execute something unintended."""
    with tempfile.TemporaryDirectory(prefix="manual10_sandbox_") as tmp:
        runner_path = Path(tmp) / "runner.py"
        runner_path.write_text(_RUNNER_SOURCE, encoding="utf-8")
        code_b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
        history_b64 = base64.b64encode(json.dumps(price_history).encode("utf-8")).decode("ascii")
        stdin_payload = code_b64 + "\n" + history_b64 + "\n"
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
            print("manual10 sandbox: strategy timed out")
            return {}

        if result.returncode != 0:
            print(f"manual10 sandbox: strategy exited {result.returncode}: {result.stderr[-500:]}")
            return {}

        try:
            weights = json.loads(result.stdout.strip().splitlines()[-1]) if result.stdout.strip() else {}
        except (json.JSONDecodeError, IndexError):
            print(f"manual10 sandbox: strategy produced non-JSON output: {result.stdout[:200]!r}")
            return {}

        if not isinstance(weights, dict):
            return {}
        cleaned: Dict[str, float] = {}
        for symbol, weight in weights.items():
            try:
                w = float(weight)
            except (TypeError, ValueError):
                continue
            if isinstance(symbol, str) and w > 0:
                cleaned[symbol.upper()] = w
        return cleaned

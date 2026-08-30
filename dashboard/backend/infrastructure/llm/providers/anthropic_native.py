"""Native Anthropic API integration (direct, no multi-provider gateway)."""

from __future__ import annotations

import os
from typing import Any, Optional

INTEGRATION_ID = "anthropic"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def default_model_name() -> str:
    return DEFAULT_MODEL


def make_client(anthropic_cls: Any, *, api_key: Optional[str] = None) -> Optional[Any]:
    """Build a native Anthropic client, or ``None`` when the key is missing.

    ``api_key``, when given, is a signed-in user's Connections-saved key and
    takes priority over ``ANTHROPIC_API_KEY`` -- see
    infrastructure/llm/providers/__init__.py's ``make_llm_client``."""
    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        return anthropic_cls(api_key=key)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"⚠️  Failed to init Anthropic client: {exc}")
        return None

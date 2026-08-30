"""Parallel LLM gateway integrations for the trading / leaderboard pipeline.

Each integration is a sibling module exposing the same surface:

* ``INTEGRATION_ID`` — config value for ``leaderboard.json`` ``integration``
* ``DEFAULT_MODEL`` / ``default_model_name()``
* ``make_client(anthropic_cls)`` → Anthropic-compatible client or ``None``

Callers pick a gateway via ``make_llm_client(integration=...)`` (explicit) or
omit it for legacy env auto-detect (CommonStack key → CommonStack, else native
Anthropic). OpenRouter is never auto-selected — set ``integration: "openrouter"``
on the leaderboard entry (or pass the kwarg).
"""

from __future__ import annotations

import os
from typing import Any, Optional

from . import anthropic_native, commonstack, openrouter

# Optional Anthropic SDK — kept here so provider modules stay free of the
# optional-import side effects / print noise from the harness.
try:
    from anthropic import Anthropic as _Anthropic

    HAS_ANTHROPIC = True
except ImportError:  # pragma: no cover - exercised when SDK missing
    _Anthropic = None
    HAS_ANTHROPIC = False

PROVIDERS = {
    commonstack.INTEGRATION_ID: commonstack,
    openrouter.INTEGRATION_ID: openrouter,
    anthropic_native.INTEGRATION_ID: anthropic_native,
}

KNOWN_INTEGRATIONS = tuple(PROVIDERS.keys())


class LLMProviderConfigurationError(RuntimeError):
    """Raised when an explicitly requested provider client is unavailable."""


# integration id -> the Connections-page provider that stores its key.
_CONNECTIONS_PROVIDER_FOR_INTEGRATION = {
    commonstack.INTEGRATION_ID: "commonstack",
    anthropic_native.INTEGRATION_ID: "anthropic_api",
    openrouter.INTEGRATION_ID: "openrouter",
}


def _connections_api_key(user_id: Optional[int], integration: str) -> Optional[str]:
    """A signed-in user's Connections-saved key for this integration, if any.

    Mirrors infrastructure/brokers/credentials.py's resolve_alpaca_credentials:
    ``user_id=None`` (unattended/background context, e.g. leaderboard
    auto-compute) always returns ``None`` and callers fall through to their
    existing env-var lookup -- this never changes behavior for callers that
    don't pass a user_id.
    """
    if user_id is None:
        return None
    provider = _CONNECTIONS_PROVIDER_FOR_INTEGRATION.get(integration)
    if provider is None:
        return None
    from dashboard.backend.domain.connections.repository import connection_store

    creds = connection_store.get_credentials(int(user_id), provider)
    return (creds or {}).get("api_key")


def resolve_integration(
    integration: Optional[str] = None, *, user_id: Optional[int] = None
) -> str:
    """Normalize an integration id, or auto-detect from env when omitted.

    Explicit values must be one of ``KNOWN_INTEGRATIONS``. When ``None``/empty,
    prefer CommonStack if ``COMMONSTACK_API_KEY`` is set (or, for a signed-in
    ``user_id``, if they've saved a CommonStack key on Connections), otherwise
    native Anthropic — matching the pre-OpenRouter gateway preference.
    OpenRouter is opt-in only (via config / kwarg).
    """
    if integration is not None and str(integration).strip():
        key = str(integration).strip().lower()
        if key not in PROVIDERS:
            raise ValueError(
                f"Unknown LLM integration {integration!r}. "
                f"Expected one of: {', '.join(KNOWN_INTEGRATIONS)}"
            )
        return key
    if os.getenv("COMMONSTACK_API_KEY") or _connections_api_key(user_id, commonstack.INTEGRATION_ID):
        return commonstack.INTEGRATION_ID
    return anthropic_native.INTEGRATION_ID


def make_llm_client(
    integration: Optional[str] = None,
    *,
    reasoning_effort: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Optional[Any]:
    """Create an Anthropic-compatible client for the chosen integration.

    Returns ``None`` when the SDK is missing or the integration's API key is
    unset / client init fails, so callers fall back to rule-based trading.

    ``user_id``, when given, prefers that signed-in user's Connections-saved
    key over the provider's env var -- only meaningful for request-scoped
    callers (e.g. Strategy Catalog's Run in Paper/Live, chat/agent
    endpoints). Leaderboard auto-compute and other unattended callers omit
    it and get exactly today's env-var-only behavior.
    """
    if not HAS_ANTHROPIC or _Anthropic is None:
        return None
    resolved = resolve_integration(integration, user_id=user_id)
    api_key = _connections_api_key(user_id, resolved)
    if resolved == openrouter.INTEGRATION_ID:
        return openrouter.make_client(
            _Anthropic,
            reasoning_effort=reasoning_effort,
            api_key=api_key,
        )
    return PROVIDERS[resolved].make_client(_Anthropic, api_key=api_key)


_ENV_VAR_FOR_INTEGRATION = {
    commonstack.INTEGRATION_ID: "COMMONSTACK_API_KEY",
    anthropic_native.INTEGRATION_ID: "ANTHROPIC_API_KEY",
    openrouter.INTEGRATION_ID: "OPENROUTER_API_KEY",
}


def connections_env_override(user_id: Optional[int]) -> dict:
    """Env-var overrides so a *subprocess* uses a signed-in user's own
    Connections-saved LLM key instead of the platform's env var.

    Every in-process caller can just pass ``user_id`` straight through to
    ``make_llm_client``. A dashboard backtest runs as a subprocess instead
    (see api/routers/backtests.py), which reads its LLM key from its own
    inherited environment and has no way to receive a Python kwarg -- this
    is the equivalent for that boundary. Apply the result to a *copy* of
    ``os.environ`` passed via ``subprocess.run(..., env=...)``, never to the
    real process environment, so it never leaks into any other request.

    Empty when ``user_id`` is ``None`` or the user has no Connections key
    saved -- the subprocess then falls back to its inherited env exactly as
    before.
    """
    if user_id is None:
        return {}
    integration = resolve_integration(user_id=user_id)
    api_key = _connections_api_key(user_id, integration)
    if not api_key:
        return {}
    env_var = _ENV_VAR_FOR_INTEGRATION.get(integration)
    if not env_var:
        return {}
    return {env_var: api_key}


def default_model_name(integration: Optional[str] = None) -> str:
    """Default model slug for the resolved integration."""
    resolved = resolve_integration(integration)
    return PROVIDERS[resolved].default_model_name()


def ensure_llm_client_available(integration: Optional[str] = None) -> Any:
    """Construct a client without making a network request, or fail safely."""
    if not HAS_ANTHROPIC or _Anthropic is None:
        raise LLMProviderConfigurationError(
            "LLM provider client is unavailable because the Anthropic SDK is missing"
        )
    resolved = resolve_integration(integration)
    client = make_llm_client(resolved)
    if client is None:
        raise LLMProviderConfigurationError(
            f"LLM provider client is unavailable for integration {resolved!r}"
        )
    return client


__all__ = [
    "HAS_ANTHROPIC",
    "KNOWN_INTEGRATIONS",
    "LLMProviderConfigurationError",
    "PROVIDERS",
    "anthropic_native",
    "commonstack",
    "connections_env_override",
    "default_model_name",
    "ensure_llm_client_available",
    "make_llm_client",
    "openrouter",
    "resolve_integration",
]

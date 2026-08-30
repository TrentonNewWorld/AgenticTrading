"""Generic per-user API connection storage (Alpaca live/paper, LLM providers).

Robinhood is intentionally NOT here: it already has its own OAuth-token store
(``domain.brokers.repository``) and its own router (``api/routers/robinhood_live.py``).
This module covers the providers that authenticate with a plain key/secret/token
the user types in: alpaca_live, alpaca_paper, anthropic_subscription,
anthropic_api, openrouter, local -- plus the paper-wallet funding mode setting.
"""

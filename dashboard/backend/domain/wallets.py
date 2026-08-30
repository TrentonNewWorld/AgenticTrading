"""Real-broker wallet resolution, shared by every dashboard's Manual page
and the Home page's combined portfolio total.

The one thing this module exists to get right: Stocks, Options, and Crypto
all trade through the *same* Alpaca paper account (one key pair, resolved
via infrastructure/brokers/credentials.py) -- so summing "Stocks wallet +
Options wallet + Crypto wallet" for a portfolio total would count that one
account's money three times. Every wallet slot below carries a ``dedup_key``;
callers building a total must group by it and count each key once, not once
per asset class. ``shared_with`` is the human-readable side of the same
fact, for per-dashboard labeling ("this is the same account as Options and
Crypto").

Credential resolution intentionally does NOT reinvent anything: Alpaca goes
through infrastructure/brokers/credentials.py exactly like every other
Alpaca call site; Tradovate/OANDA reuse the exact "single shared connection,
no per-user ownership" resolvers already built in domain/futures/engine.py
and domain/forex/engine.py for real order execution (those dashboards have
no per-strategy owner at all, so a wallet display keyed to "whichever user
is looking at the page" would be fiction -- there is one shared account);
Kalshi/Polymarket reuse domain/prediction/engine.py's per-user resolvers,
since Prediction strategies (and therefore its wallets) are genuinely
user-owned.

Every network call here is best-effort: a broker being unreachable or a
balance-read endpoint failing must never break the page it's rendered on --
callers get ``connected=True, balance=None, error="..."`` instead of an
exception.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class WalletSnapshot:
    broker: str  # "alpaca_paper" | "alpaca_live" | "tradovate" | "oanda" | "kalshi" | "polymarket"
    label: str
    connected: bool
    balance: Optional[float]
    balance_label: str  # what the number actually represents ("Portfolio value", "Cash balance", "Balance")
    dedup_key: str
    purpose: str
    shared_with: List[str] = field(default_factory=list)  # other asset classes on this same account
    error: Optional[str] = None
    currency: str = "USD"


def _not_connected(broker: str, label: str, dedup_key: str, purpose: str, note: Optional[str] = None) -> WalletSnapshot:
    return WalletSnapshot(
        broker=broker, label=label, connected=False, balance=None,
        balance_label="", dedup_key=dedup_key, purpose=purpose, error=note,
    )


def _alpaca_wallet(*, provider: str, label: str, purpose: str, user_id: Optional[int]) -> WalletSnapshot:
    from dashboard.backend.infrastructure.brokers.alpaca_paper import AlpacaPaperTradingClient
    from dashboard.backend.infrastructure.brokers.alpaca_live import AlpacaLiveCredentialsError, AlpacaLiveTradingClient
    from dashboard.backend.infrastructure.brokers.alpaca_ira import AlpacaIRACredentialsError, AlpacaIRATradingClient

    try:
        if provider == "alpaca_paper":
            client = AlpacaPaperTradingClient(user_id=user_id)
        elif provider == "alpaca_ira":
            client = AlpacaIRATradingClient(user_id=user_id)
        else:
            client = AlpacaLiveTradingClient(user_id=user_id)
    except (FileNotFoundError, AlpacaLiveCredentialsError, AlpacaIRACredentialsError) as exc:
        return _not_connected(provider, label, provider, purpose, str(exc))
    except Exception as exc:
        return _not_connected(provider, label, provider, purpose, str(exc))

    try:
        account = client.get_account()
    except Exception as exc:
        return WalletSnapshot(
            broker=provider, label=label, connected=True, balance=None, balance_label="",
            dedup_key=provider, purpose=purpose, error=f"balance lookup failed: {exc}",
        )
    if account is None:
        return _not_connected(provider, label, provider, purpose, "account not configured")
    return WalletSnapshot(
        broker=provider, label=label, connected=True,
        balance=float(account["portfolio_value"]), balance_label="Portfolio value",
        dedup_key=provider, purpose=purpose,
    )


def _tradovate_wallet(user_id: Optional[int]) -> WalletSnapshot:
    from dashboard.backend.domain.futures.engine import _tradovate_credentials
    from dashboard.backend.infrastructure.brokers.tradovate_paper import TradovateOrderError, TradovatePaperClient

    creds = _tradovate_credentials()
    if creds is None:
        return _not_connected("tradovate", "Tradovate", "tradovate", "Futures demo-exchange fills")
    try:
        client = TradovatePaperClient(credentials=creds)
        snapshot = client.get_cash_balance()
    except TradovateOrderError as exc:
        return WalletSnapshot(
            broker="tradovate", label="Tradovate", connected=True, balance=None, balance_label="",
            dedup_key="tradovate", purpose="Futures demo-exchange fills", error=str(exc),
        )
    # getcashbalancesnapshot returns a list of per-currency balance rows;
    # a demo account is USD-only in practice, so sum defensively rather
    # than assume exactly one row.
    rows = snapshot if isinstance(snapshot, list) else [snapshot]
    total = sum(float(r.get("amount", 0) or 0) for r in rows)
    return WalletSnapshot(
        broker="tradovate", label="Tradovate", connected=True, balance=total,
        balance_label="Cash balance (Tradovate's API has no combined net-liq field)",
        dedup_key="tradovate", purpose="Futures demo-exchange fills",
    )


def _oanda_wallet(user_id: Optional[int]) -> WalletSnapshot:
    from dashboard.backend.domain.forex.engine import _oanda_credentials
    from dashboard.backend.infrastructure.brokers.oanda_practice import OandaOrderError, OandaPracticeClient

    creds = _oanda_credentials()
    if creds is None:
        return _not_connected("oanda", "OANDA", "oanda", "Forex demo-exchange fills")
    try:
        client = OandaPracticeClient(credentials=creds)
        account = client.get_account_summary()
    except OandaOrderError as exc:
        return WalletSnapshot(
            broker="oanda", label="OANDA", connected=True, balance=None, balance_label="",
            dedup_key="oanda", purpose="Forex demo-exchange fills", error=str(exc),
        )
    return WalletSnapshot(
        broker="oanda", label="OANDA", connected=True, balance=float(account["NAV"]),
        balance_label="Net asset value", dedup_key="oanda", purpose="Forex demo-exchange fills",
    )


def _kalshi_credentials(user_id: Optional[int]):
    if user_id is None:
        return None
    from dashboard.backend.domain.connections.repository import connection_store

    return connection_store.get_credentials(int(user_id), "kalshi")


def _kalshi_wallet(user_id: Optional[int]) -> WalletSnapshot:
    from dashboard.backend.infrastructure.brokers.kalshi_paper import KalshiClient, KalshiCredentials, KalshiOrderError

    saved = _kalshi_credentials(user_id)
    if not saved:
        return _not_connected("kalshi", "Kalshi", f"kalshi:{user_id}", "Prediction demo-exchange fills")
    try:
        client = KalshiClient(
            credentials=KalshiCredentials(api_key_id=saved["api_key"], private_key_pem=saved["secret_key"]),
            environment="demo",
        )
        balance = client.get_balance()
    except (KalshiOrderError, KeyError) as exc:
        return WalletSnapshot(
            broker="kalshi", label="Kalshi", connected=True, balance=None, balance_label="",
            dedup_key=f"kalshi:{user_id}", purpose="Prediction demo-exchange fills", error=str(exc),
        )
    return WalletSnapshot(
        broker="kalshi", label="Kalshi", connected=True,
        balance=balance["balance"] / 100.0, balance_label="Balance",
        dedup_key=f"kalshi:{user_id}", purpose="Prediction demo-exchange fills",
    )


def _polymarket_wallet(user_id: Optional[int]) -> WalletSnapshot:
    """No balance-read endpoint exists yet -- a Polymarket balance is USDC
    held on-chain on Polygon, not a value the CLOB REST API returns
    directly, and building that (an RPC call against the USDC contract, or
    py-clob-client's allowance/balance helper) hasn't been done. Rather than
    silently show $0 or omit the card, this is honest about the gap."""
    from dashboard.backend.domain.connections.repository import connection_store

    saved = connection_store.get_credentials(int(user_id), "polymarket") if user_id is not None else None
    if not saved:
        return _not_connected("polymarket", "Polymarket", f"polymarket:{user_id}", "Prediction real-money trading")
    return WalletSnapshot(
        broker="polymarket", label="Polymarket", connected=True, balance=None, balance_label="",
        dedup_key=f"polymarket:{user_id}", purpose="Prediction real-money trading",
        error="Balance display not built yet -- Polymarket balances are on-chain USDC, not a REST field",
    )


ASSET_CLASS_LABELS = {
    "stocks": "Stocks", "options": "Options", "futures": "Futures",
    "forex": "Forex", "crypto": "Crypto", "prediction": "Prediction",
}

# asset_class -> which wallet slot(s) power its Manual page, in display order.
# Each slot is (dedup_key(user_id) -> str, build(user_id) -> WalletSnapshot):
# dedup_key is a pure, network-free function -- computing "who shares this
# account" must never itself cost a broker API call, only rendering the
# actual balance does.
_ASSET_CLASS_WALLET_SLOTS: Dict[str, List[Any]] = {
    "stocks": [
        (
            lambda uid: "alpaca_paper",
            lambda uid: _alpaca_wallet(
                provider="alpaca_paper", label="Alpaca Paper Account", user_id=uid,
                purpose="Manual, My Agents backtests/paper trading",
            ),
        ),
        (
            lambda uid: "alpaca_live",
            lambda uid: _alpaca_wallet(
                provider="alpaca_live", label="Alpaca Live Account", user_id=uid,
                purpose="Manual's Top 10 promote-to-real-money, Live Trading Leaderboard",
            ),
        ),
        (
            lambda uid: "alpaca_ira",
            lambda uid: _alpaca_wallet(
                provider="alpaca_ira", label="Alpaca IRA Account", user_id=uid,
                purpose="Retirement funds -- balance visibility only, not yet tradeable",
            ),
        ),
    ],
    "options": [(
        lambda uid: "alpaca_paper",
        lambda uid: _alpaca_wallet(
            provider="alpaca_paper", label="Alpaca Paper Account", user_id=uid,
            purpose="Options Manual paper trading",
        ),
    )],
    "crypto": [(
        lambda uid: "alpaca_paper",
        lambda uid: _alpaca_wallet(
            provider="alpaca_paper", label="Alpaca Paper Account", user_id=uid,
            purpose="Crypto Manual paper trading",
        ),
    )],
    "futures": [(lambda uid: "tradovate", lambda uid: _tradovate_wallet(uid))],
    "forex": [(lambda uid: "oanda", lambda uid: _oanda_wallet(uid))],
    "prediction": [
        (lambda uid: f"kalshi:{uid}", lambda uid: _kalshi_wallet(uid)),
        (lambda uid: f"polymarket:{uid}", lambda uid: _polymarket_wallet(uid)),
    ],
}


def _dedup_key_to_asset_classes(user_id: Optional[int]) -> Dict[str, List[str]]:
    """Which asset classes share each dedup_key -- pure/network-free."""
    owners: Dict[str, List[str]] = {}
    for asset_class, slots in _ASSET_CLASS_WALLET_SLOTS.items():
        for dedup_key_fn, _build in slots:
            key = dedup_key_fn(user_id)
            owners.setdefault(key, [])
            if asset_class not in owners[key]:
                owners[key].append(asset_class)
    return owners


def get_dashboard_wallets(asset_class: str, user_id: Optional[int]) -> List[Dict[str, Any]]:
    """Every wallet slot for one dashboard's Manual page, each labeled with
    which other dashboards share it (if any)."""
    slots = _ASSET_CLASS_WALLET_SLOTS.get(asset_class, [])
    owners = _dedup_key_to_asset_classes(user_id)
    out = []
    for dedup_key_fn, build in slots:
        snap = build(user_id)
        others = [ASSET_CLASS_LABELS[a] for a in owners.get(dedup_key_fn(user_id), []) if a != asset_class]
        snap.shared_with = others
        out.append(snap.__dict__)
    return out


def get_portfolio_summary(user_id: Optional[int]) -> Dict[str, Any]:
    """Home page's combined real-money total: one row per distinct broker
    account (deduped by dedup_key across ALL six dashboards), each row
    listing every asset class it powers, summed once each -- never once per
    asset class."""
    seen: Dict[str, Dict[str, Any]] = {}
    fetched: Dict[str, WalletSnapshot] = {}  # dedup_key -> snapshot, so Stocks/Options/Crypto
    # sharing the same Alpaca account triggers exactly one API call, not three.
    for asset_class, slots in _ASSET_CLASS_WALLET_SLOTS.items():
        for dedup_key_fn, build in slots:
            key = dedup_key_fn(user_id)
            if key in fetched:
                snap = fetched[key]
            else:
                snap = build(user_id)
                fetched[key] = snap
            row = seen.get(snap.dedup_key)
            if row is None:
                row = {
                    "broker": snap.broker, "label": snap.label, "connected": snap.connected,
                    "balance": snap.balance, "balance_label": snap.balance_label,
                    "error": snap.error, "asset_classes": [],
                }
                seen[snap.dedup_key] = row
            if ASSET_CLASS_LABELS[asset_class] not in row["asset_classes"]:
                row["asset_classes"].append(ASSET_CLASS_LABELS[asset_class])

    entries = list(seen.values())
    total = sum(e["balance"] for e in entries if e["connected"] and e["balance"] is not None)
    any_connected = any(e["connected"] and e["balance"] is not None for e in entries)
    return {"total": total if any_connected else None, "entries": entries}


def get_broker_cash_basis(asset_class: str, user_id: Optional[int] = None) -> Optional[float]:
    """The real connected broker's balance, for engines that otherwise size
    new positions against a flat simulated constant (Futures/Forex/Crypto's
    $1,000, Options' $10,000) regardless of whether a real account is
    connected. Returns the first connected wallet slot's balance for this
    asset class, or ``None`` when nothing is connected -- callers must keep
    using their own simulated constant in that case, unchanged.

    ``user_id=None`` (the default) matches every other unattended-engine
    call site in this repo (domain/futures/engine.py's and domain/forex/
    engine.py's own credential resolvers, domain/manual10/engine.py's
    alpaca_live usage): a scheduler tick has no signed-in caller, so it can
    only ever see server env-var credentials, never a specific user's
    Connections-saved key -- an explicit, existing scope boundary, not new
    here."""
    for _dedup_key_fn, build in _ASSET_CLASS_WALLET_SLOTS.get(asset_class, []):
        snap = build(user_id)
        if snap.connected and snap.balance is not None:
            return snap.balance
    return None

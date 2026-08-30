/*
 * connections.js — "API Connections" page.
 *
 * Same-origin convention (see strategy.html): same-origin locally, empty base
 * on Vercel. Named CONN_API_BASE, not the usual bare `API` -- this file
 * shares app.js's global scope (both are classic scripts on the same page),
 * and app.js already declares its own global `const API` fetch-wrapper
 * object; reusing the name is a `SyntaxError: Identifier 'API' has already
 * been declared` that silently kills this whole script before it wires up
 * anything.
 */
const CONN_API_BASE = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? window.location.origin
    : '';
const CONNECTIONS_PATH = '/api/v1/connections';
const ROBINHOOD_PATH = '/api/v1/robinhood';

function connHeaders() {
    const headers = { 'Content-Type': 'application/json' };
    if (window.SESSION_ID) headers['x-session-id'] = window.SESSION_ID;
    if (window.BROWSER_OWNER_ID) headers['x-browser-id'] = window.BROWSER_OWNER_ID;
    if (typeof window.csrfHeaders === 'function') Object.assign(headers, window.csrfHeaders());
    return headers;
}

async function connFetch(path, options = {}) {
    const res = await fetch(CONN_API_BASE + path, {
        method: options.method || 'GET',
        headers: connHeaders(),
        credentials: 'include',
        body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    });
    const contentType = res.headers.get('content-type');
    const data = contentType && contentType.includes('application/json') ? await res.json().catch(() => ({})) : null;
    if (!res.ok) {
        const message = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
        const err = new Error(typeof message === 'string' ? message : JSON.stringify(message));
        err.status = res.status;
        throw err;
    }
    return data;
}

const CONN_PROVIDER_INPUTS = {
    alpaca_live: [['api_key', 'connAlpacaLiveKey'], ['secret_key', 'connAlpacaLiveSecret']],
    alpaca_ira: [['api_key', 'connAlpacaIraKey'], ['secret_key', 'connAlpacaIraSecret']],
    alpaca_paper: [['api_key', 'connAlpacaPaperKey'], ['secret_key', 'connAlpacaPaperSecret']],
    anthropic_subscription: [['token', 'connAnthropicSubscriptionToken']],
    anthropic_api: [['api_key', 'connAnthropicApiKey']],
    openrouter: [['api_key', 'connOpenrouterKey']],
    commonstack: [['api_key', 'connCommonstackKey']],
    local: [['url', 'connLocalUrl'], ['api_key', 'connLocalKey']],
    kalshi: [['api_key', 'connKalshiKey'], ['secret_key', 'connKalshiSecret']],
    polymarket: [['api_key', 'connPolymarketKey']],
    tradovate: [
        ['username', 'connTradovateUsername'], ['password', 'connTradovatePassword'],
        ['cid', 'connTradovateCid'], ['sec', 'connTradovateSec'],
        ['account_spec', 'connTradovateAccountSpec'], ['account_id', 'connTradovateAccountId'],
    ],
    oanda: [['access_token', 'connOandaToken'], ['account_id', 'connOandaAccountId']],
};

const CONN_STATUS_EL = {
    alpaca_live: 'connStatusAlpacaLive',
    alpaca_ira: 'connStatusAlpacaIra',
    alpaca_paper: 'connStatusAlpacaPaper',
    anthropic_subscription: 'connStatusAnthropicSubscription',
    anthropic_api: 'connStatusAnthropicApi',
    openrouter: 'connStatusOpenrouter',
    commonstack: 'connStatusCommonstack',
    local: 'connStatusLocal',
    kalshi: 'connStatusKalshi',
    polymarket: 'connStatusPolymarket',
    tradovate: 'connStatusTradovate',
    oanda: 'connStatusOanda',
};

function setStatusBadge(el, connected, hint) {
    if (!el) return;
    el.textContent = connected ? `Connected${hint ? ` (${hint})` : ''}` : 'Not connected';
    el.classList.toggle('is-connected', !!connected);
    el.classList.toggle('is-error', false);
}

function setConnectionsStatusLine(message, isError) {
    const el = document.getElementById('connectionsStatus');
    if (!el) return;
    el.textContent = message || '';
    el.classList.toggle('is-success', !isError && !!message);
    el.classList.toggle('is-error', !!isError);
}

async function refreshRobinhoodStatus() {
    const badge = document.getElementById('connStatusRobinhood');
    const connectBtn = document.getElementById('connConnectRobinhoodBtn');
    const disconnectBtn = document.getElementById('connDisconnectRobinhoodBtn');
    try {
        const status = await connFetch(`${ROBINHOOD_PATH}/status`);
        const connected = !!status.connected;
        setStatusBadge(badge, connected);
        if (connectBtn) connectBtn.hidden = connected;
        if (disconnectBtn) disconnectBtn.hidden = !connected;
    } catch (error) {
        setStatusBadge(badge, false);
        if (connectBtn) connectBtn.hidden = false;
        if (disconnectBtn) disconnectBtn.hidden = true;
    }
}

function applyPaperWalletMode(mode) {
    const alpacaCard = document.getElementById('connAlpacaPaperCard');
    const virtualCard = document.getElementById('connVirtualWalletCard');
    const alpacaRadio = document.getElementById('connPaperModeAlpaca');
    const virtualRadio = document.getElementById('connPaperModeVirtual');
    if (alpacaRadio) alpacaRadio.checked = mode === 'alpaca';
    if (virtualRadio) virtualRadio.checked = mode !== 'alpaca';
    if (alpacaCard) alpacaCard.style.opacity = mode === 'alpaca' ? '1' : '0.55';
    if (virtualCard) virtualCard.style.opacity = mode === 'virtual' ? '1' : '0.55';
}

async function refreshConnections() {
    setConnectionsStatusLine('');
    try {
        const data = await connFetch(CONNECTIONS_PATH);
        const connections = data.connections || {};
        for (const [provider, statusEl] of Object.entries(CONN_STATUS_EL)) {
            const info = connections[provider] || { connected: false };
            setStatusBadge(document.getElementById(statusEl), info.connected, info.hint);
        }
        const wallet = data.paper_wallet || { mode: 'virtual', virtual_balance: 10000 };
        applyPaperWalletMode(wallet.mode);
        const amountInput = document.getElementById('connVirtualWalletAmount');
        if (amountInput && document.activeElement !== amountInput) {
            amountInput.value = wallet.virtual_balance;
        }
    } catch (error) {
        setConnectionsStatusLine(error.message || 'Could not load connections.', true);
    }
    await refreshRobinhoodStatus();
}

async function saveProviderConnection(provider) {
    const fieldMap = CONN_PROVIDER_INPUTS[provider];
    if (!fieldMap) return;
    const body = {};
    for (const [fieldName, inputId] of fieldMap) {
        const el = document.getElementById(inputId);
        const value = el ? el.value.trim() : '';
        if (value) body[fieldName] = value;
    }
    try {
        await connFetch(`${CONNECTIONS_PATH}/${provider}`, { method: 'PUT', body });
        for (const [, inputId] of fieldMap) {
            const el = document.getElementById(inputId);
            if (el) el.value = '';
        }
        setConnectionsStatusLine(`Saved ${provider.replace(/_/g, ' ')}.`, false);
        await refreshConnections();
    } catch (error) {
        setConnectionsStatusLine(error.message || `Could not save ${provider}.`, true);
    }
}

async function clearProviderConnection(provider) {
    try {
        await connFetch(`${CONNECTIONS_PATH}/${provider}`, { method: 'DELETE' });
        setConnectionsStatusLine(`Removed ${provider.replace(/_/g, ' ')}.`, false);
        await refreshConnections();
    } catch (error) {
        setConnectionsStatusLine(error.message || `Could not remove ${provider}.`, true);
    }
}

async function savePaperWalletMode(mode) {
    try {
        await connFetch(`${CONNECTIONS_PATH}/paper-wallet`, { method: 'PUT', body: { mode } });
        applyPaperWalletMode(mode);
        setConnectionsStatusLine('Paper wallet mode updated.', false);
    } catch (error) {
        setConnectionsStatusLine(error.message || 'Could not update paper wallet mode.', true);
    }
}

async function saveVirtualWalletAmount() {
    const input = document.getElementById('connVirtualWalletAmount');
    const amount = input ? Number(input.value) : NaN;
    if (!amount || amount <= 0) {
        setConnectionsStatusLine('Enter a virtual wallet amount greater than 0.', true);
        return;
    }
    try {
        await connFetch(`${CONNECTIONS_PATH}/paper-wallet`, { method: 'PUT', body: { virtual_balance: amount } });
        setConnectionsStatusLine('Virtual wallet amount saved.', false);
    } catch (error) {
        setConnectionsStatusLine(error.message || 'Could not save virtual wallet amount.', true);
    }
}

async function connectRobinhood() {
    try {
        const result = await connFetch('/api/auth/robinhood/start', { method: 'POST', body: {} });
        if (result.already_linked) {
            await refreshRobinhoodStatus();
            return;
        }
        if (result.authorize_url) {
            window.location.href = result.authorize_url;
        }
    } catch (error) {
        setConnectionsStatusLine(error.message || 'Could not start Robinhood connection.', true);
    }
}

async function disconnectRobinhood() {
    try {
        await connFetch(`${ROBINHOOD_PATH}/disconnect`, { method: 'DELETE' });
        await refreshRobinhoodStatus();
    } catch (error) {
        setConnectionsStatusLine(error.message || 'Could not disconnect Robinhood.', true);
    }
}

function wireConnectionsButtons() {
    if (window.__connectionsWired) return;
    window.__connectionsWired = true;

    document.getElementById('connectionsRefreshBtn')?.addEventListener('click', refreshConnections);
    document.getElementById('connectionsSignInBtn')?.addEventListener('click', () => {
        if (typeof window.openAuthModal === 'function') window.openAuthModal('login');
    });

    document.getElementById('connConnectRobinhoodBtn')?.addEventListener('click', connectRobinhood);
    document.getElementById('connDisconnectRobinhoodBtn')?.addEventListener('click', disconnectRobinhood);

    document.getElementById('connSaveAlpacaLiveBtn')?.addEventListener('click', () => saveProviderConnection('alpaca_live'));
    document.getElementById('connClearAlpacaLiveBtn')?.addEventListener('click', () => clearProviderConnection('alpaca_live'));
    document.getElementById('connSaveAlpacaIraBtn')?.addEventListener('click', () => saveProviderConnection('alpaca_ira'));
    document.getElementById('connClearAlpacaIraBtn')?.addEventListener('click', () => clearProviderConnection('alpaca_ira'));
    document.getElementById('connSaveAlpacaPaperBtn')?.addEventListener('click', () => saveProviderConnection('alpaca_paper'));
    document.getElementById('connClearAlpacaPaperBtn')?.addEventListener('click', () => clearProviderConnection('alpaca_paper'));
    document.getElementById('connSaveAnthropicSubscriptionBtn')?.addEventListener('click', () => saveProviderConnection('anthropic_subscription'));
    document.getElementById('connClearAnthropicSubscriptionBtn')?.addEventListener('click', () => clearProviderConnection('anthropic_subscription'));
    document.getElementById('connSaveAnthropicApiBtn')?.addEventListener('click', () => saveProviderConnection('anthropic_api'));
    document.getElementById('connClearAnthropicApiBtn')?.addEventListener('click', () => clearProviderConnection('anthropic_api'));
    document.getElementById('connSaveOpenrouterBtn')?.addEventListener('click', () => saveProviderConnection('openrouter'));
    document.getElementById('connClearOpenrouterBtn')?.addEventListener('click', () => clearProviderConnection('openrouter'));
    document.getElementById('connSaveCommonstackBtn')?.addEventListener('click', () => saveProviderConnection('commonstack'));
    document.getElementById('connClearCommonstackBtn')?.addEventListener('click', () => clearProviderConnection('commonstack'));
    document.getElementById('connSaveLocalBtn')?.addEventListener('click', () => saveProviderConnection('local'));
    document.getElementById('connClearLocalBtn')?.addEventListener('click', () => clearProviderConnection('local'));
    document.getElementById('connSaveKalshiBtn')?.addEventListener('click', () => saveProviderConnection('kalshi'));
    document.getElementById('connClearKalshiBtn')?.addEventListener('click', () => clearProviderConnection('kalshi'));
    document.getElementById('connSavePolymarketBtn')?.addEventListener('click', () => saveProviderConnection('polymarket'));
    document.getElementById('connClearPolymarketBtn')?.addEventListener('click', () => clearProviderConnection('polymarket'));
    document.getElementById('connSaveTradovateBtn')?.addEventListener('click', () => saveProviderConnection('tradovate'));
    document.getElementById('connClearTradovateBtn')?.addEventListener('click', () => clearProviderConnection('tradovate'));
    document.getElementById('connSaveOandaBtn')?.addEventListener('click', () => saveProviderConnection('oanda'));
    document.getElementById('connClearOandaBtn')?.addEventListener('click', () => clearProviderConnection('oanda'));

    document.getElementById('connPaperModeAlpaca')?.addEventListener('change', (e) => {
        if (e.target.checked) savePaperWalletMode('alpaca');
    });
    document.getElementById('connPaperModeVirtual')?.addEventListener('change', (e) => {
        if (e.target.checked) savePaperWalletMode('virtual');
    });
    document.getElementById('connSaveVirtualWalletBtn')?.addEventListener('click', saveVirtualWalletAmount);
}

function connStoredUser() {
    return typeof window.getStoredAuthUser === 'function' ? window.getStoredAuthUser() : null;
}

const ConnectionsPage = {
    syncAuth(user) {
        const signedOut = document.getElementById('connectionsSignedOut');
        const signedIn = document.getElementById('connectionsSignedIn');
        const isSignedIn = !!user;
        if (signedOut) signedOut.hidden = isSignedIn;
        if (signedIn) signedIn.hidden = !isSignedIn;
    },
    onEnter() {
        wireConnectionsButtons();
        const user = connStoredUser();
        this.syncAuth(user);
        if (user) refreshConnections();
    },
};
window.ConnectionsPage = ConnectionsPage;

document.addEventListener('DOMContentLoaded', wireConnectionsButtons);

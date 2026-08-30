// Manual page, folded into the SPA (formerly the standalone manual.html).
// Ticker/header now come from the real app shell -- this file only owns the
// #manualView content. Renamed API-base const to avoid colliding with app.js's
// own `const API` (both are classic scripts sharing one global scope).
const MANUAL_API_BASE = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"
    ? window.location.origin
    : "";
// Options and Futures each have their own sibling router with no
// screener/settings/promote-demote routes -- every uploaded options/futures
// strategy runs on its own fixed interval instead of a shared Top-10
// screener (see domain/options/engine.py's and domain/futures/engine.py's
// module docstrings), and neither has a real-money path armed by default
// (see each engine's manual_sell docstring). Reads the module-level
// `assetClass` app.js declares -- classic scripts sharing one global scope,
// same pattern strategy-catalog.js's scBasePath() uses.
const M10_ASSET_PATHS = { options: '/api/v1/options/manual', futures: '/api/v1/futures/manual', forex: '/api/v1/forex/manual', crypto: '/api/v1/crypto/manual' };
function m10Path() {
    return (typeof assetClass !== 'undefined' && M10_ASSET_PATHS[assetClass]) || '/api/v1/manual10';
}
function m10IsUploadOnly() {
    return typeof assetClass !== 'undefined' && assetClass !== 'stocks';
}

let m10StrategiesCache = [];
let m10SelectedKeys = new Set();

function m10IsVisible() {
    const view = document.getElementById('manualView');
    return !!view && view.style.display !== 'none';
}

function m10Money(v) {
    if (v === null || v === undefined) return '—';
    return '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function m10PctStr(v) {
    if (v === null || v === undefined) return '—';
    return (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%';
}
function m10LocalTimeShort(iso) {
    if (!iso) return '—';
    try { return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); } catch (e) { return iso; }
}

async function m10Api(path, options) {
    // window.csrfHeaders() (app.js) is a no-op object on a signed-out session,
    // but the local-only build auto-logs everyone in (see auth flow), which
    // means every mutating request here carries a session cookie and the
    // backend's double-submit CSRF check applies -- omitting this header
    // silently 403'd every Select/Activate/Upload/Settings-save action.
    const res = await fetch(MANUAL_API_BASE + m10Path() + path, {
        method: (options && options.method) || 'GET',
        headers: { 'Content-Type': 'application/json', ...window.csrfHeaders() },
        body: options && options.body ? JSON.stringify(options.body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);
    return data;
}

async function m10LoadWallet() {
    try {
        const status = await m10Api('/status');
        document.getElementById('m10WalletAmount').textContent = m10Money(status.open_positions_value);
        const pnlEl = document.getElementById('m10TodayPnl');
        const pnl = status.realized_pnl_today ?? status.unrealized_pnl_today ?? 0;
        pnlEl.textContent = m10Money(pnl);
        pnlEl.classList.toggle('pos', pnl > 0);
        pnlEl.classList.toggle('neg', pnl < 0);
    } catch (e) { /* keep last known values on a transient failure */ }
}

async function m10LoadBrokerWallets() {
    const row = document.getElementById('m10BrokerWalletRow');
    if (!row) return;
    const ac = (typeof assetClass !== 'undefined' && assetClass) || 'stocks';
    try {
        const res = await fetch(`${MANUAL_API_BASE}/api/v1/wallets/${ac}`, { credentials: 'include' });
        const data = await res.json();
        const wallets = data.wallets || [];
        row.innerHTML = wallets.map((w) => {
            const amount = (w.connected && w.balance !== null)
                ? m10Money(w.balance) : (w.connected ? 'Balance unavailable' : 'Not connected');
            const shared = w.shared_with && w.shared_with.length
                ? `<div class="m10-broker-wallet-shared">Same account as ${w.shared_with.join(', ')}</div>` : '';
            return `
            <div class="m10-broker-wallet-card">
                <div class="m10-broker-wallet-broker">${w.label}</div>
                <div class="m10-broker-wallet-purpose">${w.purpose}</div>
                <div class="m10-broker-wallet-amount${w.connected && w.balance !== null ? '' : ' not-connected'}">${amount}</div>
                ${shared}
            </div>`;
        }).join('');
    } catch (e) { /* leave the row empty on a transient failure */ }
}

async function m10LoadCalendar() {
    try {
        const data = await m10Api('/calendar?limit=30');
        const el = document.getElementById('m10Calendar');
        el.innerHTML = (data.days || []).slice().reverse().map(d => {
            const cls = d.result === 'win' ? 'win' : d.result === 'loss' ? 'loss' : d.result === 'flat' ? 'flat' : '';
            return `<div class="m10-cal-day ${cls}" title="${d.trading_date} (${d.strategy_key}): ${d.result || 'in progress'}"></div>`;
        }).join('');
    } catch (e) { /* ignore */ }
}

function m10StrategyCard(s) {
    const reviewBadge = s.kind === 'uploaded'
        ? `<span class="m10-badge ${s.review_status}">${s.review_status}</span>` : '';
    const canActivate = s.kind === 'builtin' || s.review_status === 'approved';
    const selectBtn = s.selected
        ? `<button class="m10-btn" data-action="deselect" data-key="${s.key}">Close panel</button>`
        : `<button class="m10-btn primary" data-action="select" data-key="${s.key}">Select</button>`;
    const activateBtn = s.activated
        ? `<button class="m10-btn danger" data-action="deactivate" data-key="${s.key}">Deactivate</button>`
        : `<button class="m10-btn success" data-action="activate" data-key="${s.key}" ${canActivate ? '' : 'disabled'}>Activate</button>`;
    const reviewButtons = (s.kind === 'uploaded' && s.review_status === 'pending')
        ? `<button class="m10-btn success" data-action="approve" data-key="${s.key}">Approve</button>
       <button class="m10-btn danger" data-action="reject" data-key="${s.key}">Reject</button>` : '';
    const deleteBtn = s.kind === 'uploaded'
        ? `<button class="m10-btn danger" data-action="delete" data-key="${s.key}">Delete</button>` : '';
    const exportBtn = s.kind === 'uploaded'
        ? `<button class="m10-btn" data-action="export" data-key="${s.key}">Export</button>` : '';
    return `
    <div class="m10-strategy-card">
      <p class="m10-strategy-name">${s.name}</p>
      <span class="m10-badge">${s.kind}</span>
      ${reviewBadge}
      <p class="m10-strategy-desc">${s.description || ''}</p>
      ${s.kind === 'uploaded' && s.review_notes ? `<p class="m10-strategy-desc" style="font-style:italic;">Risk review: ${s.review_notes}</p>` : ''}
      <div class="m10-strategy-actions">
        ${selectBtn}
        ${s.selected ? activateBtn : ''}
        ${reviewButtons}
        ${exportBtn}
        ${deleteBtn}
      </div>
    </div>`;
}

function m10ExportStrategy(key) {
    const s = m10StrategiesCache.find(x => x.key === key);
    if (!s || !s.code) { alert('No exportable code for this strategy.'); return; }
    const pkg = {
        format: 'newworldtrading-strategy-v1',
        name: s.name,
        description: s.description || '',
        code: s.code,
        exported_at: new Date().toISOString(),
        source: (typeof assetClass !== 'undefined' && M10_ASSET_PATHS[assetClass]) ? `${assetClass}-manual` : 'manual10',
    };
    const blob = new Blob([JSON.stringify(pkg, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${(s.name || 'strategy').trim().toLowerCase().replace(/\s+/g, '_')}.strategy.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}

async function m10LoadStrategies() {
    const grid = document.getElementById('m10StrategyGrid');
    if (!grid) return;
    try {
        const data = await m10Api('/strategies');
        m10StrategiesCache = data.strategies || [];
        m10SelectedKeys = new Set(m10StrategiesCache.filter(s => s.selected).map(s => s.key));
        grid.innerHTML = m10StrategiesCache.map(m10StrategyCard).join('') || '<p class="m10-empty">No strategies yet.</p>';
        grid.querySelectorAll('button[data-action]').forEach(btn => {
            btn.addEventListener('click', () => m10HandleStrategyAction(btn.dataset.action, btn.dataset.key));
        });
        m10RenderPanels();
    } catch (e) {
        grid.innerHTML = '<p class="m10-empty">Failed to load strategies: ' + e.message + '</p>';
    }
}

async function m10HandleStrategyAction(action, key) {
    if (action === 'export') {
        m10ExportStrategy(key);
        return;
    }
    try {
        if (action === 'delete') {
            // The API models delete as DELETE /strategies/{key}, not POST .../delete
            await m10Api(`/strategies/${encodeURIComponent(key)}`, { method: 'DELETE' });
        } else {
            await m10Api(`/strategies/${encodeURIComponent(key)}/${action}`, { method: 'POST' });
        }
        await m10LoadStrategies();
    } catch (e) {
        alert('Could not ' + action + ': ' + e.message);
    }
}

function m10PanelShell(strategy) {
    const isUploadOnly = m10IsUploadOnly();
    return `
    <div class="m10-panel" id="panel-${strategy.key}">
      <div class="m10-panel-header">
        <h3 class="m10-panel-title">${strategy.name}</h3>
        <span class="m10-badge" id="phase-${strategy.key}"></span>
      </div>
      ${isUploadOnly ? '' : `
      <div class="m10-subsection">
        <p class="m10-subsection-title">Screener</p>
        <div id="screener-${strategy.key}"></div>
      </div>`}
      <div class="m10-subsection">
        <p class="m10-subsection-title">Paper Money</p>
        <div id="paper-${strategy.key}"></div>
      </div>
      ${isUploadOnly ? '' : `
      <div class="m10-subsection">
        <p class="m10-subsection-title">Real Money</p>
        <div id="real-${strategy.key}"></div>
      </div>`}
      <div class="m10-subsection">
        <p class="m10-subsection-title">10 Minute Updater</p>
        <div class="m10-tenmin-box" id="tenmin-${strategy.key}"><p class="m10-empty">No data yet.</p></div>
      </div>
    </div>`;
}

function m10RenderPanels() {
    const container = document.getElementById('m10Panels');
    if (!container) return;
    const selected = m10StrategiesCache.filter(s => m10SelectedKeys.has(s.key));
    const existingIds = new Set(selected.map(s => `panel-${s.key}`));
    Array.from(container.children).forEach(child => {
        if (!existingIds.has(child.id)) child.remove();
    });
    selected.forEach(s => {
        if (!document.getElementById(`panel-${s.key}`)) {
            container.insertAdjacentHTML('beforeend', m10PanelShell(s));
        }
    });
    selected.forEach(s => m10RefreshPanel(s.key));
}

function m10PositionRow(p, extraButtons) {
    const cls = p.change_pct >= 0 ? 'pos' : 'neg';
    return `
    <tr>
      <td>${p.symbol}</td>
      <td>${m10Money(p.cost_basis)}</td>
      <td>${m10Money(p.current_value)}</td>
      <td class="${cls}">${m10PctStr(p.change_pct)}</td>
      <td class="m10-table-actions">${extraButtons(p)}</td>
    </tr>`;
}

function m10PositionsTable(positions, extraButtons) {
    if (!positions.length) return '<p class="m10-empty">Nothing here yet.</p>';
    return `
    <table class="m10-table">
      <thead><tr><th>Symbol</th><th>Original cost</th><th>Current cost</th><th>Change</th><th></th></tr></thead>
      <tbody>${positions.map(p => m10PositionRow(p, extraButtons)).join('')}</tbody>
    </table>`;
}

async function m10PositionAction(action, id) {
    try {
        await m10Api(`/positions/${id}/${action}`, { method: 'POST' });
        await m10LoadStrategies();
    } catch (e) {
        alert('Could not ' + action + ' this position: ' + e.message);
    }
}
window._m10PositionAction = m10PositionAction;

async function m10RefreshPanel(key) {
    const isUploadOnly = m10IsUploadOnly();
    try {
        if (isUploadOnly) {
            // No screener, no real-money bucket for Options this phase (see
            // m10Path()'s comment) -- just the strategy's own paper positions.
            const paper = await m10Api(`/positions?strategy_key=${encodeURIComponent(key)}&bucket=paper&status=open`);
            const strategy = m10StrategiesCache.find(s => s.key === key);
            const phaseEl = document.getElementById(`phase-${key}`);
            if (phaseEl) phaseEl.textContent = strategy && strategy.activated ? 'active' : 'holding';

            const paperEl = document.getElementById(`paper-${key}`);
            if (paperEl) {
                paperEl.innerHTML = m10PositionsTable(paper.positions, (p) => `
          <button class="m10-btn danger" onclick="_m10PositionAction('sell', ${p.id})">Sell</button>`);
            }

            window._m10LatestPositions = window._m10LatestPositions || {};
            window._m10LatestPositions[key] = [...paper.positions];
            return;
        }

        const [screener, paper, real] = await Promise.all([
            m10Api(`/screener?strategy_key=${encodeURIComponent(key)}`),
            m10Api(`/positions?strategy_key=${encodeURIComponent(key)}&bucket=paper&status=open`),
            m10Api(`/positions?strategy_key=${encodeURIComponent(key)}&bucket=real&status=open`),
        ]);

        const phaseEl = document.getElementById(`phase-${key}`);
        if (phaseEl) phaseEl.textContent = screener.phase;

        const screenerEl = document.getElementById(`screener-${key}`);
        if (screenerEl) {
            if (screener.progress_pct === null || screener.progress_pct === undefined) {
                screenerEl.innerHTML = `<p class="m10-empty">Waiting for the market to open (your local time updates automatically).</p>`;
            } else {
                screenerEl.innerHTML = `
          <div class="m10-progress-bar"><div class="m10-progress-fill" style="width:${screener.progress_pct}%"></div></div>
          <div class="m10-progress-label">${screener.progress_pct}% through the ${screener.window_minutes}-minute window &middot; ${screener.candidates.length} candidates found</div>`;
            }
        }

        const paperEl = document.getElementById(`paper-${key}`);
        if (paperEl) {
            paperEl.innerHTML = m10PositionsTable(paper.positions, (p) => `
        <button class="m10-btn success" onclick="_m10PositionAction('promote', ${p.id})">Real Money</button>
        <button class="m10-btn danger" onclick="_m10PositionAction('sell', ${p.id})">Sell</button>`);
        }

        const realEl = document.getElementById(`real-${key}`);
        if (realEl) {
            realEl.innerHTML = m10PositionsTable(real.positions, (p) => `
        <button class="m10-btn" onclick="_m10PositionAction('demote', ${p.id})">Paper Money</button>
        <button class="m10-btn danger" onclick="_m10PositionAction('sell', ${p.id})">Sell</button>`);
        }

        window._m10LatestPositions = window._m10LatestPositions || {};
        window._m10LatestPositions[key] = [...paper.positions, ...real.positions];
    } catch (e) {
        console.error('panel refresh failed for', key, e);
    }
}

function m10RefreshTenMinutePanels() {
    const latest = window._m10LatestPositions || {};
    Object.keys(latest).forEach(key => {
        const el = document.getElementById(`tenmin-${key}`);
        if (!el) return;
        const positions = latest[key];
        if (!positions.length) {
            el.innerHTML = '<p class="m10-empty">No open positions.</p>';
            return;
        }
        el.innerHTML = positions.map(p => `
      <div class="m10-tenmin-row">
        <span>${p.symbol}</span>
        <span>${m10Money(p.price_10min_ago)} &rarr; ${m10Money(p.current_price)}</span>
      </div>`).join('') + `<div class="m10-tenmin-updated">Updated ${m10LocalTimeShort(new Date().toISOString())}</div>`;
    });
}

async function m10Tick() {
    await m10LoadWallet();
    const selected = m10StrategiesCache.filter(s => m10SelectedKeys.has(s.key));
    await Promise.all(selected.map(s => m10RefreshPanel(s.key)));
}

const M10_SETTINGS_FIELD_MAP = {
    setScreenerWindow: 'screener_window_minutes',
    setTopN: 'top_n',
    setBuyIn: 'buy_in_per_stock',
    setPriceMin: 'price_min',
    setPriceMax: 'price_max',
    setPromotionWindow: 'promotion_window_minutes',
    setCloseOut: 'close_out_minutes_before_close',
    setStraggler: 'straggler_check_minutes_before_close',
};

async function m10LoadSettings() {
    // Screener-only settings (window minutes, top N, buy-in, promotion
    // window...) -- Options has no screener phase, so no /settings route.
    if (m10IsUploadOnly()) return;
    try {
        const settings = await m10Api('/settings');
        Object.entries(M10_SETTINGS_FIELD_MAP).forEach(([elId, key]) => {
            const el = document.getElementById(elId);
            if (el) el.value = settings[key];
        });
    } catch (e) { /* keep whatever was last loaded */ }
}

function m10WireStaticControls() {
    document.getElementById('m10UploadToggle')?.addEventListener('click', () => {
        const form = document.getElementById('m10UploadForm');
        const open = form.style.display !== 'none';
        form.style.display = open ? 'none' : 'block';
        document.getElementById('m10UploadBox').classList.toggle('open', !open);
    });

    document.getElementById('m10UploadSubmit')?.addEventListener('click', async () => {
        const status = document.getElementById('m10UploadStatus');
        const name = document.getElementById('m10UploadName').value.trim();
        const description = document.getElementById('m10UploadDesc').value.trim();
        const code = document.getElementById('m10UploadCode').value;
        const interval_minutes = Number(document.getElementById('m10UploadInterval').value) || 15;
        status.textContent = 'Submitting...';
        status.style.color = '';
        try {
            await m10Api('/strategies/upload', { method: 'POST', body: { name, description, code, interval_minutes } });
            status.textContent = 'Submitted -- pending review below.';
            status.style.color = 'var(--success-color)';
            document.getElementById('m10UploadName').value = '';
            document.getElementById('m10UploadDesc').value = '';
            document.getElementById('m10UploadCode').value = '';
            await m10LoadStrategies();
        } catch (e) {
            status.textContent = 'Rejected: ' + e.message;
            status.style.color = 'var(--danger-color)';
        }
    });

    document.getElementById('m10SettingsToggle')?.addEventListener('click', () => {
        const form = document.getElementById('m10SettingsForm');
        form.style.display = form.style.display === 'none' ? 'block' : 'none';
    });

    document.getElementById('m10SettingsSave')?.addEventListener('click', async () => {
        if (m10IsUploadOnly()) return;
        const status = document.getElementById('m10SettingsStatus');
        const body = {};
        Object.entries(M10_SETTINGS_FIELD_MAP).forEach(([elId, key]) => {
            const el = document.getElementById(elId);
            if (el && el.value !== '') body[key] = Number(el.value);
        });
        status.textContent = 'Saving...';
        status.style.color = '';
        try {
            await m10Api('/settings', { method: 'PUT', body });
            status.textContent = 'Saved.';
            status.style.color = 'var(--success-color)';
            setTimeout(() => { status.textContent = ''; }, 2500);
        } catch (e) {
            status.textContent = 'Failed: ' + e.message;
            status.style.color = 'var(--danger-color)';
        }
    });

    // The exportability guide's link to the Testing page -- a real SPA nav
    // rather than a full page load, since this content now lives in #manualView.
    document.querySelector('#manualView [data-nav-link="community"]')?.addEventListener('click', (e) => {
        e.preventDefault();
        navigateToPage('community');
    });
}

// Timers are wired exactly once (like strategy-testing.js's poll loop) and
// each tick no-ops off-page via m10IsVisible -- re-entering Manual must not
// stack a second set of intervals on top of the first.
function m10WireTimers() {
    if (window.__manualTimersWired) return;
    window.__manualTimersWired = true;
    setInterval(() => { if (m10IsVisible()) m10Tick(); }, 8000);
    setInterval(() => { if (m10IsVisible()) m10LoadCalendar(); }, 60000);
    setInterval(() => { if (m10IsVisible()) m10RefreshTenMinutePanels(); }, 10 * 60 * 1000);
}

window.ManualPage = {
    onEnter() {
        if (!window.__manualWired) {
            window.__manualWired = true;
            m10WireStaticControls();
        }
        // Screener-tuning settings only apply to the stocks Top-10 screener --
        // hide the toggle for Options rather than leave a button that opens a
        // form whose Save 404s. Re-evaluated on every entry since assetClass
        // can change without a full page reload.
        const isUploadOnly = m10IsUploadOnly();
        const settingsToggle = document.getElementById('m10SettingsToggle');
        if (settingsToggle) settingsToggle.style.display = isUploadOnly ? 'none' : '';
        const settingsForm = document.getElementById('m10SettingsForm');
        if (settingsForm && isUploadOnly) settingsForm.style.display = 'none';

        m10WireTimers();
        m10LoadStrategies();
        m10LoadWallet();
        m10LoadBrokerWallets();
        m10LoadCalendar();
        m10LoadSettings();
        m10RefreshTenMinutePanels();
    },
};

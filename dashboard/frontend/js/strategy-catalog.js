// Strategy Catalog page, folded into the SPA (formerly the standalone
// strategy-catalog.html). Ticker/header now come from the real app shell --
// this file only owns the #strategyCatalogView content. Reuses app.js's own
// window.csrfHeaders() rather than redeclaring it (the one function name that
// collided between the two standalone pages' scripts and app.js's globals).

function scMoney(v) {
    if (v === null || v === undefined) return '—';
    return '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function scPctStr(v) {
    if (v === null || v === undefined) return '—';
    return (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%';
}

let scCatalogData = null;

// Options and Futures entries each live under their own sibling router --
// same shape for list/report/export/delete, but deliberately NO allocation
// or Run in Paper/Live routes yet (no real-money path for either dashboard
// in this phase, see domain/options/engine.py's and domain/futures/
// engine.py's manual_sell docstrings). Reads the module-level `assetClass`
// app.js declares -- both files are classic scripts sharing one global
// scope, see app.js's own playgroundTab/competitionTab pattern this mirrors.
const SC_ASSET_PATHS = { options: '/api/v1/options/strategy-catalog', futures: '/api/v1/futures/strategy-catalog', forex: '/api/v1/forex/strategy-catalog', crypto: '/api/v1/crypto/strategy-catalog' };
function scBasePath() {
    return (typeof assetClass !== 'undefined' && SC_ASSET_PATHS[assetClass]) || '/api/v1/strategy-catalog';
}
function scIsUploadOnly() {
    return typeof assetClass !== 'undefined' && assetClass !== 'stocks';
}

async function scLoadAllocation(key) {
    const input = document.getElementById('alloc-' + key);
    const perStockInput = document.getElementById('per-stock-' + key);
    if (!input) return;
    try {
        const res = await fetch(`${scBasePath()}/${key}/allocation`);
        const data = await res.json();
        input.value = data.allocated_capital;
        if (perStockInput) perStockInput.value = data.per_stock_amount ?? '';
    } catch (e) { /* leave the inputs blank on failure */ }
}

async function scSaveAllocation(key, btn) {
    const input = document.getElementById('alloc-' + key);
    const perStockInput = document.getElementById('per-stock-' + key);
    const savedLabel = document.getElementById('alloc-saved-' + key);
    const amount = Number(input.value);
    if (!(amount > 0)) {
        alert('Allocated capital must be a positive number.');
        return;
    }
    const perStockRaw = perStockInput ? perStockInput.value.trim() : '';
    if (perStockRaw !== '' && !(Number(perStockRaw) > 0)) {
        alert('$ per stock must be a positive number, or left blank.');
        return;
    }
    btn.disabled = true;
    try {
        const res = await fetch(`${scBasePath()}/${key}/allocation`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json', ...window.csrfHeaders() },
            body: JSON.stringify({
                allocated_capital: amount,
                per_stock_amount: perStockRaw === '' ? null : Number(perStockRaw),
            }),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || res.statusText);
        }
        if (savedLabel) {
            savedLabel.textContent = 'Saved';
            setTimeout(() => { savedLabel.textContent = ''; }, 2000);
        }
    } catch (e) {
        alert('Failed to save allocation: ' + e.message);
    } finally {
        btn.disabled = false;
    }
}

async function scRemoveStrategy(key, name) {
    if (!confirm(`Remove "${name}" from Strategy? This also takes it off the Competition Leaderboard's chart.`)) return;
    try {
        const res = await fetch(`${scBasePath()}/${key}`, { method: 'DELETE', headers: window.csrfHeaders() });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || res.statusText);
        }
        scLoadCatalog(false);
    } catch (e) {
        alert('Failed to remove strategy: ' + e.message);
    }
}

function scRenderActivationButton(btn, mode, activation) {
    const active = !!(activation && activation.activated);
    btn.textContent = active ? `Deactivate ${mode === 'paper' ? 'Paper' : 'Live'}` : `Activate ${mode === 'paper' ? 'Paper' : 'Live'}`;
    btn.classList.toggle('active', active);
    btn.dataset.activated = active ? '1' : '0';
}

function scRenderActivationStatus(key, mode, activation) {
    const box = document.getElementById('result-' + key + '-' + mode);
    if (!box) return;
    if (!activation || !activation.activated) {
        box.style.display = 'none';
        box.textContent = '';
        return;
    }
    box.style.display = 'block';
    const lastRun = activation.last_run_at
        ? `Last ran ${new Date(activation.last_run_at).toLocaleString()} (${activation.last_run_status || 'unknown'})`
        : 'Not run yet -- will run on the next trading day the scheduler ticks.';
    box.textContent = `Activated (${mode}) since ${activation.activated_at ? new Date(activation.activated_at).toLocaleString() : '—'}. Trades automatically every day the market is open, until deactivated. ${lastRun}`;
}

async function scLoadActivation(key) {
    const paperBtn = document.getElementById(`btn-${key}-paper`);
    const liveBtn = document.getElementById(`btn-${key}-live`);
    if (!paperBtn && !liveBtn) return;
    try {
        const res = await fetch(`${scBasePath()}/${key}/activation`);
        const data = await res.json();
        if (paperBtn) { scRenderActivationButton(paperBtn, 'paper', data.paper); scRenderActivationStatus(key, 'paper', data.paper); }
        if (liveBtn) { scRenderActivationButton(liveBtn, 'live', data.live); scRenderActivationStatus(key, 'live', data.live); }
    } catch (e) { /* leave buttons at their default "Activate" label on failure */ }
}

async function scToggleActivation(key, mode, btn) {
    const active = btn.dataset.activated === '1';
    if (active) {
        if (!confirm(`Stop automatic ${mode} trading for this strategy? It will not trade again until you activate it.`)) return;
    } else {
        const modeLabel = mode === 'live' ? 'real money' : 'paper (simulated) money';
        if (!confirm(`Activate this strategy for ${mode}? It will trade automatically every day the market is open, using ${modeLabel}, until you deactivate it.`)) return;
    }
    btn.disabled = true;
    try {
        const res = await fetch(`${scBasePath()}/${key}/${mode}/${active ? 'deactivate' : 'activate'}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...window.csrfHeaders() },
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || res.statusText);
        }
        await scLoadActivation(key);
    } catch (e) {
        alert('Failed to update activation: ' + e.message);
    } finally {
        btn.disabled = false;
    }
}

function scRenderCard(entry, index) {
    const ret = entry.metrics.return_pct || 0;
    const retClass = ret >= 0 ? 'pos' : 'neg';
    const canvasId = 'scChart' + index;
    const isUploadOnly = scIsUploadOnly();
    const card = document.createElement('div');
    card.className = 'sc-card';
    card.innerHTML = `
    <div class="sc-card-header">
      <div>
        <p class="sc-card-name">${entry.name}</p>
        <span class="sc-card-source">${entry.source}</span>
      </div>
      <span class="sc-card-return ${retClass}">${ret >= 0 ? '+' : ''}${ret.toFixed(2)}%</span>
    </div>
    <p class="sc-card-desc">${entry.description}</p>
    ${entry.metrics.note ? `<p class="sc-card-desc" style="color:var(--text-muted); font-style:italic;">${entry.metrics.note}</p>` : ''}
    <div class="sc-card-chart"><canvas id="${canvasId}"></canvas></div>
    <div class="sc-card-stats">
      <span>Final <b>${scMoney(entry.metrics.final)}</b></span>
      <span>Max DD <b>${(entry.metrics.max_drawdown_pct || 0).toFixed(2)}%</b></span>
      <span>Trades <b>${entry.metrics.n_trades ?? '—'}</b></span>
    </div>
    ${isUploadOnly ? '' : `
    <div class="sc-alloc-row">
      <span>Allocated</span>
      <input type="number" min="1" step="1" id="alloc-${entry.key}" title="Real dollar cap: an activated strategy never sizes a new buy above this amount.">
      <span>$/stock</span>
      <input type="number" min="1" step="1" id="per-stock-${entry.key}" placeholder="auto" title="Fixed dollar amount per position, instead of splitting Allocated proportionally by weight. Leave blank for proportional sizing.">
      <button id="alloc-save-${entry.key}">Save</button>
      <span class="sc-alloc-saved" id="alloc-saved-${entry.key}"></span>
    </div>
    <p class="sc-card-desc" style="font-size:0.85em;">Activating trades this strategy automatically every day the market is open, until you deactivate it -- it does not run once and stop.</p>
    <div class="sc-card-actions">
      <button class="sc-btn paper" id="btn-${entry.key}-paper">Activate Paper</button>
      <button class="sc-btn live" id="btn-${entry.key}-live">Activate Live</button>
    </div>`}
    <div class="sc-card-actions">
      ${isUploadOnly ? '' : `<button class="sc-btn" id="btn-${entry.key}-edit">Edit</button>`}
      <button class="sc-btn" id="btn-${entry.key}-report">Reports</button>
      <button class="sc-btn" id="btn-${entry.key}-export">Export</button>
      <button class="sc-btn danger" id="btn-${entry.key}-remove">Remove</button>
    </div>
    <div class="sc-result-box" id="result-${entry.key}-paper" style="display:none;"></div>
    <div class="sc-result-box" id="result-${entry.key}-live" style="display:none;"></div>
  `;
    return card;
}

function scRenderCharts(entries) {
    const styles = getComputedStyle(document.documentElement);
    const gridColor = styles.getPropertyValue('--border-color').trim() || styles.getPropertyValue('--border').trim();
    const textColor = styles.getPropertyValue('--text-muted').trim();
    entries.forEach((entry, index) => {
        const canvas = document.getElementById('scChart' + index);
        if (!canvas) return;
        const points = entry.equity_curve || [];
        const accent = (entry.metrics.return_pct || 0) >= 0
            ? styles.getPropertyValue('--success-color').trim()
            : styles.getPropertyValue('--danger-color').trim();
        new Chart(canvas.getContext('2d'), {
            type: 'line',
            data: {
                labels: points.map(p => p.t),
                datasets: [{
                    data: points.map(p => p.equity),
                    borderColor: accent,
                    backgroundColor: 'transparent',
                    borderWidth: 1.75,
                    pointRadius: 0,
                    tension: 0.15,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'nearest', axis: 'x', intersect: false },
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: { label: (item) => '$' + Number(item.parsed.y).toFixed(2) } },
                },
                scales: {
                    x: { display: false },
                    y: { ticks: { color: textColor, font: { size: 9 }, maxTicksLimit: 4, callback: (v) => '$' + v }, grid: { color: gridColor } },
                },
            },
        });
    });
}

function scRenderStatusRow(data) {
    const row = document.getElementById('scStatusRow');
    if (!row) return;
    const computedAt = data.computed_at ? new Date(data.computed_at).toLocaleString() : 'never';
    const window_ = data.window ? `${data.window.start_date} → ${data.window.end_date}` : '—';
    row.innerHTML =
        `<span class="sc-pill">Computed ${computedAt}</span>` +
        `<span class="sc-pill">Window ${window_}</span>` +
        `<span class="sc-pill">${(data.entries || []).length} strategies</span>`;
}

async function scLoadCatalog(forceRefresh) {
    const grid = document.getElementById('scGrid');
    if (!grid) return;
    grid.innerHTML = '<p class="sc-empty">Loading catalog' + (forceRefresh ? ' (recomputing, this can take a minute)…' : '…') + '</p>';
    try {
        const res = await fetch(scBasePath() + (forceRefresh ? '?refresh=true' : ''));
        const data = await res.json();
        scCatalogData = data;
        const ts = document.getElementById('scTimestamp');
        if (ts) ts.textContent = data.computed_at ? 'Updated ' + new Date(data.computed_at).toLocaleTimeString() : '';
        scRenderStatusRow(data);
        const entries = data.entries || [];
        if (!entries.length) {
            grid.innerHTML = '<p class="sc-empty">No catalog data yet -- set ALPACA_API_KEY/ALPACA_SECRET_KEY in dashboard/.env, then click Refresh.</p>';
            return;
        }
        grid.innerHTML = '';
        entries.forEach((entry, index) => grid.appendChild(scRenderCard(entry, index)));
        scRenderCharts(entries);
        const isUploadOnly = scIsUploadOnly();
        entries.forEach((entry) => {
            if (!isUploadOnly) {
                document.getElementById(`btn-${entry.key}-paper`).addEventListener('click', (e) => scToggleActivation(entry.key, 'paper', e.target));
                document.getElementById(`btn-${entry.key}-live`).addEventListener('click', (e) => scToggleActivation(entry.key, 'live', e.target));
                document.getElementById(`btn-${entry.key}-edit`).addEventListener('click', () => {
                    window.location.href = `/strategy-edit.html?key=${encodeURIComponent(entry.key)}`;
                });
                document.getElementById(`alloc-save-${entry.key}`).addEventListener('click', (e) => scSaveAllocation(entry.key, e.target));
                scLoadAllocation(entry.key);
                scLoadActivation(entry.key);
            }
            document.getElementById(`btn-${entry.key}-remove`).addEventListener('click', () => scRemoveStrategy(entry.key, entry.name));
            document.getElementById(`btn-${entry.key}-report`).addEventListener('click', () => scShowReport(entry.key));
            document.getElementById(`btn-${entry.key}-export`).addEventListener('click', () => {
                window.location.href = `${scBasePath()}/${encodeURIComponent(entry.key)}/export`;
            });
        });
    } catch (e) {
        grid.innerHTML = '<p class="sc-empty">Failed to load: ' + e + '</p>';
    }
}

async function scShowReport(key) {
    const dialog = document.getElementById('scReportDialog');
    const title = document.getElementById('scReportTitle');
    const body = document.getElementById('scReportBody');
    if (!dialog || !title || !body) return;
    title.textContent = 'Report';
    body.innerHTML = '<p class="sc-empty">Loading (recomputing the full year)…</p>';
    dialog.showModal();
    try {
        const res = await fetch(`${scBasePath()}/${encodeURIComponent(key)}/report`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        title.textContent = `${data.name} — Report`;
        const o = data.overall || {};
        const months = (data.months || []).map(m => `
      <tr>
        <td>${m.month}</td>
        <td>${scMoney(m.start)}</td>
        <td>${scMoney(m.end)}</td>
        <td class="${m.return_pct >= 0 ? 'sc-pos' : 'sc-neg'}">${scPctStr(m.return_pct)}</td>
      </tr>`).join('');
        body.innerHTML = `
      <p class="sc-card-desc">Tested ${data.window?.start_date || ''} → ${data.window?.end_date || ''}, starting from a $1,000 wallet.</p>
      <div class="sc-report-stats">
        <div><span class="sc-report-stat-label">Starting wallet</span><span class="sc-report-stat-value">${scMoney(o.starting_wallet)}</span></div>
        <div><span class="sc-report-stat-label">Final value</span><span class="sc-report-stat-value">${scMoney(o.final)}</span></div>
        <div><span class="sc-report-stat-label">P&amp;L</span><span class="sc-report-stat-value ${o.pnl >= 0 ? 'sc-pos' : 'sc-neg'}">${scMoney(o.pnl)}</span></div>
        <div><span class="sc-report-stat-label">Return</span><span class="sc-report-stat-value ${o.return_pct >= 0 ? 'sc-pos' : 'sc-neg'}">${scPctStr(o.return_pct)}</span></div>
        <div><span class="sc-report-stat-label">Max drawdown</span><span class="sc-report-stat-value sc-neg">${scPctStr(o.max_drawdown_pct)}</span></div>
        <div><span class="sc-report-stat-label">Trades</span><span class="sc-report-stat-value">${data.n_trades ?? '—'}</span></div>
      </div>
      <table class="sc-month-table">
        <thead><tr><th>Month</th><th>Start</th><th>End</th><th>Return</th></tr></thead>
        <tbody>${months || '<tr><td colspan="4" class="sc-empty">No months yet.</td></tr>'}</tbody>
      </table>`;
    } catch (e) {
        body.innerHTML = `<p class="sc-empty">Failed to load report: ${e.message}</p>`;
    }
}

function scWireStaticControls() {
    if (window.__strategyCatalogWired) return;
    window.__strategyCatalogWired = true;
    document.getElementById('scRefreshBtn')?.addEventListener('click', () => scLoadCatalog(true));
    document.getElementById('scReportCloseBtn')?.addEventListener('click', () => {
        document.getElementById('scReportDialog')?.close();
    });
}

window.StrategyCatalogPage = {
    onEnter() {
        scWireStaticControls();
        scLoadCatalog(false);
    },
};

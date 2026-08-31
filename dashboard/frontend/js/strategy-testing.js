/*
 * strategy-testing.js — the "Testing" page: upload a strategy, watch it get
 * scanned and backtested by the server-side queue, then Add/Delete.
 *
 * Named TEST_API_BASE, not the usual bare `API`/`API_BASE`: this file shares
 * app.js's global scope (classic scripts on the same page), and app.js
 * already declares its own global `const API` object -- reusing that name is
 * a SyntaxError that silently kills this whole script (see connections.js's
 * top comment for the same lesson learned the hard way).
 */
const TEST_API_BASE = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? window.location.origin
    : '';
const TEST_QUEUE_PATH = '/api/v1/strategy-testing';
const TEST_POLL_MS = 4000;

let _testingPollTimer = null;
let _testingSubmitInFlight = false;

function testMoney(value) {
    if (value === null || value === undefined) return '—';
    return '$' + Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function testPct(value) {
    if (value === null || value === undefined) return '—';
    const n = Number(value);
    return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
}

function testLocalTime(iso) {
    if (!iso) return '—';
    try { return new Date(iso).toLocaleString(); } catch (_) { return iso; }
}

const STATUS_LABELS = {
    queued: 'Queued',
    scanning: 'Scanning for safety',
    backtesting: 'Backtesting (1 year)',
    ready: 'Ready',
    rejected: 'Rejected',
    error: 'Error',
    added: 'Added to Strategy',
    deleted: 'Deleted',
};

async function testFetch(path, options = {}) {
    // The backend's double-submit CSRF check applies to every mutating route;
    // without this header every POST from this page 403s (found by the
    // 2026-08-29 button audit -- "Add to queue" was broken for everyone).
    const csrf = typeof window.csrfHeaders === 'function' ? window.csrfHeaders() : {};
    const headers = { ...csrf, ...(options.headers || {}) };
    const res = await fetch(TEST_API_BASE + TEST_QUEUE_PATH + path, {
        method: options.method || 'GET',
        headers,
        body: options.body,
    });
    const contentType = res.headers.get('content-type');
    const isJson = contentType && contentType.includes('application/json');
    if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        if (isJson) {
            try { detail = (await res.json()).detail || detail; } catch (_) { /* ignore */ }
        }
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return isJson ? res.json() : res.blob();
}

function testEscape(str) {
    return typeof window.escapeHtml === 'function' ? window.escapeHtml(str) : String(str ?? '');
}

function renderMonthTable(months) {
    if (!months || !months.length) return '';
    const rows = months.map((m) => `
        <tr>
            <td>${testEscape(m.month)}</td>
            <td>${testMoney(m.start)}</td>
            <td>${testMoney(m.end)}</td>
            <td class="${m.return_pct >= 0 ? 'testing-pos' : 'testing-neg'}">${testPct(m.return_pct)}</td>
        </tr>`).join('');
    return `
        <table class="testing-month-table">
            <thead><tr><th>Month</th><th>Start</th><th>End</th><th>Return</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>`;
}

function renderReadyCard(item) {
    const result = item.result || {};
    const overall = result.overall || {};
    const window_ = result.window || {};
    return `
        <div class="testing-card testing-card-ready" data-id="${testEscape(item.id)}">
            <div class="testing-card-head">
                <div>
                    <span class="testing-card-name">${testEscape(item.name)}</span>
                    <span class="testing-status-badge is-ready">${STATUS_LABELS.ready}</span>
                </div>
                <span class="testing-card-time">Submitted ${testLocalTime(item.submitted_at)}</span>
            </div>
            ${item.description ? `<p class="testing-card-desc">${testEscape(item.description)}</p>` : ''}
            <div class="testing-scan-notes">
                <strong>Scan (${testEscape(item.scan_verdict || 'unknown')}):</strong> ${testEscape(item.scan_notes || '')}
            </div>
            <div class="testing-overall-stats">
                <div><span class="testing-stat-label">Starting wallet</span><span class="testing-stat-value">${testMoney(overall.starting_wallet)}</span></div>
                <div><span class="testing-stat-label">Final value</span><span class="testing-stat-value">${testMoney(overall.final)}</span></div>
                <div><span class="testing-stat-label">P&amp;L</span><span class="testing-stat-value ${overall.pnl >= 0 ? 'testing-pos' : 'testing-neg'}">${testMoney(overall.pnl)}</span></div>
                <div><span class="testing-stat-label">Return</span><span class="testing-stat-value ${overall.return_pct >= 0 ? 'testing-pos' : 'testing-neg'}">${testPct(overall.return_pct)}</span></div>
                <div><span class="testing-stat-label">Max drawdown</span><span class="testing-stat-value testing-neg">${testPct(overall.max_drawdown_pct)}</span></div>
            </div>
            <p class="control-helper">Tested ${testEscape(window_.start_date || '')} → ${testEscape(window_.end_date || '')}</p>
            <details class="testing-month-details">
                <summary>Month-by-month</summary>
                ${renderMonthTable(result.months)}
            </details>
            <div class="testing-card-actions">
                <button type="button" class="connections-btn primary" data-testing-action="add" data-id="${testEscape(item.id)}">Add to Strategy</button>
                <button type="button" class="connections-btn danger" data-testing-action="delete" data-id="${testEscape(item.id)}">Delete</button>
            </div>
        </div>`;
}

function renderProgressCard(item) {
    return `
        <div class="testing-card testing-card-progress" data-id="${testEscape(item.id)}">
            <div class="testing-card-head">
                <div>
                    <span class="testing-card-name">${testEscape(item.name)}</span>
                    <span class="testing-status-badge is-progress">${STATUS_LABELS[item.status] || item.status}</span>
                </div>
                <span class="testing-card-time">Submitted ${testLocalTime(item.submitted_at)}</span>
            </div>
        </div>`;
}

function renderHistoryCard(item) {
    const notes = item.status === 'rejected' ? (item.scan_notes || '') : (item.error || '');
    return `
        <div class="testing-card testing-card-history" data-id="${testEscape(item.id)}">
            <div class="testing-card-head">
                <div>
                    <span class="testing-card-name">${testEscape(item.name)}</span>
                    <span class="testing-status-badge is-${testEscape(item.status)}">${STATUS_LABELS[item.status] || item.status}</span>
                </div>
                <span class="testing-card-time">${testLocalTime(item.finished_at || item.submitted_at)}</span>
            </div>
            ${notes ? `<p class="testing-card-desc">${testEscape(notes)}</p>` : ''}
            <div class="testing-card-actions">
                <button type="button" class="connections-btn" data-testing-action="dismiss" data-id="${testEscape(item.id)}">Dismiss</button>
            </div>
        </div>`;
}

function renderTestingQueue(items) {
    const inProgress = items.filter((i) => ['queued', 'scanning', 'backtesting'].includes(i.status));
    const ready = items.filter((i) => i.status === 'ready');
    const history = items.filter((i) => ['rejected', 'error', 'added', 'deleted'].includes(i.status));

    const progressEl = document.getElementById('testingInProgressList');
    if (progressEl) {
        progressEl.innerHTML = inProgress.length
            ? inProgress.map(renderProgressCard).join('')
            : '<p class="testing-empty-state">Nothing queued right now.</p>';
    }
    const readyEl = document.getElementById('testingReadyList');
    if (readyEl) {
        readyEl.innerHTML = ready.length
            ? ready.map(renderReadyCard).join('')
            : '<p class="testing-empty-state">Nothing waiting on a decision.</p>';
    }
    const historyEl = document.getElementById('testingHistoryList');
    if (historyEl) {
        historyEl.innerHTML = history.length
            ? history.map(renderHistoryCard).join('')
            : '<p class="testing-empty-state">No history yet.</p>';
    }
}

async function refreshTestingQueue() {
    const view = document.getElementById('communityView');
    if (!view || view.style.display === 'none') return;
    try {
        const data = await testFetch('/queue');
        renderTestingQueue(data.items || []);
    } catch (error) {
        console.warn('Testing queue refresh failed:', error.message);
    }
}

function setTestingUploadStatus(message, isError) {
    const el = document.getElementById('testingUploadStatus');
    if (!el) return;
    el.textContent = message || '';
    el.classList.toggle('is-error', !!isError);
    el.classList.toggle('is-success', !isError && !!message);
}

async function handleTestingUploadSubmit(event) {
    event.preventDefault();
    if (_testingSubmitInFlight) return;
    const fileInput = document.getElementById('testingUploadFile');
    const nameInput = document.getElementById('testingUploadName');
    const descInput = document.getElementById('testingUploadDescription');
    const file = fileInput?.files?.[0];
    if (!file) {
        setTestingUploadStatus('Choose a file first.', true);
        return;
    }
    const formData = new FormData();
    formData.append('file', file);
    if (nameInput?.value.trim()) formData.append('name', nameInput.value.trim());
    if (descInput?.value.trim()) formData.append('description', descInput.value.trim());

    _testingSubmitInFlight = true;
    const submitBtn = document.getElementById('testingUploadSubmitBtn');
    if (submitBtn) submitBtn.disabled = true;
    setTestingUploadStatus('Uploading…', false);
    try {
        const row = await testFetch('/submit', { method: 'POST', body: formData });
        if (row.reference_installed) {
            // Built-in strategy reference: registered straight into the
            // Strategy catalog, no scan/backtest queue involved.
            setTestingUploadStatus(`Registered "${row.name}" into your Strategy catalog - open the Strategy page to allocate and run it.`, false);
            return;
        }
        setTestingUploadStatus(`Queued "${row.name}". It'll be scanned and backtested shortly.`, false);
        if (fileInput) fileInput.value = '';
        if (nameInput) nameInput.value = '';
        if (descInput) descInput.value = '';
        await refreshTestingQueue();
    } catch (error) {
        setTestingUploadStatus(error.message || 'Upload failed.', true);
    } finally {
        _testingSubmitInFlight = false;
        if (submitBtn) submitBtn.disabled = false;
    }
}

async function handleTestingQueueClick(event) {
    const button = event.target.closest('[data-testing-action]');
    if (!button) return;
    const action = button.dataset.testingAction;
    const id = button.dataset.id;
    if (!action || !id) return;
    button.disabled = true;
    try {
        if (action === 'add') {
            await testFetch(`/${encodeURIComponent(id)}/add`, { method: 'POST' });
        } else if (action === 'delete' || action === 'dismiss') {
            await testFetch(`/${encodeURIComponent(id)}/delete`, { method: 'POST' });
        }
        await refreshTestingQueue();
    } catch (error) {
        alert(error.message || 'Action failed.');
        button.disabled = false;
    }
}

function wireTestingPage() {
    if (window.__strategyTestingWired) return;
    window.__strategyTestingWired = true;
    document.getElementById('testingUploadForm')?.addEventListener('submit', handleTestingUploadSubmit);
    document.getElementById('testingInProgressList')?.addEventListener('click', handleTestingQueueClick);
    document.getElementById('testingReadyList')?.addEventListener('click', handleTestingQueueClick);
    document.getElementById('testingHistoryList')?.addEventListener('click', handleTestingQueueClick);

    if (!_testingPollTimer) {
        _testingPollTimer = setInterval(refreshTestingQueue, TEST_POLL_MS);
    }
}

const StrategyTestingPage = {
    onEnter() {
        wireTestingPage();
        refreshTestingQueue();
    },
};
window.StrategyTestingPage = StrategyTestingPage;

document.addEventListener('DOMContentLoaded', wireTestingPage);

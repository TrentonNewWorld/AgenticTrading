/*
 * prediction.js — the Prediction dashboard's one unified page: create a
 * strategy (code directly, or upload a file), watch it join the 5-real-day
 * waiting list, then Add/Delete once it's ready. Mirrors
 * strategy-testing.js's shape closely (see that file's own comments for the
 * reasoning behind several conventions reused verbatim here), adapted for
 * day-count progress instead of a scan/backtest spinner, and a fourth list
 * ("Trading now") for kept strategies that trade forward indefinitely.
 *
 * Named PRED_API_BASE, not the usual bare `API`/`API_BASE` -- see
 * strategy-testing.js's top comment for why a shared classic-script global
 * scope makes that a hard requirement, not a style preference.
 */
const PRED_API_BASE = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? window.location.origin
    : '';
const PRED_QUEUE_PATH = '/api/v1/prediction';
const PRED_POLL_MS = 10000; // slower than Testing's 4s -- a day-count only ever changes once per real day

let _predPollTimer = null;
let _predSubmitInFlight = false;

function predMoney(value) {
    if (value === null || value === undefined) return '—';
    return '$' + Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function predPct(value) {
    if (value === null || value === undefined) return '—';
    const n = Number(value);
    return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
}

function predLocalTime(iso) {
    if (!iso) return '—';
    try { return new Date(iso).toLocaleString(); } catch (_) { return iso; }
}

function predEscape(str) {
    return typeof window.escapeHtml === 'function' ? window.escapeHtml(str) : String(str ?? '');
}

const PRED_SOURCE_LABELS = { manual: 'Manual', agent: 'My Agent', upload: 'Upload' };

function predHeaders() {
    const headers = { 'Content-Type': 'application/json' };
    if (window.SESSION_ID) headers['x-session-id'] = window.SESSION_ID;
    if (window.BROWSER_OWNER_ID) headers['x-browser-id'] = window.BROWSER_OWNER_ID;
    if (typeof window.csrfHeaders === 'function') Object.assign(headers, window.csrfHeaders());
    return headers;
}

async function predFetch(path, options = {}) {
    const res = await fetch(PRED_API_BASE + PRED_QUEUE_PATH + path, {
        method: options.method || 'GET',
        headers: options.headers || predHeaders(),
        credentials: 'include',
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

function predEquitySummary(curve, initialCapital) {
    if (!curve || !curve.length) return null;
    const final = curve[curve.length - 1].equity;
    const pnl = final - initialCapital;
    const returnPct = initialCapital ? (pnl / initialCapital) * 100 : 0;
    return { final, pnl, returnPct };
}

function renderPredWaitingCard(item) {
    const summary = predEquitySummary(item.equity_curve, item.initial_capital);
    return `
        <div class="testing-card testing-card-progress" data-id="${predEscape(item.id)}">
            <div class="testing-card-head">
                <div>
                    <span class="testing-card-name">${predEscape(item.name)}</span>
                    <span class="testing-status-badge is-progress">Day ${item.day_count} of 5 &middot; ${PRED_SOURCE_LABELS[item.source_type] || item.source_type}</span>
                </div>
                <span class="testing-card-time">Submitted ${predLocalTime(item.submitted_at)}</span>
            </div>
            ${summary ? `<p class="control-helper">Running so far: <span class="${summary.pnl >= 0 ? 'testing-pos' : 'testing-neg'}">${predMoney(summary.pnl)} (${predPct(summary.returnPct)})</span> &mdash; not final, still on probation.</p>` : '<p class="control-helper">No trading day has completed yet.</p>'}
        </div>`;
}

function renderPredReadyCard(item) {
    const summary = predEquitySummary(item.equity_curve, item.initial_capital) || { final: item.initial_capital, pnl: 0, returnPct: 0 };
    return `
        <div class="testing-card testing-card-ready" data-id="${predEscape(item.id)}">
            <div class="testing-card-head">
                <div>
                    <span class="testing-card-name">${predEscape(item.name)}</span>
                    <span class="testing-status-badge is-ready">Ready &middot; ${PRED_SOURCE_LABELS[item.source_type] || item.source_type}</span>
                </div>
                <span class="testing-card-time">Ready ${predLocalTime(item.ready_at)}</span>
            </div>
            ${item.description ? `<p class="testing-card-desc">${predEscape(item.description)}</p>` : ''}
            <div class="testing-overall-stats">
                <div><span class="testing-stat-label">Starting wallet</span><span class="testing-stat-value">${predMoney(item.initial_capital)}</span></div>
                <div><span class="testing-stat-label">Final value (5 days)</span><span class="testing-stat-value">${predMoney(summary.final)}</span></div>
                <div><span class="testing-stat-label">P&amp;L</span><span class="testing-stat-value ${summary.pnl >= 0 ? 'testing-pos' : 'testing-neg'}">${predMoney(summary.pnl)}</span></div>
                <div><span class="testing-stat-label">Return</span><span class="testing-stat-value ${summary.returnPct >= 0 ? 'testing-pos' : 'testing-neg'}">${predPct(summary.returnPct)}</span></div>
                <div><span class="testing-stat-label">Fees paid</span><span class="testing-stat-value">${predMoney(item.total_fees_paid)}</span></div>
            </div>
            <div class="testing-card-actions">
                <button type="button" class="connections-btn primary" data-pred-action="add" data-id="${predEscape(item.id)}">Add &mdash; keep trading it</button>
                <button type="button" class="connections-btn danger" data-pred-action="delete" data-id="${predEscape(item.id)}">Delete</button>
            </div>
        </div>`;
}

function renderPredAddedCard(item) {
    const summary = predEquitySummary(item.equity_curve, item.initial_capital) || { final: item.initial_capital, pnl: 0, returnPct: 0 };
    return `
        <div class="testing-card testing-card-ready" data-id="${predEscape(item.id)}">
            <div class="testing-card-head">
                <div>
                    <span class="testing-card-name">${predEscape(item.name)}</span>
                    <span class="testing-status-badge is-ready">Day ${item.day_count} &middot; trading</span>
                </div>
                <span class="testing-card-time">Added ${predLocalTime(item.ready_at)}</span>
            </div>
            <div class="testing-overall-stats">
                <div><span class="testing-stat-label">Current value</span><span class="testing-stat-value">${predMoney(summary.final)}</span></div>
                <div><span class="testing-stat-label">P&amp;L</span><span class="testing-stat-value ${summary.pnl >= 0 ? 'testing-pos' : 'testing-neg'}">${predMoney(summary.pnl)}</span></div>
                <div><span class="testing-stat-label">Return</span><span class="testing-stat-value ${summary.returnPct >= 0 ? 'testing-pos' : 'testing-neg'}">${predPct(summary.returnPct)}</span></div>
                <div><span class="testing-stat-label">Fees paid</span><span class="testing-stat-value">${predMoney(item.total_fees_paid)}</span></div>
            </div>
        </div>`;
}

const PRED_HISTORY_LABELS = { rejected: 'Rejected', error: 'Error', deleted: 'Deleted' };

function renderPredHistoryCard(item) {
    const notes = item.status === 'rejected' ? (item.error || '') : (item.error || '');
    return `
        <div class="testing-card testing-card-history" data-id="${predEscape(item.id)}">
            <div class="testing-card-head">
                <div>
                    <span class="testing-card-name">${predEscape(item.name)}</span>
                    <span class="testing-status-badge is-${predEscape(item.status)}">${PRED_HISTORY_LABELS[item.status] || item.status}</span>
                </div>
                <span class="testing-card-time">${predLocalTime(item.submitted_at)}</span>
            </div>
            ${notes ? `<p class="testing-card-desc">${predEscape(notes)}</p>` : ''}
        </div>`;
}

function renderPredictionQueue(items) {
    const waiting = items.filter((i) => i.status === 'waiting');
    const ready = items.filter((i) => i.status === 'ready');
    const added = items.filter((i) => i.status === 'added');
    const history = items.filter((i) => ['rejected', 'error', 'deleted'].includes(i.status));

    const waitingEl = document.getElementById('predictionWaitingList');
    if (waitingEl) {
        waitingEl.innerHTML = waiting.length
            ? waiting.map(renderPredWaitingCard).join('')
            : '<p class="testing-empty-state">Nothing waiting right now.</p>';
    }
    const readyEl = document.getElementById('predictionReadyList');
    if (readyEl) {
        readyEl.innerHTML = ready.length
            ? ready.map(renderPredReadyCard).join('')
            : '<p class="testing-empty-state">Nothing waiting on a decision.</p>';
    }
    const addedEl = document.getElementById('predictionAddedList');
    if (addedEl) {
        addedEl.innerHTML = added.length
            ? added.map(renderPredAddedCard).join('')
            : '<p class="testing-empty-state">Nothing added yet.</p>';
    }
    const historyEl = document.getElementById('predictionHistoryList');
    if (historyEl) {
        historyEl.innerHTML = history.length
            ? history.map(renderPredHistoryCard).join('')
            : '<p class="testing-empty-state">No history yet.</p>';
    }
}

async function refreshPredictionQueue() {
    const view = document.getElementById('predictionView');
    if (!view || view.style.display === 'none') return;
    try {
        const data = await predFetch('/strategies');
        renderPredictionQueue(data.strategies || []);
    } catch (error) {
        console.warn('Prediction queue refresh failed:', error.message);
    }
}

async function loadPredictionNotice() {
    const el = document.getElementById('predictionFiveDayNotice');
    if (!el) return;
    try {
        const data = await predFetch('/notice');
        el.textContent = data.notice;
    } catch (error) {
        el.textContent = 'This strategy will paper-trade forward for 5 real days before results show.';
    }
}

function setPredSubmitStatus(message, isError) {
    const el = document.getElementById('predictionSubmitStatus');
    if (!el) return;
    el.textContent = message || '';
    el.classList.toggle('is-error', !!isError);
    el.classList.toggle('is-success', !isError && !!message);
}

function readFileAsText(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ''));
        reader.onerror = () => reject(new Error('Could not read the file.'));
        reader.readAsText(file);
    });
}

async function handlePredictionSubmit(event) {
    event.preventDefault();
    if (_predSubmitInFlight) return;
    const nameInput = document.getElementById('predictionSubmitName');
    const descInput = document.getElementById('predictionSubmitDescription');
    const fileInput = document.getElementById('predictionSubmitFile');
    const codeInput = document.getElementById('predictionSubmitCode');

    const name = nameInput?.value.trim();
    if (!name) {
        setPredSubmitStatus('Name your strategy first.', true);
        return;
    }

    const file = fileInput?.files?.[0];
    let code = codeInput?.value.trim() || '';
    let isUpload = false;
    if (file) {
        try {
            code = (await readFileAsText(file)).trim();
            isUpload = true;
        } catch (error) {
            setPredSubmitStatus(error.message, true);
            return;
        }
    }
    if (!code) {
        setPredSubmitStatus('Write a decide_prediction() function, or choose a file.', true);
        return;
    }

    _predSubmitInFlight = true;
    const submitBtn = document.getElementById('predictionSubmitBtn');
    if (submitBtn) submitBtn.disabled = true;
    setPredSubmitStatus('Submitting…', false);
    try {
        const row = await predFetch(isUpload ? '/strategies/upload' : '/strategies/manual', {
            method: 'POST',
            body: JSON.stringify({ name, description: descInput?.value.trim() || '', code }),
        });
        setPredSubmitStatus(`"${row.name}" joined the 5-day waiting list.`, false);
        if (nameInput) nameInput.value = '';
        if (descInput) descInput.value = '';
        if (fileInput) fileInput.value = '';
        if (codeInput) codeInput.value = '';
        await refreshPredictionQueue();
    } catch (error) {
        setPredSubmitStatus(error.message || 'Submit failed.', true);
    } finally {
        _predSubmitInFlight = false;
        if (submitBtn) submitBtn.disabled = false;
    }
}

async function handlePredictionQueueClick(event) {
    const button = event.target.closest('[data-pred-action]');
    if (!button) return;
    const action = button.dataset.predAction;
    const id = button.dataset.id;
    if (!action || !id) return;
    button.disabled = true;
    try {
        if (action === 'add') {
            await predFetch(`/strategies/${encodeURIComponent(id)}/add`, { method: 'POST' });
        } else if (action === 'delete') {
            await predFetch(`/strategies/${encodeURIComponent(id)}/delete`, { method: 'POST' });
        }
        await refreshPredictionQueue();
    } catch (error) {
        alert(error.message || 'Action failed.');
        button.disabled = false;
    }
}

function wirePredictionPage() {
    if (window.__predictionWired) return;
    window.__predictionWired = true;
    document.getElementById('predictionSubmitForm')?.addEventListener('submit', handlePredictionSubmit);
    document.getElementById('predictionWaitingList')?.addEventListener('click', handlePredictionQueueClick);
    document.getElementById('predictionReadyList')?.addEventListener('click', handlePredictionQueueClick);
    document.getElementById('predictionAddedList')?.addEventListener('click', handlePredictionQueueClick);

    if (!_predPollTimer) {
        _predPollTimer = setInterval(refreshPredictionQueue, PRED_POLL_MS);
    }
}

const PredictionPage = {
    onEnter() {
        wirePredictionPage();
        loadPredictionNotice();
        refreshPredictionQueue();
    },
};
window.PredictionPage = PredictionPage;

document.addEventListener('DOMContentLoaded', wirePredictionPage);

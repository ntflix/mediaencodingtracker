'use strict';

let _authHeader = '';
let currentPage = '';
let _pollTimer = null;
let _sseAbort = null;

let _appSettings = null;
const _setupStorageKey = 'met.setup.checked.v1';

let _chartCodec = null;
let _chartStorage = null;

let _filesPage = 1;
let _filesPerPage = 50;
let _filesCodec = '';
let _filesMissing = 'all';
let _filesSortBy = 'filename';
let _filesSortDir = 'asc';
let _allCodecs = [];

let _queuePage = 1;
let _queuePerPage = 50;
let _queueStatus = '';
let _queueSortBy = 'created_at';
let _queueSortDir = 'desc';

const CODEC_COLORS = {
    h264: '#198754', hevc: '#dc3545', vp9: '#fd7e14',
    vp8: '#ffc107', av1: '#6f42c1', mpeg4: '#0dcaf0',
    mpeg2video: '#6c757d', vc1: '#adb5bd', unknown: '#495057',
};

function codecColor(c) { return CODEC_COLORS[c] || '#6c757d'; }

function setCredentials(user, pass) {
    _authHeader = 'Basic ' + btoa(`${user}:${pass}`);
    sessionStorage.setItem('auth', _authHeader);
}

function loadCredentials() {
    _authHeader = sessionStorage.getItem('auth') || '';
}

function clearCredentials() {
    _authHeader = '';
    sessionStorage.removeItem('auth');
    localStorage.removeItem(_setupStorageKey);
}

async function api(method, path, body = null) {
    const opts = {
        method,
        headers: { Authorization: _authHeader, 'Content-Type': 'application/json' },
    };
    if (body !== null) opts.body = JSON.stringify(body);

    const res = await fetch(path, opts);
    if (res.status === 401) {
        showLogin();
        throw new Error('Unauthorised');
    }
    if (!res.ok) {
        const text = await res.text();
        throw new Error(`${res.status} ${text}`);
    }
    if (res.status === 204) return null;
    return res.json();
}

const get = (p) => api('GET', p);
const post = (p, b) => api('POST', p, b);
const del = (p) => api('DELETE', p);

function showLogin() {
    document.getElementById('login-overlay').classList.remove('d-none');
    document.getElementById('app-shell').classList.add('d-none');
    hideSetupOverlay();
}

function hideLogin() {
    document.getElementById('login-overlay').classList.add('d-none');
    document.getElementById('app-shell').classList.remove('d-none');
}

function showSetupOverlay() {
    document.getElementById('setup-overlay').classList.remove('d-none');
}

function hideSetupOverlay() {
    document.getElementById('setup-overlay').classList.add('d-none');
}

function showPage(name) {
    document.querySelectorAll('.page').forEach(el => el.classList.add('d-none'));
    document.getElementById(`page-${name}`).classList.remove('d-none');

    document.querySelectorAll('[data-page]').forEach(a => {
        a.classList.toggle('active', a.dataset.page === name);
    });

    clearInterval(_pollTimer);
    currentPage = name;

    if (name === 'dashboard') loadDashboard();
    if (name === 'files') loadFiles(1);
    if (name === 'queue') {
        loadQueue(1);
        _pollTimer = setInterval(() => loadQueue(_queuePage, true), 4000);
    }
    if (name === 'settings') loadSettings();
}

function formatBytes(b) {
    if (b >= 1e12) return (b / 1e12).toFixed(1) + ' TB';
    if (b >= 1e9) return (b / 1e9).toFixed(1) + ' GB';
    if (b >= 1e6) return (b / 1e6).toFixed(1) + ' MB';
    return b + ' B';
}

function fmtDur(s) {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = Math.floor(s % 60);
    return h ? `${h}h ${m}m` : `${m}m ${sec}s`;
}

function showAlert(id, type, msg) {
    const el = document.getElementById(id);
    el.className = `alert alert-${type}`;
    el.textContent = msg;
    el.classList.remove('d-none');
    setTimeout(() => el.classList.add('d-none'), 6000);
}

function card(icon, label, value, color) {
    return `<div class="col-6 col-md-4 col-xl-2">
    <div class="card text-center">
      <div class="card-body p-3">
        <div class="text-${color} fs-3 mb-1"><i class="bi ${icon}"></i></div>
        <div class="fs-4 fw-bold">${value}</div>
        <div class="small text-muted">${label}</div>
      </div>
    </div>
  </div>`;
}

async function loadDashboard() {
    const s = await get('/api/stats');

    document.getElementById('stat-cards').innerHTML = `
    ${card('bi-files', 'Total files', s.total_files, 'primary')}
    ${card('bi-hdd', 'Total size', formatBytes(s.total_size_bytes), 'info')}
    ${card('bi-arrow-repeat', 'Pending jobs', s.jobs_pending, 'warning')}
    ${card('bi-check-circle', 'Completed jobs', s.jobs_completed, 'success')}
    ${card('bi-x-circle', 'Failed jobs', s.jobs_failed, 'danger')}
    ${card('bi-question-circle', 'Missing files', s.missing_files, 'secondary')}
  `;

    const codecLabels = Object.keys(s.by_codec_count);
    const codecCounts = Object.values(s.by_codec_count);

    if (_chartCodec) _chartCodec.destroy();
    _chartCodec = new Chart(document.getElementById('chart-codec'), {
        type: 'doughnut',
        data: {
            labels: codecLabels,
            datasets: [{ data: codecCounts, backgroundColor: codecLabels.map(codecColor) }],
        },
        options: { plugins: { legend: { position: 'right' } } },
    });

    const storLabels = Object.keys(s.by_codec_bytes);
    const storBytes = Object.values(s.by_codec_bytes).map(b => +(b / 1e9).toFixed(2));

    if (_chartStorage) _chartStorage.destroy();
    _chartStorage = new Chart(document.getElementById('chart-storage'), {
        type: 'bar',
        data: {
            labels: storLabels,
            datasets: [{ label: 'GB', data: storBytes, backgroundColor: storLabels.map(codecColor) }],
        },
        options: { plugins: { legend: { display: false } } },
    });
}

async function loadFiles(page, silent = false) {
    _filesPage = page;
    _filesCodec = document.getElementById('filter-codec').value;
    _filesMissing = document.getElementById('filter-missing').value;

    const qs = new URLSearchParams({
        page: String(_filesPage),
        per_page: String(_filesPerPage),
        sort_by: _filesSortBy,
        sort_dir: _filesSortDir,
    });

    if (_filesCodec) qs.set('codec', _filesCodec);
    if (_filesMissing === 'ok') qs.set('missing', 'false');
    if (_filesMissing === 'missing') qs.set('missing', 'true');

    let data;
    try {
        data = await get(`/api/files?${qs}`);
    } catch (e) {
        if (!silent) showAlert('files-alert', 'danger', e.message);
        return;
    }

    if (_allCodecs.length === 0) {
        const stats = await get('/api/stats');
        _allCodecs = Object.keys(stats.by_codec_count).sort((a, b) => a.localeCompare(b));
        const sel = document.getElementById('filter-codec');
        _allCodecs.forEach(c => {
            const opt = document.createElement('option');
            opt.value = c;
            opt.textContent = c;
            sel.appendChild(opt);
        });
    }

    const tbody = document.getElementById('files-tbody');
    tbody.innerHTML = data.items.map(f => `
    <tr>
      <td><input type="checkbox" class="form-check-input file-check" data-id="${f.id}" /></td>
      <td class="text-break" style="max-width:300px" title="${f.path}">${f.filename}</td>
      <td><span class="badge codec-badge" style="background:${codecColor(f.video_codec)}">${f.video_codec}</span></td>
      <td>${f.width && f.height ? f.width + 'x' + f.height : '-'}</td>
      <td>${f.duration_seconds ? fmtDur(f.duration_seconds) : '-'}</td>
      <td>${formatBytes(f.size_bytes)}</td>
      <td>${f.is_missing ? '<span class="badge bg-danger">missing</span>' : '<span class="badge bg-success">ok</span>'}</td>
    </tr>
  `).join('');

    renderPagination('files-pagination', data.total, _filesPage, _filesPerPage, loadFiles);
    updateConvertBtn();
    syncSortHeaderState('files-thead', _filesSortBy, _filesSortDir, _filesCodec || _filesMissing !== 'all');
}

function updateConvertBtn() {
    const count = document.querySelectorAll('.file-check:checked').length;
    const btn = document.getElementById('convert-selected-btn');
    btn.disabled = count === 0;
    btn.textContent = count ? `Convert ${count} selected` : 'Convert selected';
}

async function loadQueue(page, silent = false) {
    _queuePage = page;
    _queueStatus = document.getElementById('filter-status').value;

    const qs = new URLSearchParams({
        page: String(_queuePage),
        per_page: String(_queuePerPage),
        sort_by: _queueSortBy,
        sort_dir: _queueSortDir,
    });
    if (_queueStatus) qs.set('status', _queueStatus);

    let data;
    try {
        data = await get(`/api/jobs?${qs}`);
    } catch (e) {
        if (!silent) console.error(e);
        return;
    }

    const tbody = document.getElementById('queue-tbody');
    tbody.innerHTML = data.items.map(j => `
    <tr>
      <td>${j.id}</td>
      <td class="text-break" style="max-width:260px">${j.media_file ? j.media_file.filename : j.media_file_id}</td>
      <td>${j.quality}</td>
      <td><span class="status-${j.status}"><i class="bi ${statusIcon(j.status)} me-1"></i>${j.status}</span></td>
      <td style="min-width:120px">
        ${j.status === 'running'
            ? `<div class="progress mt-1"><div class="progress-bar progress-bar-striped progress-bar-animated" style="width:${Math.round(j.progress * 100)}%"></div></div><span class="small">${Math.round(j.progress * 100)}%</span>`
            : j.status === 'completed' ? '100%' : '-'}
      </td>
      <td>${j.started_at ? new Date(j.started_at).toLocaleString() : '-'}</td>
      <td>
        ${['pending', 'running'].includes(j.status)
            ? `<button class="btn btn-xs btn-sm btn-outline-warning" onclick="cancelJob(${j.id})"><i class="bi bi-stop-circle"></i></button>`
            : `<button class="btn btn-xs btn-sm btn-outline-danger" onclick="deleteJob(${j.id})"><i class="bi bi-trash"></i></button>`}
      </td>
    </tr>
  `).join('');

    renderPagination('queue-pagination', data.total, _queuePage, _queuePerPage, loadQueue);
    syncSortHeaderState('queue-thead', _queueSortBy, _queueSortDir, !!_queueStatus);
}

function statusIcon(s) {
    return {
        pending: 'bi-clock',
        running: 'bi-arrow-repeat',
        completed: 'bi-check-circle',
        failed: 'bi-x-circle',
        cancelled: 'bi-slash-circle',
    }[s] || 'bi-question';
}

async function loadSettings() {
    if (!_appSettings) _appSettings = await get('/api/settings');
    const s = _appSettings;

    document.getElementById('s-scan-enabled').textContent = s.auto_scan_enabled ? 'yes' : 'no';
    document.getElementById('s-scan-cron').textContent = s.scan_schedule;
    document.getElementById('s-auto-convert').textContent = s.auto_convert_enabled ? 'yes' : 'no';
    document.getElementById('s-convert-cron').textContent = s.convert_schedule;
    document.getElementById('s-quality').textContent = s.default_quality;
    document.getElementById('s-delete-orig').textContent = s.delete_original_after_convert ? 'yes' : 'no';

    document.getElementById('destination-codec-box').innerHTML =
        `<span class="badge codec-badge" style="background:${codecColor(s.destination_codec)}">${s.destination_codec}</span>`;

    const sourceBox = document.getElementById('source-codecs-box');
    sourceBox.innerHTML = s.source_codecs
        .map(c => `<span class="badge codec-badge" style="background:${codecColor(c)}">${c}</span>`)
        .join('');
}

async function runSetupCheck() {
    const checksEl = document.getElementById('setup-checks');
    const alertEl = document.getElementById('setup-alert');
    const continueBtn = document.getElementById('setup-continue-btn');

    checksEl.innerHTML = '<li class="list-group-item">Checking compose settings...</li>';
    alertEl.className = 'alert d-none';
    continueBtn.disabled = true;

    try {
        const result = await get('/api/settings/setup-check');
        checksEl.innerHTML = result.checks.map(c => `
      <li class="list-group-item d-flex justify-content-between align-items-start">
        <div>
          <div class="fw-semibold">${c.key}</div>
          <div class="small text-muted">${c.details}</div>
        </div>
        <span class="badge ${c.configured ? 'bg-success' : 'bg-danger'}">${c.configured ? 'ok' : 'missing'}</span>
      </li>
    `).join('');

        if (result.ready) {
            alertEl.className = 'alert alert-success';
            alertEl.textContent = 'Setup check passed. Required docker-compose settings were found.';
            continueBtn.disabled = false;
        } else {
            alertEl.className = 'alert alert-danger';
            alertEl.textContent = 'Setup check failed. Update docker-compose.yml and run the check again.';
            continueBtn.disabled = true;
        }
    } catch (e) {
        alertEl.className = 'alert alert-danger';
        alertEl.textContent = `Setup check failed: ${e.message}`;
        checksEl.innerHTML = '';
    }
}

function renderPagination(containerId, total, currentPage, perPage, loader) {
    const totalPages = Math.ceil(total / perPage) || 1;
    const el = document.getElementById(containerId);
    if (totalPages <= 1) {
        el.innerHTML = '';
        return;
    }

    const pages = [];
    for (let i = Math.max(1, currentPage - 2); i <= Math.min(totalPages, currentPage + 2); i++) {
        pages.push(`<li class="page-item ${i === currentPage ? 'active' : ''}"><a class="page-link" href="#" data-page="${i}">${i}</a></li>`);
    }

    el.innerHTML = `<ul class="pagination pagination-sm mt-3">
    <li class="page-item ${currentPage === 1 ? 'disabled' : ''}"><a class="page-link" href="#" data-page="${currentPage - 1}">&lt;</a></li>
    ${pages.join('')}
    <li class="page-item ${currentPage === totalPages ? 'disabled' : ''}"><a class="page-link" href="#" data-page="${currentPage + 1}">&gt;</a></li>
  </ul>`;

    el.querySelectorAll('[data-page]').forEach(a => {
        a.addEventListener('click', e => {
            e.preventDefault();
            const p = +a.dataset.page;
            if (p >= 1 && p <= totalPages) loader(p);
        });
    });
}

function syncSortHeaderState(theadId, sortBy, sortDir, filterActive) {
    document.querySelectorAll(`#${theadId} th`).forEach(th => {
        th.classList.remove('sort-active', 'filter-active');

        if (th.dataset.sort === sortBy) {
            th.classList.add('sort-active');
            const icon = th.querySelector('.sort-icon');
            if (icon) icon.className = `bi ${sortDir === 'asc' ? 'bi-sort-up-alt' : 'bi-sort-down-alt'} sort-icon`;
        }

        if (th.dataset.filterCol && filterActive) {
            th.classList.add('filter-active');
        }
    });
}

function cycleFileCodecFilter() {
    const sel = document.getElementById('filter-codec');
    const options = Array.from(sel.options).map(o => o.value);
    const idx = options.indexOf(sel.value);
    sel.value = options[(idx + 1) % options.length] || '';
    loadFiles(1);
}

function cycleFileMissingFilter() {
    const sel = document.getElementById('filter-missing');
    const order = ['all', 'ok', 'missing'];
    const idx = order.indexOf(sel.value);
    sel.value = order[(idx + 1) % order.length];
    loadFiles(1);
}

function cycleQueueStatusFilter() {
    const sel = document.getElementById('filter-status');
    const order = ['', 'pending', 'running', 'completed', 'failed', 'cancelled'];
    const idx = order.indexOf(sel.value);
    sel.value = order[(idx + 1) % order.length];
    loadQueue(1);
}

function bindColumnInteractions() {
    document.getElementById('files-thead').addEventListener('click', e => {
        const th = e.target.closest('th');
        if (!th) return;

        if (th.dataset.filterCol === 'codec') {
            cycleFileCodecFilter();
            return;
        }
        if (th.dataset.filterCol === 'missing') {
            cycleFileMissingFilter();
            return;
        }
        if (th.dataset.sort) {
            if (_filesSortBy === th.dataset.sort) {
                _filesSortDir = _filesSortDir === 'asc' ? 'desc' : 'asc';
            } else {
                _filesSortBy = th.dataset.sort;
                _filesSortDir = 'asc';
            }
            loadFiles(1);
        }
    });

    document.getElementById('queue-thead').addEventListener('click', e => {
        const th = e.target.closest('th');
        if (!th) return;

        if (th.dataset.filterCol === 'status') {
            cycleQueueStatusFilter();
            return;
        }
        if (th.dataset.sort) {
            if (_queueSortBy === th.dataset.sort) {
                _queueSortDir = _queueSortDir === 'asc' ? 'desc' : 'asc';
            } else {
                _queueSortBy = th.dataset.sort;
                _queueSortDir = 'desc';
            }
            loadQueue(1);
        }
    });
}

async function cancelJob(id) {
    try {
        await post(`/api/jobs/${id}/cancel`);
        loadQueue(_queuePage);
    } catch (e) {
        alert(e.message);
    }
}

async function deleteJob(id) {
    try {
        await del(`/api/jobs/${id}`);
        loadQueue(_queuePage);
    } catch (e) {
        alert(e.message);
    }
}

window.cancelJob = cancelJob;
window.deleteJob = deleteJob;

function startSSE() {
    if (_sseAbort) _sseAbort.abort();
    _sseAbort = new AbortController();

    (async () => {
        try {
            const res = await fetch('/api/events', {
                headers: { Authorization: _authHeader },
                signal: _sseAbort.signal,
            });
            if (!res.ok || !res.body) {
                setConnIndicator(false);
                return;
            }
            setConnIndicator(true);

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { value, done } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });

                const parts = buffer.split('\n\n');
                buffer = parts.pop() || '';

                for (const chunk of parts) {
                    const line = chunk.split('\n').find(l => l.startsWith('data: '));
                    if (!line) continue;
                    try {
                        updateStatusBar(JSON.parse(line.slice(6)));
                    } catch {
                        // Ignore malformed frame.
                    }
                }
            }
        } catch (err) {
            if (err.name !== 'AbortError') {
                setConnIndicator(false);
                setTimeout(() => { if (_authHeader) startSSE(); }, 5000);
            }
        }
    })();
}

function stopSSE() {
    if (_sseAbort) {
        _sseAbort.abort();
        _sseAbort = null;
    }
    setConnIndicator(false);
}

function setConnIndicator(online) {
    const el = document.getElementById('sb-conn');
    if (!el) return;
    el.innerHTML = online
        ? '<i class="bi bi-circle-fill text-success" style="font-size:.45rem" title="Live updates connected"></i>'
        : '<i class="bi bi-circle-fill text-secondary" style="font-size:.45rem" title="Reconnecting..."></i>';
}

function timeAgo(date) {
    const s = Math.round((Date.now() - date.getTime()) / 1000);
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
}

function updateStatusBar(data) {
    const scan = data.scan;
    const worker = data.worker;
    const bar = document.getElementById('status-bar');

    const scanIcon = document.getElementById('sb-scan-icon');
    const scanText = document.getElementById('sb-scan-text');
    const scanProg = document.getElementById('sb-scan-progress');
    const scanBar = document.getElementById('sb-scan-bar');
    const scanPct = document.getElementById('sb-scan-pct');

    if (scan.running) {
        const pct = scan.total > 0 ? Math.round((scan.probed / scan.total) * 100) : 0;
        scanIcon.innerHTML = '<span class="spinner-border text-info" style="width:.7rem;height:.7rem;border-width:2px"></span>';
        scanText.innerHTML = `<span class="text-info">Scanning: ${scan.probed} / ${scan.total} files</span>`;
        scanBar.style.width = pct + '%';
        scanPct.textContent = pct + '%';
        scanProg.classList.remove('d-none');
        bar.classList.add('scanning');
    } else if (scan.error) {
        scanIcon.innerHTML = '<i class="bi bi-exclamation-circle-fill text-danger" style="font-size:.7rem"></i>';
        scanText.innerHTML = `<span class="text-danger">Scan error: ${scan.error}</span>`;
        scanProg.classList.add('d-none');
        bar.classList.remove('scanning');
        bar.classList.add('error');
    } else if (scan.last_at) {
        const ago = timeAgo(new Date(scan.last_at));
        scanIcon.innerHTML = '<i class="bi bi-check-circle-fill text-success" style="font-size:.7rem"></i>';
        scanText.innerHTML = `Last scan <span class="text-muted">${ago}</span> - <span class="text-success">+${scan.new}</span> <span class="text-info">~${scan.updated}</span> <span class="text-warning">${scan.missing} missing</span>`;
        scanProg.classList.add('d-none');
        bar.classList.remove('scanning', 'error');
    } else {
        scanIcon.innerHTML = '<i class="bi bi-dash-circle text-muted" style="font-size:.7rem"></i>';
        scanText.textContent = 'No scan yet';
        scanProg.classList.add('d-none');
        bar.classList.remove('scanning', 'error');
    }

    const workerIcon = document.getElementById('sb-worker-icon');
    const workerText = document.getElementById('sb-worker-text');
    const jobProg = document.getElementById('sb-job-progress');
    const jobBar = document.getElementById('sb-job-bar');
    const jobPct = document.getElementById('sb-job-pct');

    if (worker.job_id) {
        const pct = Math.round(worker.progress * 100);
        const queue = worker.queue_size > 0 ? ` · <span class="text-muted">${worker.queue_size} queued</span>` : '';
        workerIcon.innerHTML = '<span class="spinner-border text-warning" style="width:.7rem;height:.7rem;border-width:2px"></span>';
        workerText.innerHTML = `<span class="text-warning">Job #${worker.job_id} (${pct}%)</span>${queue}`;
        jobBar.style.width = pct + '%';
        jobPct.textContent = pct + '%';
        jobProg.classList.remove('d-none');
        bar.classList.add('converting');
    } else if (worker.queue_size > 0) {
        workerIcon.innerHTML = '<i class="bi bi-hourglass-split text-warning" style="font-size:.7rem"></i>';
        workerText.innerHTML = `<span class="text-warning">${worker.queue_size} job(s) queued</span>`;
        jobProg.classList.add('d-none');
        bar.classList.remove('converting');
    } else {
        workerIcon.innerHTML = '<i class="bi bi-cpu text-muted" style="font-size:.7rem"></i>';
        workerText.textContent = 'Idle';
        jobProg.classList.add('d-none');
        bar.classList.remove('converting');
    }
}

document.getElementById('login-btn').addEventListener('click', async () => {
    const user = document.getElementById('login-user').value.trim();
    const pass = document.getElementById('login-pass').value;
    setCredentials(user, pass);

    try {
        await get('/api/stats');
        hideLogin();

        const setupDone = localStorage.getItem(_setupStorageKey) === '1';
        if (!setupDone) {
            showSetupOverlay();
            await runSetupCheck();
        } else {
            hideSetupOverlay();
            showPage('dashboard');
        }

        _appSettings = await get('/api/settings');
        startSSE();
    } catch {
        document.getElementById('login-error').textContent = 'Invalid username or password.';
        document.getElementById('login-error').classList.remove('d-none');
        clearCredentials();
    }
});

document.getElementById('login-pass').addEventListener('keydown', e => {
    if (e.key === 'Enter') document.getElementById('login-btn').click();
});

document.getElementById('logout-btn').addEventListener('click', () => {
    stopSSE();
    clearCredentials();
    showLogin();
});

document.getElementById('setup-recheck-btn').addEventListener('click', () => runSetupCheck());

document.getElementById('setup-continue-btn').addEventListener('click', () => {
    localStorage.setItem(_setupStorageKey, '1');
    hideSetupOverlay();
    showPage('dashboard');
});

document.querySelectorAll('[data-page]').forEach(a => {
    a.addEventListener('click', e => {
        e.preventDefault();
        showPage(a.dataset.page);
    });
});

document.getElementById('select-all').addEventListener('change', e => {
    document.querySelectorAll('.file-check').forEach(cb => { cb.checked = e.target.checked; });
    updateConvertBtn();
});

document.getElementById('files-tbody').addEventListener('change', () => updateConvertBtn());

document.getElementById('convert-selected-btn').addEventListener('click', async () => {
    const ids = [...document.querySelectorAll('.file-check:checked')].map(cb => +cb.dataset.id);
    if (ids.length === 0) return;

    if (!_appSettings) _appSettings = await get('/api/settings');

    try {
        const jobs = await post('/api/jobs', {
            media_file_ids: ids,
            quality: _appSettings.default_quality,
            delete_original: _appSettings.delete_original_after_convert,
        });
        showAlert('files-alert', 'success', `Created ${jobs.length} job(s). Check the Queue tab.`);
        document.getElementById('select-all').checked = false;
        loadFiles(_filesPage);
    } catch (e) {
        showAlert('files-alert', 'danger', e.message);
    }
});

document.getElementById('scan-btn').addEventListener('click', async () => {
    const btn = document.getElementById('scan-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Scanning...';
    try {
        const r = await post('/api/files/scan');
        showAlert('files-alert', 'success', `Scan complete: ${r.new_files} new, ${r.updated_files} updated, ${r.missing_files} missing.`);
        loadFiles(1);
    } catch (e) {
        showAlert('files-alert', 'danger', e.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-search me-1"></i>Scan now';
    }
});

document.getElementById('filter-codec').addEventListener('change', () => loadFiles(1));
document.getElementById('filter-missing').addEventListener('change', () => loadFiles(1));
document.getElementById('filter-status').addEventListener('change', () => loadQueue(1));

document.getElementById('scan-now-btn').addEventListener('click', async () => {
    const btn = document.getElementById('scan-now-btn');
    btn.disabled = true;
    try {
        const r = await post('/api/settings/scan-now');
        const el = document.getElementById('scan-result');
        el.textContent = r.message;
        el.classList.remove('d-none');
        setTimeout(() => el.classList.add('d-none'), 5000);
    } catch (e) {
        alert(e.message);
    } finally {
        btn.disabled = false;
    }
});

document.getElementById('convert-now-btn').addEventListener('click', async () => {
    const btn = document.getElementById('convert-now-btn');
    btn.disabled = true;
    try {
        const r = await post('/api/settings/convert-now');
        const el = document.getElementById('convert-result');
        el.textContent = r.message;
        el.classList.remove('d-none');
        setTimeout(() => el.classList.add('d-none'), 5000);
    } catch (e) {
        alert(e.message);
    } finally {
        btn.disabled = false;
    }
});

bindColumnInteractions();

loadCredentials();
if (_authHeader) {
    get('/api/stats')
        .then(async () => {
            hideLogin();
            _appSettings = await get('/api/settings');
            const setupDone = localStorage.getItem(_setupStorageKey) === '1';
            if (!setupDone) {
                showSetupOverlay();
                await runSetupCheck();
            } else {
                hideSetupOverlay();
                showPage('dashboard');
            }
            startSSE();
        })
        .catch(() => showLogin());
} else {
    showLogin();
}

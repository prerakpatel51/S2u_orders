const serviceList = document.getElementById('service-list');
const runList = document.getElementById('run-list');
const latencyList = document.getElementById('latency-list');
const logList = document.getElementById('log-list');
const stockStoreList = document.getElementById('stock-store-list');
const endpointHealthList = document.getElementById('endpoint-health-list');
let operationsTimer;
let operationsData;

const serviceLabels = {stores: 'Stores', products: 'Products & barcodes', stocks: 'Stock changes', stock_reconciliation: 'Nightly stock reconciliation', receipts: 'Update monthly from KORONA', monthly_reconciliation: 'Nightly monthly reconciliation'};
const countLabels = {active_stores: 'Active stock stores', products: 'Products', stock_records: 'Stock records', thirty_day_totals: '30-day totals'};
const metricDefinitions = {
  stores: [['seen', 'Units checked'], ['created', 'Stores added'], ['updated', 'Stores updated']],
  products: [['seen', 'Products checked'], ['created', 'Products added'], ['updated', 'Products updated']],
  stocks: [['stores_checked', 'Stores checked'], ['seen', 'Stock rows checked'], ['created', 'Rows added'], ['updated', 'Quantities changed'], ['unchanged', 'Unchanged'], ['deferred', 'Deferred']],
  stock_reconciliation: [['stores_checked', 'Stores checked'], ['seen', 'Stock rows checked'], ['created', 'Rows added'], ['updated', 'Quantities repaired'], ['unchanged', 'Already correct'], ['stores_retired', 'Stores retired']],
  receipts: [['seen', 'Receipts checked'], ['created', 'Lines added'], ['updated', 'Lines updated'], ['products_recalculated', 'Product totals updated'], ['deferred', 'Deferred receipts']],
  monthly_reconciliation: [['days_refreshed', 'Days refreshed'], ['receipts_checked', 'Receipts checked'], ['receipt_lines_created', 'Lines replaced'], ['daily_summaries_rebuilt', 'Daily totals rebuilt'], ['monthly_totals_rebuilt', 'Product totals rebuilt'], ['deferred', 'Deferred receipts']],
};
const escapeOps = value => { const element = document.createElement('div'); element.textContent = value ?? ''; return element.innerHTML; };
const dateTime = value => value ? new Date(value).toLocaleString() : 'Not run';
const duration = milliseconds => {
  if (milliseconds === null || milliseconds === undefined) return '—';
  if (milliseconds >= 60000) {
    const totalSeconds = Math.round(milliseconds / 1000);
    return `${Math.floor(totalSeconds / 60)}m ${totalSeconds % 60}s`;
  }
  return milliseconds >= 1000 ? `${(milliseconds / 1000).toFixed(1)}s` : `${milliseconds}ms`;
};
const intervalLabel = seconds => seconds % 60 === 0 ? `${seconds / 60} min` : `${seconds} sec`;
const ageLabel = seconds => seconds === null || seconds === undefined ? 'Never' : seconds < 60 ? `${seconds}s` : seconds < 3600 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;

function metricsFor(serviceName, run, limit = 6) {
  if (!run) return [];
  const metrics = run.metrics || {};
  return (metricDefinitions[serviceName] || [['seen', 'Checked'], ['created', 'Added'], ['updated', 'Updated']])
    .filter(([key]) => metrics[key] !== undefined)
    .slice(0, limit)
    .map(([key, label]) => ({label, value: Number(metrics[key] || 0)}));
}

function metricGrid(serviceName, run) {
  const metrics = metricsFor(serviceName, run);
  if (!metrics.length) return `<div class="service-no-run">${run?.status === 'error' ? 'No work totals were recorded before this run stopped.' : 'Waiting for the first recorded run.'}</div>`;
  return `<div class="service-metrics">${metrics.map(metric => `<div><span>${escapeOps(metric.label)}</span><strong>${metric.value.toLocaleString()}</strong></div>`).join('')}</div>`;
}

function metricSummary(run) {
  const metrics = metricsFor(run.job_name, run, 3);
  return metrics.length ? metrics.map(metric => `${metric.value.toLocaleString()} ${metric.label.toLowerCase()}`).join(' · ') : 'No record changes';
}

function renderOverview(data) {
  const overview = data.overview;
  const health = overview.errors ? 'Needs attention' : overview.running ? 'Jobs running' : 'All services healthy';
  const healthClass = overview.errors ? 'error' : overview.running ? 'queued' : 'success';
  document.getElementById('ops-overview').innerHTML = `
    <div class="ops-health-main"><span class="status ${healthClass}">${escapeOps(health)}</span><strong>${overview.healthy} of ${overview.services_total}</strong><small>ready · ${overview.disabled} disabled</small></div>
    <div><span>Running now</span><strong>${overview.running.toLocaleString()}</strong><small>${overview.errors} need attention</small></div>
    <div><span>Checked in 24 hours</span><strong>${overview.records_checked_24h.toLocaleString()}</strong><small>KORONA records processed</small></div>
    <div><span>Changed in 24 hours</span><strong>${overview.records_changed_24h.toLocaleString()}</strong><small>created or updated locally</small></div>
    <div><span>API errors in 24 hours</span><strong>${overview.api_errors_24h.toLocaleString()}</strong><small>KORONA responses ≥ 400</small></div>`;
  const attention = document.getElementById('ops-attention');
  attention.hidden = !overview.attention.length;
  attention.innerHTML = overview.attention.length ? `<div><i data-lucide="triangle-alert"></i><div><strong>${overview.attention.length} job${overview.attention.length === 1 ? '' : 's'} need attention</strong><p>${overview.attention.map(item => `${escapeOps(serviceLabels[item.name] || item.name)}: ${escapeOps(item.message)}`).join('<br>')}</p></div></div><div>${overview.attention.map(item => `<button class="secondary-button" data-focus-service="${item.name}">View ${escapeOps(serviceLabels[item.name] || item.name)}</button>`).join('')}</div>` : '';
}

function renderServices(data) {
  const order = {stores: 0, products: 1, stocks: 2, stock_reconciliation: 3, receipts: 4, monthly_reconciliation: 5};
  const services = [...data.services].sort((a, b) => (order[a.name] ?? 99) - (order[b.name] ?? 99));
  serviceList.innerHTML = services.map(service => {
    const run = service.latest_run;
    const runLabel = service.name.includes('reconciliation') ? 'Reconcile now' : service.name === 'stocks' ? 'Sync now' : service.name === 'receipts' ? 'Update now' : 'Run now';
    const active = ['running', 'queued'].includes(service.status);
    const scheduleControl = service.fixed_schedule
      ? `<span class="schedule-chip">${escapeOps(service.schedule_label)}</span>`
      : `<div class="interval-control"><label for="interval-${service.name}">Every</label><input id="interval-${service.name}" type="number" min="30" max="86400" step="30" value="${service.interval_seconds}" data-interval="${service.name}" ${active ? 'disabled' : ''}><button type="button" class="interval-save" data-save-interval="${service.name}" ${active ? 'disabled' : ''}>Save</button><small>${intervalLabel(service.interval_seconds)}</small></div>`;
    const toggle = `<label class="switch" title="Enable ${escapeOps(serviceLabels[service.name])}"><input type="checkbox" data-toggle="${service.name}" ${service.enabled ? 'checked' : ''} ${active ? 'disabled' : ''}><span></span></label>`;
    const latestLine = run ? `Started ${dateTime(run.started_at)} · ${duration(run.duration_ms)}` : 'No recorded run';
    return `<article class="service-card" id="service-${service.name}">
      <div class="service-card-head"><div><strong>${escapeOps(serviceLabels[service.name] || service.name)}</strong><span>${escapeOps(service.fixed_schedule ? service.schedule_label : `Every ${intervalLabel(service.interval_seconds)}`)}</span></div><span class="status ${service.status}">${escapeOps(service.status)}</span></div>
      <p class="service-description">${escapeOps(service.description)}</p>
      <div class="service-latest"><div><span>Latest result</span><strong>${escapeOps(latestLine)}</strong></div>${run ? `<span class="status ${run.status}">${escapeOps(run.status)}</span>` : ''}</div>
      ${metricGrid(service.name, run)}
      ${(service.last_error || run?.error) ? `<div class="service-error">${escapeOps(service.last_error || run.error)}</div>` : ''}
      <div class="service-card-actions">${scheduleControl}${toggle}<button class="secondary-button" data-run="${service.name}" ${active ? 'disabled' : ''}><i data-lucide="play"></i><span>${runLabel}</span></button></div>
    </article>`;
  }).join('');
}

function renderStockSync(data) {
  const stock = data.stock_sync;
  const badge = document.getElementById('stock-health-badge');
  badge.className = `status ${stock.health === 'healthy' ? 'success' : stock.health}`;
  badge.textContent = stock.health;
  const latest = stock.latest_incremental;
  const reconciliation = stock.latest_reconciliation;
  const latestDetail = latest
    ? latest.change_breakdown_available
      ? `${latest.seen.toLocaleString()} checked · ${latest.unchanged.toLocaleString()} unchanged · ${duration(latest.duration_ms)}`
      : `${latest.seen.toLocaleString()} checked · change breakdown available after next run`
    : 'Not run';
  const reconciliationDetail = reconciliation
    ? reconciliation.change_breakdown_available
      ? `${reconciliation.changed.toLocaleString()} repaired · ${reconciliation.unchanged.toLocaleString()} already correct · ${dateTime(reconciliation.finished_at)}`
      : `Legacy run · accurate repair breakdown available after next run · ${dateTime(reconciliation.finished_at)}`
    : `${escapeOps(stock.nightly_schedule)} · Not run`;
  const coverage = stock.stores_total ? Math.round(stock.current / stock.stores_total * 100) : 100;
  document.getElementById('stock-coverage').innerHTML = `<div><span>Store coverage</span><strong>${coverage}% current</strong></div><div class="coverage-track" role="progressbar" aria-label="Current stock store coverage" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${coverage}"><span style="width:${coverage}%"></span></div><small>${stock.current} current · ${stock.stale} stale · ${stock.missing} missing</small>`;
  document.getElementById('stock-sync-summary').innerHTML = `
    <div><span><i data-lucide="store"></i> Current stores</span><strong>${stock.current} / ${stock.stores_total}</strong><small>Fresh within ${ageLabel(stock.stale_after_seconds)}</small></div>
    <div><span><i data-lucide="circle-alert"></i> Needs attention</span><strong>${stock.stale + stock.missing}</strong><small>${stock.stale} stale · ${stock.missing} missing</small></div>
    <div><span><i data-lucide="timer"></i> Polling interval</span><strong>${intervalLabel(stock.interval_seconds)}</strong><small>${stock.page_size.toLocaleString()} records per page</small></div>
    <div><span><i data-lucide="refresh-cw"></i> Latest stock update</span><strong>${latest ? latest.change_breakdown_available ? `${latest.changed.toLocaleString()} changed` : `${latest.seen.toLocaleString()} checked` : '—'}</strong><small>${latestDetail}</small></div>
    <div><span><i data-lucide="shield-check"></i> Nightly verification</span><strong>${reconciliation ? reconciliation.seen.toLocaleString() : '—'} checked</strong><small>${reconciliationDetail}</small></div>`;
  stockStoreList.innerHTML = stock.stores.length ? stock.stores.map(store => {
    const statusClass = store.status === 'current' ? 'success' : store.status === 'stale' ? 'warning' : 'error';
    return `<tr><td><strong>${escapeOps(store.number)}</strong><small>${escapeOps(store.name)}</small></td><td><span class="status ${statusClass}">${escapeOps(store.status)}</span></td><td>${dateTime(store.last_synced_at)}</td><td>${ageLabel(store.age_seconds)}</td><td>${Number(store.last_revision || 0).toLocaleString()}</td><td>${Number(store.stock_records || 0).toLocaleString()}</td></tr>`;
  }).join('') : '<tr><td colspan="6">No active warehouse stores.</td></tr>';
}

function fullMetricDetails(run) {
  const metrics = metricsFor(run.job_name, run, 20);
  if (!metrics.length) return escapeOps(run.error || 'No record changes');
  return `<details class="row-details"><summary>${escapeOps(metricSummary(run))}</summary><div>${metrics.map(metric => `<span><strong>${metric.value.toLocaleString()}</strong> ${escapeOps(metric.label)}</span>`).join('')}</div></details>`;
}

function renderRuns() {
  const service = document.getElementById('run-service-filter').value;
  const status = document.getElementById('run-status-filter').value;
  const rows = operationsData.runs.filter(run => (!service || run.job_name === service) && (!status || run.status === status));
  const successes = rows.filter(run => run.status === 'success').length;
  const errors = rows.filter(run => run.status === 'error').length;
  const average = rows.length ? Math.round(rows.reduce((sum, run) => sum + Number(run.duration_ms || 0), 0) / rows.length) : 0;
  document.getElementById('run-summary').innerHTML = `<span><strong>${rows.length}</strong> shown</span><span><strong>${successes}</strong> successful</span><span class="${errors ? 'summary-error' : ''}"><strong>${errors}</strong> errors</span><span><strong>${duration(average)}</strong> average duration</span>`;
  runList.innerHTML = rows.length ? rows.map(run => `<tr><td><strong>${escapeOps(serviceLabels[run.job_name] || run.job_name)}</strong></td><td><span class="status ${run.status}">${escapeOps(run.status)}</span></td><td>${dateTime(run.started_at)}</td><td>${duration(run.duration_ms)}</td><td class="run-details">${run.error ? `<span class="inline-error">${escapeOps(run.error)}</span>` : fullMetricDetails(run)}</td></tr>`).join('') : '<tr><td colspan="5" class="empty-table">No runs match these filters.</td></tr>';
}

function renderLogs() {
  const level = document.getElementById('log-level-filter').value;
  const query = document.getElementById('log-search').value.trim().toLowerCase();
  const isResolved = row => Boolean(row.context?.resolved_at);
  const levelMatches = row => !level || (level === 'unresolved' ? row.level === 'ERROR' && !isResolved(row) : row.level === level);
  const rows = operationsData.logs.filter(row => levelMatches(row) && (!query || `${row.source} ${row.message}`.toLowerCase().includes(query)));
  const errors = operationsData.logs.filter(row => row.level === 'ERROR' && !isResolved(row)).length;
  const warnings = operationsData.logs.filter(row => row.level === 'WARNING' || row.level === 'WARN').length;
  const resolved = operationsData.logs.filter(row => row.level === 'ERROR' && isResolved(row)).length;
  document.getElementById('log-summary').innerHTML = `<span><strong>${rows.length}</strong> shown</span><span class="${errors ? 'summary-error' : ''}"><strong>${errors}</strong> active errors</span><span><strong>${resolved}</strong> resolved</span><span><strong>${warnings}</strong> warnings</span><span><strong>${operationsData.logs.length}</strong> latest events loaded</span>`;
  logList.innerHTML = rows.length ? rows.map(row => {
    const context = row.context && Object.keys(row.context).length ? JSON.stringify(row.context, null, 2) : '';
    const message = context ? `<details class="row-details"><summary>${escapeOps(row.message)}</summary><pre>${escapeOps(context)}</pre></details>` : escapeOps(row.message);
    const resolvedRow = isResolved(row);
    return `<tr><td><span class="status ${resolvedRow ? 'success' : String(row.level).toLowerCase()}">${resolvedRow ? 'RESOLVED' : escapeOps(row.level)}</span></td><td>${escapeOps(row.source)}</td><td class="log-message">${message}</td><td>${dateTime(row.created_at)}</td></tr>`;
  }).join('') : '<tr><td colspan="4" class="empty-table">No logs match these filters.</td></tr>';
}

function renderLatency() {
  const filter = document.getElementById('latency-status-filter').value;
  const query = document.getElementById('latency-search').value.trim().toLowerCase();
  const matches = row => (!query || row.url_path.toLowerCase().includes(query)) && (!filter || (filter === 'error' ? row.status_code >= 400 : row.latency_ms >= 1000));
  const rows = operationsData.api_latency.filter(matches);
  const endpoints = operationsData.api_health.endpoints.filter(row => !query || row.url_path.toLowerCase().includes(query));
  const api = operationsData.api_health;
  document.getElementById('api-summary').innerHTML = `<span><strong>${api.requests_24h.toLocaleString()}</strong> requests / 24h</span><span class="${api.errors_24h ? 'summary-error' : ''}"><strong>${api.errors_24h.toLocaleString()}</strong> errors</span><span><strong>${api.average_ms.toLocaleString()}ms</strong> average</span><span><strong>${api.slowest_ms.toLocaleString()}ms</strong> slowest</span><span><strong>${api.slow_requests_24h.toLocaleString()}</strong> slow requests</span>`;
  endpointHealthList.innerHTML = endpoints.length ? endpoints.map(row => `<tr><td><span class="http-method">${escapeOps(row.method)}</span>${escapeOps(row.url_path)}</td><td>${Number(row.requests).toLocaleString()}</td><td><span class="status ${row.errors ? 'error' : 'success'}">${Number(row.errors).toLocaleString()}</span></td><td>${Math.round(row.average_ms || 0).toLocaleString()}ms</td><td>${Number(row.slowest_ms || 0).toLocaleString()}ms</td></tr>`).join('') : '<tr><td colspan="5" class="empty-table">No endpoints match this search.</td></tr>';
  latencyList.innerHTML = rows.length ? rows.map(row => `<tr><td><span class="http-method">${escapeOps(row.method)}</span>${escapeOps(row.url_path)}</td><td><span class="status ${row.status_code >= 400 ? 'error' : 'success'}">${row.status_code ?? '-'}</span></td><td><span class="latency-value ${row.latency_ms >= 1000 ? 'slow' : ''}">${row.latency_ms.toLocaleString()}ms</span></td><td>${dateTime(row.created_at)}</td></tr>`).join('') : '<tr><td colspan="4" class="empty-table">No requests match these filters.</td></tr>';
}

function renderDiagnostics() {
  renderRuns(); renderLogs(); renderLatency();
}

async function loadOperations() {
  const refreshButton = document.getElementById('refresh-ops');
  refreshButton.disabled = true; refreshButton.classList.add('loading');
  try {
    const data = await apiFetch('/api/operations/services/');
    operationsData = data;
    document.getElementById('ops-counts').innerHTML = Object.entries(data.counts).map(([name, value]) => `<div><span>${escapeOps(countLabels[name] || name.replaceAll('_', ' '))}</span><strong>${Number(value).toLocaleString()}</strong></div>`).join('');
    document.getElementById('ops-updated').textContent = `Updated ${new Date().toLocaleTimeString()}`;
    renderOverview(data); renderStockSync(data); renderServices(data); renderDiagnostics();
    lucide.createIcons();
    clearTimeout(operationsTimer);
    const active = data.services.some(service => ['queued', 'running'].includes(service.status));
    operationsTimer = setTimeout(() => loadOperations().catch(error => showToast(error.message, true)), active ? 3000 : 15000);
  } finally {
    refreshButton.disabled = false; refreshButton.classList.remove('loading');
  }
}

serviceList.addEventListener('change', async event => {
  const serviceName = event.target.dataset.toggle;
  if (!serviceName) return;
  const payload = {service_name: serviceName, enabled: event.target.checked};
  try { await apiFetch('/api/operations/services/', {method: 'PATCH', body: JSON.stringify(payload)}); await loadOperations(); }
  catch (error) { showToast(error.message, true); await loadOperations(); }
});

// Do not let a scheduled dashboard refresh replace a number while an admin is
// typing a new interval. The change handler above refreshes after the value is
// committed, and the next normal refresh is scheduled from there.
serviceList.addEventListener('focusin', event => {
  if (event.target.matches('[data-interval]')) clearTimeout(operationsTimer);
});

serviceList.addEventListener('click', async event => {
  const saveButton = event.target.closest('[data-save-interval]');
  if (saveButton) {
    const serviceName = saveButton.dataset.saveInterval;
    const input = serviceList.querySelector(`[data-interval="${serviceName}"]`);
    saveButton.disabled = true;
    try {
      await apiFetch('/api/operations/services/', {method: 'PATCH', body: JSON.stringify({service_name: serviceName, interval_seconds: Number(input.value)})});
      showToast('Interval saved'); await loadOperations();
    } catch (error) { showToast(error.message, true); await loadOperations(); }
    return;
  }
  const button = event.target.closest('[data-run]'); if (!button) return;
  button.disabled = true;
  try { await apiFetch(`/api/operations/services/${button.dataset.run}/run/`, {method: 'POST'}); showToast('Job queued'); await loadOperations(); clearTimeout(operationsTimer); operationsTimer = setTimeout(() => loadOperations().catch(error => showToast(error.message, true)), 1000); }
  catch (error) { showToast(error.message, true); button.disabled = false; }
});

document.getElementById('refresh-ops').addEventListener('click', () => loadOperations().catch(error => showToast(error.message, true)));
document.getElementById('ops-attention').addEventListener('click', event => {
  const button = event.target.closest('[data-focus-service]');
  if (!button) return;
  document.getElementById(`service-${button.dataset.focusService}`)?.scrollIntoView({behavior: 'smooth', block: 'center'});
});
['run-service-filter', 'run-status-filter'].forEach(id => document.getElementById(id).addEventListener('change', renderRuns));
document.getElementById('log-level-filter').addEventListener('change', renderLogs);
document.getElementById('log-search').addEventListener('input', renderLogs);
document.getElementById('latency-status-filter').addEventListener('change', renderLatency);
document.getElementById('latency-search').addEventListener('input', renderLatency);
document.getElementById('run-service-filter').innerHTML += Object.entries(serviceLabels).map(([value, label]) => `<option value="${value}">${escapeOps(label)}</option>`).join('');
loadOperations().catch(error => showToast(error.message, true));

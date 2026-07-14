const serviceList = document.getElementById('service-list');
const runList = document.getElementById('run-list');
const latencyList = document.getElementById('latency-list');
const logList = document.getElementById('log-list');
const stockStoreList = document.getElementById('stock-store-list');
const fullSyncDialog = document.getElementById('full-sync-dialog');
let operationsTimer;
let operationsData;

const serviceLabels = {stores: 'Stores', products: 'Products & barcodes', stocks: 'Stock changes', stock_reconciliation: 'Nightly stock reconciliation', receipts: 'Receipts & 30-day totals'};
const stageLabels = {stores: 'Stores', products: 'Products', stocks: 'Store stocks', receipts: 'Receipts', totals: '30-day totals'};
const countLabels = {active_stores: 'Active stock stores', products: 'Products', stock_records: 'Stock records', thirty_day_totals: '30-day totals'};
const escapeOps = value => { const element = document.createElement('div'); element.textContent = value ?? ''; return element.innerHTML; };
const dateTime = value => value ? new Date(value).toLocaleString() : 'Not run';
const duration = milliseconds => milliseconds >= 1000 ? `${(milliseconds / 1000).toFixed(1)} s` : `${milliseconds} ms`;
const intervalLabel = seconds => seconds % 60 === 0 ? `${seconds / 60} min` : `${seconds} sec`;
const elapsedTime = (value, finishedAt = null) => {
  if (!value) return '-';
  const seconds = Math.max(0, Math.floor(((finishedAt ? new Date(finishedAt).getTime() : Date.now()) - new Date(value).getTime()) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours ? `${hours}h ${minutes}m` : `${minutes}m ${seconds % 60}s`;
};
const ageLabel = seconds => {
  if (seconds === null || seconds === undefined) return 'Never';
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
};

function progressRow(name, value = {}) {
  const processed = Number(value.processed || 0);
  const total = Number(value.total || 0);
  const percent = total ? Math.min(100, Math.round(processed / total * 100)) : (value.status === 'complete' ? 100 : 0);
  return `<div class="stage-row"><span class="stage-state ${value.status || 'pending'}"></span><strong>${escapeOps(stageLabels[name] || name)}</strong><div class="progress-track"><span style="width:${percent}%"></span></div><span>${processed.toLocaleString()}${total ? ` / ${total.toLocaleString()}` : ''}</span><small>${value.current_batch ? `Batch ${value.current_batch}${value.total_batches ? ` / ${value.total_batches}` : ''}` : ''}</small></div>`;
}

function renderFullSync(data) {
  const job = data.full_sync;
  const actions = document.getElementById('full-sync-actions');
  const status = document.getElementById('full-sync-status');
  if (!job) {
    actions.innerHTML = data.can_manage ? '<button class="secondary-button danger-outline" data-full-start><i data-lucide="database-zap"></i><span>Full resync</span></button>' : '';
    status.innerHTML = '<div class="empty-state compact">No full reconciliation has been run.</div>';
    return;
  }

  const active = ['queued', 'running'].includes(job.status);
  actions.innerHTML = data.can_manage && job.status === 'error'
    ? `<button class="secondary-button" data-full-resume="${job.id}"><i data-lucide="rotate-cw"></i><span>Resume</span></button>`
    : data.can_manage && !active
      ? '<button class="secondary-button danger-outline" data-full-start><i data-lucide="database-zap"></i><span>Run full resync</span></button>'
      : '';
  const stageProgress = {...job.stage_progress};
  if (['stores', 'products', 'stocks', 'receipts', 'totals'].includes(job.stage)) {
    stageProgress[job.stage] = {processed: job.processed, total: job.total, current_batch: job.current_batch, total_batches: job.total_batches, status: job.status === 'success' ? 'complete' : 'running'};
  }
  status.innerHTML = `<div class="full-sync-summary"><div><span class="status ${job.status}">${escapeOps(job.status)}</span><strong>${escapeOps(stageLabels[job.stage] || job.stage)}</strong></div><div><small>Started</small><span>${dateTime(job.started_at)}</span></div><div><small>Duration</small><span>${elapsedTime(job.started_at, job.finished_at)}</span></div><div><small>Initiated by</small><span>${escapeOps(job.initiated_by)}</span></div></div><div class="stage-list">${['stores', 'products', 'stocks', 'receipts', 'totals'].map(name => progressRow(name, stageProgress[name])).join('')}</div>${job.error ? `<div class="operation-error">${escapeOps(job.error)}</div>` : ''}`;
}

function renderServices(data) {
  const order = {stocks: 0, stock_reconciliation: 1, receipts: 2, products: 3, stores: 4};
  const services = [...data.services].sort((a, b) => (order[a.name] ?? 99) - (order[b.name] ?? 99));
  serviceList.innerHTML = services.map(service => {
    const runLabel = service.name === 'stock_reconciliation' ? 'Reconcile now' : service.name === 'stocks' ? 'Sync now' : 'Run now';
    const active = ['running', 'queued'].includes(service.status);
    return `<article class="service-row"><div><div class="service-name"><strong>${escapeOps(serviceLabels[service.name] || service.name)}</strong><span class="status ${service.status}">${escapeOps(service.status)}</span></div><p class="service-description">${escapeOps(service.description)}</p><p>Last ${dateTime(service.last_run_at)} · ${service.fixed_schedule ? escapeOps(service.schedule_label) : `Next ${dateTime(service.next_run_at)}`}${service.last_error ? ` · ${escapeOps(service.last_error)}` : ''}</p></div><div class="service-actions">${data.can_manage ? `${service.fixed_schedule ? `<span class="schedule-chip">${escapeOps(service.schedule_label)}</span>` : `<label class="interval-control"><span>Interval</span><input type="number" min="30" max="86400" step="30" value="${service.interval_seconds}" data-interval="${service.name}" ${active ? 'disabled' : ''}><small>${intervalLabel(service.interval_seconds)}</small></label>`}<label class="switch" title="Enable ${escapeOps(service.name)}"><input type="checkbox" data-toggle="${service.name}" ${service.enabled ? 'checked' : ''} ${active ? 'disabled' : ''}><span></span></label><button class="secondary-button" data-run="${service.name}" ${active ? 'disabled' : ''}><i data-lucide="play"></i><span>${runLabel}</span></button>` : `<span>${service.fixed_schedule ? escapeOps(service.schedule_label) : intervalLabel(service.interval_seconds)}</span>`}</div></article>`;
  }).join('');
}

function renderStockSync(data) {
  const stock = data.stock_sync;
  const badge = document.getElementById('stock-health-badge');
  const healthClass = stock.health === 'healthy' ? 'success' : stock.health;
  badge.className = `status ${healthClass}`;
  badge.textContent = stock.health;
  const latest = stock.latest_incremental;
  const reconciliation = stock.latest_reconciliation;
  const [nightlyTime, ...nightlyZoneParts] = stock.nightly_schedule.split(' ');
  const nightlyZone = nightlyZoneParts.join(' ');
  document.getElementById('stock-sync-summary').innerHTML = `
    <div><span>Current stores</span><strong>${stock.current} / ${stock.stores_total}</strong><small>Fresh within ${ageLabel(stock.stale_after_seconds)}</small></div>
    <div><span>Needs attention</span><strong>${stock.stale + stock.missing}</strong><small>${stock.stale} stale · ${stock.missing} missing</small></div>
    <div><span>Incremental polling</span><strong>${intervalLabel(stock.interval_seconds)}</strong><small>${stock.page_size.toLocaleString()} records per page</small></div>
    <div><span>Latest incremental</span><strong>${latest ? duration(latest.duration_ms) : 'Not run'}</strong><small>${latest ? `${latest.changed.toLocaleString()} changed · ${dateTime(latest.finished_at)}` : 'Waiting for first run'}</small></div>
    <div><span>Nightly reconciliation</span><strong>${escapeOps(nightlyTime)}</strong><small>${escapeOps(nightlyZone)} · ${reconciliation ? `Last ${dateTime(reconciliation.finished_at)} · ${duration(reconciliation.duration_ms)}` : 'Not run yet'}</small></div>`;
  stockStoreList.innerHTML = stock.stores.length ? stock.stores.map(store => {
    const statusClass = store.status === 'current' ? 'success' : store.status === 'stale' ? 'warning' : 'error';
    return `<tr><td><strong>${escapeOps(store.number)}</strong><small>${escapeOps(store.name)}</small></td><td><span class="status ${statusClass}">${escapeOps(store.status)}</span></td><td>${dateTime(store.last_synced_at)}</td><td>${ageLabel(store.age_seconds)}</td><td>${Number(store.last_revision || 0).toLocaleString()}</td><td>${Number(store.stock_records || 0).toLocaleString()}</td></tr>`;
  }).join('') : '<tr><td colspan="6">No active warehouse stores.</td></tr>';
}

function renderTables(data) {
  runList.innerHTML = data.runs.length ? data.runs.map(run => `<tr><td>${escapeOps(serviceLabels[run.job_name] || run.job_name)}</td><td><span class="status ${run.status}">${escapeOps(run.status)}</span></td><td>${dateTime(run.started_at)}</td><td>${duration(run.duration_ms)}</td><td>${run.seen.toLocaleString()}</td><td>${(run.created + run.updated).toLocaleString()}</td></tr>`).join('') : '<tr><td colspan="6">No runs</td></tr>';
  latencyList.innerHTML = data.api_latency.length ? data.api_latency.map(row => `<tr><td>${escapeOps(row.url_path)}</td><td>${row.status_code ?? '-'}</td><td>${row.latency_ms} ms</td><td>${dateTime(row.created_at)}</td></tr>`).join('') : '<tr><td colspan="4">No requests</td></tr>';
  logList.innerHTML = data.logs.length ? data.logs.map(row => `<tr><td><span class="status ${String(row.level).toLowerCase()}">${escapeOps(row.level)}</span></td><td>${escapeOps(row.source)}</td><td class="log-message">${escapeOps(row.message)}</td><td>${dateTime(row.created_at)}</td></tr>`).join('') : '<tr><td colspan="4">No logs</td></tr>';
}

async function loadOperations() {
  const data = await apiFetch('/api/operations/services/');
  operationsData = data;
  document.getElementById('ops-counts').innerHTML = Object.entries(data.counts).map(([name, value]) => `<div><span>${escapeOps(countLabels[name] || name.replaceAll('_', ' '))}</span><strong>${Number(value).toLocaleString()}</strong></div>`).join('');
  document.getElementById('ops-updated').textContent = `Updated ${new Date().toLocaleTimeString()}`;
  renderStockSync(data); renderFullSync(data); renderServices(data); renderTables(data);
  lucide.createIcons();
  clearTimeout(operationsTimer);
  const active = (data.full_sync && ['queued', 'running'].includes(data.full_sync.status)) || data.services.some(service => ['queued', 'running'].includes(service.status));
  operationsTimer = setTimeout(() => loadOperations().catch(error => showToast(error.message, true)), active ? 3000 : 15000);
}

serviceList.addEventListener('change', async event => {
  const serviceName = event.target.dataset.toggle || event.target.dataset.interval;
  if (!serviceName) return;
  const payload = {service_name: serviceName};
  if (event.target.dataset.toggle) payload.enabled = event.target.checked;
  if (event.target.dataset.interval) payload.interval_seconds = Number(event.target.value);
  try { await apiFetch('/api/operations/services/', {method: 'PATCH', body: JSON.stringify(payload)}); await loadOperations(); }
  catch (error) { showToast(error.message, true); }
});

serviceList.addEventListener('click', async event => {
  const button = event.target.closest('[data-run]'); if (!button) return;
  button.disabled = true;
  try { await apiFetch(`/api/operations/services/${button.dataset.run}/run/`, {method: 'POST'}); showToast('Sync queued'); await loadOperations(); clearTimeout(operationsTimer); operationsTimer = setTimeout(() => loadOperations().catch(error => showToast(error.message, true)), 1000); }
  catch (error) { showToast(error.message, true); button.disabled = false; }
});

document.getElementById('full-sync-actions').addEventListener('click', async event => {
  const start = event.target.closest('[data-full-start]');
  const resume = event.target.closest('[data-full-resume]');
  if (start) { document.getElementById('full-sync-confirmation').value = ''; fullSyncDialog.showModal(); }
  if (resume) {
    resume.disabled = true;
    try { await apiFetch(`/api/operations/full-sync/${resume.dataset.fullResume}/resume/`, {method: 'POST', body: '{}'}); showToast('Reconciliation resumed'); await loadOperations(); }
    catch (error) { showToast(error.message, true); resume.disabled = false; }
  }
});

document.getElementById('full-sync-form').addEventListener('submit', async event => {
  event.preventDefault();
  const confirmation = document.getElementById('full-sync-confirmation').value;
  try { await apiFetch('/api/operations/full-sync/', {method: 'POST', body: JSON.stringify({confirmation})}); fullSyncDialog.close(); showToast('Full reconciliation queued'); await loadOperations(); }
  catch (error) { showToast(error.message, true); }
});
document.getElementById('full-sync-close').addEventListener('click', () => fullSyncDialog.close());
document.getElementById('full-sync-cancel').addEventListener('click', () => fullSyncDialog.close());
document.getElementById('refresh-ops').addEventListener('click', () => loadOperations().catch(error => showToast(error.message, true)));
loadOperations().catch(error => showToast(error.message, true));

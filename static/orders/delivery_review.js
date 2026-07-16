const {escape, formatDate, formatShortDate, statusBadge, photoSummary, debounce, formatBytes} = deliveryUI;
const reviewList = document.getElementById('review-list');
const summary = document.getElementById('review-summary');
let statuses = [];

function params() {
  const result = new URLSearchParams();
  const values = {q: document.getElementById('review-search').value, status: document.getElementById('review-status').value, date_from: document.getElementById('review-date-from').value, date_to: document.getElementById('review-date-to').value};
  Object.entries(values).forEach(([key, value]) => { if (value) result.set(key, value); });
  if (document.getElementById('review-issues').checked) result.set('issues', '1');
  return result.toString();
}

function renderSummary(deliveries) {
  const pending = deliveries.filter(item => ['submitted', 'under_review', 'needs_info'].includes(item.status)).length;
  const issues = deliveries.filter(item => item.has_issue && !['verified', 'resolved'].includes(item.status)).length;
  const verified = deliveries.filter(item => ['verified', 'resolved'].includes(item.status)).length;
  const photos = deliveries.reduce((sum, item) => sum + Number(item.asset_counts.invoice || 0) + Number(item.asset_counts.boxes || 0) + Number(item.asset_counts.damage || 0), 0);
  summary.innerHTML = `<article><span class="review-metric-icon pending"><i data-lucide="inbox"></i></span><div><strong>${pending}</strong><span>Awaiting review</span><small>Needs an admin decision</small></div></article><article><span class="review-metric-icon issue"><i data-lucide="triangle-alert"></i></span><div><strong>${issues}</strong><span>Open issues</span><small>Shortage or damage flagged</small></div></article><article><span class="review-metric-icon verified"><i data-lucide="badge-check"></i></span><div><strong>${verified}</strong><span>Verified / resolved</span><small>Completed records</small></div></article><article><span class="review-metric-icon photos"><i data-lucide="images"></i></span><div><strong>${photos}</strong><span>Proof photos</span><small>In current results</small></div></article>`;
}

function renderRows(deliveries) {
  document.getElementById('review-result-count').textContent = `${deliveries.length} deliver${deliveries.length === 1 ? 'y' : 'ies'} shown`;
  if (!deliveries.length) { reviewList.innerHTML = '<div class="delivery-empty compact"><i data-lucide="search-x"></i><h2>No matching deliveries</h2><p>Try removing a filter or searching a different word.</p></div>'; lucide.createIcons(); return; }
  reviewList.innerHTML = deliveries.map(item => `<a href="${item.detail_url}" class="review-row ${item.has_issue ? 'has-issue' : ''}"><div class="review-date"><strong>${formatShortDate(item.delivered_at)}</strong><span>${new Date(item.delivered_at).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'})}</span></div><div class="review-store"><span class="store-pill">${escape(item.store.number)}</span><div><strong>${escape(item.store.name)}</strong><small>${item.reference_number ? `Invoice ${escape(item.reference_number)} · ` : ''}${escape(item.submitted_by)}</small></div></div><div class="review-proof-count"><i data-lucide="receipt-text"></i><span>${escape(photoSummary(item))}</span></div><div class="review-note"><p>${escape(item.issue_notes || item.general_notes || 'No delivery notes')}</p>${item.keywords.length ? `<div>${item.keywords.slice(0, 3).map(word => `<span>#${escape(word)}</span>`).join('')}</div>` : ''}</div><div class="review-status-cell">${statusBadge(item)}<i data-lucide="chevron-right"></i></div></a>`).join('');
  lucide.createIcons();
}

async function loadReview() {
  reviewList.innerHTML = '<div class="delivery-loading">Loading verification queue…</div>';
  try {
    const data = await apiFetch(`/api/deliveries/?${params()}`); statuses = data.statuses;
    const select = document.getElementById('review-status'); const selected = select.value;
    if (select.options.length === 1) select.innerHTML += statuses.map(item => `<option value="${item.value}">${escape(item.label)}</option>`).join('');
    select.value = selected; renderSummary(data.deliveries); renderRows(data.deliveries); lucide.createIcons();
  } catch (error) { reviewList.innerHTML = `<div class="delivery-form-error">${escape(error.message)}</div>`; }
}

['review-status', 'review-date-from', 'review-date-to', 'review-issues'].forEach(id => document.getElementById(id).addEventListener('change', loadReview));
document.getElementById('review-search').addEventListener('input', debounce(loadReview));
document.getElementById('clear-review-filters').addEventListener('click', () => { ['review-search', 'review-status', 'review-date-from', 'review-date-to'].forEach(id => document.getElementById(id).value = ''); document.getElementById('review-issues').checked = false; loadReview(); });

async function loadBackups() {
  const target = document.getElementById('backup-list');
  try {
    const data = await apiFetch('/api/delivery-backups/');
    document.getElementById('create-backup').disabled = !data.storage_configured;
    target.innerHTML = data.backups.length ? data.backups.map(item => `<div class="backup-row"><span class="backup-icon ${item.status}"><i data-lucide="${item.status === 'complete' ? 'database-zap' : item.status === 'failed' ? 'circle-alert' : 'loader-circle'}"></i></span><div><strong>${formatDate(item.created_at)}</strong><small>${escape(item.created_by)} · ${item.delivery_count} deliveries · ${item.asset_count} files · ${formatBytes(item.size_bytes)}</small>${item.error ? `<p>${escape(item.error)}</p>` : ''}</div>${item.download_url ? `<a class="secondary-button" href="${item.download_url}"><i data-lucide="download"></i>Metadata</a>` : `<span class="delivery-status ${item.status === 'failed' ? 'issue' : 'pending'}">${escape(item.status)}</span>`}</div>`).join('') : '<p class="backup-empty">No metadata backup has run yet. The first scheduled backup runs nightly at 2:30 AM.</p>';
    lucide.createIcons();
  } catch (error) { target.innerHTML = `<div class="delivery-form-error">${escape(error.message)}</div>`; }
}
document.getElementById('create-backup').addEventListener('click', async event => { const button = event.currentTarget; button.disabled = true; button.innerHTML = '<i data-lucide="loader-circle"></i>Creating backup…'; lucide.createIcons(); try { await apiFetch('/api/delivery-backups/', {method: 'POST', body: '{}'}); showToast('Delivery metadata backup created'); await loadBackups(); } catch (error) { showToast(error.message, true); } finally { button.disabled = false; button.innerHTML = '<i data-lucide="database-backup"></i>Create backup now'; lucide.createIcons(); } });
loadReview(); loadBackups();

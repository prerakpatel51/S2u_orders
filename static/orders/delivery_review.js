const {escape, formatDate, statusBadge, debounce, formatBytes} = deliveryUI;
const reviewList = document.getElementById('review-list');
const summary = document.getElementById('review-summary');
const reviewDecisions = ['under_review', 'needs_info', 'issue_found', 'verified', 'resolved'];
let deliveries = [];
let statuses = [];
let exportPoll;
let lightboxPhotos = [];
let lightboxIndex = 0;
let lightboxZoom = 1;
let touchStartX = null;

function filterParams() {
  const result = new URLSearchParams({inline: '1'});
  const values = {
    q: document.getElementById('review-search').value,
    store: document.getElementById('review-store').value,
    status: document.getElementById('review-status').value,
    date_from: document.getElementById('review-date-from').value,
    date_to: document.getElementById('review-date-to').value,
  };
  Object.entries(values).forEach(([key, value]) => { if (value) result.set(key, value); });
  if (document.getElementById('review-issues').checked) result.set('issues', '1');
  return result.toString();
}

function uploadedAssets(delivery, category) {
  return (delivery.assets || []).filter(item => item.category === category && item.status === 'uploaded');
}

function renderSummary(rows) {
  const pending = rows.filter(item => ['submitted', 'under_review', 'needs_info'].includes(item.status)).length;
  const issues = rows.filter(item => item.has_issue && !['verified', 'resolved'].includes(item.status)).length;
  const verified = rows.filter(item => ['verified', 'resolved'].includes(item.status)).length;
  const photos = rows.reduce((sum, item) => sum + uploadedAssets(item, 'invoice').length + uploadedAssets(item, 'boxes').length + uploadedAssets(item, 'damage').length, 0);
  summary.innerHTML = `<article><span class="review-metric-icon pending"><i data-lucide="inbox"></i></span><div><strong>${pending}</strong><span>Awaiting review</span><small>Decision needed</small></div></article><article><span class="review-metric-icon issue"><i data-lucide="triangle-alert"></i></span><div><strong>${issues}</strong><span>Open issues</span><small>Shortage or damage</small></div></article><article><span class="review-metric-icon verified"><i data-lucide="badge-check"></i></span><div><strong>${verified}</strong><span>Verified / resolved</span><small>Completed records</small></div></article><article><span class="review-metric-icon photos"><i data-lucide="images"></i></span><div><strong>${photos}</strong><span>Full-quality photos</span><small>In current results</small></div></article>`;
}

function galleryMarkup(delivery, category, title, icon) {
  const assets = uploadedAssets(delivery, category);
  if (!assets.length) return `<section class="inline-proof-gallery empty"><div class="inline-gallery-heading"><div><i data-lucide="${icon}"></i><span><strong>${title}</strong><small>No photos supplied</small></span></div></div><div class="inline-gallery-empty"><i data-lucide="image-off"></i><span>No ${escape(title.toLowerCase())}</span></div></section>`;
  const first = assets[0];
  return `<section class="inline-proof-gallery" data-category="${category}"><div class="inline-gallery-heading"><div><i data-lucide="${icon}"></i><span><strong>${title}</strong><small>${assets.length} full-resolution photo${assets.length === 1 ? '' : 's'}</small></span></div><span class="dr-photo-state ${assets.every(item => item.replica_status === 'verified') ? 'verified' : 'pending'}"><i data-lucide="${assets.every(item => item.replica_status === 'verified') ? 'shield-check' : 'cloud-upload'}"></i>${assets.every(item => item.replica_status === 'verified') ? 'DR protected' : 'DR syncing'}</span></div><button class="inline-main-photo" type="button" data-open-gallery data-category="${category}" data-photo-index="0"><img src="${first.view_url}" alt="${escape(title)} photo 1" loading="lazy" decoding="async"><span><b>1 of ${assets.length}</b>${escape(first.filename)} · ${formatBytes(first.size_bytes)}</span><i data-lucide="maximize-2"></i></button><div class="inline-thumbnail-rail">${assets.map((asset, index) => `<button type="button" class="${index === 0 ? 'active' : ''}" data-select-photo data-category="${category}" data-photo-index="${index}" title="${escape(asset.filename)}"><img src="${asset.view_url}" alt="${escape(title)} thumbnail ${index + 1}" loading="lazy" decoding="async"><span>${index + 1}</span></button>`).join('')}</div></section>`;
}

function decisionOptions(delivery) {
  const selected = reviewDecisions.includes(delivery.status) ? delivery.status : 'under_review';
  return statuses.filter(item => reviewDecisions.includes(item.value)).map(item => `<option value="${item.value}" ${item.value === selected ? 'selected' : ''}>${escape(item.label)}</option>`).join('');
}

function renderDeliveryCard(delivery) {
  const difference = delivery.expected_cases != null && delivery.delivered_cases != null ? delivery.delivered_cases - delivery.expected_cases : null;
  const damage = uploadedAssets(delivery, 'damage');
  const canReview = delivery.status !== 'draft';
  return `<article class="inline-review-card ${delivery.has_issue ? 'has-issue' : ''}" data-delivery-id="${delivery.uuid}">
    <header class="inline-review-header"><div class="inline-store-identity"><span class="store-pill">${escape(delivery.store.number)}</span><div><div><h3>${escape(delivery.store.name)}</h3>${statusBadge(delivery)}</div><p>${formatDate(delivery.delivered_at)} · ${delivery.reference_number ? `Invoice ${escape(delivery.reference_number)} · ` : ''}Submitted by ${escape(delivery.submitted_by)}</p></div></div><div class="inline-header-actions"><a href="${delivery.detail_url}" class="icon-text-button subtle"><i data-lucide="history"></i>Audit record</a><a href="/api/deliveries/${delivery.uuid}/download.zip/" class="icon-text-button subtle"><i data-lucide="download"></i>Proof ZIP</a></div></header>
    <div class="inline-delivery-context"><div class="inline-case-facts"><span><small>Expected</small><strong>${delivery.expected_cases ?? '—'}</strong></span><span><small>Received</small><strong>${delivery.delivered_cases ?? '—'}</strong></span><span class="${difference < 0 ? 'warning' : ''}"><small>Difference</small><strong>${difference == null ? '—' : difference > 0 ? `+${difference}` : difference}</strong></span><span class="${delivery.damaged_cases ? 'warning' : ''}"><small>Damaged</small><strong>${delivery.damaged_cases}</strong></span></div><div class="inline-worker-notes"><div><small>Delivery notes</small><p>${escape(delivery.general_notes || 'No delivery notes')}</p></div><div class="${delivery.issue_notes ? 'issue' : ''}"><small>Issue notes</small><p>${escape(delivery.issue_notes || 'No issues reported')}</p></div></div></div>
    <div class="inline-compare-grid">${galleryMarkup(delivery, 'invoice', 'Bills / invoices', 'receipt-text')}${galleryMarkup(delivery, 'boxes', 'Received cases', 'boxes')}</div>
    ${damage.length ? `<div class="inline-damage-wrap">${galleryMarkup(delivery, 'damage', 'Damage evidence', 'triangle-alert')}</div>` : ''}
    <footer class="inline-review-controls"><label><span>Verification decision</span><select data-review-status ${canReview ? '' : 'disabled'}>${decisionOptions(delivery)}</select></label><label class="inline-admin-note"><span>Admin verification notes</span><textarea data-admin-notes rows="2" placeholder="Record what you checked or what needs follow-up" ${canReview ? '' : 'disabled'}>${escape(delivery.admin_notes || '')}</textarea></label><label class="inline-keywords"><span>Search keywords</span><input data-keywords value="${escape(delivery.keywords.join(', '))}" placeholder="supplier claim, holiday rush"></label><button type="button" class="primary-button inline-save-review" ${canReview ? '' : 'disabled'}><i data-lucide="badge-check"></i>${canReview ? 'Save verification' : 'Awaiting submission'}</button></footer>
  </article>`;
}

function renderDeliveries() {
  document.getElementById('review-result-count').textContent = `${deliveries.length} deliver${deliveries.length === 1 ? 'y' : 'ies'} shown with proof`;
  reviewList.innerHTML = deliveries.length ? deliveries.map(renderDeliveryCard).join('') : '<div class="delivery-empty compact"><i data-lucide="search-x"></i><h2>No matching deliveries</h2><p>Try removing a filter or searching a different word.</p></div>';
  lucide.createIcons();
}

function populateSelect(select, items, valueKey, label) {
  const current = select.value;
  if (select.options.length <= 1) select.innerHTML += items.map(item => `<option value="${escape(item[valueKey])}">${escape(label(item))}</option>`).join('');
  select.value = current;
}

async function loadReview() {
  reviewList.innerHTML = '<div class="delivery-loading">Loading full delivery proof…</div>';
  try {
    const data = await apiFetch(`/api/deliveries/?${filterParams()}`);
    deliveries = data.deliveries; statuses = data.statuses;
    populateSelect(document.getElementById('review-status'), statuses, 'value', item => item.label);
    populateSelect(document.getElementById('review-store'), data.stores, 'id', item => `${item.number} — ${item.name}`);
    renderSummary(deliveries); renderDeliveries();
  } catch (error) { reviewList.innerHTML = `<div class="delivery-form-error">${escape(error.message)}</div>`; }
}

function selectInlinePhoto(button) {
  const card = button.closest('[data-delivery-id]');
  const delivery = deliveries.find(item => item.uuid === card.dataset.deliveryId);
  const category = button.dataset.category;
  const index = Number(button.dataset.photoIndex);
  const assets = uploadedAssets(delivery, category);
  const gallery = button.closest('.inline-proof-gallery');
  const main = gallery.querySelector('[data-open-gallery]');
  const image = main.querySelector('img');
  image.src = assets[index].view_url;
  image.alt = `${category} photo ${index + 1}`;
  main.dataset.photoIndex = index;
  main.querySelector('span').innerHTML = `<b>${index + 1} of ${assets.length}</b>${escape(assets[index].filename)} · ${formatBytes(assets[index].size_bytes)}`;
  gallery.querySelectorAll('[data-select-photo]').forEach(item => item.classList.toggle('active', Number(item.dataset.photoIndex) === index));
}

async function saveReview(button) {
  const card = button.closest('[data-delivery-id]');
  const delivery = deliveries.find(item => item.uuid === card.dataset.deliveryId);
  const words = card.querySelector('[data-keywords]').value.split(',').map(item => item.trim()).filter(Boolean);
  const normalizedBefore = delivery.keywords.map(item => item.toLowerCase()).sort().join('|');
  const normalizedAfter = words.map(item => item.toLowerCase()).sort().join('|');
  button.disabled = true; button.innerHTML = '<i data-lucide="loader-circle"></i>Saving…'; lucide.createIcons();
  try {
    if (normalizedBefore !== normalizedAfter) await apiFetch(`/api/deliveries/${delivery.uuid}/keywords/`, {method: 'PUT', body: JSON.stringify({keywords: words})});
    await apiFetch(`/api/deliveries/${delivery.uuid}/review/`, {method: 'POST', body: JSON.stringify({status: card.querySelector('[data-review-status]').value, admin_notes: card.querySelector('[data-admin-notes]').value})});
    showToast('Verification saved'); await loadReview();
  } catch (error) { showToast(error.message, true); button.disabled = false; button.innerHTML = '<i data-lucide="badge-check"></i>Save verification'; lucide.createIcons(); }
}

reviewList.addEventListener('click', event => {
  const thumbnail = event.target.closest('[data-select-photo]');
  if (thumbnail) { selectInlinePhoto(thumbnail); return; }
  const main = event.target.closest('[data-open-gallery]');
  if (main) { openGallery(main.closest('[data-delivery-id]').dataset.deliveryId, main.dataset.category, Number(main.dataset.photoIndex)); return; }
  const save = event.target.closest('.inline-save-review');
  if (save) saveReview(save);
});

['review-store', 'review-status', 'review-date-from', 'review-date-to', 'review-issues'].forEach(id => document.getElementById(id).addEventListener('change', loadReview));
document.getElementById('review-search').addEventListener('input', debounce(loadReview));
document.getElementById('clear-review-filters').addEventListener('click', () => { ['review-search', 'review-store', 'review-status', 'review-date-from', 'review-date-to'].forEach(id => document.getElementById(id).value = ''); document.getElementById('review-issues').checked = false; loadReview(); });

const lightbox = document.getElementById('review-lightbox');
function openGallery(deliveryId, category, categoryIndex) {
  const delivery = deliveries.find(item => item.uuid === deliveryId);
  lightboxPhotos = ['invoice', 'boxes', 'damage'].flatMap(type => uploadedAssets(delivery, type));
  const target = uploadedAssets(delivery, category)[categoryIndex];
  lightboxIndex = Math.max(0, lightboxPhotos.findIndex(item => item.uuid === target.uuid));
  lightboxZoom = 1; renderLightbox(); lightbox.showModal();
}
function renderLightbox() {
  const asset = lightboxPhotos[lightboxIndex];
  const image = document.getElementById('gallery-image');
  image.src = asset.view_url; image.style.transform = `scale(${lightboxZoom})`;
  document.getElementById('gallery-title').textContent = asset.filename;
  document.getElementById('gallery-category').textContent = `${asset.category.toUpperCase()} · ${lightboxIndex + 1} OF ${lightboxPhotos.length}`;
  document.getElementById('gallery-caption').textContent = `${formatBytes(asset.size_bytes)} · ${asset.replica_status === 'verified' ? 'Integrity-verified DR copy available' : 'DR copy syncing'}`;
  document.getElementById('gallery-original').href = asset.view_url;
  document.getElementById('gallery-zoom-reset').textContent = `${Math.round(lightboxZoom * 100)}%`;
  document.getElementById('gallery-thumbnails').innerHTML = lightboxPhotos.map((item, index) => `<button type="button" data-lightbox-index="${index}" class="${index === lightboxIndex ? 'active' : ''}"><img src="${item.view_url}" alt="Proof thumbnail ${index + 1}"><span>${item.category}</span></button>`).join('');
}
function moveGallery(amount) { lightboxIndex = (lightboxIndex + amount + lightboxPhotos.length) % lightboxPhotos.length; lightboxZoom = 1; renderLightbox(); }
function zoomGallery(amount) { lightboxZoom = Math.max(1, Math.min(4, lightboxZoom + amount)); renderLightbox(); }
document.getElementById('gallery-close').addEventListener('click', () => lightbox.close());
document.getElementById('gallery-previous').addEventListener('click', () => moveGallery(-1));
document.getElementById('gallery-next').addEventListener('click', () => moveGallery(1));
document.getElementById('gallery-zoom-in').addEventListener('click', () => zoomGallery(.5));
document.getElementById('gallery-zoom-out').addEventListener('click', () => zoomGallery(-.5));
document.getElementById('gallery-zoom-reset').addEventListener('click', () => { lightboxZoom = 1; renderLightbox(); });
document.getElementById('gallery-thumbnails').addEventListener('click', event => { const button = event.target.closest('[data-lightbox-index]'); if (button) { lightboxIndex = Number(button.dataset.lightboxIndex); lightboxZoom = 1; renderLightbox(); } });
lightbox.addEventListener('click', event => { if (event.target === lightbox) lightbox.close(); });
document.getElementById('gallery-stage').addEventListener('touchstart', event => { touchStartX = event.touches[0].clientX; }, {passive: true});
document.getElementById('gallery-stage').addEventListener('touchend', event => { if (touchStartX == null) return; const change = event.changedTouches[0].clientX - touchStartX; if (Math.abs(change) > 60) moveGallery(change > 0 ? -1 : 1); touchStartX = null; }, {passive: true});
document.addEventListener('keydown', event => { if (!lightbox.open) return; if (event.key === 'ArrowLeft') moveGallery(-1); if (event.key === 'ArrowRight') moveGallery(1); if (event.key === '+') zoomGallery(.5); if (event.key === '-') zoomGallery(-.5); });

async function loadBackups() {
  const target = document.getElementById('backup-list');
  try {
    const data = await apiFetch('/api/delivery-backups/');
    document.getElementById('create-backup').disabled = !data.dr_storage_configured;
    document.getElementById('sync-replicas').disabled = !data.dr_storage_configured;
    const replication = data.replication;
    const healthy = data.dr_storage_configured && replication.failed_assets === 0 && replication.pending_assets === 0;
    document.getElementById('replication-health').innerHTML = `<article><span class="replication-icon ${healthy ? 'healthy' : 'attention'}"><i data-lucide="${healthy ? 'shield-check' : 'shield-alert'}"></i></span><div><strong>${data.dr_storage_configured ? `${replication.coverage_percent}% protected` : 'DR bucket not configured'}</strong><small>${replication.verified_assets} of ${replication.total_assets} files integrity verified</small></div></article><article><strong>${replication.pending_assets}</strong><small>Waiting to copy</small></article><article class="${replication.failed_assets ? 'has-error' : ''}"><strong>${replication.failed_assets}</strong><small>Need retry</small></article>`;
    target.innerHTML = data.backups.length ? data.backups.map(item => `<div class="backup-row"><span class="backup-icon ${item.status}"><i data-lucide="${item.status === 'complete' ? 'database-zap' : item.status === 'failed' ? 'circle-alert' : 'loader-circle'}"></i></span><div><strong>${formatDate(item.created_at)}</strong><small>${escape(item.created_by)} · ${item.delivery_count} deliveries · ${item.asset_count} cataloged files · ${formatBytes(item.size_bytes)}</small>${item.error ? `<p>${escape(item.error)}</p>` : ''}</div>${item.download_url ? `<a class="secondary-button" href="${item.download_url}"><i data-lucide="download"></i>Catalog</a>` : `<span class="delivery-status ${item.status === 'failed' ? 'issue' : 'pending'}">${escape(item.status)}</span>`}</div>`).join('') : '<p class="backup-empty">No metadata catalogs yet.</p>';
    lucide.createIcons();
  } catch (error) { target.innerHTML = `<div class="delivery-form-error">${escape(error.message)}</div>`; }
}

function selectedValues(id) { return [...document.getElementById(id).selectedOptions].map(option => option.value); }
function populateMultiSelect(id, items, value, label) { const select = document.getElementById(id); if (!select.options.length) select.innerHTML = items.map(item => `<option value="${escape(value(item))}">${escape(label(item))}</option>`).join(''); }
function exportDescription(item) {
  if (item.scope === 'all') return 'Entire DR bucket and backups';
  const parts = [];
  if (item.filters.date_from || item.filters.date_to) parts.push(`${item.filters.date_from || 'beginning'} to ${item.filters.date_to || 'today'}`);
  if (item.filters.store_ids?.length) parts.push(`${item.filters.store_ids.length} selected store${item.filters.store_ids.length === 1 ? '' : 's'}`);
  if (item.filters.keywords?.length) parts.push(`#${item.filters.keywords.join(', #')}`);
  return parts.join(' · ') || 'All deliveries';
}
async function loadExports() {
  clearTimeout(exportPoll);
  const target = document.getElementById('recovery-export-list');
  try {
    const data = await apiFetch('/api/delivery-recovery-exports/');
    populateMultiSelect('export-stores', data.stores, item => item.id, item => `${item.number} — ${item.name}`);
    populateMultiSelect('export-keywords', data.keywords, item => item.value, item => `#${item.name}`);
    document.getElementById('export-retention-note').textContent = `Ready ZIPs remain in DR for ${data.retention_days} days.`;
    const busy = data.exports.some(item => ['queued', 'running'].includes(item.status));
    target.innerHTML = data.exports.length ? data.exports.map(item => `<div class="backup-row recovery-export-row"><span class="backup-icon ${item.status}"><i data-lucide="${item.status === 'complete' ? 'file-archive' : item.status === 'failed' ? 'circle-alert' : item.status === 'expired' ? 'clock' : 'loader-circle'}"></i></span><div><strong>${escape(exportDescription(item))}</strong><small>${formatDate(item.created_at)} · ${escape(item.created_by)}${item.status === 'complete' ? ` · ${item.delivery_count} deliveries · ${item.file_count} files · ${formatBytes(item.size_bytes)}` : ''}</small>${item.error ? `<p>${escape(item.error)}</p>` : ''}${item.expires_at ? `<small>Available until ${formatDate(item.expires_at)}</small>` : ''}</div>${item.download_url ? `<a class="secondary-button" href="${item.download_url}"><i data-lucide="download"></i>Download ZIP</a>` : `<span class="delivery-status ${item.status === 'failed' ? 'issue' : item.status === 'complete' ? 'verified' : 'pending'}">${escape(item.status_label)}</span>`}</div>`).join('') : '<p class="backup-empty">No recovery ZIP has been built yet.</p>';
    lucide.createIcons();
    if (busy) exportPoll = window.setTimeout(loadExports, 2500);
  } catch (error) { target.innerHTML = `<div class="delivery-form-error">${escape(error.message)}</div>`; }
}
async function createExport(scope, button) {
  const payload = {scope, include_catalogs: scope === 'all' || document.getElementById('export-catalogs').checked};
  if (scope !== 'all') { payload.date_from = document.getElementById('export-date-from').value; payload.date_to = document.getElementById('export-date-to').value; payload.store_ids = selectedValues('export-stores'); payload.keywords = selectedValues('export-keywords'); }
  button.disabled = true;
  try { await apiFetch('/api/delivery-recovery-exports/', {method: 'POST', body: JSON.stringify(payload)}); showToast('Recovery ZIP queued'); await loadExports(); } catch (error) { showToast(error.message, true); } finally { button.disabled = false; }
}

document.getElementById('export-filtered').addEventListener('click', event => createExport('filtered', event.currentTarget));
document.getElementById('export-all').addEventListener('click', event => createExport('all', event.currentTarget));
document.getElementById('refresh-exports').addEventListener('click', loadExports);
document.getElementById('create-backup').addEventListener('click', async event => { const button = event.currentTarget; button.disabled = true; try { await apiFetch('/api/delivery-backups/', {method: 'POST', body: '{}'}); showToast('DR metadata catalog created'); await loadBackups(); } catch (error) { showToast(error.message, true); } finally { button.disabled = false; } });
document.getElementById('sync-replicas').addEventListener('click', async event => { const button = event.currentTarget; button.disabled = true; try { await apiFetch('/api/delivery-backups/', {method: 'POST', body: JSON.stringify({action: 'sync'})}); showToast('Recovery sync queued'); window.setTimeout(loadBackups, 2500); } catch (error) { showToast(error.message, true); } finally { button.disabled = false; } });

loadReview(); loadBackups(); loadExports();

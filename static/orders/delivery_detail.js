const {escape, formatDate, formatBytes, statusBadge} = deliveryUI;
const root = document.getElementById('delivery-detail');
const deliveryId = root.dataset.deliveryId;
const isAdmin = root.dataset.isAdmin === 'true';
let delivery;
let keywords = [];
let proofGroups = {invoice: [], boxes: [], damage: []};
let selectedPhoto = {invoice: 0, boxes: 0, damage: 0};
const detailComparePanes = {
  invoice: {category: 'invoice', assets: [], index: 0, zoom: 1, pointer: null},
  evidence: {category: 'boxes', assets: [], index: 0, zoom: 1, pointer: null},
};
let activeDetailComparePane = 'invoice';

function countLabel(count, word = 'photo') { return `${count} ${word}${count === 1 ? '' : 's'}`; }
function categoryLabel(category) { return {invoice: 'Invoice / bill', boxes: 'Cases received', damage: 'Damage evidence'}[category] || category; }
function clampSelected(group) { selectedPhoto[group] = Math.min(selectedPhoto[group] || 0, Math.max(0, proofGroups[group].length - 1)); }

function galleryMarkup(assets, emptyLabel, group) {
  if (!assets.length) return `<div class="proof-empty"><i data-lucide="image-off"></i><p>No ${emptyLabel} photos</p></div>`;
  const activeIndex = Math.min(selectedPhoto[group], assets.length - 1);
  const asset = assets[activeIndex];
  const thumbnails = assets.length > 1 ? `<div class="detail-proof-thumbnails" aria-label="${escape(categoryLabel(group))} photos">${assets.map((item, index) => `<button type="button" data-select-proof="${group}" data-proof-index="${index}" class="${index === activeIndex ? 'active' : ''}" aria-label="Show ${escape(categoryLabel(group))} photo ${index + 1}" aria-pressed="${index === activeIndex}"><img src="${item.view_url}" loading="lazy" alt=""><span>${index + 1}</span></button>`).join('')}</div>` : '';
  return `<div class="detail-proof-viewer"><button class="proof-photo proof-photo-main" data-asset="${asset.uuid}" type="button" aria-label="Inspect ${escape(categoryLabel(group))} photo ${activeIndex + 1}"><span class="proof-image-frame"><img src="${asset.view_url}" loading="lazy" alt="${escape(categoryLabel(group))} proof ${activeIndex + 1}">${isAdmin ? `<em class="replica-badge ${asset.replica_status === 'verified' ? 'verified' : 'pending'}"><i data-lucide="${asset.replica_status === 'verified' ? 'shield-check' : 'cloud-upload'}"></i>${asset.replica_status === 'verified' ? 'DR protected' : 'DR syncing'}</em>` : ''}<b class="proof-inspect-hint"><i data-lucide="scan-search"></i>Inspect &amp; zoom</b></span><span class="proof-file-caption"><strong>${activeIndex + 1}</strong>${escape(asset.filename)} · ${formatBytes(asset.size_bytes)}</span><i data-lucide="maximize-2"></i></button>${thumbnails}</div>`;
}

function renderProofGalleries() {
  document.getElementById('invoice-gallery').innerHTML = galleryMarkup(proofGroups.invoice, 'invoice', 'invoice');
  document.getElementById('boxes-gallery').innerHTML = galleryMarkup(proofGroups.boxes, 'box', 'boxes');
  document.getElementById('damage-section').hidden = !proofGroups.damage.length;
  document.getElementById('damage-gallery').innerHTML = galleryMarkup(proofGroups.damage, 'damage', 'damage');
  document.querySelectorAll('.proof-photo-main').forEach(button => button.addEventListener('click', () => openLightbox(button.dataset.asset)));
  document.querySelectorAll('[data-select-proof]').forEach(button => button.addEventListener('click', () => {
    selectedPhoto[button.dataset.selectProof] = Number(button.dataset.proofIndex);
    renderProofGalleries();
  }));
  lucide.createIcons();
}

function render() {
  proofGroups = {
    invoice: delivery.assets.filter(item => item.category === 'invoice' && item.status === 'uploaded'),
    boxes: delivery.assets.filter(item => item.category === 'boxes' && item.status === 'uploaded'),
    damage: delivery.assets.filter(item => item.category === 'damage' && item.status === 'uploaded'),
  };
  Object.keys(proofGroups).forEach(clampSelected);
  document.getElementById('detail-store').textContent = `${delivery.store.number} — ${delivery.store.name}`;
  document.getElementById('detail-subline').textContent = `${formatDate(delivery.delivered_at)} · Submitted by ${delivery.submitted_by}${delivery.reference_number ? ` · Invoice ${delivery.reference_number}` : ''}`;
  document.getElementById('detail-head-actions').innerHTML = `${statusBadge(delivery)}<button id="compare-proof-button" class="secondary-button" type="button"><i data-lucide="columns-2"></i>Compare proof</button>${delivery.download_url ? `<a class="secondary-button" href="${delivery.download_url}"><i data-lucide="download"></i>Download proof</a>` : ''}`;
  const alert = document.getElementById('detail-alert');
  if (delivery.has_issue) { alert.hidden = false; alert.innerHTML = `<i data-lucide="triangle-alert"></i><div><strong>Exception evidence attached</strong><p>${escape(delivery.issue_notes || 'Damage photos or a case-count difference were recorded. Review before verifying.')}</p></div>`; }
  else alert.hidden = true;
  document.getElementById('invoice-count').textContent = countLabel(proofGroups.invoice.length);
  document.getElementById('boxes-count').textContent = countLabel(proofGroups.boxes.length);
  renderProofGalleries();
  const difference = delivery.expected_cases != null && delivery.delivered_cases != null ? delivery.delivered_cases - delivery.expected_cases : null;
  document.getElementById('delivery-facts').innerHTML = `<div class="verify-card-title"><i data-lucide="clipboard-check"></i><h2>Delivery facts</h2></div><dl class="delivery-facts"><div><dt>Status</dt><dd>${escape(delivery.status_label)}</dd></div><div><dt>Invoice / reference</dt><dd>${escape(delivery.reference_number || 'Not provided')}</dd></div><div><dt>Expected cases</dt><dd>${delivery.expected_cases ?? '—'}</dd></div><div><dt>Delivered cases</dt><dd>${delivery.delivered_cases ?? '—'}</dd></div><div class="${difference < 0 ? 'fact-warning' : ''}"><dt>Difference</dt><dd>${difference == null ? '—' : difference > 0 ? `+${difference}` : difference}</dd></div><div class="${delivery.damaged_cases ? 'fact-warning' : ''}"><dt>Damaged</dt><dd>${delivery.damaged_cases}</dd></div></dl><div class="storage-path"><span>Primary + DR object path</span><code>${escape(delivery.storage_prefix || 'Private storage')}</code></div>`;
  document.getElementById('worker-notes').innerHTML = `<div><span>Delivery notes</span><p>${escape(delivery.general_notes || 'No general notes')}</p></div><div class="${delivery.issue_notes ? 'issue' : ''}"><span>Issue notes</span><p>${escape(delivery.issue_notes || 'No issue notes')}</p></div>`;
  document.getElementById('delivery-timeline').innerHTML = delivery.events.length ? delivery.events.map(event => `<div><span class="timeline-dot"></span><p><strong>${escape(event.message)}</strong><small>${escape(event.actor || 'System')} · ${formatDate(event.created_at)}</small></p></div>`).join('') : '<p>No activity recorded.</p>';
  if (isAdmin) { document.getElementById('review-decision').value = ['submitted', 'draft'].includes(delivery.status) ? 'under_review' : delivery.status; document.getElementById('admin-notes').value = delivery.admin_notes || ''; keywords = [...delivery.keywords]; renderKeywords(); }
  document.getElementById('compare-proof-button').addEventListener('click', () => openLightbox(proofGroups.invoice[0]?.uuid || proofGroups.boxes[0]?.uuid || proofGroups.damage[0]?.uuid));
  lucide.createIcons();
}

function renderKeywords() {
  document.getElementById('keyword-chips').innerHTML = keywords.length ? keywords.map((word, index) => `<button type="button" data-keyword-index="${index}" title="Remove keyword">#${escape(word)} <i data-lucide="x"></i></button>`).join('') : '<span>No keywords yet</span>';
  document.querySelectorAll('[data-keyword-index]').forEach(button => button.addEventListener('click', () => { keywords.splice(Number(button.dataset.keywordIndex), 1); renderKeywords(); }));
  lucide.createIcons();
}
function addKeyword() { const input = document.getElementById('keyword-input'); const value = input.value.trim().replace(/^#/, ''); if (value && !keywords.some(word => word.toLowerCase() === value.toLowerCase())) keywords.push(value.slice(0, 64)); input.value = ''; renderKeywords(); }
if (isAdmin) {
  document.getElementById('add-keyword').addEventListener('click', addKeyword);
  document.getElementById('keyword-input').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); addKeyword(); } });
  document.getElementById('save-keywords').addEventListener('click', async event => { event.currentTarget.disabled = true; try { await apiFetch(`/api/deliveries/${deliveryId}/keywords/`, {method: 'PUT', body: JSON.stringify({keywords})}); showToast('Search keywords saved'); await load(); } catch (error) { showToast(error.message, true); } finally { event.currentTarget.disabled = false; } });
  document.getElementById('save-review').addEventListener('click', async event => { const button = event.currentTarget; button.disabled = true; try { delivery = await apiFetch(`/api/deliveries/${deliveryId}/review/`, {method: 'POST', body: JSON.stringify({status: document.getElementById('review-decision').value, admin_notes: document.getElementById('admin-notes').value})}); showToast('Verification decision saved'); render(); } catch (error) { showToast(error.message, true); } finally { button.disabled = false; } });
}

const lightbox = document.getElementById('proof-lightbox');
function openLightbox(uuid) {
  const target = Object.values(proofGroups).flat().find(item => item.uuid === uuid);
  const evidenceCategory = target?.category === 'damage' ? 'damage' : 'boxes';
  detailComparePanes.invoice.assets = proofGroups.invoice;
  detailComparePanes.invoice.index = target?.category === 'invoice' ? Math.max(0, proofGroups.invoice.findIndex(item => item.uuid === uuid)) : selectedPhoto.invoice;
  detailComparePanes.invoice.zoom = 1;
  detailComparePanes.evidence.category = evidenceCategory;
  detailComparePanes.evidence.assets = proofGroups[evidenceCategory];
  detailComparePanes.evidence.index = target?.category === evidenceCategory ? Math.max(0, proofGroups[evidenceCategory].findIndex(item => item.uuid === uuid)) : selectedPhoto[evidenceCategory];
  detailComparePanes.evidence.zoom = 1;
  activeDetailComparePane = target?.category === 'invoice' ? 'invoice' : 'evidence';
  document.getElementById('detail-compare-title').textContent = evidenceCategory === 'damage' ? 'Invoice and damage evidence' : 'Invoice and received cases';
  renderDetailComparePane('invoice', true); renderDetailComparePane('evidence', true); lightbox.showModal();
}
function detailPaneElement(name) { return lightbox.querySelector(`[data-detail-compare-pane="${name}"]`); }
function centerDetailPane(name) { const scroll = detailPaneElement(name).querySelector('[data-compare-scroll]'); scroll.scrollLeft = (scroll.scrollWidth - scroll.clientWidth) / 2; scroll.scrollTop = (scroll.scrollHeight - scroll.clientHeight) / 2; }
function updateDetailZoom(name, nextZoom, resetPosition = false) {
  const state = detailComparePanes[name], pane = detailPaneElement(name), scroll = pane.querySelector('[data-compare-scroll]');
  const oldMaxX = Math.max(1, scroll.scrollWidth - scroll.clientWidth), oldMaxY = Math.max(1, scroll.scrollHeight - scroll.clientHeight), xRatio = scroll.scrollLeft / oldMaxX, yRatio = scroll.scrollTop / oldMaxY;
  state.zoom = Math.max(1, Math.min(5, Math.round(nextZoom * 2) / 2));
  pane.querySelector('[data-compare-image]').style.transform = `scale(${state.zoom})`;
  pane.querySelector('[data-compare-zoom-reset]').textContent = `${Math.round(state.zoom * 100)}%`;
  pane.querySelector('[data-compare-zoom-out]').disabled = state.zoom === 1;
  pane.querySelector('[data-compare-zoom-in]').disabled = state.zoom === 5;
  scroll.classList.toggle('zoomed', state.zoom > 1);
  requestAnimationFrame(() => { if (resetPosition) centerDetailPane(name); else { scroll.scrollLeft = xRatio * Math.max(0, scroll.scrollWidth - scroll.clientWidth); scroll.scrollTop = yRatio * Math.max(0, scroll.scrollHeight - scroll.clientHeight); } });
}
function renderDetailComparePane(name, resetPosition = false) {
  const state = detailComparePanes[name], pane = detailPaneElement(name), asset = state.assets[state.index], image = pane.querySelector('[data-compare-image]'), empty = pane.querySelector('[data-compare-empty]'), original = pane.querySelector('[data-compare-original]');
  if (name === 'evidence') pane.querySelector('[data-compare-side-label]').textContent = state.category === 'damage' ? 'RIGHT · DAMAGE EVIDENCE' : 'RIGHT · RECEIVED CASES';
  pane.classList.toggle('active', activeDetailComparePane === name); image.hidden = !asset; empty.hidden = Boolean(asset); original.hidden = !asset;
  pane.querySelector('[data-compare-filename]').textContent = asset?.filename || (name === 'invoice' ? 'No invoice photos' : state.category === 'damage' ? 'No damage photos' : 'No box photos');
  pane.querySelector('[data-compare-position]').textContent = asset ? `${state.index + 1} / ${state.assets.length}` : '0 / 0';
  pane.querySelector('[data-compare-previous]').disabled = state.assets.length < 2; pane.querySelector('[data-compare-next]').disabled = state.assets.length < 2;
  pane.querySelector('[data-compare-caption]').textContent = asset ? `${formatBytes(asset.size_bytes)}${isAdmin ? ` · ${asset.replica_status === 'verified' ? 'DR protected' : 'DR syncing'}` : ''}` : '';
  pane.querySelector('[data-compare-thumbnails]').innerHTML = state.assets.map((item, index) => `<button type="button" data-compare-index="${index}" class="${index === state.index ? 'active' : ''}" aria-label="Show ${escape(categoryLabel(state.category))} photo ${index + 1}"><img src="${item.view_url}" alt=""><span>${index + 1}</span></button>`).join('');
  if (asset) { original.href = asset.view_url; image.alt = `${categoryLabel(state.category)} — ${asset.filename}`; if (image.src !== asset.view_url) { image.onload = () => centerDetailPane(name); image.src = asset.view_url; } }
  updateDetailZoom(name, state.zoom, resetPosition); pane.querySelector('[data-compare-zoom-reset]').disabled = !asset;
  if (!asset) { pane.querySelector('[data-compare-zoom-out]').disabled = true; pane.querySelector('[data-compare-zoom-in]').disabled = true; }
}
function selectDetailPhoto(name, index) { detailComparePanes[name].index = index; detailComparePanes[name].zoom = 1; activeDetailComparePane = name; lightbox.querySelectorAll('[data-detail-compare-pane]').forEach(pane => pane.classList.toggle('active', pane.dataset.detailComparePane === name)); renderDetailComparePane(name, true); }
function moveDetailPhoto(name, amount) { const state = detailComparePanes[name]; if (state.assets.length) selectDetailPhoto(name, (state.index + amount + state.assets.length) % state.assets.length); }
document.getElementById('detail-lightbox-close').addEventListener('click', () => lightbox.close());
lightbox.querySelectorAll('[data-detail-compare-pane]').forEach(pane => {
  const name = pane.dataset.detailComparePane, state = detailComparePanes[name], scroll = pane.querySelector('[data-compare-scroll]'), image = pane.querySelector('[data-compare-image]');
  pane.addEventListener('pointerdown', () => { activeDetailComparePane = name; lightbox.querySelectorAll('[data-detail-compare-pane]').forEach(item => item.classList.toggle('active', item === pane)); });
  pane.querySelector('[data-compare-previous]').addEventListener('click', () => moveDetailPhoto(name, -1)); pane.querySelector('[data-compare-next]').addEventListener('click', () => moveDetailPhoto(name, 1));
  pane.querySelector('[data-compare-zoom-out]').addEventListener('click', () => updateDetailZoom(name, state.zoom - .5)); pane.querySelector('[data-compare-zoom-in]').addEventListener('click', () => updateDetailZoom(name, state.zoom + .5)); pane.querySelector('[data-compare-zoom-reset]').addEventListener('click', () => updateDetailZoom(name, 1, true));
  pane.querySelector('[data-compare-thumbnails]').addEventListener('click', event => { const button = event.target.closest('[data-compare-index]'); if (button) selectDetailPhoto(name, Number(button.dataset.compareIndex)); });
  image.addEventListener('dblclick', () => updateDetailZoom(name, state.zoom === 1 ? 2 : 1, true));
  scroll.addEventListener('wheel', event => { if (!(event.ctrlKey || event.metaKey)) return; event.preventDefault(); updateDetailZoom(name, state.zoom + (event.deltaY < 0 ? .5 : -.5)); }, {passive: false});
  scroll.addEventListener('pointerdown', event => { if (state.zoom === 1) return; state.pointer = {x: event.clientX, y: event.clientY, left: scroll.scrollLeft, top: scroll.scrollTop}; scroll.setPointerCapture(event.pointerId); scroll.classList.add('dragging'); event.preventDefault(); });
  scroll.addEventListener('pointermove', event => { if (state.pointer) { scroll.scrollLeft = state.pointer.left - (event.clientX - state.pointer.x); scroll.scrollTop = state.pointer.top - (event.clientY - state.pointer.y); } });
  const finish = () => { state.pointer = null; scroll.classList.remove('dragging'); }; scroll.addEventListener('pointerup', finish); scroll.addEventListener('pointercancel', finish);
});
lightbox.addEventListener('click', event => { if (event.target === lightbox) lightbox.close(); });
document.addEventListener('keydown', event => {
  if (!lightbox.open) return;
  const state = detailComparePanes[activeDetailComparePane];
  if (event.key === 'ArrowLeft') moveDetailPhoto(activeDetailComparePane, -1);
  if (event.key === 'ArrowRight') moveDetailPhoto(activeDetailComparePane, 1);
  if (event.key === '+' || event.key === '=') updateDetailZoom(activeDetailComparePane, state.zoom + .5);
  if (event.key === '-') updateDetailZoom(activeDetailComparePane, state.zoom - .5);
  if (event.key === '0') updateDetailZoom(activeDetailComparePane, 1, true);
});

async function load() { try { delivery = await apiFetch(`/api/deliveries/${deliveryId}/`); render(); } catch (error) { root.innerHTML = `<div class="delivery-form-error">${escape(error.message)}</div>`; } }
load();

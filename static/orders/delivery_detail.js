const {escape, formatDate, formatBytes, statusBadge} = deliveryUI;
const root = document.getElementById('delivery-detail');
const deliveryId = root.dataset.deliveryId;
const isAdmin = root.dataset.isAdmin === 'true';
let delivery;
let keywords = [];
let proofGroups = {invoice: [], boxes: [], damage: []};
let selectedPhoto = {invoice: 0, boxes: 0, damage: 0};
let lightboxPhotos = [];
let lightboxIndex = 0;
let lightboxZoom = 1;
let pointerStart = null;

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
  lightboxPhotos = [...proofGroups.invoice, ...proofGroups.boxes, ...proofGroups.damage];
  document.getElementById('detail-store').textContent = `${delivery.store.number} — ${delivery.store.name}`;
  document.getElementById('detail-subline').textContent = `${formatDate(delivery.delivered_at)} · Submitted by ${delivery.submitted_by}${delivery.reference_number ? ` · Invoice ${delivery.reference_number}` : ''}`;
  document.getElementById('detail-head-actions').innerHTML = `${statusBadge(delivery)}${delivery.download_url ? `<a class="secondary-button" href="${delivery.download_url}"><i data-lucide="download"></i>Download proof</a>` : ''}`;
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
const lightboxImage = document.getElementById('detail-gallery-image');
const lightboxScroll = document.getElementById('detail-gallery-scroll');
const zoomReset = document.getElementById('detail-zoom-reset');
function setLightboxZoom(nextZoom, keepPosition = false) {
  lightboxZoom = Math.max(1, Math.min(5, Math.round(nextZoom * 2) / 2));
  lightboxImage.style.transform = `scale(${lightboxZoom})`;
  zoomReset.textContent = `${Math.round(lightboxZoom * 100)}%`;
  lightboxScroll.classList.toggle('zoomed', lightboxZoom > 1);
  document.getElementById('detail-zoom-out').disabled = lightboxZoom === 1;
  document.getElementById('detail-zoom-in').disabled = lightboxZoom === 5;
  if (!keepPosition) requestAnimationFrame(() => {
    lightboxScroll.scrollLeft = (lightboxScroll.scrollWidth - lightboxScroll.clientWidth) / 2;
    lightboxScroll.scrollTop = (lightboxScroll.scrollHeight - lightboxScroll.clientHeight) / 2;
  });
}
function lightboxThumbnailMarkup() {
  return lightboxPhotos.map((asset, index) => `<button type="button" data-detail-gallery-index="${index}" class="${index === lightboxIndex ? 'active' : ''}" aria-label="Show ${escape(categoryLabel(asset.category))} photo ${index + 1}"><img src="${asset.view_url}" loading="lazy" alt=""><span>${escape(categoryLabel(asset.category))}</span></button>`).join('');
}
function showLightboxPhoto() {
  const asset = lightboxPhotos[lightboxIndex];
  if (!asset) return;
  setLightboxZoom(1);
  lightboxImage.src = asset.view_url;
  lightboxImage.alt = `${categoryLabel(asset.category)} — ${asset.filename}`;
  document.getElementById('detail-gallery-category').textContent = categoryLabel(asset.category).toUpperCase();
  document.getElementById('detail-gallery-title').textContent = asset.filename;
  document.getElementById('detail-gallery-caption').textContent = `${categoryLabel(asset.category)} · ${lightboxIndex + 1} of ${lightboxPhotos.length} · ${formatBytes(asset.size_bytes)}${isAdmin ? ` · ${asset.replica_status === 'verified' ? 'DR protected' : 'DR syncing'}` : ''}`;
  document.getElementById('detail-gallery-original').href = asset.view_url;
  document.getElementById('detail-gallery-thumbnails').innerHTML = lightboxThumbnailMarkup();
  document.querySelectorAll('[data-detail-gallery-index]').forEach(button => button.addEventListener('click', () => { lightboxIndex = Number(button.dataset.detailGalleryIndex); showLightboxPhoto(); }));
}
function openLightbox(uuid) {
  lightboxIndex = Math.max(0, lightboxPhotos.findIndex(item => item.uuid === uuid));
  showLightboxPhoto();
  lightbox.showModal();
}
function moveLightbox(amount) {
  if (!lightboxPhotos.length) return;
  lightboxIndex = (lightboxIndex + amount + lightboxPhotos.length) % lightboxPhotos.length;
  showLightboxPhoto();
}
document.getElementById('detail-lightbox-close').addEventListener('click', () => lightbox.close());
document.getElementById('detail-gallery-previous').addEventListener('click', () => moveLightbox(-1));
document.getElementById('detail-gallery-next').addEventListener('click', () => moveLightbox(1));
document.getElementById('detail-zoom-out').addEventListener('click', () => setLightboxZoom(lightboxZoom - .5));
document.getElementById('detail-zoom-in').addEventListener('click', () => setLightboxZoom(lightboxZoom + .5));
zoomReset.addEventListener('click', () => setLightboxZoom(1));
lightboxImage.addEventListener('dblclick', () => setLightboxZoom(lightboxZoom === 1 ? 2 : 1));
lightboxScroll.addEventListener('wheel', event => {
  if (!(event.ctrlKey || event.metaKey)) return;
  event.preventDefault();
  setLightboxZoom(lightboxZoom + (event.deltaY < 0 ? .5 : -.5), true);
}, {passive: false});
lightboxScroll.addEventListener('pointerdown', event => {
  pointerStart = {x: event.clientX, y: event.clientY, scrollLeft: lightboxScroll.scrollLeft, scrollTop: lightboxScroll.scrollTop};
  lightboxScroll.setPointerCapture(event.pointerId);
  lightboxScroll.classList.add('dragging');
});
lightboxScroll.addEventListener('pointermove', event => {
  if (!pointerStart || lightboxZoom === 1) return;
  lightboxScroll.scrollLeft = pointerStart.scrollLeft - (event.clientX - pointerStart.x);
  lightboxScroll.scrollTop = pointerStart.scrollTop - (event.clientY - pointerStart.y);
});
function finishPointer(event) {
  if (!pointerStart) return;
  const deltaX = event.clientX - pointerStart.x;
  if (lightboxZoom === 1 && Math.abs(deltaX) > 60) moveLightbox(deltaX < 0 ? 1 : -1);
  pointerStart = null;
  lightboxScroll.classList.remove('dragging');
}
lightboxScroll.addEventListener('pointerup', finishPointer);
lightboxScroll.addEventListener('pointercancel', finishPointer);
lightbox.addEventListener('click', event => { if (event.target === lightbox) lightbox.close(); });
document.addEventListener('keydown', event => {
  if (!lightbox.open) return;
  if (event.key === 'ArrowLeft') moveLightbox(-1);
  if (event.key === 'ArrowRight') moveLightbox(1);
  if (event.key === '+' || event.key === '=') setLightboxZoom(lightboxZoom + .5);
  if (event.key === '-') setLightboxZoom(lightboxZoom - .5);
  if (event.key === '0') setLightboxZoom(1);
});

async function load() { try { delivery = await apiFetch(`/api/deliveries/${deliveryId}/`); render(); } catch (error) { root.innerHTML = `<div class="delivery-form-error">${escape(error.message)}</div>`; } }
load();

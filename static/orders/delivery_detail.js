const {escape, formatDate, formatBytes, statusBadge} = deliveryUI;
const root = document.getElementById('delivery-detail');
const deliveryId = root.dataset.deliveryId;
const isAdmin = root.dataset.isAdmin === 'true';
let delivery;
let keywords = [];
let lightboxPhotos = [];
let lightboxIndex = 0;

function countLabel(count, word = 'photo') { return `${count} ${word}${count === 1 ? '' : 's'}`; }
function galleryMarkup(assets, category) {
  if (!assets.length) return `<div class="proof-empty"><i data-lucide="image-off"></i><p>No ${category} photos</p></div>`;
  return assets.map((asset, index) => `<button class="proof-photo" data-asset="${asset.uuid}" type="button"><img src="${asset.view_url}" loading="lazy" alt="${escape(category)} proof ${index + 1}">${isAdmin ? `<em class="replica-badge ${asset.replica_status === 'verified' ? 'verified' : 'pending'}"><i data-lucide="${asset.replica_status === 'verified' ? 'shield-check' : 'cloud-upload'}"></i>${asset.replica_status === 'verified' ? 'DR protected' : 'DR syncing'}</em>` : ''}<span><strong>${index + 1}</strong>${escape(asset.filename)} · ${formatBytes(asset.size_bytes)}</span><i data-lucide="maximize-2"></i></button>`).join('');
}

function render() {
  const invoice = delivery.assets.filter(item => item.category === 'invoice' && item.status === 'uploaded');
  const boxes = delivery.assets.filter(item => item.category === 'boxes' && item.status === 'uploaded');
  const damage = delivery.assets.filter(item => item.category === 'damage' && item.status === 'uploaded');
  lightboxPhotos = [...invoice, ...boxes, ...damage];
  document.getElementById('detail-store').textContent = `${delivery.store.number} — ${delivery.store.name}`;
  document.getElementById('detail-subline').textContent = `${formatDate(delivery.delivered_at)} · Submitted by ${delivery.submitted_by}${delivery.reference_number ? ` · Invoice ${delivery.reference_number}` : ''}`;
  document.getElementById('detail-head-actions').innerHTML = `${statusBadge(delivery)}${delivery.download_url ? `<a class="secondary-button" href="${delivery.download_url}"><i data-lucide="download"></i>Download proof</a>` : ''}`;
  const alert = document.getElementById('detail-alert');
  if (delivery.has_issue) { alert.hidden = false; alert.innerHTML = `<i data-lucide="triangle-alert"></i><div><strong>Exception evidence attached</strong><p>${escape(delivery.issue_notes || 'Damage photos or a case-count difference were recorded. Review before verifying.')}</p></div>`; }
  else alert.hidden = true;
  document.getElementById('invoice-count').textContent = countLabel(invoice.length);
  document.getElementById('boxes-count').textContent = countLabel(boxes.length);
  document.getElementById('invoice-gallery').innerHTML = galleryMarkup(invoice, 'invoice');
  document.getElementById('boxes-gallery').innerHTML = galleryMarkup(boxes, 'box');
  document.getElementById('damage-section').hidden = !damage.length;
  document.getElementById('damage-gallery').innerHTML = galleryMarkup(damage, 'damage');
  const difference = delivery.expected_cases != null && delivery.delivered_cases != null ? delivery.delivered_cases - delivery.expected_cases : null;
  document.getElementById('delivery-facts').innerHTML = `<div class="verify-card-title"><i data-lucide="clipboard-check"></i><h2>Delivery facts</h2></div><dl class="delivery-facts"><div><dt>Status</dt><dd>${escape(delivery.status_label)}</dd></div><div><dt>Invoice / reference</dt><dd>${escape(delivery.reference_number || 'Not provided')}</dd></div><div><dt>Expected cases</dt><dd>${delivery.expected_cases ?? '—'}</dd></div><div><dt>Delivered cases</dt><dd>${delivery.delivered_cases ?? '—'}</dd></div><div class="${difference < 0 ? 'fact-warning' : ''}"><dt>Difference</dt><dd>${difference == null ? '—' : difference > 0 ? `+${difference}` : difference}</dd></div><div class="${delivery.damaged_cases ? 'fact-warning' : ''}"><dt>Damaged</dt><dd>${delivery.damaged_cases}</dd></div></dl><div class="storage-path"><span>Primary + DR object path</span><code>${escape(delivery.storage_prefix || 'Private storage')}</code></div>`;
  document.getElementById('worker-notes').innerHTML = `<div><span>Delivery notes</span><p>${escape(delivery.general_notes || 'No general notes')}</p></div><div class="${delivery.issue_notes ? 'issue' : ''}"><span>Issue notes</span><p>${escape(delivery.issue_notes || 'No issue notes')}</p></div>`;
  document.getElementById('delivery-timeline').innerHTML = delivery.events.length ? delivery.events.map(event => `<div><span class="timeline-dot"></span><p><strong>${escape(event.message)}</strong><small>${escape(event.actor || 'System')} · ${formatDate(event.created_at)}</small></p></div>`).join('') : '<p>No activity recorded.</p>';
  if (isAdmin) { document.getElementById('review-decision').value = ['submitted', 'draft'].includes(delivery.status) ? 'under_review' : delivery.status; document.getElementById('admin-notes').value = delivery.admin_notes || ''; keywords = [...delivery.keywords]; renderKeywords(); }
  document.querySelectorAll('.proof-photo').forEach(button => button.addEventListener('click', () => openLightbox(button.dataset.asset)));
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
function showLightboxPhoto() { const asset = lightboxPhotos[lightboxIndex]; document.getElementById('lightbox-image').src = asset.view_url; document.getElementById('lightbox-caption').textContent = `${asset.category.toUpperCase()} · ${asset.filename} · ${lightboxIndex + 1} of ${lightboxPhotos.length}`; }
function openLightbox(uuid) { lightboxIndex = Math.max(0, lightboxPhotos.findIndex(item => item.uuid === uuid)); showLightboxPhoto(); lightbox.showModal(); }
function moveLightbox(amount) { lightboxIndex = (lightboxIndex + amount + lightboxPhotos.length) % lightboxPhotos.length; showLightboxPhoto(); }
lightbox.querySelector('.lightbox-close').addEventListener('click', () => lightbox.close());
lightbox.querySelector('.previous').addEventListener('click', () => moveLightbox(-1));
lightbox.querySelector('.next').addEventListener('click', () => moveLightbox(1));
lightbox.addEventListener('click', event => { if (event.target === lightbox) lightbox.close(); });
document.addEventListener('keydown', event => { if (!lightbox.open) return; if (event.key === 'ArrowLeft') moveLightbox(-1); if (event.key === 'ArrowRight') moveLightbox(1); });

async function load() { try { delivery = await apiFetch(`/api/deliveries/${deliveryId}/`); render(); } catch (error) { root.innerHTML = `<div class="delivery-form-error">${escape(error.message)}</div>`; } }
load();

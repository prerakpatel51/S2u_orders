const {escape} = deliveryUI;
const files = {invoice: [], boxes: [], damage: []};
const existingCounts = {invoice: 0, boxes: 0, damage: 0};
const form = document.getElementById('delivery-form');
const errorBox = document.getElementById('delivery-form-error');
const progress = document.getElementById('upload-progress');
const draftId = new URLSearchParams(location.search).get('draft');
let currentDeliveryId = draftId;
let storageConfigured = false;

const localDateTimeValue = date => {
  const value = new Date(date); value.setMinutes(value.getMinutes() - value.getTimezoneOffset()); return value.toISOString().slice(0, 16);
};
document.getElementById('delivered-at').value = localDateTimeValue(new Date());

async function initialize() {
  try {
    const [stores, deliveryData] = await Promise.all([apiFetch('/api/stores/'), apiFetch('/api/deliveries/')]);
    document.getElementById('delivery-store').innerHTML = '<option value="">Choose a store…</option>' + stores.map(store => `<option value="${store.id}">${escape(store.number)} — ${escape(store.name)}</option>`).join('');
    storageConfigured = deliveryData.storage_configured;
    const state = document.getElementById('storage-state');
    state.classList.toggle('ready', storageConfigured); state.classList.toggle('error', !storageConfigured);
    state.innerHTML = `<i data-lucide="${storageConfigured ? 'cloud-check' : 'cloud-off'}"></i>${storageConfigured ? 'Secure storage ready' : 'Storage setup required'}`;
    if (draftId) await loadDraft(draftId);
    lucide.createIcons();
  } catch (error) { showError(error.message); }
}

async function loadDraft(id) {
  const delivery = await apiFetch(`/api/deliveries/${id}/`);
  if (!delivery.can_edit) throw new Error('This delivery is no longer editable.');
  document.getElementById('delivery-store').value = delivery.store.id;
  document.getElementById('delivered-at').value = localDateTimeValue(delivery.delivered_at);
  document.getElementById('reference-number').value = delivery.reference_number || '';
  document.getElementById('expected-cases').value = delivery.expected_cases ?? '';
  document.getElementById('delivered-cases').value = delivery.delivered_cases ?? '';
  document.getElementById('damaged-cases').value = delivery.damaged_cases ?? 0;
  document.getElementById('general-notes').value = delivery.general_notes || '';
  document.getElementById('issue-notes').value = delivery.issue_notes || '';
  delivery.assets.filter(asset => asset.category !== 'notes' && asset.status === 'uploaded').forEach(asset => existingCounts[asset.category]++);
  if (Object.values(existingCounts).some(Boolean)) { document.getElementById('delivery-store').disabled = true; document.getElementById('delivered-at').disabled = true; document.getElementById('delivery-store').title = document.getElementById('delivered-at').title = 'Store and date are locked after the first photo upload.'; }
  Object.keys(existingCounts).forEach(renderPreviews);
  document.querySelector('.delivery-create-head h1').textContent = delivery.status === 'needs_info' ? 'Add requested delivery proof' : 'Continue delivery draft';
}

function showError(message) { errorBox.textContent = message; errorBox.hidden = false; errorBox.scrollIntoView({behavior: 'smooth', block: 'center'}); }
function clearError() { errorBox.hidden = true; errorBox.textContent = ''; }

document.querySelectorAll('[data-photo-input]').forEach(input => input.addEventListener('change', event => {
  const category = input.dataset.photoInput;
  [...event.target.files].forEach(file => { if (file.type.startsWith('image/')) files[category].push({id: crypto.randomUUID(), file, url: URL.createObjectURL(file)}); });
  input.value = ''; renderPreviews(category);
}));

function renderPreviews(category) {
  const target = document.querySelector(`[data-preview-list="${category}"]`);
  const retained = existingCounts[category] ? `<div class="existing-photo-count"><i data-lucide="cloud-check"></i>${existingCounts[category]} already uploaded</div>` : '';
  target.innerHTML = retained + files[category].map(item => `<div class="photo-preview" data-file-id="${item.id}"><img src="${item.url}" alt="Selected ${category} photo"><button type="button" aria-label="Remove photo"><i data-lucide="x"></i></button><span>${escape(item.file.name)}</span></div>`).join('');
  target.querySelectorAll('.photo-preview button').forEach(button => button.addEventListener('click', () => { const id = button.closest('.photo-preview').dataset.fileId; const index = files[category].findIndex(item => item.id === id); if (index >= 0) { URL.revokeObjectURL(files[category][index].url); files[category].splice(index, 1); renderPreviews(category); } }));
  const total = Object.values(files).reduce((sum, rows) => sum + rows.length, 0) + Object.values(existingCounts).reduce((a, b) => a + b, 0);
  document.getElementById('photo-count').textContent = `${total} photo${total === 1 ? '' : 's'}`;
  lucide.createIcons();
}

function payload() {
  const nullable = id => document.getElementById(id).value === '' ? null : Number(document.getElementById(id).value);
  return {store_id: document.getElementById('delivery-store').value, delivered_at: new Date(document.getElementById('delivered-at').value).toISOString(), reference_number: document.getElementById('reference-number').value, expected_cases: nullable('expected-cases'), delivered_cases: nullable('delivered-cases'), damaged_cases: nullable('damaged-cases') || 0, general_notes: document.getElementById('general-notes').value, issue_notes: document.getElementById('issue-notes').value};
}

async function compressImage(file) {
  const bitmap = await createImageBitmap(file);
  const maxSide = 2400; const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement('canvas'); canvas.width = Math.round(bitmap.width * scale); canvas.height = Math.round(bitmap.height * scale);
  const context = canvas.getContext('2d', {alpha: false}); context.fillStyle = '#fff'; context.fillRect(0, 0, canvas.width, canvas.height); context.drawImage(bitmap, 0, 0, canvas.width, canvas.height); bitmap.close();
  return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(new File([blob], file.name.replace(/\.[^.]+$/, '') + '.jpg', {type: 'image/jpeg'})) : reject(new Error('Could not prepare an image.')), 'image/jpeg', .86));
}

async function checksum(blob) { const bytes = await blob.arrayBuffer(); const hash = await crypto.subtle.digest('SHA-256', bytes); return [...new Uint8Array(hash)].map(value => value.toString(16).padStart(2, '0')).join(''); }
async function retryUpload(url, blob, headers) { let last; for (let attempt = 1; attempt <= 3; attempt++) { try { const response = await fetch(url, {method: 'PUT', headers, body: blob}); if (!response.ok) throw new Error(`Upload failed (${response.status})`); return; } catch (error) { last = error; if (attempt < 3) await new Promise(resolve => setTimeout(resolve, attempt * 700)); } } throw last; }

async function uploadFile(deliveryId, category, item) {
  let prepared;
  try { prepared = await compressImage(item.file); } catch (_) { prepared = item.file; }
  const reservation = await apiFetch(`/api/deliveries/${deliveryId}/assets/presign/`, {method: 'POST', body: JSON.stringify({category, filename: prepared.name, content_type: prepared.type, size_bytes: prepared.size})});
  await retryUpload(reservation.upload_url, prepared, reservation.headers);
  await apiFetch(`/api/deliveries/${deliveryId}/assets/${reservation.asset_uuid}/complete/`, {method: 'POST', body: JSON.stringify({checksum_sha256: await checksum(prepared)})});
}

function setProgress(done, total, detail) { progress.hidden = false; document.getElementById('upload-progress-title').textContent = total ? `Uploading photo ${Math.min(done + 1, total)} of ${total}` : 'Saving delivery'; document.getElementById('upload-progress-detail').textContent = detail || ''; document.getElementById('upload-progress-bar').style.width = `${total ? Math.round(done / total * 100) : 15}%`; }

async function save(submit) {
  clearError();
  if (!form.reportValidity()) return;
  const newCount = Object.values(files).reduce((sum, rows) => sum + rows.length, 0);
  if (submit && !storageConfigured) { showError('Railway Object Storage must be configured before delivery proof can be submitted.'); return; }
  if (submit && existingCounts.invoice + files.invoice.length < 1) { showError('Add at least one clear invoice photo.'); return; }
  if (submit && existingCounts.boxes + files.boxes.length < 1) { showError('Add at least one boxes photo.'); return; }
  document.querySelectorAll('.delivery-submit-bar button').forEach(button => button.disabled = true);
  try {
    const method = currentDeliveryId ? 'PATCH' : 'POST'; const endpoint = currentDeliveryId ? `/api/deliveries/${currentDeliveryId}/` : '/api/deliveries/';
    const delivery = await apiFetch(endpoint, {method, body: JSON.stringify(payload())}); currentDeliveryId = delivery.uuid;
    const queue = Object.entries(files).flatMap(([category, rows]) => rows.map(item => ({category, item})));
    for (let index = 0; index < queue.length; index++) { setProgress(index, queue.length, `Securing ${queue[index].category} photo…`); await uploadFile(currentDeliveryId, queue[index].category, queue[index].item); }
    setProgress(queue.length, queue.length, submit ? 'Submitting for admin verification…' : 'Draft saved securely');
    if (submit) await apiFetch(`/api/deliveries/${currentDeliveryId}/submit/`, {method: 'POST', body: '{}'});
    document.getElementById('upload-progress-bar').style.width = '100%';
    location.href = submit ? `/deliveries/${currentDeliveryId}/` : '/deliveries/';
  } catch (error) { showError(`${error.message}${currentDeliveryId ? ' Your draft was kept; you can safely try again.' : ''}`); document.querySelectorAll('.delivery-submit-bar button').forEach(button => button.disabled = false); }
}
form.addEventListener('submit', event => { event.preventDefault(); save(true); });
document.getElementById('save-draft').addEventListener('click', () => save(false));
initialize();

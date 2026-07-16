const {escape, formatDate, statusBadge, photoSummary, debounce} = deliveryUI;
const list = document.getElementById('delivery-list');
const summary = document.getElementById('delivery-summary');

function renderSummary(deliveries) {
  const counts = {all: deliveries.length, draft: 0, pending: 0, issue: 0, verified: 0};
  deliveries.forEach(item => { if (item.status === 'draft') counts.draft++; else if (['verified', 'resolved'].includes(item.status)) counts.verified++; else counts.pending++; if (item.has_issue && !['verified', 'resolved'].includes(item.status)) counts.issue++; });
  summary.innerHTML = `<article><i data-lucide="truck"></i><span><strong>${counts.all}</strong><small>Recent deliveries</small></span></article><article><i data-lucide="pencil-line"></i><span><strong>${counts.draft}</strong><small>Drafts</small></span></article><article><i data-lucide="clock-3"></i><span><strong>${counts.pending}</strong><small>Awaiting review</small></span></article><article class="${counts.issue ? 'attention' : ''}"><i data-lucide="triangle-alert"></i><span><strong>${counts.issue}</strong><small>Issues flagged</small></span></article>`;
}

function renderList(deliveries) {
  if (!deliveries.length) { list.innerHTML = `<div class="delivery-empty"><i data-lucide="camera"></i><h2>No delivery proof yet</h2><p>Record the invoice and cases when the next shipment arrives.</p><a href="/deliveries/new/" class="primary-button">Record first delivery</a></div>`; lucide.createIcons(); return; }
  list.innerHTML = deliveries.map(item => `<a class="delivery-list-row ${item.has_issue ? 'has-issue' : ''}" href="${item.can_edit ? `/deliveries/new/?draft=${item.uuid}` : item.detail_url}"><div class="delivery-store-badge"><strong>${escape(item.store.number)}</strong><span>Store</span></div><div class="delivery-row-main"><div><strong>${escape(item.store.name)}</strong>${item.reference_number ? `<span>Ref ${escape(item.reference_number)}</span>` : ''}</div><p>${escape(item.general_notes || item.issue_notes || 'No notes added')}</p><small>${formatDate(item.delivered_at)} · ${escape(photoSummary(item))}</small></div><div class="delivery-row-state">${statusBadge(item)}<span>${item.can_edit ? (item.status === 'needs_info' ? 'Add requested proof' : 'Continue draft') : 'View proof'} <i data-lucide="chevron-right"></i></span></div></a>`).join('');
  lucide.createIcons();
}

async function loadDeliveries(query = '') {
  try { const data = await apiFetch(`/api/deliveries/${query ? `?q=${encodeURIComponent(query)}` : ''}`); renderSummary(data.deliveries); renderList(data.deliveries); }
  catch (error) { list.innerHTML = `<div class="delivery-form-error">${escape(error.message)}</div>`; }
}
document.getElementById('delivery-search').addEventListener('input', debounce(event => loadDeliveries(event.target.value)));
loadDeliveries();

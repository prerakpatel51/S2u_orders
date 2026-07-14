const storeGrid = document.getElementById('store-grid');
const dialog = document.getElementById('new-order-dialog');
const storeSelect = document.getElementById('order-store');
let stores = [];
let orders = [];
const canManageOrderLists = Boolean(window.CAN_MANAGE_ORDER_LISTS);

async function loadHome() {
  try {
    [stores, orders] = await Promise.all([apiFetch('/api/stores/'), apiFetch('/api/orders/')]);
    const grouped = orders.reduce((map, order) => { (map[order.store.id] ||= []).push(order); return map; }, {});
    storeGrid.innerHTML = stores.length ? stores.map(store => {
      const recent = (grouped[store.id] || []).slice(0, 4);
      return `<article class="store-card"><div class="store-card-head"><div class="store-number">STORE</div><span>${store.order_count} lists</span></div><h2>${escapeHtml(store.number)}</h2><div class="recent-lists">${recent.length ? recent.map(order => `<div class="recent-list-row"><a href="/orders/${order.id}/"><span>${order.order_date}</span><span>${order.item_count} items</span></a>${canManageOrderLists ? `<button type="button" class="list-card-delete" data-delete-order="${order.id}" data-order-date="${order.order_date}" data-store-number="${escapeHtml(store.number)}" title="Delete list" aria-label="Delete ${order.order_date} list for store ${escapeHtml(store.number)}"><i data-lucide="trash-2"></i></button>` : ''}</div>`).join('') : '<p>No order lists yet</p>'}</div><button class="secondary-button store-new" data-store="${store.id}"><i data-lucide="plus"></i>New list</button></article>`;
    }).join('') : '<div class="empty-state">No stores are synchronized yet. An administrator can run the Stores service from Operations.</div>';
    storeSelect.innerHTML = stores.map(s => `<option value="${s.id}">${escapeHtml(s.number)}</option>`).join('');
    lucide.createIcons();
  } catch (error) { storeGrid.innerHTML = `<div class="form-error">${escapeHtml(error.message)}</div>`; }
}
function openDialog(storeId) {
  if (storeId) storeSelect.value = storeId;
  document.getElementById('order-date').value = new Date().toISOString().slice(0, 10);
  dialog.showModal();
}
document.getElementById('new-order-button').addEventListener('click', () => openDialog());
storeGrid.addEventListener('click', async event => {
  const newButton = event.target.closest('.store-new');
  if (newButton) { openDialog(newButton.dataset.store); return; }
  const deleteButton = event.target.closest('[data-delete-order]');
  if (!deleteButton) return;
  if (!confirm(`Delete the ${deleteButton.dataset.orderDate} list for store ${deleteButton.dataset.storeNumber}? This cannot be undone.`)) return;
  deleteButton.disabled = true;
  try { await apiFetch(`/api/orders/${deleteButton.dataset.deleteOrder}/`, {method: 'DELETE'}); showToast('Order list deleted'); await loadHome(); }
  catch (error) { deleteButton.disabled = false; showToast(error.message, true); }
});
dialog.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => dialog.close()));
document.getElementById('new-order-form').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    const order = await apiFetch('/api/orders/', {method: 'POST', body: JSON.stringify({store_id: storeSelect.value, order_date: document.getElementById('order-date').value})});
    window.location.href = `/orders/${order.id}/`;
  } catch (error) { showToast(error.message, true); }
});
function escapeHtml(value) { const el = document.createElement('div'); el.textContent = value ?? ''; return el.innerHTML; }
loadHome();

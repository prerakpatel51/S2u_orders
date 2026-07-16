const gridElement = document.getElementById('order-grid');
const searchInput = document.getElementById('product-search');
const searchResults = document.getElementById('search-results');
const selectedPanel = document.getElementById('selected-product');
const batchSelectionPanel = document.getElementById('batch-selection');
const otherStoresButton = document.getElementById('other-stores-button');
const GRID_FORMAT_COOKIE = 'store_orders_grid_format_v2';
const orderPermissions = window.ORDER_PERMISSIONS || {isAdmin: false, canEdit: false, isFinalized: false};
let gridApi;
let orderData;
let selectedProduct;
let searchTimer;
let preferenceTimer;
let showingOtherStores = false;
let cameraStream;
let cameraScanPending = false;
let cameraScanInProgress = false;
let cameraScanAttempts = 0;
let reopenCameraAfterAdd = false;
let cameraZoomTrack;
let cameraUsesHardwareZoom = false;
let suggestionIndex = -1;
let suggestionProducts = [];
let rawSuggestionProducts = [];
let orderSearchCategoryFilter;
let searchRequestId = 0;
let keyboardSelectionPending = false;
let quickAddAfterSearch = false;
let suppressPreferenceSave = false;
let transferEditingRow;
let transferDraft = [];
let categoryPreference = {hidden: [], custom: []};
let notesTimer;
let gridPdfObjectUrl = '';
let gridPdfFilename = '';
let gridPdfPayload = null;
let gridExportTitleTimer;
const batchSelectedProducts = new Map();

const escapeHtml = value => { const el = document.createElement('div'); el.textContent = value ?? ''; return el.innerHTML; };
const numeric = params => Number(params.newValue ?? params.value ?? 0);
const quantityFormat = params => Number(params.value || 0).toLocaleString(undefined, {maximumFractionDigits: 3});
const barcodeVariants = value => { const code=String(value||'').trim(); if(!/^\d{7,15}$/.test(code)) return new Set(); const stripped=code.replace(/^0+/,'')||'0', values=new Set([code,stripped,`0${code}`,`0${stripped}`]); if(code.endsWith('0')&&code.length>7){values.add(code.slice(0,-1));values.add(code.slice(0,-1).replace(/^0+/,'')||'0');} if(code.length<15)values.add(`${code}0`); return values; };
const exactBarcodeProduct = (products, query) => { const variants=barcodeVariants(query); if(!variants.size)return null; const matches=products.filter(product=>product.codes.some(code=>variants.has(String(code).replace(/\D/g,'')))); return matches.length===1?matches[0]:null; };
const PRODUCT_CATEGORIES = [
  ['50ml', '50 ml', 'Size / pack', /(^|[^0-9a-z])50\s*ml(?=$|[^0-9a-z])/i],
  ['100ml', '100 ml', 'Size / pack', /(^|[^0-9a-z])100\s*ml(?=$|[^0-9a-z])/i],
  ['200ml', '200 ml', 'Size / pack', /(^|[^0-9a-z])200\s*ml(?=$|[^0-9a-z])/i],
  ['375ml', '375 ml', 'Size / pack', /(^|[^0-9a-z])375\s*ml(?=$|[^0-9a-z])/i],
  ['750ml', '750 ml', 'Size / pack', /(^|[^0-9a-z])750\s*ml(?=$|[^0-9a-z])/i],
  ['1l', '1 L', 'Size / pack', /(^|[^0-9a-z.])1\s*l(?=$|[^0-9a-z])/i],
  ['1.75l', '1.75 L', 'Size / pack', /(^|[^0-9a-z])1\s*[.]\s*75\s*l(?=$|[^0-9a-z])/i],
  ['12oz', '12 oz', 'Size / pack', /(^|[^0-9a-z])12\s*oz(?=$|[^0-9a-z])/i],
  ['16oz', '16 oz', 'Size / pack', /(^|[^0-9a-z])16\s*oz(?=$|[^0-9a-z])/i],
  ['4pk', '4 pack', 'Size / pack', /(^|[^0-9a-z])4\s*[-]?\s*(?:pk|pack)(?=$|[^0-9a-z])/i],
  ['6pk', '6 pack', 'Size / pack', /(^|[^0-9a-z])6\s*[-]?\s*(?:pk|pack)(?=$|[^0-9a-z])/i],
  ['8pk', '8 pack', 'Size / pack', /(^|[^0-9a-z])8\s*[-]?\s*(?:pk|pack)(?=$|[^0-9a-z])/i],
  ['12pk', '12 pack', 'Size / pack', /(^|[^0-9a-z])12\s*[-]?\s*(?:pk|pack)(?=$|[^0-9a-z])/i],
  ['24pk', '24 pack', 'Size / pack', /(^|[^0-9a-z])24\s*[-]?\s*(?:pk|pack)(?=$|[^0-9a-z])/i],
  ['vodka', 'Vodka', 'Liquor type', /\bvodka\b/i],
  ['whiskey', 'Whiskey / Bourbon / Rye / Scotch', 'Liquor type', /\b(?:whiske?y|bourbon|rye|scotch)\b/i],
  ['tequila', 'Tequila', 'Liquor type', /\btequila\b/i],
  ['rum', 'Rum', 'Liquor type', /\brum\b/i],
  ['gin', 'Gin', 'Liquor type', /\bgin\b/i],
  ['cognac', 'Cognac / Brandy', 'Liquor type', /\b(?:cognac|brandy)\b/i],
  ['mezcal', 'Mezcal', 'Liquor type', /\bmezcal\b/i],
  ['liqueur', 'Liqueur / Cordial', 'Liquor type', /\b(?:liqueur|cordial)\b/i],
  ['pinotnoir', 'Pinot Noir', 'Wine / style', /\bpinot\s+noir\b/i],
  ['pinotgrigio', 'Pinot Grigio / Gris', 'Wine / style', /\bpinot\s+(?:grigio|gris)\b/i],
  ['moscato', 'Moscato', 'Wine / style', /\bmoscato\b/i],
  ['chardonnay', 'Chardonnay', 'Wine / style', /\bchardonnay\b/i],
  ['cabernet', 'Cabernet Sauvignon', 'Wine / style', /\b(?:cabernet|cab\s+sauv)\b/i],
  ['merlot', 'Merlot', 'Wine / style', /\bmerlot\b/i],
  ['sauvignonblanc', 'Sauvignon Blanc', 'Wine / style', /\bsauvignon\s+blanc\b/i],
  ['riesling', 'Riesling', 'Wine / style', /\briesling\b/i],
  ['rose', 'Rosé / Rose', 'Wine / style', /\bros[eé]\b/i],
  ['sparkling', 'Sparkling / Champagne / Prosecco', 'Wine / style', /\b(?:sparkling|champagne|prosecco|cava)\b/i],
  ['gold', 'Gold', 'Tequila / spirit style', /\bgold\b/i],
  ['silver', 'Silver', 'Tequila / spirit style', /\bsilver\b/i],
  ['blanco', 'Blanco', 'Tequila / spirit style', /\bblanco\b/i],
  ['reposado', 'Reposado', 'Tequila / spirit style', /\breposado\b/i],
  ['plata', 'Plata', 'Tequila / spirit style', /\bplata\b/i],
  ['anejo', 'Añejo', 'Tequila / spirit style', /\b(?:anejo|a[eñ]ejo)\b/i],
];
const customCategoryDefinition = item => [item.id, item.label, item.group, new RegExp(item.terms.split(',').map(term => term.trim()).filter(Boolean).map(term => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') || '(?!)', 'i')];
const availableCategories = () => [...PRODUCT_CATEGORIES.filter(([id]) => !categoryPreference.hidden.includes(id)), ...categoryPreference.custom.map(customCategoryDefinition)];
const categoryDefinition = value => availableCategories().find(([id]) => id === String(value || '').toLowerCase().replaceAll(' ', '').replace('pack', 'pk'));
const normalizeProductText = value => String(value || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '');
const FILTER_OPERATORS = [['contains', 'Contains'], ['notContains', 'Does not contain'], ['equals', 'Equals'], ['notEqual', 'Does not equal'], ['startsWith', 'Starts with'], ['endsWith', 'Ends with']];

class ProductNameCategoryFilter {
  init(params) {
    this.params = params;
    this.gui = document.createElement('div');
    this.gui.className = 'product-category-filter';
    const categories = availableCategories();
    const groups = [...new Set(categories.map(([, , group]) => group))];
    this.gui.innerHTML = `<label><span>Product name filter</span><select data-product-filter-operator>${FILTER_OPERATORS.map(([id, label]) => `<option value="${id}">${label}</option>`).join('')}</select><input type="search" data-product-name-filter autocomplete="off" placeholder="Filter value"></label><fieldset><legend>Categories <small>(select multiple)</small></legend><div class="category-options">${groups.map(group => `<div class="category-group"><strong>${escapeHtml(group)}</strong>${categories.filter(([, , itemGroup]) => itemGroup === group).map(([id, label]) => `<label class="category-row"><input type="checkbox" value="${escapeHtml(id)}" data-product-category-filter><span>${escapeHtml(label)}</span><button type="button" class="category-delete" data-category-delete="${escapeHtml(id)}" title="Remove category">×</button></label>`).join('')}</div>`).join('')}</div></fieldset><button type="button" class="secondary-button category-manage-toggle" data-category-manage>Add category</button><div class="category-manager" data-category-manager hidden><input data-category-label placeholder="Category name"><input data-category-terms placeholder="Matching words, comma separated"><input data-category-group placeholder="Group (optional)"><div class="category-manager-actions"><button type="button" class="primary-button" data-category-save>Add</button><button type="button" class="secondary-button" data-category-cancel>Cancel</button></div></div><button type="button" class="secondary-button" data-product-filter-clear>Clear all</button>`;
    this.nameInput = this.gui.querySelector('[data-product-name-filter]');
    this.operatorSelect = this.gui.querySelector('[data-product-filter-operator]');
    this.categoryInputs = [...this.gui.querySelectorAll('[data-product-category-filter]')];
    this.nameInput.addEventListener('input', () => this.params.filterChangedCallback());
    this.operatorSelect.addEventListener('change', () => this.params.filterChangedCallback());
    this.categoryInputs.forEach(input => input.addEventListener('change', () => this.params.filterChangedCallback()));
    const manager = this.gui.querySelector('[data-category-manager]');
    this.gui.querySelector('[data-category-manage]').addEventListener('click', () => { manager.hidden = false; this.gui.querySelector('[data-category-label]').focus(); });
    this.gui.querySelector('[data-category-cancel]').addEventListener('click', () => { manager.hidden = true; });
    this.gui.querySelector('[data-category-save]').addEventListener('click', async () => {
      const label = this.gui.querySelector('[data-category-label]').value.trim();
      const terms = this.gui.querySelector('[data-category-terms]').value.trim();
      const group = this.gui.querySelector('[data-category-group]').value.trim() || 'Custom';
      if (!label || !terms) return showToast('Enter a category name and matching words', true);
      categoryPreference.custom.push({id: `custom-${Date.now()}`, label, terms, group});
      await saveCategoryPreference(); this.params.api.destroyFilter('product_name'); showToast('Category added');
    });
    this.gui.querySelectorAll('[data-category-delete]').forEach(button => button.addEventListener('click', async event => {
      event.preventDefault(); event.stopPropagation();
      const id = button.dataset.categoryDelete;
      categoryPreference.custom = categoryPreference.custom.filter(item => item.id !== id);
      if (PRODUCT_CATEGORIES.some(([builtInId]) => builtInId === id)) categoryPreference.hidden.push(id);
      await saveCategoryPreference(); this.params.api.destroyFilter('product_name'); showToast('Category removed');
    }));
    this.gui.querySelector('[data-product-filter-clear]').addEventListener('click', () => {
      this.nameInput.value = '';
      this.operatorSelect.value = 'contains';
      this.categoryInputs.forEach(input => { input.checked = false; });
      this.params.filterChangedCallback();
    });
  }
  getGui() { return this.gui; }
  afterGuiAttached() { this.nameInput.focus(); }
  selectedCategories() { return this.categoryInputs.filter(input => input.checked).map(input => input.value); }
  isFilterActive() { return Boolean(this.nameInput.value.trim() || this.selectedCategories().length); }
  doesFilterPass(params) {
    const productName = String(this.params.getValue(params.node) || '');
    const rawQuery = this.nameInput.value.trim();
    const query = normalizeProductText(rawQuery);
    const productNameNormalized = normalizeProductText(productName);
    const selected = this.selectedCategories().map(categoryDefinition).filter(Boolean);
    const typedCategory = categoryDefinition(rawQuery);
    if (selected.length) {
      const groups = [...new Set(selected.map(category => category[2]))];
      if (!groups.every(group => selected.filter(category => category[2] === group).some(category => category[3].test(productName)))) return false;
    }
    if (typedCategory && !typedCategory[3].test(productName)) return false;
    if (query && !typedCategory) {
      const matches = {contains: productNameNormalized.includes(query), notContains: !productNameNormalized.includes(query), equals: productNameNormalized === query, notEqual: productNameNormalized !== query, startsWith: productNameNormalized.startsWith(query), endsWith: productNameNormalized.endsWith(query)};
      if (!matches[this.operatorSelect.value]) return false;
    }
    return true;
  }
  getModel() {
    return this.isFilterActive() ? {filterType: 'productNameCategory', type: this.operatorSelect.value, query: this.nameInput.value.trim(), categories: this.selectedCategories()} : null;
  }
  setModel(model) {
    this.nameInput.value = model?.query || '';
    this.operatorSelect.value = model?.type || 'contains';
    const categories = new Set(model?.categories || (model?.category ? [model.category] : []));
    this.categoryInputs.forEach(input => { input.checked = categories.has(input.value); });
  }
}
const pairedRenderer = params => {
  const data = params.value || {stock: 0, monthly_needed: 0};
  const element = document.createElement('div');
  element.className = 'paired-value';
  element.innerHTML = `<strong>${Number(data.stock || 0).toLocaleString()}</strong><span>${Number(data.monthly_needed || 0).toLocaleString()}/m</span>`;
  return element;
};

function transferStoreRenderer(params) {
  if (!orderPermissions.isAdmin) return params.data.transfers?.length ? params.data.transfers.map(item => item.from_store_number).join(', ') : '—';
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'transfer-summary-button';
  button.textContent = params.data.transfers.length ? params.data.transfers.map(item => item.from_store_number).join(', ') : 'Add stores';
  button.title = 'Edit transfer stores';
  button.addEventListener('click', () => openTransferEditor(params.data));
  return button;
}

function transferQuantityRenderer(params) {
  if (!orderPermissions.isAdmin) return params.data.transfers?.length ? params.data.transfers.map(item => Number(item.quantity).toLocaleString()).join(' + ') : '—';
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'transfer-summary-button quantity-summary';
  button.textContent = params.data.transfers.length ? params.data.transfers.map(item => Number(item.quantity).toLocaleString()).join(' + ') : '0';
  button.title = params.data.transfers.map(item => `${item.from_store_number}: ${Number(item.quantity).toLocaleString()} bottles`).join(', ') || 'Edit transfer quantities';
  button.addEventListener('click', () => openTransferEditor(params.data));
  return button;
}

function openTransferEditor(row) {
  transferEditingRow = row;
  transferDraft = (row.transfers || []).map(item => ({from_store_id: item.from_store_id, quantity: Number(item.quantity)}));
  if (!transferDraft.length) transferDraft.push({from_store_id: '', quantity: 1});
  document.getElementById('transfer-product-name').textContent = `${row.product_number} · ${row.product_name}`;
  renderTransferDraft();
  document.getElementById('transfer-dialog').showModal();
}

function renderTransferDraft() {
  const stores = orderData.stores.filter(store => store.id !== orderData.order.store.id);
  document.getElementById('transfer-editor-rows').innerHTML = transferDraft.map((item, index) => `
    <div class="transfer-editor-row" data-index="${index}">
      <select aria-label="Transfer store ${index + 1}"><option value="">Select store</option>${stores.map(store => `<option value="${store.id}" ${Number(item.from_store_id) === store.id ? 'selected' : ''}>${escapeHtml(store.number)}</option>`).join('')}</select>
      <input aria-label="Transfer bottles ${index + 1}" type="number" min="1" step="1" value="${Number(item.quantity) || 1}">
      <button type="button" class="icon-button compact transfer-remove-row" title="Remove transfer" aria-label="Remove transfer">×</button>
    </div>`).join('');
}

function closeTransferEditor() {
  document.getElementById('transfer-dialog').close();
  transferEditingRow = null; transferDraft = [];
}

async function saveTransferDraft() {
  if (!transferEditingRow) return;
  const transfers = transferDraft
    .map(item => ({from_store_id: Number(item.from_store_id), quantity: Number(item.quantity)}))
    .filter(item => item.from_store_id && item.quantity > 0);
  if (new Set(transfers.map(item => item.from_store_id)).size !== transfers.length) {
    showToast('Each transfer store can only be selected once', true); return;
  }
  const button = document.getElementById('transfer-save'); button.disabled = true;
  try {
    const updated = await apiFetch(`/api/items/${transferEditingRow.id}/`, {method: 'PATCH', body: JSON.stringify({transfers})});
    Object.assign(transferEditingRow, updated); gridApi.refreshCells({columns: ['transfer_store', 'transfer_quantity'], force: true});
    closeTransferEditor(); showToast('Transfers updated');
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; }
}

function coreColumns() {
  const columns = [
    {field: 'product_number', headerName: 'Product #', pinned: 'left', width: 110, minWidth: 96, comparator: gridNaturalCompare},
    {field: 'product_name', headerName: 'Product name', pinned: 'left', width: 210, minWidth: 150, filter: ProductNameCategoryFilter, comparator: gridNaturalCompare},
    {field: 'commodity_group', headerName: 'Commodity group', width: 165, minWidth: 130, comparator: gridNaturalCompare},
    {field: 'commodity_group_number', headerName: 'Commodity group #', width: 130, minWidth: 110, hide: true, comparator: gridNaturalCompare},
    {
      field: 'supplier_name', headerName: 'Supplier', width: 120, minWidth: 96, comparator: gridNaturalCompare,
      editable: params => orderPermissions.isAdmin && params.data.supplier_options.length > 1,
      cellEditor: 'agSelectCellEditor',
      cellEditorParams: params => ({values: [...new Set(params.data.supplier_options.map(option => option.short_name))]}),
      cellClass: params => orderPermissions.isAdmin && params.data.supplier_options.length > 1 ? 'editable-cell' : '',
      tooltipValueGetter: params => params.data.supplier_full_name,
    },
    {field: 'supplier_number', headerName: 'Supplier #', width: 105, minWidth: 90, hide: true, comparator: gridNaturalCompare},
    {field: 'supplier_order_code', headerName: 'Supplier order code', width: 145, minWidth: 120, hide: true, comparator: gridNaturalCompare},
    {field: 'supplier_pack_size', headerName: 'Case / pack', width: 105, minWidth: 90, hide: true, type: 'numericColumn', comparator: gridNumberCompare, valueFormatter: quantityFormat},
    {field: 'supplier_purchase_price', headerName: 'Purchase price', width: 115, minWidth: 100, hide: true, type: 'numericColumn', comparator: gridNumberCompare, valueFormatter: params => Number(params.value || 0).toLocaleString(undefined, {style: 'currency', currency: 'USD'})},
    {field: 'supplier_names', headerName: 'All suppliers', width: 260, minWidth: 180, hide: true, comparator: gridNaturalCompare},
    {field: 'on_shelf_quantity', headerName: 'Shelf', pinned: 'left', width: 88, minWidth: 78, editable: orderPermissions.canEdit, valueParser: numeric, valueFormatter: quantityFormat, cellEditor: 'agNumberCellEditor', cellEditorParams: {min: 0, step: 1, precision: 3, showStepperButtons: true}, cellClass: orderPermissions.canEdit ? 'editable-cell stock-input-cell' : 'stock-input-cell', type: 'numericColumn', comparator: gridNumberCompare},
  ];
  if (orderPermissions.isAdmin) {
    columns.push(
      {colId: 'current_store', headerName: 'System stock', pinned: 'left', width: 96, minWidth: 84, headerClass: 'store-blue-header', cellClass: 'store-blue-cell', valueGetter: p => ({stock: p.data.current_store_stock, monthly_needed: p.data.current_store_monthly_needed}), cellRenderer: pairedRenderer, comparator: (a, b) => gridNumberCompare(a?.stock, b?.stock)},
      {field: 'joe_quantity', headerName: 'JOE', pinned: 'left', width: 82, minWidth: 72, editable: orderPermissions.canEdit, valueParser: numeric, valueFormatter: quantityFormat, cellEditor: 'agNumberCellEditor', cellEditorParams: {min: 0, step: 1, precision: 3, showStepperButtons: true}, headerClass: 'joe-header', cellClass: 'editable-cell joe-cell', type: 'numericColumn', comparator: gridNumberCompare},
      {field: 'bt_quantity', headerName: 'BT', pinned: 'left', width: 82, minWidth: 72, editable: orderPermissions.canEdit, valueParser: numeric, valueFormatter: quantityFormat, cellEditor: 'agNumberCellEditor', cellEditorParams: {min: 0, step: 1, precision: 3, showStepperButtons: true}, headerClass: 'bt-header', cellClass: 'editable-cell bt-cell', type: 'numericColumn', comparator: gridNumberCompare},
      {field: 'sqw_quantity', headerName: 'SQW', pinned: 'left', width: 82, minWidth: 72, editable: orderPermissions.canEdit, valueParser: numeric, valueFormatter: quantityFormat, cellEditor: 'agNumberCellEditor', cellEditorParams: {min: 0, step: 1, precision: 3, showStepperButtons: true}, headerClass: 'sqw-header', cellClass: 'editable-cell sqw-cell', type: 'numericColumn', comparator: gridNumberCompare},
    );
  } else {
    columns.push({colId: 'current_store', headerName: 'System stock', pinned: 'left', width: 110, minWidth: 96, headerClass: 'store-blue-header', cellClass: 'store-blue-cell', valueGetter: p => ({stock: p.data.current_store_stock, monthly_needed: p.data.current_store_monthly_needed}), cellRenderer: pairedRenderer, comparator: (a, b) => gridNumberCompare(a?.stock, b?.stock)});
  }
  columns.push({field: 'notes', headerName: 'Notes', width: 200, editable: orderPermissions.canEdit, hide: orderPermissions.isAdmin, cellClass: orderPermissions.canEdit ? 'editable-cell' : '', comparator: gridNaturalCompare});
  return columns;
}

function otherStoreColumns() {
  if (!orderPermissions.isAdmin) return [];
  const colors = ['store-green', 'store-peach', 'store-lilac', 'store-mint'];
  return orderData.stores.filter(store => store.id !== orderData.order.store.id).map((store, index) => ({
    colId: `store_${store.id}`, headerName: store.number, width: 90, minWidth: 80, hide: true,
    headerClass: `${colors[index % colors.length]}-header`, cellClass: `${colors[index % colors.length]}-cell`,
    valueGetter: params => params.data.other_stores.find(item => item.store_id === store.id) || {stock: 0, monthly_needed: 0},
    cellRenderer: pairedRenderer, comparator: (a, b) => gridNumberCompare(a?.stock, b?.stock),
  }));
}

function transferColumns() {
  if (!orderPermissions.isAdmin && !orderPermissions.isFinalized) return [];
  const hide = orderPermissions.isAdmin;
  return [
    {colId: 'transfer_store', headerName: 'Transfer from', width: 122, minWidth: 110, hide, sortable: false, filter: false, cellRenderer: transferStoreRenderer},
    ...(!orderPermissions.isAdmin ? [{colId: 'transfer_to', headerName: 'Transfer to', width: 110, valueGetter: p => p.data.transfers?.length ? orderData.order.store.number : '—', comparator: gridNaturalCompare}] : []),
    {colId: 'transfer_quantity', headerName: 'Transfer bottles', width: 108, minWidth: 96, hide, sortable: false, filter: false, cellClass: 'transfer-quantity-cell', cellRenderer: transferQuantityRenderer},
  ];
}

function deleteColumn() {
  return {colId: 'delete', headerName: '', width: 40, minWidth: 40, maxWidth: 40, sortable: false, filter: false, resizable: false, cellRenderer: deleteRenderer};
}

function deleteRenderer(params) {
  const button = document.createElement('button'); button.className = 'grid-delete'; button.title = 'Delete row'; button.innerHTML = '×';
  button.addEventListener('click', async () => {
    if (!confirm(`Delete ${params.data.product_name} from this list?`)) return;
    try { await apiFetch(`/api/items/${params.data.id}/`, {method: 'DELETE'}); gridApi.applyTransaction({remove: [params.data]}); updateCount(); } catch (error) { showToast(error.message, true); }
  }); return button;
}

async function saveCategoryPreference() {
  categoryPreference = await apiFetch('/api/product-categories/', {method: 'PUT', body: JSON.stringify(categoryPreference)});
}

function setupOrderNotes() {
  const input = document.getElementById('order-list-notes');
  const status = document.getElementById('order-notes-status');
  const noteButton = document.getElementById('list-note-button');
  const updateNoteAttention = value => {
    const hasNote = Boolean(String(value || '').trim());
    noteButton.classList.toggle('has-note-attention', orderPermissions.isAdmin && hasNote);
    noteButton.setAttribute('aria-label', hasNote ? 'List note present — open and review' : 'List note');
  };
  input.value = orderData.order.notes || '';
  updateNoteAttention(input.value);
  const dialog = document.getElementById('list-note-dialog');
  const close = () => dialog.close();
  document.getElementById('list-note-button').addEventListener('click', () => { dialog.showModal(); input.focus(); });
  document.getElementById('list-note-close').addEventListener('click', close);
  document.getElementById('list-note-done').addEventListener('click', close);
  if (!orderPermissions.canEdit) return;
  input.addEventListener('input', () => {
    updateNoteAttention(input.value);
    clearTimeout(notesTimer); status.textContent = 'Saving…';
    notesTimer = setTimeout(async () => {
      try {
        const updated = await apiFetch(`/api/orders/${window.ORDER_LIST_ID}/`, {method: 'PATCH', body: JSON.stringify({notes: input.value})});
        orderData.order.notes = updated.notes; updateNoteAttention(updated.notes); status.textContent = 'Saved automatically';
      } catch (error) { status.textContent = 'Could not save'; showToast(error.message, true); }
    }, 500);
  });
}

async function loadOrder() {
  [orderData, categoryPreference] = await Promise.all([
    apiFetch(`/api/orders/${window.ORDER_LIST_ID}/`),
    apiFetch('/api/product-categories/'),
  ]);
  setupOrderNotes();
  suppressPreferenceSave = true;
  const gridOptions = {
    rowData: orderData.items,
    columnDefs: [...coreColumns(), ...otherStoreColumns(), ...transferColumns(), ...(orderPermissions.canEdit ? [deleteColumn()] : [])],
    defaultColDef: {sortable: true, resizable: true, filter: true, suppressHeaderMenuButton: false, wrapHeaderText: true, autoHeaderHeight: true},
    rowHeight: 46,
    // Prevent accidental click-and-drag browser selections from leaving large
    // blue blocks across the spreadsheet. Focused-cell copy is handled below.
    enableCellTextSelection: false,
    ensureDomOrder: true,
    singleClickEdit: true,
    stopEditingWhenCellsLoseFocus: true,
    enterNavigatesVertically: true,
    enterNavigatesVerticallyAfterEdit: true,
    animateRows: true,
    suppressDragLeaveHidesColumns: true,
    getRowId: params => String(params.data.id),
    onCellValueChanged: saveCell,
    onCellKeyDown: handleCellKeyDown,
    onColumnMoved: queuePreference,
    onColumnResized: event => { if (event.finished) queuePreference(); },
    onColumnVisible: () => {
      queuePreference();
      if (!gridApi) return;
      syncOtherStoresToggle();
      if (!document.getElementById('column-panel').hidden) buildColumnPanel();
    },
    onColumnPinned: () => { queuePreference(); buildFreezePanel(); updateFreezeButton(); },
    onSortChanged: queuePreference,
    onFilterChanged: () => { queuePreference(); updateCount(); },
  };
  gridApi = agGrid.createGrid(gridElement, gridOptions);
  const restored = restorePreference();
  if (orderPermissions.isAdmin) {
    if (!restored) setOtherStoresVisible(false);
    else syncOtherStoresToggle();
  }
  updateCount(); buildColumnPanel(); buildFreezePanel(); updateFreezeButton();
  clearTimeout(preferenceTimer); suppressPreferenceSave = false;
}

function handleCellKeyDown(event) {
  const keyboardEvent = event.event;
  if ((keyboardEvent.ctrlKey || keyboardEvent.metaKey) && keyboardEvent.key.toLowerCase() === 'c') {
    const activeSelection = window.getSelection()?.toString();
    const input = keyboardEvent.target.closest?.('input, textarea, [contenteditable="true"]');
    const inputHasSelection = input && input.selectionStart !== undefined && input.selectionStart !== input.selectionEnd;
    if (activeSelection || inputHasSelection) return;
    const cell = keyboardEvent.target.closest?.('.ag-cell') || document.querySelector('#order-grid .ag-cell-focus');
    const text = cell?.innerText?.trim();
    if (!text) return;
    keyboardEvent.preventDefault();
    copyTextToClipboard(text);
    return;
  }
  if (event.colDef.field !== 'on_shelf_quantity' || !['ArrowUp', 'ArrowDown'].includes(event.event.key)) return;
  const next = Math.max(0, Number(event.value || 0) + (event.event.key === 'ArrowUp' ? 1 : -1));
  event.event.preventDefault();
  event.node.setDataValue('on_shelf_quantity', next);
}

async function copyTextToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    const textarea = document.createElement('textarea');
    textarea.value = text; textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed'; textarea.style.opacity = '0';
    document.body.appendChild(textarea); textarea.select();
    document.execCommand('copy'); textarea.remove();
  }
}

async function saveCell(event) {
  if (event.colDef.field === 'supplier_name') {
    const option = event.data.supplier_options.find(item => item.short_name === event.newValue);
    if (!option) return;
    try {
      const updated = await apiFetch(`/api/products/${event.data.product}/preferred-supplier/`, {method: 'PATCH', body: JSON.stringify({supplier_id: option.id})});
      Object.assign(event.data, updated); gridApi.refreshCells({rowNodes: [event.node], force: true});
    } catch (error) {
      event.data.supplier_name = event.oldValue; gridApi.refreshCells({rowNodes: [event.node]}); showToast(error.message, true);
    }
    return;
  }
  if (!['on_shelf_quantity', 'joe_quantity', 'bt_quantity', 'sqw_quantity', 'notes'].includes(event.colDef.field)) return;
  try {
    const updated = await apiFetch(`/api/items/${event.data.id}/`, {method: 'PATCH', body: JSON.stringify({[event.colDef.field]: event.newValue})});
    Object.assign(event.data, updated); gridApi.refreshCells({rowNodes: [event.node], force: true});
  } catch (error) { event.data[event.colDef.field] = event.oldValue; gridApi.refreshCells({rowNodes: [event.node]}); showToast(error.message, true); }
}

function queuePreference() {
  if (suppressPreferenceSave) return;
  clearTimeout(preferenceTimer); preferenceTimer = setTimeout(savePreference, 350);
}
function cookieValue(name) {
  const prefix = `${name}=`;
  return document.cookie.split('; ').find(value => value.startsWith(prefix))?.slice(prefix.length) || '';
}
function writeFormatCookie(payload) {
  let encoded = encodeURIComponent(JSON.stringify(payload));
  if (encoded.length > 3800) {
    delete payload.f;
    encoded = encodeURIComponent(JSON.stringify(payload));
  }
  const secure = window.location.protocol === 'https:' ? '; Secure' : '';
  document.cookie = `${GRID_FORMAT_COOKIE}=${encoded}; Max-Age=31536000; Path=/; SameSite=Lax${secure}`;
}
function clearFormatCookie() {
  document.cookie = `${GRID_FORMAT_COOKIE}=; Max-Age=0; Path=/; SameSite=Lax`;
}
function savePreference() {
  if (!gridApi || suppressPreferenceSave) return;
  const columns = gridApi.getColumnState().map(item => [
    item.colId,
    Math.round(item.width || 0),
    item.hide ? 1 : 0,
    item.pinned === 'left' ? 'l' : item.pinned === 'right' ? 'r' : '',
    item.sort === 'asc' ? 'a' : item.sort === 'desc' ? 'd' : '',
    item.sortIndex ?? '',
  ]);
  writeFormatCookie({v: 1, c: columns, f: gridApi.getFilterModel()});
}
function restorePreference() {
  const raw = cookieValue(GRID_FORMAT_COOKIE);
  if (!raw) return false;
  try {
    const saved = JSON.parse(decodeURIComponent(raw));
    if (!Array.isArray(saved.c)) return false;
    gridApi.applyColumnState({
      state: saved.c.map(item => ({
        colId: item[0], width: item[1], hide: Boolean(item[2]),
        pinned: item[3] === 'l' ? 'left' : item[3] === 'r' ? 'right' : null,
        sort: item[4] === 'a' ? 'asc' : item[4] === 'd' ? 'desc' : null,
        sortIndex: item[5] === '' ? null : item[5],
      })),
      applyOrder: true,
    });
    if (saved.f) gridApi.setFilterModel(saved.f);
    return true;
  } catch (_) {
    clearFormatCookie(); return false;
  }
}

async function searchProducts(selectBestMatch = false) {
  const query = searchInput.value.trim();
  const fromCamera = cameraScanPending; cameraScanPending = false;
  const requestId = ++searchRequestId;
  suggestionIndex = -1;
  if (!query) { suggestionProducts = []; searchResults.hidden = true; return; }
  searchResults.innerHTML = '<p class="suggestion-loading">Checking live stock...</p>';
  searchResults.hidden = false;
  try {
    const products = await apiFetch(`/api/products/search/?q=${encodeURIComponent(query)}&order_id=${window.ORDER_LIST_ID}`);
    if (requestId !== searchRequestId) return;
    const scanned = exactBarcodeProduct(products, query);
    if (scanned) { await chooseProduct(scanned, {reopenCamera: fromCamera}); if (fromCamera) document.getElementById('selected-on-shelf').focus(); else quickAddSelected({increment: true, reopenCamera: false}); return; }
    if (selectBestMatch && products.length) {
      await chooseProduct(products[0]);
      if (quickAddAfterSearch) quickAddSelected();
      return;
    }
    if (products.length === 1 && (products[0].number.toLowerCase() === query.toLowerCase() || products[0].codes.some(code => code.toLowerCase() === query.toLowerCase()))) return chooseProduct(products[0]);
    rawSuggestionProducts = products; suggestionProducts = orderSearchCategoryFilter ? orderSearchCategoryFilter.filter(products) : products;
    renderSuggestions();
    searchResults.hidden = false;
  } catch (error) { showToast(error.message, true); }
  finally { if (selectBestMatch) keyboardSelectionPending = false; }
}
function renderSuggestions() {
  searchResults.innerHTML = suggestionProducts.length ? `<p class="suggestion-result-count">${suggestionProducts.length.toLocaleString()} matching product${suggestionProducts.length === 1 ? '' : 's'}</p>` + suggestionProducts.map((p, index) => {
    const selected = batchSelectedProducts.has(p.id);
    return `<div class="suggestion-item order-suggestion${index === suggestionIndex ? ' active' : ''}${selected ? ' batch-selected' : ''}" data-id="${p.id}" data-index="${index}"><label class="order-checkbox-wrap" title="Select product"><input class="order-batch-checkbox" type="checkbox" aria-label="Select ${escapeHtml(p.name)}" ${selected ? 'checked' : ''}></label><button type="button" class="order-product-action"><span class="suggestion-copy"><strong>${escapeHtml(p.name)}</strong><small>${escapeHtml(p.number)}${p.codes.length ? ` · ${escapeHtml(p.codes[0])}` : ''}</small></span><span class="suggestion-metrics"><span><small>Stock</small><strong>${Number(p.current_stock || 0).toLocaleString()}</strong></span><span><small>Monthly</small><strong>${Number(p.monthly_needed || 0).toLocaleString()}</strong></span></span></button></div>`;
  }).join('') : '<p>No matching products</p>';
  if (suggestionIndex >= 0) searchResults.querySelector('.suggestion-item.active')?.scrollIntoView({block: 'nearest'});
}
function updateBatchSelection() {
  const products = [...batchSelectedProducts.values()];
  batchSelectionPanel.hidden = products.length === 0;
  document.getElementById('batch-selection-count').textContent = products.length;
  document.getElementById('batch-selection-names').textContent = products.length ? products.slice(0, 3).map(product => product.name).join(', ') + (products.length > 3 ? ` +${products.length - 3}` : '') : '';
}
function toggleBatchProduct(product) {
  if (!product) return;
  if (batchSelectedProducts.has(product.id)) batchSelectedProducts.delete(product.id);
  else batchSelectedProducts.set(product.id, product);
  updateBatchSelection();
  renderSuggestions();
}
function selectBatchProduct(product) {
  if (!product) return;
  batchSelectedProducts.set(product.id, product); updateBatchSelection(); renderSuggestions();
}
function clearBatchSelection() {
  batchSelectedProducts.clear();
  updateBatchSelection();
  if (!searchResults.hidden && suggestionProducts.length) renderSuggestions();
}
searchInput.addEventListener('input', () => { cameraScanPending = false; clearTimeout(searchTimer); keyboardSelectionPending = false; quickAddAfterSearch = false; searchTimer = setTimeout(searchProducts, 220); });
searchInput.addEventListener('keydown', event => {
  if (event.key === 'ArrowDown' && suggestionProducts.length) {
    event.preventDefault(); suggestionIndex = Math.min(suggestionIndex + 1, suggestionProducts.length - 1); if (event.shiftKey) selectBatchProduct(suggestionProducts[suggestionIndex]); else renderSuggestions(); return;
  }
  if (event.key === 'ArrowUp' && suggestionProducts.length) {
    event.preventDefault(); suggestionIndex = Math.max(suggestionIndex - 1, 0); if (event.shiftKey) selectBatchProduct(suggestionProducts[suggestionIndex]); else renderSuggestions(); return;
  }
  if (event.key === 'Escape') { searchResults.hidden = true; suggestionIndex = -1; return; }
  if (event.key === 'Enter') {
    event.preventDefault(); clearTimeout(searchTimer);
    if (event.shiftKey && suggestionProducts.length) {
      toggleBatchProduct(suggestionProducts[suggestionIndex >= 0 ? suggestionIndex : 0]);
      searchInput.value = ''; suggestionProducts = []; suggestionIndex = -1; searchResults.hidden = true;
      return;
    }
    if (batchSelectedProducts.size) { document.getElementById('add-batch-selection').click(); return; }
    if (selectedProduct) { quickAddSelected(); return; }
    if (keyboardSelectionPending) { quickAddAfterSearch = true; return; }
    if (suggestionProducts.length) { chooseProduct(suggestionProducts[suggestionIndex >= 0 ? suggestionIndex : 0]); return; }
    keyboardSelectionPending = true; searchProducts(true);
  }
});
searchResults.addEventListener('click', event => {
  const row = event.target.closest('[data-id]'); if (!row) return;
  const product = suggestionProducts.find(p => p.id === Number(row.dataset.id));
  if (event.target.closest('.order-batch-checkbox')) {
    event.preventDefault(); toggleBatchProduct(product);
  } else if (event.target.closest('.order-product-action')) {
    if (event.shiftKey) toggleBatchProduct(product); else chooseProduct(product);
  }
});
async function chooseProduct(product, {reopenCamera = false} = {}) {
  selectedProduct = product; searchResults.hidden = true; selectedPanel.hidden = false;
  reopenCameraAfterAdd = reopenCamera;
  document.getElementById('selected-name').textContent = product.name; document.getElementById('selected-number').textContent = product.number;
  const stockElement = document.getElementById('selected-stock'); stockElement.textContent = 'Loading...';
  const existing = orderData.items.find(item => item.product === product.id);
  // Manual selection preserves an existing count; a new item is left blank.
  document.getElementById('selected-on-shelf').value = existing ? existing.on_shelf_quantity : '';
  try {
    const availability = await apiFetch(`/api/products/${product.id}/availability/?order_id=${window.ORDER_LIST_ID}`);
    product.current_stock = availability.current_stock; stockElement.textContent = Number(availability.current_stock).toLocaleString();
    if (availability.stale) showToast('Showing cached stock; live refresh failed', true);
  } catch (error) { stockElement.textContent = existing ? existing.current_store_stock : '--'; showToast(error.message, true); }
}
document.getElementById('cancel-product').addEventListener('click', clearSelected);
function clearSelected() { selectedProduct = null; suggestionProducts = []; suggestionIndex = -1; keyboardSelectionPending = false; quickAddAfterSearch = false; selectedPanel.hidden = true; searchResults.hidden = true; searchInput.value = ''; searchInput.focus(); }
function quickAddSelected({increment = false, reopenCamera = false} = {}) {
  if (!selectedProduct) return;
  const input = document.getElementById('selected-on-shelf');
  if (increment) {
    const existing = orderData.items.find(item => item.product === selectedProduct.id);
    input.value = Number(existing?.on_shelf_quantity || 0) + 1;
  } else if (!input.value.trim()) input.value = 1;
  reopenCameraAfterAdd = reopenCamera;
  document.getElementById('add-product').click();
}
document.getElementById('add-product').addEventListener('click', async () => {
  if (!selectedProduct) return;
  const button = document.getElementById('add-product'); button.disabled = true; button.textContent = 'Adding...';
  const shouldReopenCamera = reopenCameraAfterAdd;
  reopenCameraAfterAdd = false;
  try {
    const row = await apiFetch(`/api/orders/${window.ORDER_LIST_ID}/items/`, {method: 'POST', body: JSON.stringify({product_id: selectedProduct.id, on_shelf_quantity: Number(document.getElementById('selected-on-shelf').value || 1), refresh_stock: selectedProduct.current_stock === undefined})});
    const existing = gridApi.getRowNode(String(row.id)); if (existing) existing.setData(row); else gridApi.applyTransaction({add: [row]});
    const index = orderData.items.findIndex(item => item.id === row.id); if (index >= 0) orderData.items[index] = row; else orderData.items.push(row);
    updateCount(); clearSelected(); showToast('Product added');
    // Keep the camera capture flow moving, without opening it for a physical scanner.
    if (shouldReopenCamera) startCamera();
  } catch (error) { showToast(error.message, true); } finally { button.disabled = false; button.textContent = 'Add to list'; }
});
document.getElementById('clear-batch-selection').addEventListener('click', clearBatchSelection);
document.getElementById('add-batch-selection').addEventListener('click', async event => {
  const productIds = [...batchSelectedProducts.keys()];
  if (!productIds.length) return;
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const result = await apiFetch(`/api/orders/${window.ORDER_LIST_ID}/items/bulk/`, {method: 'POST', body: JSON.stringify({product_ids: productIds})});
    const addedRows = [];
    result.items.forEach(row => {
      const index = orderData.items.findIndex(item => item.id === row.id);
      const node = gridApi.getRowNode(String(row.id));
      if (index >= 0) orderData.items[index] = row; else { orderData.items.push(row); addedRows.push(row); }
      if (node) node.setData(row);
    });
    if (addedRows.length) gridApi.applyTransaction({add: addedRows});
    clearBatchSelection();
    searchInput.value = ''; suggestionProducts = []; searchResults.hidden = true; searchInput.focus();
    updateCount();
    showToast(result.created ? `${result.created} products added` : 'Selected products are already in the list');
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; }
});

function setOtherStoresVisible(visible) {
  gridApi.setColumnsVisible(toggleColumnIds(), visible);
  syncOtherStoresToggle();
  if (!document.getElementById('column-panel').hidden) buildColumnPanel();
}
function toggleColumnIds() {
  return ['joe_quantity', 'bt_quantity', 'sqw_quantity', ...orderData.stores.filter(s => s.id !== orderData.order.store.id).map(s => `store_${s.id}`), 'transfer_store', 'transfer_quantity'];
}
function syncOtherStoresToggle() {
  if (!gridApi || !orderData) return;
  const columns = toggleColumnIds().map(id => gridApi.getColumn(id)).filter(Boolean);
  const visibleCount = columns.filter(column => column.isVisible()).length;
  showingOtherStores = columns.length > 0 && visibleCount === columns.length;
  otherStoresButton.setAttribute('aria-pressed', String(showingOtherStores));
  otherStoresButton.classList.toggle('mixed', visibleCount > 0 && visibleCount < columns.length);
  otherStoresButton.title = visibleCount > 0 && visibleCount < columns.length ? `${visibleCount} of ${columns.length} optional columns visible` : '';
}
otherStoresButton.addEventListener('click', () => setOtherStoresVisible(!showingOtherStores));
document.getElementById('freeze-button').addEventListener('click', event => {
  const panel = document.getElementById('freeze-panel');
  document.getElementById('column-panel').hidden = true;
  document.getElementById('columns-button').setAttribute('aria-expanded', 'false');
  panel.hidden = !panel.hidden;
  event.currentTarget.setAttribute('aria-expanded', String(!panel.hidden));
  if (!panel.hidden) { buildFreezePanel(); positionColumnPanel(panel, event.currentTarget); }
});
function gridPdfCellValue(node, column) {
  const colId = column.getColId();
  if (colId === 'transfer_store') return (node.data.transfers || []).map(item => item.from_store_number).join(', ');
  if (colId === 'transfer_to') return (node.data.transfers || []).length ? orderData.order.store.number : '';
  if (colId === 'transfer_quantity') return (node.data.transfers || []).map(item => Number(item.quantity).toLocaleString()).join(' + ');
  const value = gridApi.getValue(column, node);
  if (value && typeof value === 'object' && 'stock' in value) {
    return `${Number(value.stock || 0).toLocaleString()}\n${Number(value.monthly_needed || 0).toLocaleString()}/m`;
  }
  const formatter = column.getColDef().valueFormatter;
  if (formatter && value !== null && value !== undefined && typeof value !== 'object') {
    try { return formatter({value, data: node.data, node, colDef: column.getColDef(), column, api: gridApi}); } catch (_) {}
  }
  return value ?? '';
}

function currentGridPdfPayload() {
  const columns = gridApi.getAllDisplayedColumns().filter(column => column.getColId() !== 'delete');
  const rows = [];
  gridApi.forEachNodeAfterFilterAndSort(node => {
    rows.push(columns.map(column => String(gridPdfCellValue(node, column))));
  });
  return {
    columns: columns.map(column => ({id: column.getColId(), label: column.getColDef().headerName || column.getColId(), width: column.getActualWidth()})),
    rows,
  };
}

function defaultGridExportTitle() {
  const store = orderData.order.store;
  const name = String(store.name || 'Spirits2u').trim();
  const number = String(store.number || '').trim();
  return number && !name.toLowerCase().includes(number.toLowerCase()) ? `${name} ${number}` : name;
}

function selectedGridExportTitle() {
  return document.getElementById('grid-export-title').value.trim() || defaultGridExportTitle();
}

async function refreshGridPdfPreview() {
  if (!gridPdfPayload) return;
  const orientation = document.querySelector('[name="grid-pdf-orientation"]:checked').value;
  const loading = document.getElementById('grid-pdf-loading');
  const frame = document.getElementById('grid-pdf-frame');
  const download = document.getElementById('grid-pdf-download');
  loading.hidden = false; loading.textContent = 'Preparing preview…'; download.disabled = true;
  frame.removeAttribute('src');
  if (gridPdfObjectUrl) URL.revokeObjectURL(gridPdfObjectUrl);
  gridPdfObjectUrl = '';
  try {
    const response = await fetch(`/api/orders/${window.ORDER_LIST_ID}/export-grid.pdf`, {
      method: 'POST',
      headers: {'Accept': '*/*', 'Content-Type': 'application/json', 'X-CSRFToken': window.csrfToken},
      body: JSON.stringify({...gridPdfPayload, orientation, title: selectedGridExportTitle()}),
    });
    if (!response.ok) {
      let message = `Could not create preview (${response.status})`;
      try { const body = await response.json(); message = body.detail || Object.values(body)[0] || message; } catch (_) {}
      throw new Error(message);
    }
    const blob = await response.blob();
    gridPdfObjectUrl = URL.createObjectURL(blob);
    gridPdfFilename = response.headers.get('X-PDF-Filename') || `grid-${orientation}.pdf`;
    frame.src = gridPdfObjectUrl; loading.hidden = true; download.disabled = false;
  } catch (error) {
    loading.textContent = error.message; showToast(error.message, true);
  }
}

function closeGridPdfPreview() {
  document.getElementById('grid-pdf-dialog').close();
  document.getElementById('grid-pdf-frame').removeAttribute('src');
  if (gridPdfObjectUrl) URL.revokeObjectURL(gridPdfObjectUrl);
  gridPdfObjectUrl = ''; gridPdfPayload = null;
}

document.getElementById('grid-pdf-preview-button').addEventListener('click', () => {
  gridPdfPayload = currentGridPdfPayload();
  document.getElementById('grid-export-title').value = defaultGridExportTitle();
  document.getElementById('grid-pdf-summary').textContent = `${gridPdfPayload.rows.length.toLocaleString()} filtered rows · ${gridPdfPayload.columns.length} visible columns`;
  document.getElementById('grid-pdf-dialog').showModal();
  refreshGridPdfPreview();
});
document.querySelectorAll('[name="grid-pdf-orientation"]').forEach(input => input.addEventListener('change', refreshGridPdfPreview));
document.getElementById('grid-export-title').addEventListener('input', () => {
  clearTimeout(gridExportTitleTimer);
  gridExportTitleTimer = setTimeout(refreshGridPdfPreview, 450);
});
document.getElementById('grid-xlsx-download').addEventListener('click', async event => {
  if (!gridPdfPayload) return;
  const button = event.currentTarget; button.disabled = true;
  try {
    const response = await fetch(`/api/orders/${window.ORDER_LIST_ID}/export-grid.xlsx`, {
      method: 'POST',
      headers: {'Accept': '*/*', 'Content-Type': 'application/json', 'X-CSRFToken': window.csrfToken},
      body: JSON.stringify({...gridPdfPayload, title: selectedGridExportTitle()}),
    });
    if (!response.ok) {
      let message = `Could not create Excel file (${response.status})`;
      try { const body = await response.json(); message = body.detail || Object.values(body)[0] || message; } catch (_) {}
      throw new Error(message);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a'); link.href = url;
    link.download = response.headers.get('X-XLSX-Filename') || 'current-grid.xlsx'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; }
});
document.getElementById('grid-pdf-download').addEventListener('click', () => {
  if (!gridPdfObjectUrl) return;
  const link = document.createElement('a'); link.href = gridPdfObjectUrl; link.download = gridPdfFilename; link.click();
});
document.getElementById('grid-pdf-close').addEventListener('click', closeGridPdfPreview);
document.getElementById('grid-pdf-dialog').addEventListener('cancel', event => { event.preventDefault(); closeGridPdfPreview(); });
document.querySelectorAll('[data-export-menu]').forEach(button => button.addEventListener('click', () => {
  const selected = document.getElementById(button.dataset.exportMenu);
  document.querySelectorAll('.export-choice-menu').forEach(menu => { if (menu !== selected) menu.hidden = true; });
  selected.hidden = !selected.hidden;
}));
document.addEventListener('click', event => {
  if (event.target.closest('.menu-wrap')) return;
  document.querySelectorAll('.export-choice-menu').forEach(menu => { menu.hidden = true; });
});
function buildColumnPanel() {
  const container = document.getElementById('column-options'); if (!gridApi) return;
  const sections = [
    ['Main', col => !['joe_quantity', 'bt_quantity', 'sqw_quantity', 'transfer_store', 'transfer_quantity'].includes(col.getColId()) && !col.getColId().startsWith('store_')],
    ['Distributors', col => ['joe_quantity', 'bt_quantity', 'sqw_quantity'].includes(col.getColId())],
    ['Other stores', col => col.getColId().startsWith('store_')],
    ['Transfers', col => ['transfer_store', 'transfer_quantity'].includes(col.getColId())],
  ];
  const columns = gridApi.getColumns().filter(col => col.getColId() !== 'delete');
  container.innerHTML = sections.map(([label, include]) => {
    const rows = columns.filter(include);
    if (!rows.length) return '';
    const storeSelectAll = label === 'Other stores' ? `<label class="column-select-all"><input type="checkbox" data-visible-store-all><span>All other stores</span></label>` : '';
    return `<div class="column-option-group"><strong>${label}</strong>${storeSelectAll}${rows.map(col => `<label><input type="checkbox" data-column="${col.getColId()}" ${col.isVisible() ? 'checked' : ''}><span>${escapeHtml(col.getColDef().headerName || col.getColId())}</span></label>`).join('')}</div>`;
  }).join('');
  const storeColumns = columns.filter(col => col.getColId().startsWith('store_'));
  const allStores = container.querySelector('[data-visible-store-all]');
  if (allStores) {
    const visibleCount = storeColumns.filter(col => col.isVisible()).length;
    allStores.checked = storeColumns.length > 0 && visibleCount === storeColumns.length;
    allStores.indeterminate = visibleCount > 0 && visibleCount < storeColumns.length;
  }
}
function buildFreezePanel() {
  const container = document.getElementById('freeze-options'); if (!gridApi) return;
  const columns = gridApi.getColumns().filter(col => col.getColId() !== 'delete');
  const storeColumns = columns.filter(col => col.getColId().startsWith('store_'));
  container.innerHTML = columns.map((col, index) => {
    const selectAll = col.getColId().startsWith('store_') && (index === 0 || !columns[index - 1].getColId().startsWith('store_'))
      ? `<label class="column-select-all"><input type="checkbox" data-freeze-store-all><span>Freeze all other stores</span></label>` : '';
    return `${selectAll}<label><input type="checkbox" data-freeze-column="${col.getColId()}" ${col.getPinned() === 'left' ? 'checked' : ''}><span>${escapeHtml(col.getColDef().headerName || col.getColId())}</span></label>`;
  }).join('');
  const allStores = container.querySelector('[data-freeze-store-all]');
  if (allStores) {
    const frozenCount = storeColumns.filter(col => col.getPinned() === 'left').length;
    allStores.checked = storeColumns.length > 0 && frozenCount === storeColumns.length;
    allStores.indeterminate = frozenCount > 0 && frozenCount < storeColumns.length;
  }
}
function updateFreezeButton() {
  const frozen = gridApi?.getColumns().some(col => col.getColId() !== 'delete' && col.getPinned() === 'left');
  document.getElementById('freeze-button').classList.toggle('active', Boolean(frozen));
}
function positionColumnPanel(panel, button) {
  const rect = button.getBoundingClientRect();
  const width = Math.min(300, window.innerWidth - 24);
  const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12));
  panel.style.width = `${width}px`;
  panel.style.left = `${left}px`;
  panel.style.right = 'auto';
  panel.style.top = `${Math.min(rect.bottom + 8, window.innerHeight - 180)}px`;
  panel.style.maxHeight = `${Math.max(160, window.innerHeight - rect.bottom - 24)}px`;
}
document.getElementById('columns-button').addEventListener('click', event => {
  const panel = document.getElementById('column-panel');
  document.getElementById('freeze-panel').hidden = true;
  document.getElementById('freeze-button').setAttribute('aria-expanded', 'false');
  panel.hidden = !panel.hidden;
  event.currentTarget.setAttribute('aria-expanded', String(!panel.hidden));
  if (!panel.hidden) { buildColumnPanel(); positionColumnPanel(panel, event.currentTarget); }
});
document.querySelector('#column-panel .icon-button').addEventListener('click', () => { document.getElementById('column-panel').hidden = true; document.getElementById('columns-button').setAttribute('aria-expanded', 'false'); });
document.querySelector('#freeze-panel .icon-button').addEventListener('click', () => { document.getElementById('freeze-panel').hidden = true; document.getElementById('freeze-button').setAttribute('aria-expanded', 'false'); });
document.getElementById('column-options').addEventListener('change', event => {
  if (event.target.matches('[data-visible-store-all]')) {
    const storeIds = gridApi.getColumns().map(col => col.getColId()).filter(id => id.startsWith('store_'));
    gridApi.setColumnsVisible(storeIds, event.target.checked);
    syncOtherStoresToggle(); buildColumnPanel();
    return;
  }
  if (!event.target.dataset.column) return;
  gridApi.setColumnsVisible([event.target.dataset.column], event.target.checked);
  syncOtherStoresToggle();
});
document.getElementById('freeze-options').addEventListener('change', event => {
  if (event.target.matches('[data-freeze-store-all]')) {
    const state = gridApi.getColumns().filter(col => col.getColId().startsWith('store_')).map(col => ({colId: col.getColId(), pinned: event.target.checked ? 'left' : null}));
    gridApi.applyColumnState({state}); buildFreezePanel(); updateFreezeButton();
    return;
  }
  const colId = event.target.dataset.freezeColumn;
  if (!colId) return;
  gridApi.applyColumnState({state: [{colId, pinned: event.target.checked ? 'left' : null}]});
  updateFreezeButton();
});
document.getElementById('reset-format-button').addEventListener('click', () => {
  clearTimeout(preferenceTimer); suppressPreferenceSave = true; clearFormatCookie();
  gridApi.setFilterModel(null); gridApi.resetColumnState(); setOtherStoresVisible(false);
  document.getElementById('column-panel').hidden = true; document.getElementById('freeze-panel').hidden = true;
  document.getElementById('columns-button').setAttribute('aria-expanded', 'false');
  buildColumnPanel(); buildFreezePanel(); updateFreezeButton();
  setTimeout(() => { suppressPreferenceSave = false; clearFormatCookie(); }, 100);
  showToast('Column format reset');
});
window.addEventListener('resize', () => {
  const columnsPanel = document.getElementById('column-panel');
  const freezePanel = document.getElementById('freeze-panel');
  if (!columnsPanel.hidden) positionColumnPanel(columnsPanel, document.getElementById('columns-button'));
  if (!freezePanel.hidden) positionColumnPanel(freezePanel, document.getElementById('freeze-button'));
});
document.addEventListener('click', event => {
  const columnsPanel = document.getElementById('column-panel');
  const freezePanel = document.getElementById('freeze-panel');
  if (!columnsPanel.hidden && !event.target.closest('#column-panel') && !event.target.closest('#columns-button')) {
    columnsPanel.hidden = true; document.getElementById('columns-button').setAttribute('aria-expanded', 'false');
  }
  if (!freezePanel.hidden && !event.target.closest('#freeze-panel') && !event.target.closest('#freeze-button')) {
    freezePanel.hidden = true; document.getElementById('freeze-button').setAttribute('aria-expanded', 'false');
  }
});
function updateCount() { document.getElementById('item-count').textContent = gridApi ? gridApi.getDisplayedRowCount() : 0; }

document.getElementById('transfer-editor-rows').addEventListener('change', event => {
  const row = event.target.closest('.transfer-editor-row'); if (!row) return;
  const index = Number(row.dataset.index);
  if (event.target.matches('select')) transferDraft[index].from_store_id = Number(event.target.value) || '';
  if (event.target.matches('input')) transferDraft[index].quantity = Number(event.target.value) || 0;
});
document.getElementById('transfer-editor-rows').addEventListener('click', event => {
  const button = event.target.closest('.transfer-remove-row'); if (!button) return;
  transferDraft.splice(Number(button.closest('.transfer-editor-row').dataset.index), 1); renderTransferDraft();
});
document.getElementById('transfer-add-row').addEventListener('click', () => { transferDraft.push({from_store_id: '', quantity: 1}); renderTransferDraft(); });
document.getElementById('transfer-save').addEventListener('click', saveTransferDraft);
document.getElementById('transfer-cancel').addEventListener('click', closeTransferEditor);
document.getElementById('transfer-close').addEventListener('click', closeTransferEditor);
document.getElementById('transfer-dialog').addEventListener('cancel', event => { event.preventDefault(); closeTransferEditor(); });

document.getElementById('camera-button').addEventListener('click', startCamera);
document.getElementById('camera-close').addEventListener('click', stopCamera);
document.getElementById('barcode-photo').addEventListener('change', scanBarcodePhoto);
document.getElementById('camera-zoom').addEventListener('input', updateCameraZoom);
document.getElementById('finalize-order-button')?.addEventListener('click', async event => {
  if (!confirm('Finalize this order list? Regular users will no longer be able to edit it.')) return;
  event.currentTarget.disabled = true;
  try { await apiFetch(`/api/orders/${window.ORDER_LIST_ID}/finalize/`, {method: 'POST'}); location.reload(); }
  catch (error) { event.currentTarget.disabled = false; showToast(error.message, true); }
});
document.getElementById('delete-order-button')?.addEventListener('click', async event => {
  if (!confirm('Delete this entire order list? This cannot be undone.')) return;
  event.currentTarget.disabled = true;
  try { await apiFetch(`/api/orders/${window.ORDER_LIST_ID}/`, {method: 'DELETE'}); location.href = '/'; }
  catch (error) { event.currentTarget.disabled = false; showToast(error.message, true); }
});
async function startCamera() {
  const dialog = document.getElementById('camera-dialog'); const status = document.getElementById('camera-status'); dialog.showModal();
  if (!window.isSecureContext && location.hostname !== 'localhost') { status.textContent = 'Live preview needs HTTPS on phones. Use “Take photo” below instead.'; return; }
  if (!('BarcodeDetector' in window)) { status.textContent = 'Live scanning is not supported here. Use “Take photo” below instead.'; return; }
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({video: {facingMode: {ideal: 'environment'}}});
    const video = document.getElementById('camera-video'); video.srcObject = cameraStream; status.textContent = 'Scanning...';
    setupCameraZoom(cameraStream.getVideoTracks()[0]);
    cameraScanAttempts = 0;
    const detector = new BarcodeDetector({formats: ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128']});
    const scan = async () => {
      if (!cameraStream || cameraScanInProgress) return;
      cameraScanInProgress = true;
      try {
        const codes = await detector.detect(video);
        cameraScanAttempts += 1;
        if (codes.length) {
          const code = codes[0].rawValue;
          status.textContent = 'Checking barcode…';
          const products = await apiFetch(`/api/products/search/?q=${encodeURIComponent(code)}&order_id=${window.ORDER_LIST_ID}`);
          const product = exactBarcodeProduct(products, code);
          if (product) {
            cameraScanPending = true;
            searchInput.value = code;
            stopCamera();
            await chooseProduct(product, {reopenCamera: true});
            document.getElementById('selected-on-shelf').focus();
            return;
          }
          status.textContent = 'That barcode was not recognized. Keep it centered and we will keep trying.';
        } else if (cameraScanAttempts % 60 === 0) {
          status.textContent = 'Still scanning — hold the barcode steady in good light.';
        }
      } catch (_) {
        // A missed frame is normal on phone cameras; continue trying instead of closing.
      } finally {
        cameraScanInProgress = false;
      }
      if (cameraStream) setTimeout(() => requestAnimationFrame(scan), 180);
    };
    scan();
  } catch (error) { status.textContent = `Camera unavailable: ${error.message}`; }
}
async function scanBarcodePhoto(event) {
  const file = event.target.files?.[0]; if (!file) return;
  const status = document.getElementById('camera-status'); status.textContent = 'Reading barcode from photo…';
  if (!window.Html5Qrcode) { status.textContent = 'Photo scanner did not load. Check your connection and try again.'; return; }
  const scanner = new Html5Qrcode('camera-file-reader');
  try {
    const code = await scanner.scanFile(file, true);
    cameraScanPending = true; searchInput.value = code; event.target.value = ''; stopCamera(); searchProducts();
  } catch (_) { status.textContent = 'No barcode found. Try again with the barcode centered and in good light.'; event.target.value = ''; }
}
function setupCameraZoom(track) {
  const slider = document.getElementById('camera-zoom'); const capabilities = track?.getCapabilities?.() || {};
  cameraZoomTrack = track; cameraUsesHardwareZoom = Boolean(capabilities.zoom);
  slider.min = cameraUsesHardwareZoom ? capabilities.zoom.min : 1; slider.max = cameraUsesHardwareZoom ? capabilities.zoom.max : 3; slider.step = cameraUsesHardwareZoom ? capabilities.zoom.step || 0.1 : 0.1; slider.value = cameraUsesHardwareZoom ? capabilities.zoom.min : 1;
  updateCameraZoom();
}
async function updateCameraZoom() {
  const slider = document.getElementById('camera-zoom'); const value = Number(slider.value || 1); document.getElementById('camera-zoom-value').textContent = `${value.toFixed(value % 1 ? 1 : 0)}×`;
  if (cameraUsesHardwareZoom && cameraZoomTrack) { try { await cameraZoomTrack.applyConstraints({advanced: [{zoom: value}]}); } catch (_) {} }
  else document.getElementById('camera-video').style.transform = `scale(${value})`;
}
function stopCamera() { cameraStream?.getTracks().forEach(track => track.stop()); cameraStream = null; cameraScanInProgress = false; cameraZoomTrack = null; cameraUsesHardwareZoom = false; const video=document.getElementById('camera-video'); video.style.transform='scale(1)'; document.getElementById('camera-dialog').close(); }

loadOrder().catch(error => { gridElement.innerHTML = `<div class="form-error">${escapeHtml(error.message)}</div>`; });
orderSearchCategoryFilter = new SearchCategoryFilter({button:document.getElementById('order-search-filter-button'),panel:document.getElementById('order-search-filter-panel'),onChange:()=>{suggestionProducts=orderSearchCategoryFilter.filter(rawSuggestionProducts);renderSuggestions();searchResults.hidden=!suggestionProducts.length;},onSelectAll:()=>{suggestionProducts.forEach(product=>batchSelectedProducts.set(product.id,product));updateBatchSelection();renderSuggestions();}});

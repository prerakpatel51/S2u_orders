const searchInput = document.getElementById('inventory-search');
const results = document.getElementById('inventory-results');
const addButton = document.getElementById('add-inventory-selection');
const countElement = document.getElementById('inventory-selection-count');
const gridElement = document.getElementById('inventory-grid');
const emptyElement = document.getElementById('inventory-empty');
const selected = new Map();
let suggestions = [];
let suggestionIndex = -1;
let selectionAnchor = -1;
let searchTimer;
let requestId = 0;
let gridApi;
let displayedProducts = new Map();
let rawSuggestions = [];
let searchCategoryFilter;

const escapeHtml = value => { const el = document.createElement('div'); el.textContent = value ?? ''; return el.innerHTML; };
const formatNumber = value => Number(value || 0).toLocaleString(undefined, {maximumFractionDigits: 3});
const barcodeVariants = value => { const code=String(value||'').trim(); if(!/^\d{7,15}$/.test(code)) return new Set(); const stripped=code.replace(/^0+/,'')||'0', values=new Set([code,stripped,`0${code}`,`0${stripped}`]); if(code.endsWith('0')&&code.length>7){values.add(code.slice(0,-1));values.add(code.slice(0,-1).replace(/^0+/,'')||'0');} if(code.length<15)values.add(`${code}0`); return values; };
const exactBarcodeProduct = (products, query) => { const variants=barcodeVariants(query); if(!variants.size)return null; const matches=products.filter(product=>product.codes.some(code=>variants.has(String(code).replace(/\D/g,'')))); return matches.length===1?matches[0]:null; };
const normalizeSearchText = value => String(value || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '');
function searchNameMatches(name, query) {
  const normalizedName = normalizeSearchText(name);
  return query.split(/\s+/).map(normalizeSearchText).filter(Boolean).every(word => {
    const variants = [word];
    if (/^[a-z]+$/.test(word) && word.length > 3) {
      if (word.endsWith('ies') && word.length > 4) variants.push(`${word.slice(0, -3)}y`);
      if (word.endsWith('es') && word.length > 4) variants.push(word.slice(0, -2));
      if (word.endsWith('s')) variants.push(word.slice(0, -1));
    }
    return variants.some(variant => normalizedName.includes(variant));
  });
}

function renderSuggestions() {
  results.innerHTML = suggestions.length ? `<p class="suggestion-result-count">${suggestions.length.toLocaleString()} matching product${suggestions.length === 1 ? '' : 's'}</p>${suggestions.map((product, index) => `<div class="suggestion-item inventory-suggestion${index === suggestionIndex ? ' active' : ''}${selected.has(product.id) ? ' batch-selected' : ''}" data-id="${product.id}" data-index="${index}"><label class="inventory-checkbox-wrap" title="Select product"><input class="inventory-checkbox" type="checkbox" aria-label="Select ${escapeHtml(product.name)}" ${selected.has(product.id) ? 'checked' : ''}></label><button type="button" class="inventory-product-action"><span class="suggestion-copy"><strong>${escapeHtml(product.name)}</strong><small>${escapeHtml(product.number)}${product.codes.length ? ` · ${escapeHtml(product.codes[0])}` : ''}</small></span><span class="suggestion-action">${displayedProducts.has(product.id) ? 'Added' : 'Add'}</span></button></div>`).join('')}` : '<p class="empty-state compact">No matching products</p>';
  results.hidden = false;
  results.querySelector('.suggestion-item.active')?.scrollIntoView({block: 'nearest'});
}

function updateSelection() {
  countElement.textContent = selected.size;
  addButton.disabled = selected.size === 0;
  if (!results.hidden) renderSuggestions();
}

function selectRange(toIndex) {
  const start = selectionAnchor < 0 ? toIndex : Math.min(selectionAnchor, toIndex);
  const end = selectionAnchor < 0 ? toIndex : Math.max(selectionAnchor, toIndex);
  for (let index = start; index <= end; index += 1) selected.set(suggestions[index].id, suggestions[index]);
  selectionAnchor = toIndex; updateSelection();
}

function toggleProduct(product, index) {
  if (selected.has(product.id)) selected.delete(product.id); else selected.set(product.id, product);
  selectionAnchor = index; updateSelection();
}

function selectKeyboardProduct(product, index) {
  if (!product) return;
  selected.set(product.id, product); selectionAnchor = index; updateSelection();
}

async function searchProducts() {
  const query = searchInput.value.trim(); const currentRequest = ++requestId;
  suggestionIndex = -1; selectionAnchor = -1;
  if (!query) { suggestions = []; results.hidden = true; return; }
  results.innerHTML = '<p class="suggestion-loading">Searching products…</p>'; results.hidden = false;
  try { const response = await apiFetch(`/api/products/search/?q=${encodeURIComponent(query)}`); if (currentRequest !== requestId) return; const scanned=exactBarcodeProduct(response,query); if(scanned){results.hidden=true;await addProducts([scanned]);return;} rawSuggestions = response; applySuggestionFilters(); }
  catch (error) { showToast(error.message, true); }
}

function applySuggestionFilters() {
  const query = searchInput.value.trim().toLowerCase();
  if (!query) { suggestions = []; results.hidden = true; return; }
  const field = document.getElementById('inventory-search-field').value;
  const hideAdded = document.getElementById('inventory-hide-added').checked;
  suggestions = (searchCategoryFilter ? searchCategoryFilter.filter(rawSuggestions) : rawSuggestions).filter(product => {
    if (hideAdded && displayedProducts.has(product.id)) return false;
    if (field === 'name') return searchNameMatches(product.name, query);
    if (field === 'number') return product.number.toLowerCase().includes(query) || product.codes.some(code => code.toLowerCase().includes(query));
    return true;
  });
  suggestionIndex = -1; selectionAnchor = -1; renderSuggestions();
}

function pairedRenderer(params) {
  const value = params.value || {};
  const element = document.createElement('div'); element.className = 'paired-value inventory-pair';
  element.innerHTML = `<strong>${formatNumber(value.stock)}</strong><span>${formatNumber(value.monthly_needed)}/m</span>`;
  return element;
}

function removeRenderer(params) {
  const button = document.createElement('button'); button.type = 'button'; button.className = 'grid-delete'; button.title = 'Remove product'; button.textContent = '×';
  button.addEventListener('click', () => { displayedProducts.delete(params.data.id); gridApi.applyTransaction({remove: [params.data]}); updateEmpty(); });
  return button;
}

function updateEmpty() {
  const empty = displayedProducts.size === 0; gridElement.hidden = empty; emptyElement.hidden = !empty;
}

function buildGrid(payload) {
  const storeColumns = payload.stores.map((store, index) => ({
    colId: `store_${store.id}`, headerName: store.number, headerTooltip: store.name, minWidth: 94, width: 110,
    headerClass: `${['store-blue', 'store-green', 'store-peach', 'store-lilac', 'store-mint'][index % 5]}-header`,
    cellClass: `${['store-blue', 'store-green', 'store-peach', 'store-lilac', 'store-mint'][index % 5]}-cell`,
    valueGetter: params => params.data.stores.find(item => item.store_id === store.id), cellRenderer: pairedRenderer,
    comparator: (a, b) => gridNumberCompare(a?.stock, b?.stock),
  }));
  const columnDefs = [
    {field: 'number', headerName: 'Product #', pinned: 'left', width: 115, filter: true, comparator: gridNaturalCompare},
    {field: 'name', headerName: 'Product name', pinned: 'left', minWidth: 210, width: 280, filter: ProductNameCategoryFilter, comparator: gridNaturalCompare},
    ...storeColumns,
    {colId: 'remove', headerName: '', pinned: 'right', width: 42, minWidth: 42, maxWidth: 42, sortable: false, filter: false, resizable: false, cellRenderer: removeRenderer},
  ];
  if (!gridApi) gridApi = agGrid.createGrid(gridElement, {columnDefs, rowData: payload.products, defaultColDef: {sortable: true, resizable: true}, rowHeight: 48, getRowId: params => String(params.data.id)});
  else { gridApi.setGridOption('columnDefs', columnDefs); gridApi.setGridOption('rowData', [...displayedProducts.values()]); }
}

async function addProducts(products) {
  const ids = products.map(product => product.id).filter(id => !displayedProducts.has(id));
  if (!ids.length) { selected.clear(); updateSelection(); showToast('Those products are already shown'); return; }
  addButton.disabled = true;
  try {
    const payload = await apiFetch('/api/inventory/compare/', {method: 'POST', body: JSON.stringify({product_ids: [...displayedProducts.keys(), ...ids]})});
    displayedProducts = new Map(payload.products.map(product => [product.id, product])); gridElement.hidden = false; buildGrid(payload);
    selected.clear(); updateSelection(); searchInput.value = ''; suggestions = []; rawSuggestions = []; results.hidden = true; updateEmpty(); searchInput.focus();
    showToast(`${ids.length} product${ids.length === 1 ? '' : 's'} added`);
  } catch (error) { showToast(error.message, true); }
  finally { addButton.disabled = selected.size === 0; }
}

searchInput.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(searchProducts, 220); });
document.getElementById('inventory-search-field').addEventListener('change', applySuggestionFilters);
document.getElementById('inventory-hide-added').addEventListener('change', applySuggestionFilters);
document.getElementById('inventory-barcode-photo').addEventListener('change', async event => {
  const file = event.target.files?.[0]; if (!file) return;
  if (!window.Html5Qrcode) { showToast('Photo scanner did not load. Check your connection and try again.', true); return; }
  showToast('Reading barcode…');
  try {
    const scanner = new Html5Qrcode('inventory-file-reader');
    searchInput.value = await scanner.scanFile(file, true); event.target.value = ''; searchProducts();
  } catch (_) { event.target.value = ''; showToast('No barcode found. Center it in good light and try again.', true); }
});
searchInput.addEventListener('keydown', event => {
  if (event.key === 'ArrowDown' && suggestions.length) { event.preventDefault(); suggestionIndex = Math.min(suggestionIndex + 1, suggestions.length - 1); if (event.shiftKey) selectKeyboardProduct(suggestions[suggestionIndex], suggestionIndex); else renderSuggestions(); }
  else if (event.key === 'ArrowUp' && suggestions.length) { event.preventDefault(); suggestionIndex = Math.max(suggestionIndex - 1, 0); if (event.shiftKey) selectKeyboardProduct(suggestions[suggestionIndex], suggestionIndex); else renderSuggestions(); }
  else if (event.key === 'Escape') { results.hidden = true; }
  else if (event.key === 'Enter' && suggestions.length) { event.preventDefault(); const index = suggestionIndex < 0 ? 0 : suggestionIndex; if (event.shiftKey) toggleProduct(suggestions[index], index); else if (selected.size) addProducts([...selected.values()]); else addProducts([suggestions[index]]); }
});
results.addEventListener('click', event => {
  event.stopPropagation();
  const button = event.target.closest('[data-id]'); if (!button) return;
  const index = Number(button.dataset.index); const product = suggestions[index];
  if (event.target.closest('.inventory-checkbox')) {
    event.preventDefault();
    if (event.shiftKey) selectRange(index); else toggleProduct(product, index);
  } else if (event.target.closest('.inventory-product-action')) {
    if (event.shiftKey) selectRange(index); else addProducts([product]);
  }
});
addButton.addEventListener('click', () => addProducts([...selected.values()]));
document.getElementById('clear-inventory').addEventListener('click', () => { displayedProducts.clear(); if (gridApi) gridApi.setGridOption('rowData', []); updateEmpty(); searchInput.focus(); });
document.addEventListener('click', event => { if (!event.target.closest('.inventory-search-wrap')) results.hidden = true; });
updateEmpty();
searchCategoryFilter = new SearchCategoryFilter({button:document.getElementById('inventory-search-filter-button'),panel:document.getElementById('inventory-search-filter-panel'),onChange:applySuggestionFilters,onSelectAll:()=>{suggestions.forEach(product=>selected.set(product.id,product));updateSelection();}});

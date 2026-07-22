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
let inventoryStores = [];
const inventoryMobileLayout = window.matchMedia('(max-width: 700px)');
let cameraStream;
let cameraScanner;
let cameraScanInProgress = false;
let cameraScanAttempts = 0;
let cameraZoomTrack;
let cameraUsesHardwareZoom = false;
let cameraZoomMin = 1;
let cameraZoomMax = 3;
let cameraZoomStep = 0.1;
let cameraPinchGesture = null;
let cameraTorchSupported = false;
let cameraTorchOn = false;
let cameraContinuousFocus = false;

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

function mobileProductRenderer(params) {
  const element = document.createElement('div'); element.className = 'inventory-mobile-product';
  element.innerHTML = `<strong>${escapeHtml(params.data.name)}</strong><small>#${escapeHtml(params.data.number)}</small>`;
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
  inventoryStores = payload.stores;
  const mobile = inventoryMobileLayout.matches;
  const storeColumns = payload.stores.map((store, index) => ({
    colId: `store_${store.id}`, headerName: store.number, headerTooltip: store.name, minWidth: mobile ? 76 : 94, width: mobile ? 84 : 110,
    headerClass: `${['store-blue', 'store-green', 'store-peach', 'store-lilac', 'store-mint'][index % 5]}-header`,
    cellClass: `${['store-blue', 'store-green', 'store-peach', 'store-lilac', 'store-mint'][index % 5]}-cell`,
    valueGetter: params => params.data.stores.find(item => item.store_id === store.id), cellRenderer: pairedRenderer,
    comparator: (a, b) => gridNumberCompare(a?.stock, b?.stock),
  }));
  const removeColumn = {colId: 'remove', headerName: '', pinned: mobile ? null : 'right', width: 42, minWidth: 42, maxWidth: 42, sortable: false, filter: false, resizable: false, cellRenderer: removeRenderer};
  const columnDefs = mobile ? [
    {field: 'name', headerName: 'Product', pinned: 'left', lockPinned: true, minWidth: 150, width: 150, maxWidth: 150, filter: ProductNameCategoryFilter, comparator: gridNaturalCompare, cellRenderer: mobileProductRenderer},
    ...storeColumns,
    removeColumn,
  ] : [
    {field: 'number', headerName: 'Product #', pinned: 'left', width: 115, filter: true, comparator: gridNaturalCompare},
    {field: 'name', headerName: 'Product name', pinned: 'left', minWidth: 210, width: 280, filter: ProductNameCategoryFilter, comparator: gridNaturalCompare},
    ...storeColumns,
    removeColumn,
  ];
  if (!gridApi) gridApi = agGrid.createGrid(gridElement, {columnDefs, rowData: payload.products, defaultColDef: {sortable: true, resizable: true}, rowHeight: mobile ? 58 : 48, getRowId: params => String(params.data.id)});
  else { gridApi.setGridOption('columnDefs', columnDefs); gridApi.setGridOption('rowData', [...displayedProducts.values()]); }
  gridApi.setGridOption('rowHeight', mobile ? 58 : 48); gridApi.resetRowHeights();
}

function refreshInventoryLayout() {
  if (!gridApi || !inventoryStores.length) return;
  buildGrid({stores: inventoryStores, products: [...displayedProducts.values()]});
}
if (inventoryMobileLayout.addEventListener) inventoryMobileLayout.addEventListener('change', refreshInventoryLayout);
else inventoryMobileLayout.addListener(refreshInventoryLayout);

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
document.getElementById('camera-button').addEventListener('click', startCamera);
document.getElementById('camera-close').addEventListener('click', stopCamera);
document.getElementById('barcode-photo').addEventListener('change', scanBarcodePhoto);
document.getElementById('camera-zoom').addEventListener('input', updateCameraZoom);
document.getElementById('camera-zoom-out').addEventListener('click', () => adjustCameraZoom(-1));
document.getElementById('camera-zoom-in').addEventListener('click', () => adjustCameraZoom(1));
document.getElementById('camera-torch').addEventListener('click', toggleCameraTorch);
document.getElementById('camera-dialog').addEventListener('cancel', event => { event.preventDefault(); stopCamera(); });
const cameraVideoFrame = document.getElementById('camera-video-frame');
cameraVideoFrame.addEventListener('touchstart', startCameraPinch, {passive: false});
cameraVideoFrame.addEventListener('touchmove', moveCameraPinch, {passive: false});
cameraVideoFrame.addEventListener('touchend', endCameraPinch);
cameraVideoFrame.addEventListener('touchcancel', endCameraPinch);
window.addEventListener('pagehide', () => { cameraStream?.getTracks().forEach(track => track.stop()); });
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

async function startCamera() {
  const dialog = document.getElementById('camera-dialog'); const status = document.getElementById('camera-status');
  if (!dialog.open) dialog.showModal();
  status.textContent = 'Starting camera…';
  if (!window.isSecureContext && location.hostname !== 'localhost') { status.textContent = 'Live preview needs HTTPS on phones. Use “Take photo” below instead.'; return; }
  if (!navigator.mediaDevices?.getUserMedia) { status.textContent = 'This browser cannot open a live camera. Use “Take barcode photo” below instead.'; return; }
  if (!('BarcodeDetector' in window)) {
    await startCompatibleCameraScanner(status);
    return;
  }
  try {
    useNativeCameraView(true);
    cameraStream = await navigator.mediaDevices.getUserMedia({video: {facingMode: {ideal: 'environment'}, width: {ideal: 1920}, height: {ideal: 1080}, frameRate: {ideal: 30}}});
    const video = document.getElementById('camera-video'); video.srcObject = cameraStream;
    await video.play().catch(() => {});
    status.textContent = 'Scanning… Pinch or use the controls to zoom.';
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
          if (await handleDetectedCameraCode(codes[0].rawValue, status)) return;
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
  } catch (error) { status.textContent = cameraErrorMessage(error); }
}

async function startCompatibleCameraScanner(status) {
  if (!window.Html5Qrcode) { status.textContent = 'The iPhone-compatible scanner did not load. Check your connection or use “Take barcode photo” below.'; return; }
  useNativeCameraView(false);
  try {
    const formats = window.Html5QrcodeSupportedFormats ? [Html5QrcodeSupportedFormats.EAN_13, Html5QrcodeSupportedFormats.EAN_8, Html5QrcodeSupportedFormats.UPC_A, Html5QrcodeSupportedFormats.UPC_E, Html5QrcodeSupportedFormats.CODE_128] : undefined;
    cameraScanner = new Html5Qrcode('camera-live-reader', formats ? {formatsToSupport: formats, verbose: false} : {verbose: false});
    await cameraScanner.start(
      {facingMode: 'environment'},
      {fps: 10, disableFlip: true},
      async code => {
        if (cameraScanInProgress) return;
        cameraScanInProgress = true;
        try { await handleDetectedCameraCode(code, status); }
        finally { cameraScanInProgress = false; }
      },
      () => {}
    );
    await optimizeCompatibleCameraTrack();
    setupCameraZoom(null);
    status.textContent = 'Scanning… Pinch or use the controls to zoom.';
  } catch (error) {
    await clearCompatibleCameraScanner();
    status.textContent = cameraErrorMessage(error);
  }
}

async function optimizeCompatibleCameraTrack() {
  if (!cameraScanner?.applyVideoConstraints) return;
  let capabilities = {};
  try { capabilities = cameraScanner.getRunningTrackCapabilities?.() || {}; } catch (_) {}
  const continuousFocus = Array.isArray(capabilities.focusMode) && capabilities.focusMode.includes('continuous');
  const constraints = {width: {ideal: 1280}, height: {ideal: 720}};
  if (continuousFocus) constraints.advanced = [{focusMode: 'continuous'}];
  try { await cameraScanner.applyVideoConstraints(constraints); }
  catch (_) {
    if (continuousFocus) {
      try { await cameraScanner.applyVideoConstraints({advanced: [{focusMode: 'continuous'}]}); } catch (_) {}
    }
  }
}

async function handleDetectedCameraCode(code, status) {
  status.textContent = 'Checking barcode…';
  const products = await apiFetch(`/api/products/search/?q=${encodeURIComponent(code)}`);
  const product = exactBarcodeProduct(products, code);
  if (!product) { status.textContent = 'That barcode was not recognized. Keep it centered and we will keep trying.'; return false; }
  searchInput.value = code;
  await stopCamera();
  await addProducts([product]);
  return true;
}

function useNativeCameraView(native) {
  document.getElementById('camera-video').hidden = !native;
  document.getElementById('camera-live-reader').hidden = native;
}

function cameraErrorMessage(error) {
  const detail = String(error?.message || error || '').toLowerCase();
  if (error?.name === 'NotAllowedError' || detail.includes('permission') || detail.includes('notallowed')) return 'Camera access was blocked. On iPhone, open Settings › Safari › Camera and allow access, then try again.';
  if (error?.name === 'NotFoundError' || detail.includes('not found') || detail.includes('notfound')) return 'No rear camera was found. Use “Take barcode photo” below instead.';
  return `Camera unavailable: ${error?.message || error}`;
}

async function scanBarcodePhoto(event) {
  const file = event.target.files?.[0]; if (!file) return;
  const status = document.getElementById('camera-status'); status.textContent = 'Reading barcode from photo…';
  if (!window.Html5Qrcode) { status.textContent = 'Photo scanner did not load. Check your connection and try again.'; return; }
  const scanner = new Html5Qrcode('camera-file-reader');
  try {
    const code = await scanner.scanFile(file, true);
    searchInput.value = code; event.target.value = ''; await stopCamera(); await searchProducts();
  } catch (_) { status.textContent = 'No barcode found. Try again with the barcode centered and in good light.'; event.target.value = ''; }
}

function setupCameraZoom(track) {
  const slider = document.getElementById('camera-zoom');
  let capabilities = {}; let settings = {};
  try { capabilities = track?.getCapabilities?.() || cameraScanner?.getRunningTrackCapabilities?.() || {}; } catch (_) {}
  try { settings = track?.getSettings?.() || cameraScanner?.getRunningTrackSettings?.() || {}; } catch (_) {}
  cameraZoomTrack = track;
  cameraUsesHardwareZoom = Boolean(capabilities.zoom && Number.isFinite(Number(capabilities.zoom.min)) && Number.isFinite(Number(capabilities.zoom.max)));
  cameraZoomMin = cameraUsesHardwareZoom ? Number(capabilities.zoom.min) : 1;
  cameraZoomMax = cameraUsesHardwareZoom ? Number(capabilities.zoom.max) : 3;
  cameraZoomStep = cameraUsesHardwareZoom ? Number(capabilities.zoom.step || 0.1) : 0.1;
  slider.min = cameraZoomMin; slider.max = cameraZoomMax; slider.step = cameraZoomStep;
  slider.value = cameraUsesHardwareZoom && Number.isFinite(Number(settings.zoom)) ? settings.zoom : cameraZoomMin;
  cameraTorchSupported = capabilities.torch === true;
  cameraTorchOn = false;
  cameraContinuousFocus = Array.isArray(capabilities.focusMode) && capabilities.focusMode.includes('continuous');
  const torchButton = document.getElementById('camera-torch');
  torchButton.hidden = !cameraTorchSupported; torchButton.setAttribute('aria-pressed', 'false');
  updateCameraZoom();
  if (cameraContinuousFocus && !cameraUsesHardwareZoom) applyCameraConstraints({focusMode: 'continuous'}).catch(() => {});
}

function clampCameraZoom(value) { return Math.max(cameraZoomMin, Math.min(cameraZoomMax, value)); }

function updateCameraZoomDisplay(value) {
  const decimals = Math.abs(value - Math.round(value)) < .01 ? 0 : 1;
  document.getElementById('camera-zoom-value').textContent = `${value.toFixed(decimals)}×`;
  document.getElementById('camera-zoom-out').disabled = value <= cameraZoomMin + .001;
  document.getElementById('camera-zoom-in').disabled = value >= cameraZoomMax - .001;
}

function applyCameraPreviewScale(value) {
  const scale = cameraUsesHardwareZoom ? 1 : value;
  document.getElementById('camera-video').style.transform = `scale(${scale})`;
  const compatibleVideo = document.querySelector('#camera-live-reader video');
  if (compatibleVideo) compatibleVideo.style.transform = `scale(${scale})`;
}

async function applyCameraConstraints(values) {
  const preferred = {...values};
  if (cameraContinuousFocus && preferred.focusMode === undefined) preferred.focusMode = 'continuous';
  if (cameraUsesHardwareZoom && preferred.zoom === undefined) preferred.zoom = Number(document.getElementById('camera-zoom').value);
  if (cameraTorchSupported && preferred.torch === undefined) preferred.torch = cameraTorchOn;
  const constraints = {advanced: [preferred]};
  if (cameraZoomTrack) return cameraZoomTrack.applyConstraints(constraints);
  if (cameraScanner?.applyVideoConstraints) return cameraScanner.applyVideoConstraints(constraints);
  throw new Error('No active camera track');
}

async function updateCameraZoom() {
  const slider = document.getElementById('camera-zoom');
  const value = clampCameraZoom(Number(slider.value || cameraZoomMin));
  slider.value = value; updateCameraZoomDisplay(value); applyCameraPreviewScale(value);
  if (cameraUsesHardwareZoom) {
    try { await applyCameraConstraints({zoom: value}); }
    catch (_) {
      document.getElementById('camera-video').style.transform = `scale(${value})`;
      const compatibleVideo = document.querySelector('#camera-live-reader video');
      if (compatibleVideo) compatibleVideo.style.transform = `scale(${value})`;
    }
  }
}

function adjustCameraZoom(direction) {
  const slider = document.getElementById('camera-zoom');
  const buttonStep = Math.max(cameraZoomStep, (cameraZoomMax - cameraZoomMin) / 8, .25);
  slider.value = clampCameraZoom(Number(slider.value) + direction * buttonStep);
  updateCameraZoom();
}

function cameraTouchDistance(touches) { return Math.hypot(touches[0].clientX - touches[1].clientX, touches[0].clientY - touches[1].clientY); }
function startCameraPinch(event) {
  if (event.touches.length !== 2) return;
  cameraPinchGesture = {distance: cameraTouchDistance(event.touches), zoom: Number(document.getElementById('camera-zoom').value)};
  event.preventDefault();
}
function moveCameraPinch(event) {
  if (!cameraPinchGesture || event.touches.length !== 2) return;
  const distance = cameraTouchDistance(event.touches);
  if (!cameraPinchGesture.distance) return;
  const slider = document.getElementById('camera-zoom');
  slider.value = clampCameraZoom(cameraPinchGesture.zoom * distance / cameraPinchGesture.distance);
  updateCameraZoom(); event.preventDefault();
}
function endCameraPinch(event) { if (event.touches.length < 2) cameraPinchGesture = null; }

async function toggleCameraTorch() {
  if (!cameraTorchSupported) return;
  const next = !cameraTorchOn;
  try {
    await applyCameraConstraints({torch: next}); cameraTorchOn = next;
    document.getElementById('camera-torch').setAttribute('aria-pressed', String(next));
  } catch (_) {
    cameraTorchSupported = false; document.getElementById('camera-torch').hidden = true;
  }
}

async function clearCompatibleCameraScanner() {
  const scanner = cameraScanner; cameraScanner = null;
  if (!scanner) return;
  try { await scanner.stop(); } catch (_) {}
  try { scanner.clear(); } catch (_) {}
}

async function stopCamera() {
  cameraStream?.getTracks().forEach(track => track.stop()); cameraStream = null;
  await clearCompatibleCameraScanner();
  cameraScanInProgress = false; cameraZoomTrack = null; cameraUsesHardwareZoom = false; cameraPinchGesture = null; cameraTorchSupported = false; cameraTorchOn = false; cameraContinuousFocus = false;
  const video = document.getElementById('camera-video'); video.srcObject = null; video.style.transform = 'scale(1)';
  document.getElementById('camera-torch').hidden = true;
  const compatibleVideo = document.querySelector('#camera-live-reader video'); if (compatibleVideo) compatibleVideo.style.transform = 'scale(1)';
  const dialog = document.getElementById('camera-dialog'); if (dialog.open) dialog.close();
}

updateEmpty();
searchCategoryFilter = new SearchCategoryFilter({button:document.getElementById('inventory-search-filter-button'),panel:document.getElementById('inventory-search-filter-panel'),onChange:applySuggestionFilters,onSelectAll:()=>{suggestions.forEach(product=>selected.set(product.id,product));updateSelection();}});

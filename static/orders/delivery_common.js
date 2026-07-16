window.deliveryUI = (() => {
  const escape = value => { const node = document.createElement('div'); node.textContent = value ?? ''; return node.innerHTML; };
  const formatDate = value => value ? new Intl.DateTimeFormat(undefined, {month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit'}).format(new Date(value)) : 'Not submitted';
  const formatShortDate = value => value ? new Intl.DateTimeFormat(undefined, {month: 'short', day: 'numeric', year: 'numeric'}).format(new Date(value)) : '—';
  const formatBytes = bytes => { const value = Number(bytes || 0); if (value < 1024) return `${value} B`; if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`; return `${(value / 1048576).toFixed(1)} MB`; };
  const statusClass = status => ({verified: 'verified', resolved: 'verified', issue_found: 'issue', needs_info: 'warning', submitted: 'pending', under_review: 'reviewing', draft: 'draft'}[status] || 'draft');
  const statusBadge = delivery => `<span class="delivery-status ${statusClass(delivery.status)}"><i data-lucide="${delivery.has_issue && !['verified', 'resolved'].includes(delivery.status) ? 'triangle-alert' : delivery.status === 'verified' ? 'badge-check' : delivery.status === 'draft' ? 'pencil' : 'clock-3'}"></i>${escape(delivery.status_label)}</span>`;
  const photoSummary = delivery => `${Number(delivery.asset_counts?.invoice || 0)} invoice · ${Number(delivery.asset_counts?.boxes || 0)} boxes${delivery.asset_counts?.damage ? ` · ${delivery.asset_counts.damage} damage` : ''}`;
  const debounce = (fn, wait = 250) => { let timeout; return (...args) => { clearTimeout(timeout); timeout = setTimeout(() => fn(...args), wait); }; };
  return {escape, formatDate, formatShortDate, formatBytes, statusClass, statusBadge, photoSummary, debounce};
})();

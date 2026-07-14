function getCookie(name) {
  return document.cookie.split(';').map(v => v.trim()).find(v => v.startsWith(name + '='))?.split('=')[1] || '';
}
window.csrfToken = decodeURIComponent(getCookie('csrftoken'));
window.apiFetch = async (url, options = {}) => {
  const headers = {Accept: 'application/json', ...(options.headers || {})};
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  if (!['GET', 'HEAD'].includes((options.method || 'GET').toUpperCase())) headers['X-CSRFToken'] = window.csrfToken;
  const response = await fetch(url, {...options, headers});
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try { const body = await response.json(); message = body.detail || Object.values(body)[0] || message; } catch (_) {}
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
};
window.showToast = (message, isError = false) => {
  const toast = document.getElementById('toast');
  toast.textContent = message; toast.classList.toggle('error', isError); toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 2800);
};
document.addEventListener('DOMContentLoaded', () => { if (window.lucide) lucide.createIcons(); });

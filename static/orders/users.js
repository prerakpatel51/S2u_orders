const userList = document.getElementById('user-list');
const userDialog = document.getElementById('user-dialog');
let users = [];
const escapeUserHtml = value => { const node = document.createElement('div'); node.textContent = value ?? ''; return node.innerHTML; };
const formatLogin = value => value ? new Date(value).toLocaleString() : 'Never';

function renderUsers() {
  userList.innerHTML = users.map(user => {
    const protectedUser = user.is_superuser, self = user.id === window.CURRENT_USER_ID;
    const name = [user.first_name, user.last_name].filter(Boolean).join(' ');
    return `<tr data-user="${user.id}"><td><strong>${escapeUserHtml(user.username)}</strong>${name ? `<small class="user-detail">${escapeUserHtml(name)}</small>` : ''}${user.email ? `<small class="user-detail">${escapeUserHtml(user.email)}</small>` : ''}</td><td><span class="status ${protectedUser ? 'running' : user.is_admin ? 'success' : ''}">${protectedUser ? 'Super user' : user.is_admin ? 'Admin' : 'User'}</span></td><td><span class="status ${user.is_active ? 'success' : 'error'}">${user.is_active ? 'Active' : 'Disabled'}</span></td><td>${escapeUserHtml(formatLogin(user.last_login))}</td><td><div class="user-actions">${protectedUser ? '<span class="protected-user"><i data-lucide="shield-check"></i>Protected</span>' : `<button class="secondary-button" data-admin="${user.is_admin ? '0' : '1'}" ${self ? 'disabled' : ''}>${user.is_admin ? 'Make user' : 'Make admin'}</button><button class="secondary-button" data-active="${user.is_active ? '0' : '1'}" ${self ? 'disabled' : ''}>${user.is_active ? 'Disable' : 'Enable'}</button><button class="icon-button danger-outline" data-delete title="Delete user" ${self ? 'disabled' : ''}><i data-lucide="trash-2"></i></button>`}</div></td></tr>`;
  }).join('') || '<tr><td colspan="5">No users found.</td></tr>';
  lucide.createIcons();
}
async function loadUsers() { users = await apiFetch('/api/users/'); renderUsers(); }
document.getElementById('new-user-button').addEventListener('click', () => userDialog.showModal());
userDialog.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => userDialog.close()));
document.getElementById('user-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  try {
    const user = await apiFetch('/api/users/', {method: 'POST', body: JSON.stringify({username: document.getElementById('user-username').value, first_name: document.getElementById('user-first-name').value, last_name: document.getElementById('user-last-name').value, email: document.getElementById('user-email').value, password: document.getElementById('user-password').value, is_admin: document.getElementById('user-is-admin').checked})});
    users.push(user); userDialog.close(); event.target.reset(); renderUsers(); showToast('User created');
  } catch (error) { showToast(error.message, true); } finally { button.disabled = false; }
});
userList.addEventListener('click', async event => {
  const row = event.target.closest('[data-user]'), button = event.target.closest('button'); if (!row || !button) return;
  const user = users.find(item => item.id === Number(row.dataset.user));
  try {
    if (button.dataset.admin !== undefined) { const updated = await apiFetch(`/api/users/${user.id}/`, {method: 'PATCH', body: JSON.stringify({is_admin: button.dataset.admin === '1'})}); Object.assign(user, updated); renderUsers(); showToast(updated.is_admin ? 'Administrator access granted' : 'Administrator access removed'); }
    else if (button.dataset.active !== undefined) { const updated = await apiFetch(`/api/users/${user.id}/`, {method: 'PATCH', body: JSON.stringify({is_active: button.dataset.active === '1'})}); Object.assign(user, updated); renderUsers(); showToast(updated.is_active ? 'User enabled' : 'User disabled'); }
    else if (button.hasAttribute('data-delete')) { if (!confirm(`Delete ${user.username}? This cannot be undone.`)) return; await apiFetch(`/api/users/${user.id}/`, {method: 'DELETE'}); users = users.filter(item => item.id !== user.id); renderUsers(); showToast('User deleted'); }
  } catch (error) { showToast(error.message, true); }
});
loadUsers().catch(error => { userList.innerHTML = `<tr><td colspan="5" class="form-error">${escapeUserHtml(error.message)}</td></tr>`; });

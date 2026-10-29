'use strict';
let csrf = '';
const message = text => { document.getElementById('message').textContent = text; };
async function request(path, method = 'GET', data) {
  const options = {method, credentials: 'same-origin', headers: {}};
  if (method !== 'GET') {
    options.headers = {'Content-Type': 'application/json', 'X-CSRF-Token': csrf};
    options.body = JSON.stringify(data || {});
  }
  const response = await fetch(path, options);
  const payload = await response.json();
  if (response.status === 401 && path !== '/api/login') {
    location.assign('/login');
    throw new Error('Sign in required.');
  }
  if (!response.ok) throw new Error(payload.error || 'Request failed.');
  return payload;
}
function cell(row, value, className) {
  const td = document.createElement('td');
  td.textContent = value;
  if (className) td.className = className;
  row.append(td);
  return td;
}
function bytes(number) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let unit = 0;
  while (number >= 1024 && unit < units.length - 1) { number /= 1024; unit++; }
  return `${number.toFixed(2)} ${units[unit]}`;
}
async function loadClients() {
  const data = await request('/api/clients');
  const body = document.getElementById('clients');
  body.replaceChildren();
  for (const client of data.clients) {
    const row = document.createElement('tr');
    cell(row, client.name);
    const downloads = cell(row, client.profiles.length ? '' : 'No generated profiles');
    for (const profile of client.profiles) {
      const item = document.createElement('div');
      item.className = 'profile';
      const link = document.createElement('a');
      link.className = 'download';
      link.textContent = `Download ${profile.variant}`;
      link.href = '/api/download?file=' + encodeURIComponent(profile.file);
      link.download = profile.file;
      const filename = document.createElement('small');
      filename.className = 'profile-file';
      filename.textContent = profile.file;
      item.append(link, filename);
      downloads.append(item);
    }
    const actions = cell(row, '');
    const revoke = document.createElement('button');
    revoke.className = 'danger';
    revoke.textContent = 'Revoke & delete';
    revoke.addEventListener('click', async () => {
      if (!window.confirm(`Revoke ${client.name} and delete all its generated profiles? Both default and routed profiles will stop working for new connections. Existing sessions may remain connected until disconnected. This cannot be undone.`)) return;
      revoke.disabled = true;
      message(`Revoking ${client.name}…`);
      try {
        const result = await request('/api/clients/revoke', 'POST', {name: client.name});
        await loadClients();
        message(`Revoked ${client.name} and deleted ${result.deleted_profiles} profiles. Existing sessions may remain connected until disconnected.`);
      } catch (error) { message(error.message); revoke.disabled = false; }
    });
    actions.append(revoke);
    body.append(row);
  }
  if (!data.clients.length) { const row = body.insertRow(); cell(row, 'No clients yet. Create one above.').colSpan = 3; }
}
async function loadUsage() {
  const data = await request('/api/usage');
  const body = document.getElementById('usage');
  body.replaceChildren();
  let online = 0, download = 0, upload = 0;
  for (const client of data.clients) {
    online += Number(client.online); download += client.download; upload += client.upload;
    const row = body.insertRow();
    cell(row, client.name); cell(row, client.online ? 'Online' : 'Offline', client.online ? 'online' : 'muted');
    cell(row, bytes(client.download)); cell(row, bytes(client.upload));
    cell(row, client.real || '—'); cell(row, client.virtual || '—'); cell(row, client.since || '—');
  }
  if (!data.clients.length) cell(body.insertRow(), 'Waiting for client traffic…').colSpan = 7;
  document.getElementById('online').textContent = online;
  document.getElementById('download').textContent = bytes(download);
  document.getElementById('upload').textContent = bytes(upload);
  document.getElementById('updated').textContent = data.sampled_at ? 'Last sampled: ' + new Date(data.sampled_at * 1000).toLocaleString() : 'Waiting for the first sample';
  message(data.warning || '');
}
async function start() {
  const session = await request('/api/session');
  csrf = session.csrf;
  const login = document.getElementById('login-form');
  if (login) {
    login.addEventListener('submit', async event => {
      event.preventDefault();
      const button = login.querySelector('button'); button.disabled = true; message('Signing in…');
      try {
        await request('/api/login', 'POST', {username: login.elements.username.value, password: login.elements.password.value});
        login.elements.password.value = ''; location.assign('/clients');
      } catch (error) { message(error.message); } finally { button.disabled = false; }
    });
    return;
  }
  if (!session.authenticated) { location.assign('/login'); return; }
  document.getElementById('logout').addEventListener('click', async () => {
    try { await request('/api/logout', 'POST'); location.assign('/login'); } catch (error) { message(error.message); }
  });
  const usage = location.pathname === '/usage';
  document.getElementById(usage ? 'usage-page' : 'clients-page').hidden = false;
  document.querySelector(`nav a[href="${usage ? '/usage' : '/clients'}"]`).setAttribute('aria-current', 'page');
  if (usage) {
    await loadUsage();
    // Refresh sequentially so a slow server never accumulates overlapping requests.
    const refresh = async () => { try { await loadUsage(); } catch (error) { message(error.message); } finally { setTimeout(refresh, 5000); } };
    setTimeout(refresh, 5000);
  } else {
    await loadClients();
    const form = document.getElementById('create-form');
    form.addEventListener('submit', async event => {
      event.preventDefault(); const button = form.querySelector('button'); button.disabled = true; message('Creating client…');
      try {
        const client = await request('/api/clients', 'POST', {name: form.elements.name.value, days: Number(form.elements.days.value)});
        await loadClients(); form.elements.name.value = ''; message(`Created ${client.name}. Both profiles are ready to download.`);
      } catch (error) { message(error.message); } finally { button.disabled = false; }
    });
  }
}
start().catch(error => message(error.message));

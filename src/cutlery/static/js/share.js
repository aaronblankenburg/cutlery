// Share page: links that let a friend review the project (watch the cuts, comment, look through the footage and make
// suggestions there) without the instance's login. The link is shown once; it can be withdrawn at any time.
import { load, t, lang } from './i18n.js';

const $ = (id) => document.getElementById(id);
const project = $('share-form').dataset.project;
await load();

async function api(path, options = {}) {
  const res = await fetch(path, { credentials: 'same-origin', headers: { 'content-type': 'application/json' }, ...options });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}
const when = (iso) => (iso ? new Intl.DateTimeFormat(lang, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(iso)) : '–');

async function list() {
  const { shares } = await api(`/api/projects/${project}/shares`);
  const body = $('share-table').tBodies[0];
  body.textContent = '';
  $('share-table').hidden = !shares.length;
  $('share-none').hidden = shares.length > 0;
  for (const s of shares.slice().reverse()) {
    const r = body.insertRow();
    const state = s.revoked ? t('share.state.revoked') : s.active ? t('share.state.active') : t('share.state.expired');
    for (const v of [s.name, when(s.created), s.expires ? when(s.expires) : t('share.never'), when(s.last_used), state]) r.insertCell().textContent = v;
    const cell = r.insertCell();
    if (s.active) {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'button danger small-button'; b.textContent = t('share.revoke');
      b.addEventListener('click', async () => {
        if (!confirm(t('share.revoke_confirm', { name: s.name }))) return;
        try { await api(`/api/projects/${project}/shares/${s.id}`, { method: 'DELETE' }); await list(); } catch (e) { alert(e.message); }
      });
      cell.append(b);
    }
  }
}

$('share-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  $('share-error').hidden = true;
  try {
    const s = await api(`/api/projects/${project}/shares`, { method: 'POST',
      body: JSON.stringify({ name: $('share-name').value, days: Number($('share-days').value) }) });
    $('share-made').hidden = false;
    $('share-made-title').textContent = t('share.made', { name: s.name });
    $('share-url').value = s.url;
    $('share-url').select();
    $('share-name').value = '';
    await list();
  } catch (err) {
    $('share-error').hidden = false;
    $('share-error').textContent = err.message;
  }
});
$('share-copy').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('share-url').value); } catch { $('share-url').select(); document.execCommand('copy'); }
  $('share-copy').textContent = t('share.copied');
  setTimeout(() => { $('share-copy').textContent = t('share.copy'); }, 1500);
});

await list();

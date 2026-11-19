// Share page: links that let a friend review the project (watch the cuts, comment, look through the footage and make
// suggestions there) without the instance's login. An active link can be shown and passed on again, renewed (a new
// one in its place) and withdrawn at any time.
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
// the link box under the form: for a new link, and for one shown again from the list
function showLink(name, url, again = false) {
  $('share-made').hidden = false;
  $('share-made-title').textContent = t(again ? 'share.again' : 'share.made', { name });
  $('share-url').value = url;
  $('share-send').hidden = !navigator.share;
  $('share-send').dataset.name = name;
  $('share-made').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  $('share-url').select();
}
const button = (label, cls, onClick) => {
  const b = document.createElement('button');
  b.type = 'button'; b.className = `button small-button ${cls}`; b.textContent = label;
  b.addEventListener('click', () => onClick().catch((e) => alert(e.message)));
  return b;
};

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
    cell.className = 'share-actions';
    if (s.active) {
      if (s.derived) {
        cell.append(button(t('share.show'), '', async () => {
          const { url } = await api(`/api/projects/${project}/shares/${s.id}/link`);
          showLink(s.name, url, true);
        }));
      } else {
        cell.append(button(t('share.renew'), '', async () => {
          if (!confirm(t('share.renew_confirm', { name: s.name }))) return;
          const n = await api(`/api/projects/${project}/shares/${s.id}/link`, { method: 'POST' });
          showLink(n.name, n.url);
          await list();
        }));
      }
      cell.append(button(t('share.revoke'), 'danger', async () => {
        if (!confirm(t('share.revoke_confirm', { name: s.name }))) return;
        await api(`/api/projects/${project}/shares/${s.id}`, { method: 'DELETE' });
        $('share-made').hidden = true;
        await list();
      }));
    }
  }
}

$('share-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  $('share-error').hidden = true;
  try {
    const s = await api(`/api/projects/${project}/shares`, { method: 'POST',
      body: JSON.stringify({ name: $('share-name').value, days: Number($('share-days').value) }) });
    showLink(s.name, s.url);
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
$('share-send').addEventListener('click', async () => {
  try {
    await navigator.share({ title: 'Cutlery', text: t('share.send_text', { name: $('share-send').dataset.name }), url: $('share-url').value });
  } catch { /* cancelled */ }
});

await list();

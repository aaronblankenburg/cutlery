// Settings: enter or remove the Claude subscription token (sent once to the server and never shown again), and the
// user's preferences for all films (given to every job).
import { load, t } from './i18n.js';

await load();
const $ = (id) => document.getElementById(id);
const form = $('token-form');

async function send(method, body) {
  const res = await fetch('/api/claude/token', {
    method, credentials: 'same-origin',
    headers: body ? { 'content-type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
}

form?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const token = $('token').value.trim();
  $('token').value = '';                       // never keep it in the page
  try {
    await send('PUT', { token });
    $('token-status').textContent = t('settings.claude.saved');
    setTimeout(() => location.reload(), 800);
  } catch (err) {
    $('token-status').textContent = t('settings.claude.error', { message: err.message });
  }
});

$('token-remove')?.addEventListener('click', async () => {
  if (!confirm(t('settings.claude.remove_confirm'))) return;
  try {
    await send('DELETE');
    location.reload();
  } catch (err) {
    $('token-status').textContent = t('settings.claude.error', { message: err.message });
  }
});

// ------------------------------------------------------------------ preferences

const prefs = $('preferences');
fetch('/api/preferences', { credentials: 'same-origin' }).then((r) => r.json()).then((d) => { prefs.value = d.text || ''; })
  .catch(() => {});
$('preferences-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    const res = await fetch('/api/preferences', { method: 'PUT', credentials: 'same-origin',
      headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text: prefs.value }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText);
    $('preferences-status').textContent = t('settings.preferences.saved');
  } catch (err) {
    $('preferences-status').textContent = t('settings.claude.error', { message: err.message });
  }
});

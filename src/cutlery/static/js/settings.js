// Settings: enter or remove the Claude subscription token. It is sent once to the server and never shown again.
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

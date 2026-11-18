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

// ------------------------------------------------------------------ Telegram

async function telegram(method = 'GET', body) {
  const res = await fetch('/api/telegram', { method, credentials: 'same-origin',
    headers: body ? { 'content-type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function showTelegram(st, note = '') {
  $('telegram-token-form').hidden = st.configured;
  $('telegram-actions').hidden = !st.configured;
  $('telegram-connect').hidden = st.linked;
  $('telegram-test').hidden = !st.linked;
  $('telegram-unlink').hidden = !st.linked;
  $('telegram-remove').hidden = st.from_env;
  if (st.linked) $('telegram-link').hidden = true;
  $('telegram-state').textContent = note || (!st.configured ? t('telegram.state.no_bot')
    : st.linked ? t('telegram.state.linked', { bot: st.bot || '', user: st.user || '' })
      : t('telegram.state.bot', { bot: st.bot || '' }));
}

let linkPoll = null;
const telegramError = (err) => { $('telegram-state').textContent = t('settings.claude.error', { message: err.message }); };
telegram().then((st) => showTelegram(st)).catch(() => {});
$('telegram-token-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const token = $('telegram-token').value.trim();
  $('telegram-token').value = '';
  try { showTelegram(await telegram('POST', { action: 'token', token })); } catch (err) { telegramError(err); }
});
$('telegram-connect').addEventListener('click', async () => {
  try {
    const st = await telegram('POST', { action: 'link' });
    const a = $('telegram-link');
    a.href = st.link;
    a.hidden = false;
    $('telegram-connect').hidden = true;
    showTelegram({ ...st, linked: false }, t('telegram.state.waiting'));
    $('telegram-connect').hidden = true;
    window.open(st.link, '_blank', 'noopener');
    clearInterval(linkPoll);
    linkPoll = setInterval(async () => {
      const now = await telegram().catch(() => null);
      if (now?.linked) { clearInterval(linkPoll); showTelegram(now); }
    }, 3000);
  } catch (err) { telegramError(err); }
});
$('telegram-test').addEventListener('click', async () => {
  try { await telegram('POST', { action: 'test' }); $('telegram-state').textContent = t('telegram.state.test_sent'); }
  catch (err) { telegramError(err); }
});
$('telegram-unlink').addEventListener('click', async () => {
  try { showTelegram(await telegram('POST', { action: 'unlink' })); } catch (err) { telegramError(err); }
});
$('telegram-remove').addEventListener('click', async () => {
  if (!confirm(t('telegram.remove_confirm'))) return;
  try { showTelegram(await telegram('DELETE')); } catch (err) { telegramError(err); }
});

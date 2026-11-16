// Briefing and Claude Code jobs on the project page: save the briefing, start a draft, follow the job live
// (server-sent events), resume a paused job, cancel.
import { load, t } from './i18n.js';

const lang = document.documentElement.lang || 'en';
const $ = (id) => document.getElementById(id);
const project = $('jobs').dataset.project;
let source = null, shown = 0, current = null;

await load();

async function api(path, options = {}) {
  const res = await fetch(path, { credentials: 'same-origin', ...options });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(body.message || body.error || res.statusText), { status: res.status });
  return body;
}

// ------------------------------------------------------------------ briefing

const form = $('briefing');
const fields = ['prompt', 'purpose', 'length', 'style', 'music', 'nogos'];
const saved = await api(`/api/projects/${project}/briefing`).catch(() => ({}));
for (const f of fields) if (saved[f] && form.elements[f]) form.elements[f].value = saved[f];
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(fields.map((f) => [f, form.elements[f].value]));
  try {
    await api(`/api/projects/${project}/briefing`, { method: 'PUT', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(data) });
    $('briefing-status').textContent = t('briefing.saved');
  } catch (err) {
    $('briefing-status').textContent = err.message;
  }
});

// ------------------------------------------------------------------ job

const time = (iso) => iso ? new Date(iso).toLocaleTimeString(lang, { hour: '2-digit', minute: '2-digit' }) : '';

function logLine(ev) {
  const li = document.createElement('li');
  li.className = `k-${ev.kind}`;
  const tm = document.createElement('time');
  tm.textContent = time(ev.t);
  const tag = document.createElement('span');
  tag.className = 'tag';
  tag.textContent = ev.kind === 'tool' && ev.tool ? ev.tool : t(`jobs.kind.${ev.kind}`) !== `jobs.kind.${ev.kind}` ? t(`jobs.kind.${ev.kind}`) : t(`jobs.state.${ev.kind}`);
  const msg = document.createElement('span');
  msg.className = 'msg';
  msg.textContent = ev.text || '';
  li.append(tm, tag, msg);
  $('job-log').append(li);
  $('job-log').scrollTop = $('job-log').scrollHeight;
}

function showJob(j) {
  current = j;
  $('job').hidden = false;
  $('job-heading').textContent = t('jobs.heading', { cut: j.cut, state: t(`jobs.state.${j.state}`) });
  $('job-state').textContent = t(`jobs.state.${j.state}`);
  $('job-state').className = `pill ${{ done: 'ok', running: 'warn', queued: 'warn', paused: 'error', failed: 'error' }[j.state] || ''}`;
  let meta = t('jobs.meta', { model: j.model, attempt: j.attempt });
  if (j.turns) meta += ' · ' + t('jobs.cost', { turns: j.turns, cost: j.cost_usd != null ? `$${j.cost_usd.toFixed(2)}` : '–' });
  $('job-meta').textContent = meta;
  const paused = $('job-paused');
  paused.hidden = !['paused', 'failed'].includes(j.state);
  if (j.state === 'paused') {
    let text = t(`jobs.paused.${j.reason}`);
    if (text === `jobs.paused.${j.reason}`) text = t('jobs.failed', { message: j.message || j.reason });
    if (j.reason === 'usage_limit' && j.resume_after) text += ' ' + t('jobs.paused.usage_limit_until', { time: time(j.resume_after) });
    paused.textContent = text;
    paused.className = 'notice';
  } else if (j.state === 'failed') {
    paused.textContent = t('jobs.failed', { message: j.message || j.reason });
    paused.className = 'notice error';
  }
  $('job-resume').hidden = !['paused', 'failed'].includes(j.state);
  $('job-cancel').hidden = !['queued', 'running', 'paused'].includes(j.state);
  $('job-raw').href = `/api/jobs/${j.id}/raw`;
  $('job-draft').hidden = true;
  $('job-note').hidden = true;
}

function follow(j) {
  source?.close();
  showJob(j);
  source = new EventSource(`/api/jobs/${j.id}/events?after=${shown}`);
  source.addEventListener('log', (e) => { const ev = JSON.parse(e.data); shown = ev.n; logLine(ev); });
  source.addEventListener('job', (e) => {
    const cur = JSON.parse(e.data);
    showJob(cur);
    if (!['queued', 'running'].includes(cur.state)) { source.close(); source = null; }
  });
  source.onerror = () => { source?.close(); source = null; setTimeout(() => refresh(), 5000); };
}

async function refresh() {
  const { jobs } = await api(`/api/projects/${project}/jobs`);
  if (!jobs.length) return;
  const j = jobs[0];
  if (current?.id !== j.id) { $('job-log').replaceChildren(); shown = 0; }
  follow(j);
}

function showError(err) {
  $('job-error').hidden = !err;
  $('job-error').textContent = err ? err.message : '';
}

$('job-draft').addEventListener('click', async () => {
  showError(null);
  try { follow(await api(`/api/projects/${project}/jobs`, { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ kind: 'draft' }) })); } catch (err) { showError(err); }
});
$('job-resume').addEventListener('click', async () => {
  showError(null);
  try { follow(await api(`/api/jobs/${current.id}/resume`, { method: 'POST' })); } catch (err) { showError(err); }
});
$('job-cancel').addEventListener('click', async () => {
  try { showJob(await api(`/api/jobs/${current.id}/cancel`, { method: 'POST' })); } catch (err) { showError(err); }
});

const claude = await api('/api/claude').catch(() => null);
if (claude?.warn) {
  $('claude-warning').hidden = false;
  $('claude-warning').textContent = t('claude.token_warning', { days: claude.days_left, date: claude.token_expires });
}
await refresh();
window.cutleryJobs = { refresh, get current() { return current; } };

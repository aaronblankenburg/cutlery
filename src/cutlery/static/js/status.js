// Status page: the job of the project, live (server-sent events), pause and resume, cancel; the people's names.
import { load, t } from './i18n.js';

// "cut3" -> "Cut 3" / "Fassung 3"
const cutName = (id) => t('cuts.name', { n: (String(id).match(/\d+/g) || [id]).pop() });
import { duration as fmtDuration } from './format.js';

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

const time = (iso) => iso ? new Date(iso).toLocaleTimeString(lang, { hour: '2-digit', minute: '2-digit' }) : '';
const has = (key) => t(key) !== key;

// What a log line is, in the words of the design: Reads, Views, Plans, Command, Writes, Assumption …
function label(ev) {
  if (ev.kind === 'tool') {
    if (ev.tool === 'Read' && /\.(jpe?g|png|webp)$/i.test(ev.text || '')) return ['tool', t('jobs.tool.view')];
    if (ev.tool === 'Write' && /decisions\.json$/.test(ev.text || '')) return ['decision', t('jobs.tool.decisions')];
    if (has(`jobs.tool.${ev.tool}`)) return ['tool', t(`jobs.tool.${ev.tool}`)];
    return ['tool', ev.tool || t('jobs.kind.tool')];
  }
  if (ev.kind === 'text') return ['think', t('jobs.kind.text')];
  if (ev.kind === 'think') return ['think', t('jobs.kind.think')];
  if (ev.kind === 'egress') return ['decision', t('jobs.kind.egress')];
  if (ev.kind === 'egress_allowed') return ['ok', t('jobs.kind.egress_allowed')];
  if (['done', 'result'].includes(ev.kind)) return ['ok', t(`jobs.kind.${ev.kind === 'done' ? 'done' : 'result'}`)];
  if (['paused', 'failed', 'error'].includes(ev.kind)) return ['bad', t(`jobs.kind.${ev.kind}`)];
  return ['tool', has(`jobs.kind.${ev.kind}`) ? t(`jobs.kind.${ev.kind}`) : ev.kind];
}

// Lines Cutlery writes itself carry their facts, so they are said in the UI language here.
function text(ev) {
  if (ev.kind === 'queued') return t('jobs.log.queued');
  if (ev.kind === 'waiting') return t(`jobs.log.waiting_${ev.reason}`);
  if (ev.kind === 'start' && !ev.session_id) {
    const s = t('jobs.log.attempt', { attempt: (ev.text || '').match(/\d+/)?.[0] || 1 });
    return ev.reachable?.length ? `${s} ${t('jobs.log.reachable', { domains: ev.reachable.join(', ') })}` : s;
  }
  if (ev.kind === 'egress') return t('jobs.log.egress', { domain: ev.domain });
  if (ev.kind === 'egress_allowed') return t('jobs.log.egress_allowed', { domain: ev.domain });
  if (ev.kind === 'start') return t('jobs.log.session', { model: ev.model || '' });
  if (ev.kind === 'cancelled') return t('jobs.state.cancelled');
  return ev.text || '';
}

function logLine(ev) {
  const row = document.createElement('div');
  const [cls, name] = label(ev);
  row.className = `log-row k-${cls}`;
  const tm = document.createElement('time');
  tm.textContent = time(ev.t);
  const tag = document.createElement('span');
  tag.className = 'tag';
  tag.textContent = name;
  const msg = document.createElement('span');
  msg.className = 'msg';
  msg.textContent = text(ev);
  row.append(tm, tag, msg);
  const log = $('job-log');
  const atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
  log.append(row);
  if (atEnd) log.scrollTop = log.scrollHeight;
}

function showJob(j) {
  current = j;
  const active = ['queued', 'running'].includes(j.state);
  $('job-heading').textContent = t(`jobs.title.${j.state}`, { cut: cutName(j.cut) });
  let meta = `Claude Code · ${j.model}`;
  if (j.started) meta += ' · ' + t('jobs.started', { time: time(j.started) });
  if (j.state === 'running' && j.started) meta += ' · ' + t('jobs.running_for', { time: fmtDuration((Date.now() - Date.parse(j.started)) / 1000) });
  if (j.attempt > 1) meta += ' · ' + t('jobs.attempt', { n: j.attempt });
  if (j.turns) meta += ' · ' + t('jobs.cost', { turns: j.turns, cost: j.cost_usd != null ? `$${j.cost_usd.toFixed(2)}` : '–' });
  $('job-meta').textContent = meta;
  $('job-note-box').hidden = !j.note;
  $('job-note').textContent = j.note || '';
  const banner = $('pause-banner');
  banner.hidden = !['paused', 'failed'].includes(j.state);
  if (j.state === 'paused') {
    let s = has(`jobs.paused.${j.reason}`) ? t(`jobs.paused.${j.reason}`) : t('jobs.failed', { message: j.message || j.reason });
    if (j.reason === 'usage_limit' && j.resume_after) s += ' ' + t('jobs.paused.usage_limit_until', { time: time(j.resume_after) });
    $('pause-text').textContent = s;
    banner.className = 'banner-card warn';
  } else if (j.state === 'failed') {
    $('pause-text').textContent = t('jobs.failed', { message: j.message || j.reason });
    banner.className = 'banner-card bad';
  }
  $('job-cancel').hidden = !(active || j.state === 'paused');
  $('job-raw-box').hidden = j.state === 'queued';
  $('job-raw').href = `/api/jobs/${j.id}/raw`;
}

function follow(j) {
  source?.close();
  showJob(j);
  source = new EventSource(`/api/jobs/${j.id}/events?after=${shown}`);
  source.addEventListener('log', (e) => {
    const ev = JSON.parse(e.data); shown = ev.n; logLine(ev);
    if (ev.kind === 'egress' || ev.kind === 'egress_allowed') loadEgress().catch(() => {});
  });
  source.addEventListener('job', (e) => {
    const cur = JSON.parse(e.data);
    const was = current?.state;
    showJob(cur);
    if (!['queued', 'running'].includes(cur.state)) {
      source.close();
      source = null;
      if (was && was !== cur.state && cur.state === 'done') setTimeout(() => location.reload(), 1500);
    }
  });
  source.onerror = () => { source?.close(); source = null; setTimeout(refresh, 5000); };
}

async function refresh() {
  const { jobs } = await api(`/api/projects/${project}/jobs`);
  if (!jobs.length) return;
  if (current?.id !== jobs[0].id) { $('job-log').replaceChildren(); shown = 0; }
  follow(jobs[0]);
}

function showError(err) {
  $('job-error').hidden = !err;
  $('job-error').textContent = err ? err.message : '';
}

$('job-resume').addEventListener('click', async () => {
  showError(null);
  try { follow(await api(`/api/jobs/${current.id}/resume`, { method: 'POST' })); } catch (err) { showError(err); }
});
$('job-cancel').addEventListener('click', async () => {
  if (!confirm(t('jobs.cancel_confirm'))) return;
  try { showJob(await api(`/api/jobs/${current.id}/cancel`, { method: 'POST' })); } catch (err) { showError(err); }
});

// ------------------------------------------------------------------ people

for (const input of document.querySelectorAll('[data-person]')) {
  input.addEventListener('change', async () => {
    try {
      await api(`/api/projects/${project}/people/${input.dataset.person}`, { method: 'PUT',
        headers: { 'content-type': 'application/json' }, body: JSON.stringify({ name: input.value }) });
      $('people-status').textContent = t('people.saved', { id: input.dataset.person });
    } catch (err) {
      $('people-status').textContent = err.message;
    }
  });
}

// ------------------------------------------------------------------ internet access: Claude's questions

async function loadEgress() {
  const d = await api(`/api/projects/${project}/egress`);
  const asks = d.requests.filter((r) => r.state === 'asked');
  $('egress-asks').replaceChildren(...asks.map((r) => {
    const box = document.createElement('div');
    box.className = 'banner-card info';
    const text = document.createElement('span');
    text.textContent = t('egress.ask', { domain: r.domain });
    const yes = document.createElement('button');
    yes.className = 'button primary';
    yes.type = 'button';
    yes.textContent = t('egress.allow');
    const no = document.createElement('button');
    no.className = 'button';
    no.type = 'button';
    no.textContent = t('egress.refuse');
    const answer = (allow) => api(`/api/projects/${project}/egress`, { method: 'POST',
      headers: { 'content-type': 'application/json' }, body: JSON.stringify({ domain: r.domain, allow }) })
      .then(loadEgress).catch(showError);
    yes.addEventListener('click', () => answer(true));
    no.addEventListener('click', () => answer(false));
    box.append(text, yes, no);
    return box;
  }));
  const items = [
    ...d.allowed.map((a) => [a.domain, t(`egress.source.${a.source}`)]),
    ...d.requests.filter((r) => r.state === 'refused').map((r) => [r.domain, t('egress.source.refused')]),
  ];
  $('egress-list').replaceChildren(...(items.length ? items : [[t('egress.none'), '']]).map(([dom, src]) => {
    const li = document.createElement('li');
    li.textContent = src ? `${dom} · ${src}` : dom;
    return li;
  }));
}
loadEgress().catch(() => {});
setInterval(() => loadEgress().catch(() => {}), 10000);

// ------------------------------------------------------------------ project memory (CLAUDE.md)

async function loadMemory() {
  const { text } = await api(`/api/projects/${project}/memory`);
  $('memory-text').textContent = text || t('memory.empty');
  return text;
}
$('memory-edit').addEventListener('click', async () => {
  $('memory-input').value = await loadMemory();
  $('memory-form').hidden = false;
  $('memory-box').hidden = true;
  $('memory-input').focus();
});
$('memory-cancel').addEventListener('click', () => { $('memory-form').hidden = true; $('memory-box').hidden = false; });
$('memory-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    await api(`/api/projects/${project}/memory`, { method: 'PUT', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ text: $('memory-input').value }) });
    $('memory-form').hidden = true;
    $('memory-box').hidden = false;
    $('memory-status').textContent = t('memory.saved');
    await loadMemory();
  } catch (err) {
    $('memory-status').textContent = err.message;
  }
});
$('memory-box').addEventListener('toggle', () => { if ($('memory-box').open) loadMemory().catch(() => {}); });

await refresh();
setInterval(() => { if (current?.state === 'running') showJob(current); }, 30000);
window.cutleryJobs = { refresh, get current() { return current; } };

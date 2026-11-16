// Review page: watch a cut, comment on moments, read the agent's changes and replies, finish the review.
// Talks to cutter's review API (/projects/<id>/review/api/…), which Cutlery serves behind its login.
import { load, t } from './i18n.js';

const $ = (id) => document.getElementById(id);
const root = $('review');
const project = root.dataset.project;
const base = `/projects/${project}/review`;
const lang = document.documentElement.lang || 'en';
const video = $('video');
let cuts = [], cut = null, tl = null, entries = [], rev = '', previousDone = 0;

await load();

async function api(path, options = {}) {
  const res = await fetch(base + '/api/' + path, { credentials: 'same-origin', ...options });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(body.message || body.error || res.statusText), { status: res.status, body });
  return body;
}

const ts = (s, dec = 1) => {
  s = Math.max(0, s || 0);
  const m = Math.floor(s / 60), r = s - m * 60;
  return `${String(m).padStart(2, '0')}:${r.toFixed(dec).padStart(dec ? 3 + dec : 2, '0')}`;
};
const shotAt = (x) => tl?.shots?.find((s) => x >= s.start && x < s.end);
const sectionAt = (x) => tl?.sections?.find((s) => x >= s.start && x < s.end);
const cutLabel = (c) => {
  const n = (c.id.match(/\d+/) || [c.id])[0];
  const d = c.created ? new Date(c.created) : null;
  return t('review.cut_label', { n, date: d ? d.toLocaleDateString(lang, { day: '2-digit', month: '2-digit' }) : '',
    time: d ? d.toLocaleTimeString(lang, { hour: '2-digit', minute: '2-digit' }) : '' });
};

// ------------------------------------------------------------------ cuts

async function loadCuts() {
  const d = await api('cuts');
  cuts = d.cuts;
  const want = new URLSearchParams(location.search).get('cut');
  const sel = $('cut');
  sel.replaceChildren(...[...cuts].reverse().map((c) => {
    const o = document.createElement('option');
    o.value = c.id;
    o.textContent = cutLabel(c);
    return o;
  }));
  await selectCut(cuts.find((c) => c.id === want) ? want : cuts[cuts.length - 1].id);
}

async function selectCut(id) {
  cut = cuts.find((c) => c.id === id);
  $('cut').value = id;
  const url = new URL(location.href);
  url.searchParams.set('cut', id);
  history.replaceState(null, '', url);
  tl = await (await fetch(`${base}/media/${id}/timeline.json`, { credentials: 'same-origin' })).json();
  video.src = cut.video ? `${base}/media/${id}/review.mp4` : '';
  if (tl.poster) video.poster = `${base}/media/${id}/${tl.poster}`;
  const idx = cuts.indexOf(cut);
  const prev = idx > 0 ? cuts[idx - 1] : null;
  $('legend-change').textContent = prev ? t('review.legend.change', { cut: (prev.id.match(/\d+/) || [prev.id])[0] }) : t('review.legend.decision');
  previousDone = 0;
  if (prev) {
    const pe = (await api(`entries?cut=${prev.id}`)).entries || [];
    previousDone = pe.filter((e) => e.status === 'done').length;
  }
  const newest = cut === cuts[cuts.length - 1];
  const done = $('review-done');
  done.hidden = !newest || cut.locked;
  done.textContent = t('review.done', { n: Number((cut.id.match(/\d+/) || [0])[0]) + 1 });
  $('review-locked').hidden = !cut.locked;
  $('new').disabled = Boolean(cut.locked);
  rev = '';
  sections();
  await loadEntries();
}
$('cut').addEventListener('change', (e) => selectCut(e.target.value));

// ------------------------------------------------------------------ comments

async function loadEntries() {
  const d = await api(`entries?cut=${cut.id}&rev=${rev}`);
  if (d.unchanged) return;
  rev = d.rev;
  entries = d.entries;
  render();
}

function render() {
  marks();
  const open = entries.filter((e) => e.status === 'open').length;
  $('counts').textContent = t('review.counts', { open, done: previousDone });
  const decisions = (tl.decisions?.entries || []).filter((d) => d.t != null);
  const items = [...entries.map((e) => ({ kind: 'comment', t: e.t, e })), ...decisions.map((d) => ({ kind: 'decision', t: d.t, d }))]
    .sort((a, b) => a.t - b.t);
  $('comments').replaceChildren(...items.map((it) => (it.kind === 'comment' ? commentCard(it.e) : decisionCard(it.d))));
}

function el(tag, cls, text) {
  const x = document.createElement(tag);
  if (cls) x.className = cls;
  if (text != null) x.textContent = text;
  return x;
}

function seekButton(t0) {
  const b = el('button', 'tc mono', ts(t0, 0));
  b.type = 'button';
  b.addEventListener('click', () => { video.currentTime = t0; video.focus(); });
  return b;
}

function commentCard(e) {
  const a = el('article', `comment s-${e.status}`);
  const head = el('div', 'head');
  head.append(seekButton(e.t), el('span', 'where small muted', e.section || ''),
    el('span', `pill ${{ open: 'warn', done: 'ok', rejected: '' }[e.status] || ''}`, t(`review.status.${e.status}`)));
  a.append(head);
  if (e.reaction) a.append(el('span', 'reaction', e.reaction));
  if (e.text) a.append(el('p', '', e.text));
  if (e.reply) {
    const r = el('div', 'reply');
    r.append(el('strong', '', t('review.agent') + ' '), document.createTextNode(e.reply));
    a.append(r);
  }
  if (!cut.locked && e.status === 'open') {
    const del = el('button', 'link-button small', t('review.delete'));
    del.type = 'button';
    del.addEventListener('click', async () => {
      if (!confirm(t('review.delete_confirm'))) return;
      try { await api(`entries/${e.id}`, { method: 'DELETE' }); rev = ''; await loadEntries(); } catch (err) { showError(err); }
    });
    a.append(del);
  }
  return a;
}

function decisionCard(d) {
  const a = el('article', `comment decision ${d.kind === 'assumption' ? 'assumption' : ''}`);
  const head = el('div', 'head');
  head.append(seekButton(d.t), el('span', 'where small muted', sectionAt(d.t)?.name || ''),
    el('span', 'pill info', t(d.kind === 'assumption' ? 'review.assumption' : 'review.change')));
  a.append(head, el('p', '', d.title));
  if (d.text) a.append(el('p', 'small', d.text));
  if (d.why) a.append(el('p', 'small muted', t('review.why', { why: d.why })));
  return a;
}

$('new-comment').addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = $('new').value.trim();
  if (!text) return;
  try {
    await api('entries', { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ cut: cut.id, t: Math.min(video.currentTime || 0, tl.duration), text }) });
    $('new').value = '';
    rev = '';
    await loadEntries();
  } catch (err) { showError(err); }
});

function showError(err) {
  $('review-error').hidden = !err;
  $('review-error').textContent = err ? (err.status === 409 ? t('review.locked') : err.message) : '';
}

// ------------------------------------------------------------------ timeline

function sections() {
  const bar = $('sections');
  const head = $('playhead');
  bar.replaceChildren(...(tl.sections || []).map((s, i) => {
    const d = el('button', `section c${i % 5}`, s.name);
    d.type = 'button';
    d.style.flex = String(Math.max(0.0001, s.end - s.start));
    d.title = `${s.name} · ${ts(s.start, 0)}`;
    d.addEventListener('click', () => { video.currentTime = s.start; });
    return d;
  }), head);
}

function marks() {
  const box = $('marks');
  const dur = tl.duration || 1;
  const pos = (x) => `${Math.min(100, (x / dur) * 100)}%`;
  const dots = entries.map((e) => {
    const d = el('button', `mark ${e.status === 'done' ? 'done' : e.status === 'rejected' ? 'rejected' : 'open'}`);
    d.type = 'button';
    d.style.left = pos(e.t);
    d.title = `${ts(e.t, 0)} ${t(`review.status.${e.status}`)}`;
    d.setAttribute('aria-label', d.title);
    d.addEventListener('click', () => { video.currentTime = e.t; });
    return d;
  });
  const diamonds = (tl.decisions?.entries || []).filter((x) => x.t != null).map((x) => {
    const d = el('button', `mark change ${x.kind === 'assumption' ? 'assumption' : ''}`);
    d.type = 'button';
    d.style.left = pos(x.t);
    d.title = `${ts(x.t, 0)} ${x.title}`;
    d.setAttribute('aria-label', d.title);
    d.addEventListener('click', () => { video.currentTime = x.t; });
    return d;
  });
  box.replaceChildren(...dots, ...diamonds);
}

function tick() {
  const x = video.currentTime || 0;
  const dur = tl?.duration || video.duration || 0;
  $('clock').replaceChildren(document.createTextNode(ts(x) + ' '), el('span', 'muted', `/ ${ts(dur)}`));
  $('playhead').style.left = `${dur ? (x / dur) * 100 : 0}%`;
  $('new-at').textContent = ts(x);
  $('comment-here').textContent = t('review.comment_at', { time: ts(x, 0) });
  const shot = shotAt(x), sec = sectionAt(x);
  $('where').textContent = shot ? t('review.where', { section: sec?.name || shot.section || '', nr: shot.nr }) : '';
  const sub = (tl?.subtitles || []).find((s) => x >= s.start && x < s.end);
  $('subtitle').textContent = sub ? sub.text : '';
}
video.addEventListener('timeupdate', tick);
video.addEventListener('seeked', tick);
$('play').addEventListener('click', () => (video.paused ? video.play() : video.pause()));
video.addEventListener('play', () => $('play').setAttribute('aria-label', t('review.pause')));
video.addEventListener('pause', () => $('play').setAttribute('aria-label', t('review.play')));
$('comment-here').addEventListener('click', () => { video.pause(); $('new').focus(); });
document.addEventListener('keydown', (e) => {
  if (['TEXTAREA', 'INPUT', 'SELECT'].includes(document.activeElement?.tagName)) return;
  if (e.key === 'c' || e.key === 'C') { e.preventDefault(); video.pause(); $('new').focus(); }
  if (e.key === ' ') { e.preventDefault(); video.paused ? video.play() : video.pause(); }
  if (e.key === 'ArrowLeft') video.currentTime = Math.max(0, video.currentTime - (e.shiftKey ? 5 : 1 / 25));
  if (e.key === 'ArrowRight') video.currentTime = video.currentTime + (e.shiftKey ? 5 : 1 / 25);
});

// ------------------------------------------------------------------ finish the review

$('review-done').addEventListener('click', async () => {
  const open = entries.filter((e) => e.status === 'open').length;
  if (!confirm(t('review.done_confirm', { n: open }))) return;
  try {
    const res = await fetch(`/api/projects/${project}/review/done`, { method: 'POST', credentials: 'same-origin',
      headers: { 'content-type': 'application/json' }, body: JSON.stringify({ cut: cut.id }) });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.message || body.error || res.statusText);
    location.href = `/projects/${project}`;
  } catch (err) { showError(err); }
});

await loadCuts();
tick();
setInterval(() => loadEntries().catch(() => {}), 5000);
window.cutleryReview = { get entries() { return entries; }, get cut() { return cut; }, selectCut };

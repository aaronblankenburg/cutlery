// Review page: cutter's review app in a frame (served unchanged from the cutter package, see review.py), filling the
// width and the rest of the window, and Cutlery's bar above it: "Review done → next cut", and the note for the next
// cut, which can be written (and is saved) all through the review.
// The frame tells the page which cut is shown (postMessage {source: 'cutter-review', cut, open}).
import { load, t } from './i18n.js';

const $ = (id) => document.getElementById(id);
const project = $('review').dataset.project;
const guest = $('review').dataset.guest || null;       // a friend with a shared link: comments only
const frame = $('review-frame');
let cuts = [], shown = null, open = 0;

await load();

async function api(path, options = {}) {
  const res = await fetch(path, { credentials: 'same-origin', ...options });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(body.message || body.error || res.statusText), { status: res.status });
  return body;
}
const num = (id) => Number((String(id).match(/\d+/g) || [0]).pop());

// One scrollbar: on a wide screen the frame is as tall as the window below the bar (cutter's page fits it, only its
// list of comments scrolls; the page scrolls once so that bar and frame fill the window); on a narrow screen the frame
// is as tall as cutter's page says it needs, and only this page scrolls.
let wide = true, needed = 0, placed = false;
function fit() {
  const bar = $('review');
  if (wide) frame.style.height = `${Math.max(480, window.innerHeight - bar.offsetHeight - 24)}px`;
  else if (needed) frame.style.height = `${needed + frame.offsetHeight - frame.clientHeight}px`;   // plus the border
  if (wide && !placed && frame.getBoundingClientRect().bottom > window.innerHeight) {
    placed = true;
    window.scrollTo({ top: bar.getBoundingClientRect().top + window.scrollY - 8 });
  }
}
addEventListener('resize', fit);
fit();
if ('ResizeObserver' in window) new ResizeObserver(() => fit()).observe($('review'));   // the note grows and shrinks

// a cut asked for in the address (links from the status page) goes on to the frame
const want = new URLSearchParams(location.search).get('cut');
if (want) frame.src = `/projects/${project}/review/app/?cut=${encodeURIComponent(want)}`;

async function loadCuts() {
  cuts = (await api(`/projects/${project}/review/api/cuts`)).cuts;
  render();
}

function render() {
  const newest = cuts[cuts.length - 1];
  const cut = cuts.find((c) => c.id === shown) || newest;
  if (!cut) return;
  const isNewest = cut === newest;
  const done = $('review-done');
  done.hidden = !isNewest || cut.locked || !!guest;
  done.textContent = t('review.done', { n: num(cut.id) + 1 });
  $('next-note').hidden = !isNewest || cut.locked || !!guest;
  $('next-note-label').textContent = t('review.note_label', { n: num(cut.id) + 1 });
  $('next-note-text').placeholder = t('review.note_placeholder', { n: num(cut.id) + 1 });
  const n = cut.id === shown ? open : cut.open;
  $('review-state').textContent = cut.locked ? t('review.locked_short', { n: num(cut.id) })
    : t('review.state', { n: num(cut.id), open: n });
}

addEventListener('message', (e) => {
  if (e.origin !== location.origin || e.source !== frame.contentWindow || e.data?.source !== 'cutter-review') return;
  if ('height' in e.data) {
    wide = e.data.wide !== false;
    needed = Number(e.data.height) || 0;
    fit();
    return;
  }
  shown = e.data.cut;
  open = Number(e.data.open) || 0;
  if (!cuts.some((c) => c.id === shown)) loadCuts().catch(() => {});
  else render();
});

// ------------------------------------------------------------------ the note for the next cut

const noteBox = $('next-note-text');
let noteTimer = null, noteSaved = '';
function noteState(key, values) { $('next-note-state').textContent = key ? t(key, values) : ''; }
async function saveNote() {
  clearTimeout(noteTimer);
  const text = noteBox.value;
  if (text === noteSaved) return;
  noteState('review.note_saving');
  try {
    await api(`/api/projects/${project}/review/note`, { method: 'PUT', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ text }) });
    noteSaved = text;
    noteState('review.note_saved', { time: new Date().toLocaleTimeString(document.documentElement.lang, { hour: '2-digit', minute: '2-digit' }) });
  } catch (err) { noteState('review.note_error', { error: err.message }); }
}
// taller while it is written, and while it holds more than a line (so what was written stays in view)
const filled = () => noteBox.classList.toggle('filled', noteBox.value.includes('\n') || noteBox.value.length > 80);
noteBox.addEventListener('input', filled);
noteBox.addEventListener('input', () => { noteState('review.note_unsaved'); clearTimeout(noteTimer); noteTimer = setTimeout(saveNote, 800); });
noteBox.addEventListener('blur', saveNote);
addEventListener('beforeunload', (e) => { if (noteBox.value !== noteSaved) { saveNote(); e.preventDefault(); } });
if (!guest) try {
  const d = await api(`/api/projects/${project}/review/note`);
  noteBox.value = noteSaved = d.text;
  filled();
  if (d.text && d.saved) noteState('review.note_saved', { time: new Date(d.saved).toLocaleTimeString(document.documentElement.lang, { hour: '2-digit', minute: '2-digit' }) });
} catch { /* the field stays empty */ }

// ------------------------------------------------------------------ finish the review

$('review-done').addEventListener('click', () => {
  const cut = cuts[cuts.length - 1];
  const n = cut.id === shown ? open : cut.open;
  $('done-title').textContent = t('review.done', { n: num(cut.id) + 1 });
  $('done-lead').textContent = t('review.done_lead', { n });
  $('done-ok').textContent = t('review.done_start', { n: num(cut.id) + 1 });
  $('done-note').value = noteBox.value;                       // what was written during the review, still editable
  $('done-dialog').showModal();
  $('done-note').focus();
});

$('done-dialog').addEventListener('close', async () => {
  if (noteBox.value !== $('done-note').value) {               // changed in the dialog: kept, also when cancelled
    noteBox.value = $('done-note').value;
    filled();
    await saveNote();
  }
  if ($('done-dialog').returnValue !== 'ok') return;
  const cut = cuts[cuts.length - 1];
  $('review-error').hidden = true;
  try {
    await api(`/api/projects/${project}/review/done`, { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ cut: cut.id, note: $('done-note').value }) });
    noteSaved = noteBox.value = '';
    location.href = `/projects/${project}`;
  } catch (err) {
    $('review-error').hidden = false;
    $('review-error').textContent = err.message;
  }
});

await loadCuts();
setInterval(() => loadCuts().catch(() => {}), 15000);
window.cutleryReview = { get cuts() { return cuts; }, get shown() { return shown; } };

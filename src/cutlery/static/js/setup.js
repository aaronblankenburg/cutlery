// New project page: briefing, further files (storyboard, images, music with licences), the readiness checklist and
// "Cut a draft" (which may be pressed before upload and analysis are done: the job waits for them).
import { load, t } from './i18n.js';
import { size as fmtSize } from './format.js';

const $ = (id) => document.getElementById(id);
const project = $('readiness').dataset.project;
const LICENSES = ['own', 'CC BY 4.0', 'CC0', 'other'];
const licKey = (l) => `files.license.${l.replaceAll(' ', '_').toLowerCase()}`;

await load();

async function api(path, options = {}) {
  const res = await fetch(path, { credentials: 'same-origin', ...options });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(body.message || body.error || res.statusText), { status: res.status });
  return body;
}

// ------------------------------------------------------------------ briefing

const form = $('briefing');
const fields = ['prompt', 'purpose', 'length', 'style', 'music', 'nogos', 'frame'];
let dirty = false;
form.addEventListener('input', () => { dirty = true; });

async function saveBriefing() {
  // namedItem: form.elements['length'] is the number of fields, not the field called "length"
  const data = Object.fromEntries(fields.map((f) => [f, form.elements.namedItem(f).value]));
  await api(`/api/projects/${project}/briefing`, { method: 'PUT', headers: { 'content-type': 'application/json' },
    body: JSON.stringify(data) });
  dirty = false;
  $('briefing-status').textContent = t('briefing.saved');
  refreshState();
}
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  try { await saveBriefing(); } catch (err) { $('briefing-status').textContent = err.message; }
});

// ------------------------------------------------------------------ files

function renderFiles(list) {
  for (const kind of ['storyboard', 'images', 'music', 'other']) {
    document.querySelector(`[data-count="${kind}"]`).textContent = t('files.count', { n: list[kind].length });
  }
  const ul = $('file-list');
  ul.replaceChildren(...['storyboard', 'images', 'other'].flatMap((kind) => list[kind].map((f) => {
    const li = document.createElement('li');
    const name = document.createElement('span');
    name.textContent = `${t('files.' + kind)} · ${f.name} · ${fmtSize(f.size)}`;
    li.append(name, removeButton(kind, f.name));
    return li;
  })));
  const tbody = $('music-table').tBodies[0];
  $('music-wrap').hidden = !list.music.length;
  tbody.replaceChildren(...list.music.map((f, i) => {
    const tr = document.createElement('tr');
    const td1 = document.createElement('td');
    td1.className = 'name';
    td1.textContent = f.name;
    const td2 = document.createElement('td');
    const label = document.createElement('label');
    label.className = 'visually-hidden';
    label.htmlFor = `lic-${i}`;
    label.textContent = t('files.license_for', { name: f.name });
    const sel = document.createElement('select');
    sel.id = `lic-${i}`;
    for (const l of LICENSES) {
      const o = document.createElement('option');
      o.value = l;
      o.textContent = t(licKey(l));
      o.selected = (f.license || 'other') === l;
      sel.append(o);
    }
    sel.addEventListener('change', async () => {
      try { renderFiles(await api(`/api/projects/${project}/music/${encodeURIComponent(f.name)}/license`,
        { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ license: sel.value }) })); refreshState(); }
      catch (err) { $('files-status').textContent = err.message; }
    });
    td2.append(label, sel);
    if ((f.license || 'other') === 'other') {
      const note = document.createElement('span');
      note.className = 'small error-text';
      note.textContent = ' ' + t('files.not_used');
      td2.append(note);
    }
    const td3 = document.createElement('td');
    td3.append(removeButton('music', f.name));
    tr.append(td1, td2, td3);
    return tr;
  }));
}

function removeButton(kind, name) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'link-button small';
  b.textContent = t('files.remove');
  b.setAttribute('aria-label', t('files.remove_name', { name }));
  b.addEventListener('click', async () => {
    renderFiles(await api(`/api/projects/${project}/files/${kind}/${encodeURIComponent(name)}`, { method: 'DELETE' }));
    refreshState();
  });
  return b;
}

function put(kind, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', `/api/projects/${project}/files/${kind}/${encodeURIComponent(file.name)}`);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      const body = JSON.parse(xhr.responseText || '{}');
      xhr.status < 300 ? resolve(body) : reject(new Error(body.error || xhr.statusText));
    };
    xhr.onerror = () => reject(new Error(t('footage.error.network')));
    xhr.send(file);
  });
}

for (const input of document.querySelectorAll('#files input[type=file]')) {
  input.addEventListener('change', async () => {
    const kind = input.dataset.kind;
    for (const file of input.files) {
      try {
        renderFiles(await put(kind, file, (p) => {
          $('files-status').textContent = t('files.uploading', { name: file.name, percent: Math.round(p * 100) });
        }));
        $('files-status').textContent = '';
      } catch (err) {
        $('files-status').textContent = t('files.error', { name: file.name, error: err.message });
      }
    }
    input.value = '';
    refreshState();
  });
}
renderFiles(await api(`/api/projects/${project}/files`));

// ------------------------------------------------------------------ readiness

function check(state, label, note) {
  const li = document.createElement('li');
  const dot = document.createElement('span');
  dot.className = `dot ${state}`;
  const text = document.createElement('span');
  const strong = document.createElement('strong');
  strong.textContent = label;
  const small = document.createElement('span');
  small.className = 'small muted';
  small.textContent = note;
  text.append(strong, document.createElement('br'), small);
  li.append(dot, text);
  return li;
}

let current = null;
async function refreshState() {
  let st;
  try { st = await api(`/api/projects/${project}/state`); } catch { return; }
  current = st;
  const items = [];
  const footageDone = st.usable && st.uploaded === st.usable;
  items.push(check(footageDone ? 'ok' : st.usable ? 'busy' : 'wait', t('ready.footage'),
    st.usable ? t('ready.footage_note', { done: st.uploaded, total: st.usable }) : t('ready.footage_none')));
  const a = st.analysis;
  const analysisDone = st.uploaded && a.clips >= st.uploaded && !['waiting', 'running'].includes(a.state);
  items.push(check(a.state === 'failed' ? 'bad' : analysisDone ? 'ok' : st.uploaded ? 'busy' : 'wait', t('ready.analysis'),
    a.state === 'failed' ? t('ready.analysis_failed') : analysisDone ? t('ready.analysis_done', { n: a.clips })
      : t('ready.analysis_running', { n: a.analysed })));
  const bOk = (form.elements.prompt.value || '').trim();
  items.push(check(bOk ? 'ok' : 'wait', t('ready.briefing'), bOk ? t('ready.briefing_ok') : t('ready.briefing_missing')));
  const sb = st.files.storyboard.length, im = st.files.images.length;
  items.push(check(sb || im ? 'ok' : 'wait', t('ready.files'), sb || im ? t('ready.files_note', { sb, im }) : t('ready.files_none')));
  const mu = st.files.music.length;
  items.push(check(st.music_unclear ? 'bad' : mu ? 'ok' : 'wait', t('ready.music'),
    st.music_unclear ? t('ready.music_unclear', { n: st.music_unclear }) : mu ? t('ready.music_ok', { n: mu }) : t('ready.music_none')));
  $('checks').replaceChildren(...items);
  const job = st.job && ['queued', 'running', 'paused'].includes(st.job.state) ? st.job : null;
  $('job-draft').disabled = Boolean(job) || Boolean(st.cuts.length) || !st.usable;
  if (job) $('ready-note').textContent = t('ready.job_active', { state: t(`jobs.state.${job.state}`) });
}

$('job-draft').addEventListener('click', async () => {
  $('job-error').hidden = true;
  try {
    if (dirty || !current?.briefing_saved) await saveBriefing();
    await api(`/api/projects/${project}/jobs`, { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ kind: 'draft', note: $('job-note-input').value }) });
    location.href = `/projects/${project}`;
  } catch (err) {
    $('job-error').hidden = false;
    $('job-error').textContent = err.message;
  }
});

await refreshState();
setInterval(refreshState, 5000);

// what the analysis looks at: saved at once, a change analyses the project again (only what is missing)
const partsForm = $('parts-form');
partsForm?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const boxes = [...partsForm.querySelectorAll('input[data-part]')];
  const parts = boxes.filter((b) => b.checked).map((b) => b.dataset.part);
  $('parts-status').textContent = '';
  try {
    const r = await api(`/api/projects/${project}/analysis/parts`, { method: 'PUT', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ parts }) });
    $('parts-count').textContent = t('parts.summary', { n: r.parts.length, all: boxes.length });
    $('parts-status').textContent = r.changed ? t('parts.saved_again') : t('parts.saved');
    refreshState();
  } catch (err) {
    $('parts-status').textContent = err.message;
  }
});

// Footage page: pick the folder, read every clip's metadata, transcode to 360p in a worker and upload with tus,
// clip by clip, while the next clip is already transcoding. Survives a reload: the server knows which clips are
// complete, IndexedDB remembers transcoded files in OPFS and unfinished uploads.
import { load, t } from './i18n.js';
import { size as fmtSize, duration as fmtDuration } from './format.js';

const VIDEO_EXT = new Set(['mp4', 'mov', 'm4v', 'mts', 'm2ts', 'mkv', 'avi', 'mxf', 'webm', '3gp', 'mpg', 'mpeg']);
const SKIP_EXT = new Set(['lrv', 'thm']);   // GoPro low-res previews and thumbnails
const MAX_WAITING = 3;                       // transcoded files waiting for upload (OPFS space)
const CHUNK = 8 * 1024 * 1024;

const root = document.getElementById('footage');
const project = root.dataset.project;
const $ = (id) => document.getElementById(id);
const rows = new Map();     // rel_path -> row state

// ------------------------------------------------------------------ worker

const worker = new Worker('/static/js/pipeline-worker.js', { type: 'module' });
let nextId = 1;
const pending = new Map();
worker.onmessage = ({ data }) => {
  const p = pending.get(data.id);
  if (!p) return;
  if ('progress' in data) { p.onProgress?.(data.progress); return; }
  pending.delete(data.id);
  data.ok ? p.resolve(data.result) : p.reject(Object.assign(new Error(data.error), { code: data.code }));
};
function call(type, payload = {}, onProgress) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject, onProgress });
    worker.postMessage({ id, type, ...payload });
  });
}

// ------------------------------------------------------------------ IndexedDB: resume state per clip

const idb = new Promise((resolve, reject) => {
  const req = indexedDB.open('cutlery', 1);
  req.onupgradeneeded = () => req.result.createObjectStore('clips');
  req.onsuccess = () => resolve(req.result);
  req.onerror = () => reject(req.error);
});
async function store(mode, fn) {
  const db = await idb;
  return new Promise((resolve, reject) => {
    const tx = db.transaction('clips', mode);
    const req = fn(tx.objectStore('clips'));
    tx.oncomplete = () => resolve(req?.result);
    tx.onerror = () => reject(tx.error);
  });
}
const stateKey = (fp) => `${project}:${fp.size}:${fp.head_sha256}:${fp.tail_sha256}`;
const getState = (fp) => store('readonly', (s) => s.get(stateKey(fp)));
const putState = (fp, value) => store('readwrite', (s) => s.put(value, stateKey(fp)));
const dropState = (fp) => store('readwrite', (s) => s.delete(stateKey(fp)));

// ------------------------------------------------------------------ wake lock

let wakeLock = null, busy = false;
async function holdWakeLock() {
  if (!busy || wakeLock || !('wakeLock' in navigator) || document.visibilityState !== 'visible') return;
  try {
    wakeLock = await navigator.wakeLock.request('screen');
    wakeLock.addEventListener('release', () => { wakeLock = null; });
  } catch { /* not allowed (e.g. battery saver); work continues */ }
}
document.addEventListener('visibilitychange', holdWakeLock);
function setBusy(on) {
  busy = on;
  if (on) holdWakeLock(); else { wakeLock?.release(); wakeLock = null; }
}
window.addEventListener('beforeunload', (e) => { if (busy) { e.preventDefault(); e.returnValue = ''; } });

// ------------------------------------------------------------------ table

function statusPill(row) {
  const map = { waiting: '', reading: 'info', transcoding: 'warn', transcoded: 'warn', uploading: 'warn',
    uploaded: 'ok', unsupported: 'error', failed: 'error' };
  const vals = { percent: Math.round((row.progress || 0) * 100) };
  return `<span class="pill ${map[row.status] || ''}">${escape(t('footage.status.' + row.status, vals))}</span>`;
}
function escape(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function describe(sc) {
  if (!sc?.video) return '';
  const v = sc.video;
  const res = v.rotation % 180 ? `${v.height}×${v.width}` : `${v.width}×${v.height}`;
  const fps = v.avg_frame_rate ? (eval_rate(v.avg_frame_rate)).toFixed(2).replace(/\.00$/, '') : '';
  return `${(v.codec || '').toUpperCase()} · ${res}${fps ? ' · ' + fps + ' fps' : ''}${v.vfr ? ' VFR' : ''}`;
}
function eval_rate(r) { const [a, b] = r.split('/').map(Number); return b ? a / b : 0; }
function reasonText(u) { return t('footage.reason.' + u.reason, { detail: u.detail || '' }); }

function render(row) {
  if (!row.tr) {
    row.tr = document.createElement('tr');
    $('clip-table').tBodies[0].append(row.tr);
  }
  const reason = row.reason ? `<div class="small muted">${escape(row.reason)}</div>` : '';
  row.tr.innerHTML = `<td class="name">${escape(row.relPath)}${reason}</td>
    <td class="hide-narrow small">${escape(describe(row.sidecar))}</td>
    <td class="num hide-narrow">${row.size ? fmtSize(row.size) : ''}</td>
    <td class="num">${row.standinSize ? fmtSize(row.standinSize) : ''}</td>
    <td>${statusPill(row)}</td>`;
  $('clip-table').hidden = false;
}

let startedAt = 0, bytesDone = 0, bytesTotal = 0;
function summary() {
  const all = [...rows.values()];
  const usable = all.filter((r) => r.status !== 'unsupported');
  const done = usable.filter((r) => r.status === 'uploaded').length;
  const uploaded = usable.filter((r) => r.status === 'uploaded').reduce((n, r) => n + (r.standinSize || 0), 0);
  $('progress-block').hidden = !usable.length;
  $('progress-bar').style.width = `${usable.length ? (done / usable.length) * 100 : 0}%`;
  let text = t('footage.progress', { done, total: usable.length, uploaded: fmtSize(uploaded) });
  if (busy && bytesDone > 0 && startedAt) {
    const rate = bytesDone / ((performance.now() - startedAt) / 1000);
    if (rate > 0) text += ' · ' + t('footage.remaining', { time: fmtDuration((bytesTotal - bytesDone) / rate) });
  }
  if (done === usable.length && usable.length && !busy) text = t('footage.done');
  $('progress-text').textContent = text;

  const bad = all.filter((r) => r.status === 'unsupported');
  const notice = $('unsupported-notice');
  notice.hidden = !bad.length;
  if (bad.length) {
    const hints = new Set();
    for (const r of bad) {
      const u = r.sidecar?.unsupported || {};
      if (u.reason === 'container') hints.add('footage.hint.container');
      else if (/^(hevc|hvc1|hev1)/i.test(u.detail || r.sidecar?.video?.codec || '')) hints.add('footage.hint.hevc');
      else hints.add('footage.hint.other');
    }
    notice.textContent = [t('footage.unsupported_head', { n: bad.length }), ...[...hints].map((h) => t(h))].join(' ');
  }
}

function showError(text) {
  const el = $('error-notice');
  el.hidden = !text;
  el.textContent = text || '';
}

// ------------------------------------------------------------------ server

async function api(path, options = {}) {
  const res = await fetch(path, { credentials: 'same-origin', ...options });
  const body = res.headers.get('content-type')?.includes('json') ? await res.json() : await res.text();
  if (!res.ok) throw Object.assign(new Error(body?.error || body || res.statusText), { status: res.status });
  return body;
}

async function loadServerState() {
  const data = await api(`/api/projects/${project}/clips`);
  for (const c of data.clips) {
    const row = rows.get(c.rel_path) || { relPath: c.rel_path };
    row.size = c.size;
    row.clipId = c.id;
    row.standinSize = c.standin_size;
    row.status = { registered: 'waiting', uploaded: 'uploaded', unsupported: 'unsupported', failed: 'failed' }[c.state];
    row.reason = c.reason && c.state !== 'unsupported' ? c.reason : row.reason;
    rows.set(c.rel_path, row);
    render(row);
  }
  summary();
}

// ------------------------------------------------------------------ pipeline

function collect(fileList) {
  const files = [...fileList];
  const srt = new Map();
  for (const f of files) {
    if (f.name.toLowerCase().endsWith('.srt')) srt.set(f.webkitRelativePath.replace(/\.srt$/i, '').toLowerCase(), f);
  }
  const clips = [];
  for (const f of files) {
    const parts = f.webkitRelativePath.split('/');
    if (parts.some((p) => p.startsWith('.'))) continue;
    const ext = f.name.split('.').pop().toLowerCase();
    if (!VIDEO_EXT.has(ext) || SKIP_EXT.has(ext)) continue;
    clips.push({ file: f, relPath: parts.slice(1).join('/'), folder: parts[0],
      srt: srt.get(f.webkitRelativePath.replace(/\.[^.]+$/, '').toLowerCase()) || null });
  }
  clips.sort((a, b) => a.relPath.localeCompare(b.relPath));
  return clips;
}

async function registerClip(item) {
  const row = rows.get(item.relPath) || { relPath: item.relPath };
  rows.set(item.relPath, row);
  row.size = item.file.size;
  row.item = item;
  if (row.status !== 'uploaded') row.status = 'reading';
  render(row);
  const { sidecar, gpmd } = await call('probe', { file: item.file, relPath: item.relPath });
  if (item.srt) sidecar.telemetry.srt = item.srt.name;
  row.sidecar = sidecar;
  const status = await api(`/api/projects/${project}/clips`, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(sidecar) });
  row.clipId = status.id;
  if (status.need.gpmd && gpmd) {
    await api(`/api/projects/${project}/clips/${status.id}/telemetry/gpmd`, { method: 'PUT', body: gpmd });
  }
  if (status.need.srt && item.srt) {
    await api(`/api/projects/${project}/clips/${status.id}/telemetry/srt`, { method: 'PUT', body: item.srt });
  }
  if (status.state === 'unsupported') {
    row.status = 'unsupported';
    row.reason = reasonText(sidecar.unsupported);
  } else if (!status.need.standin) {
    row.status = 'uploaded';
  } else {
    row.status = 'waiting';
    row.needsUpload = true;
  }
  render(row);
  summary();
  return row;
}

let caps = null;

async function transcodeRow(row) {
  const fp = row.sidecar.fingerprint;
  const saved = await getState(fp);
  if (saved?.opfs && saved.transcoded) {
    row.output = saved;
    row.status = 'transcoded';
    render(row);
    return;
  }
  const outName = `${project}-${row.clipId}.mp4`;
  row.status = 'transcoding';
  row.progress = 0;
  render(row);
  const result = await call('transcode', { file: row.item.file, outName }, (p) => {
    row.progress = p;
    render(row);
  });
  row.output = { opfs: result.name, size: result.size, transcoded: true };
  row.standinSize = result.size;
  await putState(fp, row.output);
  row.status = 'transcoded';
  render(row);
}

async function opfsFile(name) {
  const dir = await (await navigator.storage.getDirectory()).getDirectoryHandle('cutlery', { create: true });
  return (await dir.getFileHandle(name)).getFile();
}

function uploadRow(row) {
  return new Promise(async (resolve, reject) => {
    const fp = row.sidecar.fingerprint;
    const file = await opfsFile(row.output.opfs);
    row.status = 'uploading';
    row.progress = 0;
    render(row);
    const upload = new window.tus.Upload(file, {
      endpoint: '/tus/',
      uploadUrl: row.output.uploadUrl || null,
      metadata: { project, clip: row.clipId, filename: row.relPath },
      chunkSize: CHUNK,
      retryDelays: [0, 1000, 3000, 5000, 10000, 20000, 30000, 60000],
      storeFingerprintForResuming: false,
      onUploadUrlAvailable: async () => {
        row.output.uploadUrl = upload.url;
        await putState(fp, row.output);
      },
      onProgress: (sent, total) => { row.progress = total ? sent / total : 0; render(row); },
      onError: (err) => {
        const status = err.originalResponse?.getStatus?.();
        if (status === 404 || status === 410) { row.output.uploadUrl = null; putState(fp, row.output); }
        reject(Object.assign(err, { status, body: err.originalResponse?.getBody?.() }));
      },
      onSuccess: async () => {
        row.status = 'uploaded';
        row.standinSize = file.size;
        render(row);
        await dropState(fp);
        await call('remove', { name: row.output.opfs });
        resolve();
      },
    });
    upload.start();
  });
}

let runToken = 0;

async function run(fileList) {
  const token = ++runToken;
  showError('');
  const items = collect(fileList);
  if (!items.length) return;
  const folder = items[0].folder;
  const total = items.reduce((n, i) => n + i.file.size, 0);
  $('folder-summary').textContent = t('footage.summary', { folder, n: items.length, size: fmtSize(total) });
  $('pick-folder').textContent = t('footage.pick_other');
  $('telemetry-note').hidden = false;
  setBusy(true);
  for (const it of items) {
    const row = rows.get(it.relPath) || { relPath: it.relPath, status: 'waiting', size: it.file.size };
    rows.set(it.relPath, row);
    render(row);
  }
  summary();

  const queue = [];            // rows waiting for transcoding
  const uploads = [];          // rows transcoded, waiting for upload
  let probing = true;
  bytesTotal = 0; bytesDone = 0; startedAt = performance.now();

  const prober = (async () => {
    for (const item of items) {
      if (token !== runToken) return;
      try {
        const row = await registerClip(item);
        if (row.needsUpload) { queue.push(row); bytesTotal += item.file.size; }
      } catch (e) {
        const row = rows.get(item.relPath);
        row.status = 'failed';
        row.reason = e.message;
        render(row);
        if (e.status) showError(t('footage.error.server', { file: item.relPath, error: e.message }));
      }
    }
    probing = false;
  })();

  const transcoder = (async () => {
    while (token === runToken && (probing || queue.length)) {
      if (!queue.length || uploads.length >= MAX_WAITING) { await sleep(200); continue; }
      const row = queue.shift();
      try {
        await transcodeRow(row);
        uploads.push(row);
      } catch (e) {
        row.status = 'failed';
        row.reason = e.message;
        render(row);
      }
      bytesDone += row.size;
      summary();
    }
  })();

  const uploader = (async () => {
    let transcoding = true;
    transcoder.then(() => { transcoding = false; });
    while (token === runToken && (transcoding || uploads.length)) {
      if (!uploads.length) { await sleep(200); continue; }
      const row = uploads[0];
      try {
        await uploadRow(row);
      } catch (e) {
        row.status = 'failed';
        row.reason = e.status === 422 ? (e.body || e.message) : e.message;
        render(row);
        if (e.status === 422) { await dropState(row.sidecar.fingerprint); await call('remove', { name: row.output.opfs }); }
      }
      uploads.shift();
      summary();
    }
  })();

  await Promise.all([prober, transcoder, uploader]);
  if (token === runToken) {
    setBusy(false);
    summary();
  }
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

// ------------------------------------------------------------------ start

await load();
await loadServerState();
caps = await call('capabilities');
const problems = [];
if (!caps.webcodecs) problems.push(t('footage.browser.no_webcodecs'));
else if (!caps.avc) problems.push(t('footage.browser.no_avc'));
if (!caps.opfs) problems.push(t('footage.browser.no_opfs'));
if (caps.webcodecs && !caps.aac) $('browser-notice').textContent = t('footage.browser.no_aac');
$('browser-notice').hidden = !(caps.webcodecs && !caps.aac);
if (problems.length) {
  showError(problems.join(' '));
  $('pick-folder').setAttribute('aria-disabled', 'true');
  $('folder-input').disabled = true;
}
$('folder-input').addEventListener('change', (e) => run(e.target.files));
window.cutlery = { caps, rows, run };   // for tests and debugging

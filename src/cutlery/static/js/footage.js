// Footage page: pick the folder, read every clip's metadata, transcode to 360p in a worker and upload with tus,
// clip by clip, while the next clip is already transcoding. Survives a reload: the server knows which clips are
// complete, IndexedDB remembers transcoded files in OPFS and unfinished uploads.
import { load, t } from './i18n.js';
import { size as fmtSize, duration as fmtDuration } from './format.js';

const VIDEO_EXT = new Set(['mp4', 'mov', 'm4v', 'mts', 'm2ts', 'mkv', 'avi', 'mxf', 'webm', '3gp', 'mpg', 'mpeg']);
const SKIP_EXT = new Set(['lrv', 'thm']);   // GoPro low-res previews and thumbnails
const MAX_WAITING = 3;                       // transcoded files waiting for upload (OPFS space)
// Clips transcoded at the same time. Measured with tools/bench_transcode.py (docs/m1-feasibility.md): with a GPU a
// second or third worker gains 5-10 % (they share the hardware decoder, which already uses several cores), without
// one it is 13 % slower. So one; ?parallel=N in the address tries more on other machines.
const PARALLEL = () => {
  const forced = Number(new URLSearchParams(location.search).get('parallel'));
  return forced >= 1 && forced <= 8 ? Math.floor(forced) : 1;
};
const CHUNK = 8 * 1024 * 1024;

const root = document.getElementById('footage');
const project = root.dataset.project;
const $ = (id) => document.getElementById(id);
const rows = new Map();     // rel_path -> row state

// ------------------------------------------------------------------ worker

// One worker probes and transcodes; more workers transcode further clips at the same time (PARALLEL).
let nextId = 1;
function makeWorker() {
  const w = new Worker('/static/js/pipeline-worker.js', { type: 'module' });
  w.pending = new Map();
  w.failed = null;
  w.onmessage = ({ data }) => {
    const p = w.pending.get(data.id);
    if (!p) return;
    if ('progress' in data) { p.onProgress?.(data.progress); return; }
    w.pending.delete(data.id);
    data.ok ? p.resolve(data.result) : p.reject(Object.assign(new Error(data.error), { code: data.code }));
  };
  // A worker whose code did not load (for example a download cut short) fires "error" and never answers:
  // every waiting call fails instead of hanging, and so does every later one.
  w.onerror = (e) => {
    e.preventDefault?.();
    w.failed = new Error(e.message || 'the transcoding worker did not start');
    for (const p of w.pending.values()) p.reject(w.failed);
    w.pending.clear();
  };
  return w;
}
const workers = [makeWorker()];
let probeWorker = null;      // probing has its own worker: a stuck transcode that is stopped does not take it along
function workerFor(k) {
  while (workers.length <= k) workers.push(makeWorker());
  return workers[k];
}
function getProbeWorker() {
  if (!probeWorker) probeWorker = makeWorker();
  return probeWorker;
}
// Stop a worker (it hangs, or the upload starts over): every call waiting on it fails, a fresh one takes its place.
function stopWorker(w, reason) {
  w.terminate();
  w.failed = w.failed || new Error(reason);
  for (const p of w.pending.values()) { clearTimeout(p.timer); p.reject(w.failed); }
  w.pending.clear();
  const k = workers.indexOf(w);
  if (k >= 0) workers[k] = makeWorker();
  if (w === probeWorker) probeWorker = null;
}
// idle: give up (and stop the worker) when it sends neither progress nor an answer for this long.
function call(type, payload = {}, onProgress, w = workers[0], idle = 0) {
  if (w.failed) return Promise.reject(w.failed);
  const id = nextId++;
  return new Promise((resolve, reject) => {
    const p = { resolve, reject, onProgress, timer: null };
    const arm = () => {
      if (!idle) return;
      clearTimeout(p.timer);
      p.timer = setTimeout(() => stopWorker(w, `no answer for ${Math.round(idle / 1000)} s (${type})`), idle);
    };
    p.onProgress = (v) => { arm(); onProgress?.(v); };
    p.resolve = (v) => { clearTimeout(p.timer); resolve(v); };
    p.reject = (e) => { clearTimeout(p.timer); reject(e); };
    w.pending.set(id, p);
    arm();
    w.postMessage({ id, type, ...payload });
  });
}
const IDLE = { probe: 120000, transcode: 300000, remove: 30000 };
function withTimeout(promise, ms) {
  return Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), ms))]);
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
    tx.onabort = () => reject(tx.error || new Error('IndexedDB transaction aborted'));
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
  $('upload-stop').hidden = !on;
  for (const r of rows.values()) if (r.tr) render(r);      // delete buttons only while nothing runs
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
    <td>${statusPill(row)}</td>
    <td class="num">${row.clipId && !busy ? `<button class="link-button small" type="button" data-delete="${escape(row.relPath)}"
      aria-label="${escape(t('footage.delete_clip', { name: row.relPath }))}" >${escape(t('footage.delete'))}</button>` : ''}</td>`;
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
  }  updateRestart();
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
  const { sidecar, gpmd } = await call('probe', { file: item.file, relPath: item.relPath }, null,
    getProbeWorker(), IDLE.probe);
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

async function transcodeRow(row, w) {
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
  }, w, IDLE.transcode);
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

// Clean-up after an upload: never worth hanging for.
async function forget(row) {
  try { await dropState(row.sidecar.fingerprint); } catch { /* the entry stays; the server knows the clip is done */ }
  try { await call('remove', { name: row.output.opfs }, null, workers[0], IDLE.remove); } catch { /* left in OPFS */ }
}

const activeUploads = new Set();

// 409 "does not need an upload": an earlier, stopped upload of this clip reached the server after all.
async function finishedMeanwhile(row) {
  try {
    const c = (await api(`/api/projects/${project}/clips`)).clips.find((x) => x.id === row.clipId);
    if (c?.state !== 'uploaded') return false;
    row.status = 'uploaded';
    row.standinSize = c.standin_size;
    row.reason = '';
    render(row);
    await forget(row);
    return true;
  } catch {
    return false;
  }
}

async function uploadRow(row) {
  const fp = row.sidecar.fingerprint;
  const file = await opfsFile(row.output.opfs);
  row.status = 'uploading';
  row.progress = 0;
  render(row);
  await new Promise((resolve, reject) => {
    const upload = new window.tus.Upload(file, {
      endpoint: '/tus/',
      uploadUrl: row.output.uploadUrl || null,
      metadata: { project, clip: row.clipId, filename: row.relPath },
      chunkSize: CHUNK,
      retryDelays: [0, 1000, 3000, 5000, 10000, 20000, 30000, 60000],
      storeFingerprintForResuming: false,
      // tus-js-client's rule (retry 5xx, network errors, 409, 423), except a 409 on creation: "this clip does not
      // need an upload" will not change (an earlier upload arrived after all), so do not retry it for two minutes.
      onShouldRetry: (err) => {
        const status = err.originalResponse?.getStatus?.() || 0;
        if (status === 409 && err.originalRequest?.getMethod?.() === 'POST') return false;
        return !(status >= 400 && status < 500) || status === 409 || status === 423;
      },
      onUploadUrlAvailable: () => {
        row.output.uploadUrl = upload.url;
        putState(fp, row.output).catch(() => {});
      },
      onProgress: (sent, total) => { row.progress = total ? sent / total : 0; render(row); },
      onError: (err) => {
        activeUploads.delete(upload);
        const status = err.originalResponse?.getStatus?.();
        if (status === 404 || status === 410) { row.output.uploadUrl = null; putState(fp, row.output).catch(() => {}); }
        reject(Object.assign(err, { status, body: err.originalResponse?.getBody?.() }));
      },
      onSuccess: () => { activeUploads.delete(upload); resolve(); },
    });
    upload.abortRun = () => { activeUploads.delete(upload); upload.abort(); reject(new Error('stopped')); };
    activeUploads.add(upload);
    upload.start();
  });
  row.status = 'uploaded';
  row.standinSize = file.size;
  render(row);
  await forget(row);
}

let runToken = 0;
let lastFiles = null;        // the chosen folder, for "start the upload again" (gone after a reload)

// Show "start again" while clips of the chosen folder are not uploaded yet (stuck, failed or still to come).
function updateRestart() {
  const open = [...rows.values()].some((r) => r.item && !['uploaded', 'unsupported'].includes(r.status));
  $('upload-restart').hidden = !(lastFiles && open);
}

// Stop everything that runs: uploads, workers. Clips half done go back to "waiting" (they continue next time).
function stopAll() {
  runToken++;
  for (const u of [...activeUploads]) u.abortRun();
  for (const w of [...workers]) stopWorker(w, 'stopped');
  if (probeWorker) stopWorker(probeWorker, 'stopped');
  for (const r of rows.values()) {
    if (['reading', 'transcoding', 'transcoded', 'uploading'].includes(r.status)) { r.status = 'waiting'; r.progress = 0; }
  }
  setBusy(false);
  summary();
}

// A deleted clip: off the table, and its transcoded file and resume state out of the browser.
function forgetRow(row) {
  row.tr?.remove();
  rows.delete(row.relPath);
  if (row.sidecar?.fingerprint) dropState(row.sidecar.fingerprint).catch(() => {});
  if (row.output?.opfs) call('remove', { name: row.output.opfs }, null, workers[0], IDLE.remove).catch(() => {});
}

// Stop everything that runs (uploads, workers) and go through the folder again: done clips are skipped by the
// server, transcoded ones are taken from OPFS, interrupted uploads continue where they stopped.
function restart() {
  if (!lastFiles) return;
  stopAll();
  run(lastFiles);
}

async function run(fileList) {
  const token = ++runToken;
  lastFiles = fileList;
  showError('');
  const items = collect(fileList);
  if (!items.length) {
    showError(t('footage.error.no_videos', { n: fileList.length }));
    return;
  }
  $('folder-summary').textContent = t('footage.preparing');
  try {
    await ready;                     // the browser's capabilities (the worker has started)
  } catch {
    return;                          // the message is shown already
  }
  const folder = items[0].folder;
  api(`/api/projects/${project}/folder`, { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name: folder }) }).catch(() => {});
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
      // uploaded already under the same name with the same size: neither read nor upload it again. (Clips this
      // browser could not read are read again: a newer Cutlery or browser may manage.)
      const known = rows.get(item.relPath);
      if (known?.clipId && known.status === 'uploaded' && known.size === item.file.size) {
        known.item = item;
        continue;
      }
      try {
        const row = await registerClip(item);
        if (row.needsUpload) { queue.push(row); bytesTotal += item.file.size; }
      } catch (e) {
        if (token !== runToken) return;
        const row = rows.get(item.relPath);
        row.status = 'failed';
        row.reason = e.message;
        render(row);
        if (e.status) showError(t('footage.error.server', { file: item.relPath, error: e.message }));
      }
    }
    probing = false;
  })();

  const parallel = PARALLEL();
  const transcoder = Promise.all(Array.from({ length: parallel }, (_, k) => (async () => {
    while (token === runToken && (probing || queue.length)) {
      if (!queue.length || uploads.length >= MAX_WAITING + parallel - 1) { await sleep(200); continue; }
      const row = queue.shift();
      try {
        await transcodeRow(row, workerFor(k));
        uploads.push(row);
      } catch (e) {
        if (token !== runToken) return;
        row.status = 'failed';
        row.reason = e.message;
        render(row);
      }
      bytesDone += row.size;
      summary();
    }
  })()));

  const uploader = (async () => {
    let transcoding = true;
    transcoder.then(() => { transcoding = false; });
    while (token === runToken && (transcoding || uploads.length)) {
      if (!uploads.length) { await sleep(200); continue; }
      const row = uploads[0];
      try {
        await uploadRow(row);
      } catch (e) {
        if (token !== runToken) return;
        if (e.status === 409 && await finishedMeanwhile(row)) { uploads.shift(); summary(); continue; }
        row.status = 'failed';
        row.reason = e.status === 422 ? (e.body || e.message) : e.message;
        render(row);
        if (e.status === 422) await forget(row);
      }
      uploads.shift();
      summary();
      updateRestart();
    }
  })();

  await Promise.all([prober, transcoder, uploader]);
  if (token === runToken) {
    setBusy(false);
    summary();
    updateRestart();
  }
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

// ------------------------------------------------------------------ analysis on the server

async function showAnalysis() {
  let a;
  try { a = await api(`/api/projects/${project}/analysis`); } catch { return; }
  const card = $('analysis');
  card.hidden = rows.size === 0 && !a.uploaded;
  let text, failed = a.state === 'failed', stopped = a.state === 'stopped';
  if (failed) text = t('footage.analysis.failed', { message: a.message || '' });
  else if (stopped) text = t('footage.analysis.stopped');
  else if (a.state === 'running') text = t('footage.analysis.running', a);
  if (['running', 'waiting'].includes(a.state)) {
    text += ' ' + (a.remaining_s != null ? t('footage.analysis.remaining', { time: fmtDuration(Math.max(60, a.remaining_s)) })
      : t('footage.analysis.remaining_unknown'));
  }
  else if (a.state === 'waiting') text = t('footage.analysis.waiting');
  else if (a.clips && a.clips >= a.uploaded) text = t('footage.analysis.done', a);
  else text = t('footage.analysis.idle');
  $('analysis-text').textContent = text;
  const f = a.footage;
  const facts = [];
  if (f?.recorded_from) {
    const from = new Date(f.recorded_from), to = new Date(f.recorded_to);
    const fmt = new Intl.DateTimeFormat(document.documentElement.lang || undefined,
      // cameras write their wall-clock time without a zone, which reads as UTC: show it as written
      { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' });
    facts.push(t('footage.facts.recorded', { range: fmt.formatRange ? fmt.formatRange(from, to) : `${fmt.format(from)} – ${fmt.format(to)}` }));
  }
  if (f?.footage_seconds) facts.push(t('footage.facts.length', { time: fmtDuration(f.footage_seconds) }));
  if (f?.work_bytes || f?.standin_bytes) {
    facts.push(t('footage.facts.space', { total: fmtSize(f.work_bytes || f.standin_bytes), standins: fmtSize(f.standin_bytes || 0) }));
  }
  $('footage-facts').textContent = facts.join(' · ');
  $('analysis-stop').hidden = !['waiting', 'running'].includes(a.state);
  $('clips-delete-all').hidden = !(a.uploaded || [...rows.values()].some((r) => r.clipId));
  $('footage-facts').hidden = !facts.length;
  $('analysis-retry').hidden = !(failed || stopped);
  $('analysis-retry').textContent = t(stopped ? 'footage.analysis.continue' : 'footage.analysis.retry');
}
$('analysis-stop').addEventListener('click', async () => {
  await api(`/api/projects/${project}/analysis/stop`, { method: 'POST' });
  showAnalysis();
});
$('analysis-retry').addEventListener('click', async () => {
  await api(`/api/projects/${project}/analysis`, { method: 'POST' });
  showAnalysis();
});
setInterval(showAnalysis, 5000);

// ------------------------------------------------------------------ start

await load();
// The folder can be chosen at once; run() waits until the worker is ready.
$('folder-input').addEventListener('change', (e) => run(e.target.files));
$('upload-restart').addEventListener('click', restart);
$('upload-stop').addEventListener('click', stopAll);
$('clip-table').addEventListener('click', async (e) => {
  const rel = e.target.closest('[data-delete]')?.dataset.delete;
  const row = rel && rows.get(rel);
  if (!row || busy || !confirm(t('footage.delete_confirm', { name: rel }))) return;
  try {
    await api(`/api/projects/${project}/clips/${row.clipId}`, { method: 'DELETE' });
  } catch (err) {
    showError(err.message);
    return;
  }
  forgetRow(row);
  summary();
  showAnalysis();
});
$('clips-delete-all').addEventListener('click', async () => {
  if (busy || !confirm(t('footage.delete_all_confirm', { n: [...rows.values()].filter((r) => r.clipId).length }))) return;
  try {
    await api(`/api/projects/${project}/clips`, { method: 'DELETE' });
  } catch (err) {
    showError(err.message);
    return;
  }
  for (const row of [...rows.values()]) forgetRow(row);
  $('clip-table').hidden = true;
  summary();
  showAnalysis();
});
const ready = withTimeout(call('capabilities'), 30000).then((c) => { caps = c; return c; },
  (err) => {
    console.error('cutlery: transcoding worker', err);
    showError(t('footage.browser.worker_failed'));
    $('pick-folder').setAttribute('aria-disabled', 'true');
    $('folder-input').disabled = true;
    throw err;
  });
ready.catch(() => {});
window.cutlery = { get caps() { return caps; }, rows, run, restart, ready, workers };   // for tests and debugging
await loadServerState();
try { await ready; } catch { /* shown above */ }
const problems = [];
if (caps) {
  if (!caps.webcodecs) problems.push(t('footage.browser.no_webcodecs'));
  else if (!caps.avc) problems.push(t('footage.browser.no_avc'));
  if (!caps.opfs) problems.push(t('footage.browser.no_opfs'));
  if (caps.webcodecs && !caps.aac) $('browser-notice').textContent = t('footage.browser.no_aac');
  $('browser-notice').hidden = !(caps.webcodecs && !caps.aac);
}
if (problems.length) {
  showError(problems.join(' '));
  $('pick-folder').setAttribute('aria-disabled', 'true');
  $('folder-input').disabled = true;
}
showAnalysis();

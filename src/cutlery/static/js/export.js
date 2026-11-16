// Export page: the footage path on the user's computer, the check of the originals against the manifest (nothing is
// uploaded: only size and fingerprint are compared), the format and the download of the package.
import { load, t } from './i18n.js';
import { fingerprint, sameFingerprint } from './fingerprint.js';
import { size as fmtSize } from './format.js';

const form = document.getElementById('export-form');
const project = form.dataset.project;
const folder = form.dataset.folder;
const $ = (id) => document.getElementById(id);
const store = `cutlery-export-${project}`;
let pickedFolder = null;

await load();

// ------------------------------------------------------------------ remembered settings (per browser)

function remember() {
  try {
    localStorage.setItem(store, JSON.stringify({ os: form.os.value, root: $('root').value }));
  } catch { /* private window: not remembered */ }
}
try {
  const saved = JSON.parse(localStorage.getItem(store) || 'null');
  if (saved) {
    const radio = form.querySelector(`input[name=os][value="${saved.os}"]`);
    if (radio) radio.checked = true;
    $('root').value = saved.root || '';
  }
} catch { /* nothing saved */ }

// ------------------------------------------------------------------ operating system and path

function lastPart(path) {
  return path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || '';
}

function updateWhere() {
  const os = form.os.value;
  $('root').placeholder = $('root').dataset[`placeholder${os[0].toUpperCase()}${os.slice(1)}`] || '';
  const root = $('root').value.trim();
  const warn = $('root-mismatch');
  warn.hidden = !(root && pickedFolder && lastPart(root) !== pickedFolder);
  if (!warn.hidden) warn.textContent = t('export.root.mismatch', { path: lastPart(root), folder: pickedFolder });
  tree();
}

function tree() {
  const os = form.os.value;
  const sep = os === 'windows' ? '\\' : '/';
  const root = ($('root').value.trim() || $('root').placeholder).replace(/[\\/]+$/, '');
  const fmt = form.querySelector('input[name=format]:checked');
  const lines = [
    `${root}${sep}`,
    `├─ …${sep}                ${$('tree').dataset.originals}`,
    `└─ _cutlery${sep}`,
    `   ├─ *.xml`, `   ├─ *.srt`, `   ├─ titles${sep}`, `   ├─ maps${sep}`, `   ├─ music${sep}`,
    `   └─ ${fmt && fmt.value === 'premiere' ? 'IMPORT-PREMIERE.txt' : 'README'}`,
  ];
  $('tree').textContent = lines.join('\n');
}

form.addEventListener('change', (e) => {
  if (e.target.name === 'os' || e.target.name === 'format') updateWhere();
  if (e.target.name === 'format') {
    $('download').textContent = t('export.download.button', { format: e.target.dataset.label });
  }
  remember();
});
$('root').addEventListener('input', () => { updateWhere(); remember(); });
updateWhere();

// ------------------------------------------------------------------ check the originals

async function manifest() {
  const res = await fetch(`/api/projects/${project}/manifest`, { credentials: 'same-origin' });
  return res.json();
}

function relPath(file) {
  return file.webkitRelativePath.split('/').slice(1).join('/');
}

async function check(fileList) {
  const files = [...fileList].filter((f) => !f.webkitRelativePath.split('/').some((p) => p.startsWith('.')));
  if (!files.length) return;
  pickedFolder = files[0].webkitRelativePath.split('/')[0];
  updateWhere();
  const { clips } = await manifest();
  const bySize = new Map();
  for (const f of files) {
    if (!bySize.has(f.size)) bySize.set(f.size, []);
    bySize.get(f.size).push(f);
  }
  const prints = new Map();
  const fp = async (f) => {
    if (!prints.has(f)) prints.set(f, await fingerprint(f));
    return prints.get(f);
  };
  const byPath = new Map(files.map((f) => [relPath(f), f]));
  const relink = {};
  const lines = [];
  let found = 0, renamed = 0, missing = 0, done = 0;
  const progress = $('check-progress');
  progress.hidden = false;
  for (const c of clips) {
    progress.textContent = t('export.check.running', { done: ++done, total: clips.length });
    const same = byPath.get(c.rel_path);
    if (same && same.size === c.size && sameFingerprint(await fp(same), c.fingerprint)) {
      found++;
      continue;
    }
    let hit = null;
    for (const f of bySize.get(c.size) || []) {
      if (f !== same && sameFingerprint(await fp(f), c.fingerprint)) { hit = f; break; }
    }
    if (hit) {
      renamed++;
      relink[c.rel_path] = relPath(hit);
      lines.push(['info', t('export.check.renamed_line', { old: c.rel_path, new: relPath(hit) })]);
    } else {
      missing++;
      lines.push(['error', t(same ? 'export.check.changed_line' : 'export.check.missing_line', { path: c.rel_path })]);
    }
  }
  progress.hidden = true;
  $('relink').value = JSON.stringify(relink);
  $('n-found').textContent = found;
  $('n-renamed').textContent = renamed;
  $('n-missing').textContent = missing;
  $('check-counts').hidden = false;
  $('check-skip').hidden = true;
  const ul = $('check-lines');
  ul.replaceChildren(...lines.map(([kind, text]) => {
    const li = document.createElement('li');
    li.className = kind;
    li.textContent = form.os.value === 'windows' ? text.replaceAll('/', '\\') : text;
    return li;
  }));
  window.cutleryExport.result = { found, renamed, missing, relink };
}

$('check-input').addEventListener('change', (e) => check(e.target.files));

// ------------------------------------------------------------------ download

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const status = $('download-status'), error = $('download-error'), button = $('download');
  error.hidden = true;
  status.hidden = false;
  status.textContent = t('export.download.building');
  button.disabled = true;
  try {
    const res = await fetch(form.action, { method: 'POST', body: new URLSearchParams(new FormData(form)), credentials: 'same-origin' });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.message || body.error || res.statusText);
    }
    const blob = await res.blob();
    const name = /filename="([^"]+)"/.exec(res.headers.get('content-disposition') || '')?.[1] || 'cutlery.zip';
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 60000);
    status.textContent = `${name} · ${fmtSize(blob.size)}`;
  } catch (err) {
    status.hidden = true;
    error.hidden = false;
    error.textContent = t('export.error.failed', { message: err.message });
  } finally {
    button.disabled = false;
  }
});

window.cutleryExport = { check, folder };

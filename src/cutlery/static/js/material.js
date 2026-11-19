// Material page: the footage by day, as in an editor's project window. Tiles (or a list) sorted by recording time,
// grouped by day; moving the mouse across a tile scrubs through the clip; a click opens the source monitor with the
// transcript (a click on a word jumps there) and the user's selection: favourites, rejects and own tags for a whole
// clip or an In/Out range, with a note (selects.json; Claude reads it for the next cut). Tags found by the analysis
// (what is in the picture, objects, camera moves) filter the footage too and show where they are in the clip.
// Ctrl/Shift-click chooses several clips to tag or mark at once. Everything stays on this page: the data comes from
// /api/projects/<id>/material, the pictures from the analysis, the video from the 360p copies.
import { load, t, lang } from './i18n.js';
import { duration as fmtDuration } from './format.js';

const $ = (id) => document.getElementById(id);
const root = $('material');
const project = root.dataset.project;
const FPS = 25;
const store = {
  get(k, d) { try { const v = localStorage.getItem(`cutlery-material-${k}`); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(`cutlery-material-${k}`, JSON.stringify(v)); } catch { /* private window */ } },
};
const S = { clips: [], byId: new Map(), days: [], people: new Map(), cuts: [], selects: [], shown: [], open: null,
  rIn: null, rOut: null, words: [], speed: 0, detail: null, chosen: new Set(), anchor: null,
  hidden: { all: [], clips: {} }, editTags: false };

await load();

async function api(path, options = {}) {
  const res = await fetch(path, { credentials: 'same-origin', headers: { 'content-type': 'application/json' }, ...options });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};
const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
function mmss(s, tenths = true) {
  s = Math.max(0, s || 0);
  const m = Math.floor(s / 60), r = s - m * 60;
  return tenths ? `${m}:${r.toFixed(1).padStart(4, '0')}` : `${m}:${String(Math.floor(r)).padStart(2, '0')}`;
}
const timeOf = (c) => (c.created && c.created.length >= 16 ? c.created.slice(11, 16) : '');
function dayTitle(day) {
  if (!day) return t('material.no_date');
  const d = new Date(`${day}T12:00:00`);
  return new Intl.DateTimeFormat(lang, { weekday: 'short', day: 'numeric', month: 'long', year: 'numeric' }).format(d);
}
const dayShort = (day) => (day ? new Intl.DateTimeFormat(lang, { day: 'numeric', month: 'numeric' }).format(new Date(`${day}T12:00:00`)) : '?');
const marksOf = (cid) => S.selects.filter((s) => s.clip === cid);
const userTagsOf = (cid) => marksOf(cid).filter((s) => s.kind === 'tag');
// tags: "u:<name>" the user's own, "c:" what is in the picture, "o:" objects, "m:" camera moves
const KINDS = [['u', 'material.tags_mine'], ['c', 'material.tags_content'], ['o', 'material.tags_objects'], ['m', 'material.tags_moves']];
const FIELD = { c: 'tags', o: 'objects', m: 'moves' };
function tagLabel(kind, name) {
  if (kind === 'u') return name;
  const key = `tag.${kind}.${name}`, v = t(key);
  return v === key ? name.replaceAll('_', ' ') : v;
}
function clipHas(c, key) {
  const kind = key[0], name = key.slice(2);
  if (kind === 'u') return userTagsOf(c.id).some((m) => m.tag === name);
  return (c[FIELD[kind]] || []).some(([n]) => n === name);
}
const clipUrl = (cid) => `/projects/${project}/analysis/clip/${encodeURIComponent(cid)}.mp4`;
const stripUrl = (cid) => `/projects/${project}/analysis/strips/${encodeURIComponent(cid)}.jpg`;
const posterUrl = (cid) => `/projects/${project}/material/poster/${encodeURIComponent(cid)}.jpg`;
const latestCut = () => S.cuts[S.cuts.length - 1] || null;

// ------------------------------------------------------------------ data

async function refresh() {
  const d = await api(`/api/projects/${project}/material`);
  S.clips = d.clips; S.days = d.days; S.cuts = d.cuts; S.selects = d.selects; S.hidden = d.hidden || { all: [], clips: {} };
  S.byId = new Map(S.clips.map((c) => [c.id, c]));
  S.people = new Map(d.people.map((p) => [p.id, p.name]));
  for (const id of [...S.chosen]) if (!S.byId.has(id)) S.chosen.delete(id);
  $('m-loading').hidden = true;
  $('m-empty').hidden = S.clips.length > 0;
  $('material-bar').hidden = S.clips.length === 0;
  options();
  render();
}

function options() {
  const fill = (sel, items) => {
    const keep = sel.value;
    for (const o of [...sel.options].slice(1)) o.remove();
    for (const [v, label] of items) sel.append(new Option(label, v));
    sel.value = items.some(([v]) => v === keep) ? keep : '';
  };
  const cams = [...new Set(S.clips.map((c) => c.camera))].sort();
  fill($('m-camera'), cams.map((c) => [c, c]));
  fill($('m-person'), [...S.people].map(([id, name]) => [id, name]));
  const latest = latestCut();
  $('m-used').options[1].textContent = latest ? t('material.used_in', { cut: latest }) : t('material.used_yes');
  $('m-used').options[2].textContent = latest ? t('material.unused_in', { cut: latest }) : t('material.used_no');
  tagNames();
}

// the names offered when typing a tag: the user's own first, then what the analysis found in the picture
function tagNames() {
  const dl = $('m-tag-names');
  dl.textContent = '';
  const own = [...new Set(S.selects.filter((s) => s.kind === 'tag').map((s) => s.tag))].sort((a, b) => a.localeCompare(b, lang));
  const found = [...new Set(S.clips.flatMap((c) => (c.tags || []).map(([n]) => tagLabel('c', n))))].sort((a, b) => a.localeCompare(b, lang));
  for (const n of [...own, ...found.filter((x) => !own.includes(x))]) dl.append(new Option(n));
}

// ------------------------------------------------------------------ filters

const F = store.get('filters', {});
for (const [id, key] of [['m-camera', 'camera'], ['m-person', 'person'], ['m-used', 'used'], ['m-mark', 'mark']]) {
  $(id).addEventListener('change', () => { F[key] = $(id).value; store.set('filters', F); render(); });
}
$('m-speech').checked = !!F.speech;
$('m-speech').addEventListener('change', () => { F.speech = $('m-speech').checked; store.set('filters', F); render(); });
let searchTimer = null;
$('m-search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(render, 150); });

function passes(c) {
  const q = $('m-search').value.trim().toLowerCase();
  if (q && !`${c.file} ${c.id} ${c.text}`.toLowerCase().includes(q)) return false;
  if (F.camera && c.camera !== F.camera) return false;
  if (F.person && !c.people.includes(F.person)) return false;
  if (F.speech && !c.words) return false;
  const latest = latestCut();
  if (F.used === 'used' && !c.used.some((u) => u.cut === latest)) return false;
  if (F.used === 'unused' && c.used.some((u) => u.cut === latest)) return false;
  const kinds = new Set(marksOf(c.id).map((m) => m.kind));
  if (F.mark === 'favorite' && !kinds.has('favorite')) return false;
  if (F.mark === 'reject' && !kinds.has('reject')) return false;
  if (F.mark === 'none' && [...kinds].some((k) => k !== 'tag')) return false;
  for (const key of F.tags || []) if (!clipHas(c, key)) return false;
  return true;
}

// ------------------------------------------------------------------ tag filter (all chosen tags must be there)

F.tags = Array.isArray(F.tags) ? F.tags : [];
const pop = $('m-tags-pop'), popBtn = $('m-tags-btn');
function setTags(keys) { F.tags = keys; store.set('filters', F); render(); }
function openPop(open) {
  pop.hidden = !open;
  popBtn.setAttribute('aria-expanded', String(open));
  if (open) { $('m-tags-search').value = ''; tagList(); $('m-tags-search').focus(); }
}
popBtn.addEventListener('click', () => openPop(pop.hidden));
document.addEventListener('pointerdown', (e) => { if (!pop.hidden && !e.target.closest('.tag-filter')) openPop(false); });
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !pop.hidden) { e.stopImmediatePropagation(); openPop(false); popBtn.focus(); }
}, true);
$('m-tags-search').addEventListener('input', tagList);
function counts() {
  const n = new Map();
  const add = (key) => n.set(key, (n.get(key) || 0) + 1);
  for (const c of S.clips) {
    for (const tag of new Set(userTagsOf(c.id).map((m) => m.tag))) add(`u:${tag}`);
    for (const kind of ['c', 'o', 'm']) for (const [name] of c[FIELD[kind]] || []) add(`${kind}:${name}`);
  }
  return n;
}
// managing tags (the popover's "Edit"): own tags renamed or deleted everywhere, automatic ones hidden as wrong
async function tagAction(body) {
  const r = await api(`/api/projects/${project}/tags`, { method: 'POST', body: JSON.stringify(body) });
  S.selects = r.selects;
  if (r.hidden) S.hidden = r.hidden;
  return r;
}
$('m-tags-edit').addEventListener('click', () => {
  S.editTags = !S.editTags;
  $('m-tags-edit').setAttribute('aria-pressed', String(S.editTags));
  $('m-tags-edit').textContent = t(S.editTags ? 'material.tags_edit_done' : 'material.tags_edit');
  $('m-tags-edit-hint').hidden = !S.editTags;
  tagList();
});
async function renameTag(name) {
  const to = (prompt(t('material.tag_rename_prompt', { tag: name }), name) || '').trim();
  if (!to || to === name) return;
  try {
    await tagAction({ action: 'rename', tag: name, to });
    setTags([...new Set(F.tags.map((k) => (k === `u:${name}` ? `u:${to}` : k)))]);
    afterTags();
  } catch (e) { alert(e.message); }
}
async function deleteTag(name, n) {
  if (!confirm(t('material.tag_delete_confirm', { tag: name, n }))) return;
  try {
    await tagAction({ action: 'remove', tag: name });
    setTags(F.tags.filter((k) => k !== `u:${name}`));
    afterTags();
  } catch (e) { alert(e.message); }
}
async function hideTag(key, clip, hidden = true) {
  try {
    await tagAction({ action: 'hide', key, clip: clip ?? null, hidden });
    if (hidden && clip == null) setTags(F.tags.filter((k) => k !== key));
    await refresh();                                   // the clips' tags come without the hidden ones
    afterTags();
  } catch (e) { alert(e.message); }
}
function afterTags() {
  tagNames(); render();
  if (!pop.hidden) tagList();
  if (S.open) { showTags(); track(); reloadDetail(); }
}
function editChip(kind, key, n) {
  const name = key.slice(2);
  const chip = el('span', `tag-chip k-${kind} editing`);
  chip.append(el('span', null, tagLabel(kind, name)), el('span', 'n', String(n)));
  const act = (label, title, fn) => {
    const b = el('button', 'x', label);
    b.type = 'button'; b.title = title; b.setAttribute('aria-label', title);
    b.addEventListener('click', fn);
    chip.append(b);
  };
  if (kind === 'u') {
    act('✎', t('material.tag_rename', { tag: name }), () => renameTag(name));
    act('×', t('material.tag_delete_all', { tag: name }), () => deleteTag(name, n));
  } else act('×', t('material.tag_hide', { tag: tagLabel(kind, name) }), () => hideTag(key));
  return chip;
}
function tagList() {
  const box = $('m-tags-list'), q = $('m-tags-search').value.trim().toLowerCase();
  box.textContent = '';
  const n = counts();
  for (const [kind, title] of KINDS) {
    const keys = [...n.keys()].filter((k) => k[0] === kind && tagLabel(kind, k.slice(2)).toLowerCase().includes(q))
      .sort((a, b) => n.get(b) - n.get(a) || a.localeCompare(b, lang));
    if (!keys.length) continue;
    box.append(el('h4', 'tag-group-title', t(title)));
    const row = el('div', 'tag-chips');
    for (const key of keys.slice(0, 60)) {
      if (S.editTags) { row.append(editChip(kind, key, n.get(key))); continue; }
      const on = F.tags.includes(key);
      const b = el('button', `tag-chip k-${kind}${on ? ' on' : ''}`);
      b.type = 'button';
      b.setAttribute('aria-pressed', String(on));
      b.append(el('span', null, tagLabel(kind, key.slice(2))), el('span', 'n', String(n.get(key))));
      b.dataset.key = key;
      b.addEventListener('click', () => {
        setTags(on ? F.tags.filter((x) => x !== key) : [...F.tags, key]);
        tagList();
        box.querySelector(`.tag-chip[data-key="${CSS.escape(key)}"]`)?.focus();     // the list is new: keep the place
      });
      row.append(b);
    }
    box.append(row);
  }
  if (S.editTags && S.hidden.all.length) {                 // hidden everywhere: shown again with a click
    box.append(el('h4', 'tag-group-title', t('material.tags_hidden')));
    const row = el('div', 'tag-chips');
    for (const key of S.hidden.all) {
      const b = el('button', `tag-chip k-${key[0]} hidden-tag`);
      b.type = 'button';
      b.title = t('material.tag_show');
      b.append(el('span', null, tagLabel(key[0], key.slice(2))), el('span', 'x', '↺'));
      b.addEventListener('click', () => hideTag(key, null, false));
      row.append(b);
    }
    box.append(row);
  }
  if (!box.children.length) box.append(el('p', 'small muted', S.clips.length ? t('material.tags_none_found') : t('material.tags_none')));
}
function activeTags() {
  const box = $('m-active-tags');
  box.textContent = '';
  box.hidden = !F.tags.length;
  $('m-tags-n').textContent = F.tags.length ? String(F.tags.length) : '';
  for (const key of F.tags) {
    const chip = el('button', `tag-chip k-${key[0]} on`);
    chip.type = 'button';
    chip.title = t('material.tag_remove_filter');
    chip.append(el('span', null, tagLabel(key[0], key.slice(2))), el('span', 'x', '×'));
    chip.addEventListener('click', () => setTags(F.tags.filter((x) => x !== key)));
    box.append(chip);
  }
  if (F.tags.length > 1) {
    const clear = el('button', 'link-button small', t('material.tags_clear'));
    clear.type = 'button';
    clear.addEventListener('click', () => setTags([]));
    box.append(clear);
  }
}

// ------------------------------------------------------------------ view

let view = store.get('view', 'grid');
let size = store.get('size', 220);
$('m-size').value = size;
function setView(v) {
  view = v; store.set('view', v);
  $('m-grid').setAttribute('aria-pressed', String(v === 'grid'));
  $('m-list').setAttribute('aria-pressed', String(v === 'list'));
  $('m-size').disabled = v !== 'grid';
  render();
}
$('m-grid').setAttribute('aria-pressed', String(view === 'grid'));
$('m-list').setAttribute('aria-pressed', String(view === 'list'));
$('m-size').disabled = view !== 'grid';
$('m-grid').addEventListener('click', () => setView('grid'));
$('m-list').addEventListener('click', () => setView('list'));
$('m-size').addEventListener('input', () => {
  size = Number($('m-size').value); store.set('size', size);
  root.style.setProperty('--tile', `${size}px`);
});
root.style.setProperty('--tile', `${size}px`);

function render() {
  for (const [id, key] of [['m-camera', 'camera'], ['m-person', 'person'], ['m-used', 'used'], ['m-mark', 'mark']]) $(id).value = F[key] || '';
  S.shown = S.clips.filter(passes);
  activeTags();
  selection();
  $('m-count').textContent = t('material.count', { n: S.shown.length, all: S.clips.length });
  const body = $('m-body');
  body.textContent = '';
  const chips = $('m-days');
  chips.textContent = '';
  const groups = [];
  for (const c of S.shown) {
    if (!groups.length || groups[groups.length - 1].day !== c.day) groups.push({ day: c.day, clips: [] });
    groups[groups.length - 1].clips.push(c);
  }
  for (const g of groups) {
    const id = `day-${g.day || 'none'}`;
    const secs = g.clips.reduce((a, c) => a + (c.duration || 0), 0);
    const chip = el('a', 'day-chip', `${dayShort(g.day)} · ${g.clips.length}`);
    chip.href = `#${id}`;
    chip.title = dayTitle(g.day);
    chips.append(chip);
    const sec = el('section', 'day');
    sec.id = id;
    const head = el('h2', 'day-head');
    head.append(el('span', null, dayTitle(g.day)),
      el('span', 'small muted', t(g.clips.length === 1 ? 'material.day_sum_one' : 'material.day_sum', { n: g.clips.length, time: fmtDuration(secs) })));
    sec.append(head);
    sec.append(view === 'grid' ? grid(g.clips) : table(g.clips));
    body.append(sec);
  }
  if (!groups.length && S.clips.length) body.append(el('p', 'muted', t('material.nothing_found')));
  observe();
}

// ------------------------------------------------------------------ tiles

function badges(c) {
  const box = el('span', 'tile-badges');
  if (c.words) box.append(el('span', 'badge', `🗣 ${c.words}`));
  if (c.people.length) box.append(el('span', 'badge', `👤 ${c.people.map((p) => S.people.get(p) || p).join(', ')}`));
  const latest = latestCut();
  const used = c.used.filter((u) => u.cut === latest);
  if (used.length) box.append(el('span', 'badge used', `✂ ${latest} · ${used.map((u) => u.nr).join(', ')}`));
  const kinds = new Set(marksOf(c.id).map((m) => m.kind));
  if (kinds.has('favorite')) box.append(el('span', 'badge fav', '★'));
  if (kinds.has('reject')) box.append(el('span', 'badge rej', '⊘'));
  // tags: the user's own (accent) first, then what is in the picture; three at most, "+n" for the rest
  const own = [...new Set(userTagsOf(c.id).map((m) => m.tag))].map((n) => ['u', n]);
  const found = (c.tags || []).map(([n]) => ['c', n]);
  const all = [...own, ...found];
  for (const [kind, name] of all.slice(0, 3)) box.append(el('span', `badge ${kind === 'u' ? 'utag' : 'atag'}`, tagLabel(kind, name)));
  if (all.length > 3) {
    const more = el('span', 'badge atag', `+${all.length - 3}`);
    more.title = all.slice(3).map(([k, n]) => tagLabel(k, n)).join(', ');
    box.append(more);
  }
  return box;
}

function qualityBar(c, cls = 'qbar') {
  const bar = el('div', cls);
  const d = c.duration || 1;
  const seg = (a, b, kind, title) => {
    const s = el('span', kind);
    s.style.left = `${(a / d) * 100}%`;
    s.style.width = `${Math.max(0.5, ((b - a) / d) * 100)}%`;
    if (title) s.title = title;
    bar.append(s);
  };
  for (const [a, b, steady] of c.usable) seg(a, b, steady ? 'q-steady' : 'q-usable', steady ? t('material.q_steady') : t('material.q_usable'));
  for (const [a, b, why] of c.flaws) seg(a, b, 'q-flaw', why.map((w) => t(`material.flaw.${w}`)).join(', '));
  return bar;
}

function grid(clips) {
  const g = el('div', 'tiles');
  for (const c of clips) {
    const tile = el('div', S.chosen.has(c.id) ? 'tile chosen' : 'tile');
    tile.tabIndex = 0;
    tile.dataset.id = c.id;
    tile.setAttribute('role', 'button');
    tile.setAttribute('aria-label', `${c.file} ${timeOf(c)}`);
    const pic = el('div', 'tile-pic');
    const frame = el('div', 'tile-frame');
    frame.style.aspectRatio = `${c.width} / ${c.height}`;
    if (c.width / c.height < 16 / 9 - 0.01) frame.classList.add('portrait');     // taller than the tile: fit the height
    frame.dataset.poster = posterUrl(c.id);
    if (c.strip) frame.dataset.strip = stripUrl(c.id);
    pic.append(frame);
    pic.append(el('span', 'tile-time', timeOf(c)), el('span', 'tile-dur', mmss(c.duration, false)));
    const pick = el('input', 'tile-pick');
    pick.type = 'checkbox';
    pick.checked = S.chosen.has(c.id);
    pick.setAttribute('aria-label', t('material.sel_clip', { file: c.file }));
    pick.addEventListener('click', (e) => { e.stopPropagation(); choose(c.id, e.shiftKey ? 'range' : 'toggle'); });
    pic.append(pick);
    tile.append(pic, qualityBar(c));
    const name = el('div', 'tile-name', c.file);
    name.title = `${c.id} · ${c.rel_path} · ${c.camera}`;
    tile.append(name, badges(c));
    tile.addEventListener('click', (e) => {
      if (e.shiftKey) choose(c.id, 'range');
      else if (e.ctrlKey || e.metaKey) choose(c.id, 'toggle');
      else if (S.chosen.size) choose(c.id, 'toggle');                 // while choosing, a click adds or removes
      else openMonitor(c.id);
    });
    tile.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); openMonitor(c.id); }
      if (e.key === ' ') { e.preventDefault(); choose(c.id, e.shiftKey ? 'range' : 'toggle'); }
    });
    pic.addEventListener('mouseenter', () => scrubStart(pic, c));
    pic.addEventListener('mousemove', (e) => scrubMove(pic, c, e));
    pic.addEventListener('mouseleave', () => scrubEnd(pic));
    g.append(tile);
  }
  return g;
}

// the poster: a still from the middle of the clip in the stand-in's own size; while it is not there (or cannot be
// made) the middle picture of the clip's film strip (five small pictures side by side, 3 px apart)
const io = 'IntersectionObserver' in window ? new IntersectionObserver((entries) => {
  for (const e of entries) {
    if (!e.isIntersecting) continue;
    io.unobserve(e.target);
    poster(e.target);
  }
}, { rootMargin: '400px' }) : null;
function observe() {
  for (const f of document.querySelectorAll('.tile-frame[data-poster]')) (io ? io.observe(f) : poster(f));
}
function poster(frame) {
  const img = new Image();
  img.onload = () => {
    frame.style.backgroundImage = `url("${img.src}")`;
    frame.style.backgroundSize = 'cover';
    frame.style.backgroundPosition = 'center';
    frame.classList.add('sharp');
  };
  img.onerror = () => { if (frame.dataset.strip) fromStrip(frame); };
  img.src = frame.dataset.poster;
}
function fromStrip(frame) {
  const img = new Image();
  img.onload = () => {
    const h = img.naturalHeight, ratio = frame.clientWidth / Math.max(1, frame.clientHeight);
    const fw = Math.round(h * ratio);
    const n = Math.max(1, Math.round((img.naturalWidth + 3) / (fw + 3)));
    const k = Math.floor(n / 2);
    frame.style.backgroundImage = `url("${img.src}")`;
    frame.style.backgroundSize = `auto 100%`;
    const scale = frame.clientHeight / h;
    frame.style.backgroundPosition = `${-k * (fw + 3) * scale}px 0`;
  };
  img.src = frame.dataset.strip;
}

// hover scrub: a muted video in the tile follows the pointer
let scrub = null;
function scrubStart(pic, c) {
  scrubEnd();
  const v = el('video', 'tile-video');
  v.muted = true; v.preload = 'auto'; v.playsInline = true;
  v.src = clipUrl(c.id);
  pic.append(v);
  scrub = { pic, v, want: null, busy: false };
}
function scrubMove(pic, c, e) {
  if (!scrub || scrub.pic !== pic) scrubStart(pic, c);
  const r = pic.getBoundingClientRect();
  const x = clamp((e.clientX - r.left) / r.width, 0, 1);
  scrub.want = x * Math.max(0, (c.duration || 0) - 0.05);
  pic.style.setProperty('--scrub', `${x * 100}%`);
  pic.classList.add('scrubbing');
  const go = () => {
    if (!scrub || scrub.busy || scrub.want == null || scrub.v.readyState < 1) return;
    scrub.busy = true;
    scrub.v.currentTime = scrub.want;
    scrub.want = null;
  };
  scrub.v.onseeked = () => { if (scrub) { scrub.busy = false; scrub.v.classList.add('ready'); go(); } };
  scrub.v.onloadedmetadata = go;
  go();
}
function scrubEnd() {
  if (!scrub) return;
  scrub.v.removeAttribute('src'); scrub.v.load(); scrub.v.remove();
  scrub.pic.classList.remove('scrubbing');
  scrub = null;
}

// ------------------------------------------------------------------ several clips at once

function choose(cid, how) {
  if (how === 'range' && S.anchor && S.anchor !== cid) {
    const ids = S.shown.map((c) => c.id), a = ids.indexOf(S.anchor), b = ids.indexOf(cid);
    if (a >= 0 && b >= 0) for (const id of ids.slice(Math.min(a, b), Math.max(a, b) + 1)) S.chosen.add(id);
  } else if (S.chosen.has(cid)) S.chosen.delete(cid);
  else S.chosen.add(cid);
  S.anchor = cid;
  for (const tile of document.querySelectorAll('.tile[data-id]')) {
    const on = S.chosen.has(tile.dataset.id);
    tile.classList.toggle('chosen', on);
    const box = tile.querySelector('.tile-pick');
    if (box) box.checked = on;
  }
  for (const row of document.querySelectorAll('.material-table tr[data-id]')) row.classList.toggle('chosen', S.chosen.has(row.dataset.id));
  selection();
}
function selection() {
  const n = S.chosen.size;
  $('m-selbar').hidden = !n;
  $('m-sel-hint').hidden = n > 0 || !S.clips.length;
  if (n) $('m-sel-n').textContent = t(n === 1 ? 'material.sel_one' : 'material.sel_n', { n });
}
async function addMany(items, done) {
  try {
    const r = await api(`/api/projects/${project}/selects`, { method: 'POST', body: JSON.stringify({ items }) });
    for (const s of r.selects) if (!S.selects.some((x) => x.id === s.id)) S.selects.push(s);
    tagNames();
    render();
    flashBar(done);
  } catch (e) { alert(e.message); }
}
function flashBar(text) {
  const n = $('m-sel-n'), old = n.textContent;
  n.textContent = text;
  setTimeout(() => { if (n.textContent === text) selection(); }, 1600);
  if (!old) selection();
}
$('m-sel-form').addEventListener('submit', (e) => {
  e.preventDefault();
  const tag = $('m-sel-tag').value.trim();
  if (!tag || !S.chosen.size) { $('m-sel-tag').focus(); return; }
  addMany([...S.chosen].map((clip) => ({ clip, kind: 'tag', tag })), t('material.tagged_n', { tag, n: S.chosen.size }));
  $('m-sel-tag').value = '';
});
$('m-sel-untag').addEventListener('click', async () => {
  const tag = $('m-sel-tag').value.trim();
  if (!tag || !S.chosen.size) { $('m-sel-tag').focus(); return; }
  try {
    const r = await tagAction({ action: 'remove', tag, clips: [...S.chosen] });
    $('m-sel-tag').value = '';
    afterTags();
    flashBar(t('material.untagged_n', { tag, n: r.removed }));
  } catch (e) { alert(e.message); }
});
$('m-sel-fav').addEventListener('click', () => addMany([...S.chosen].map((clip) => ({ clip, kind: 'favorite' })), t('material.saved_favorite')));
$('m-sel-rej').addEventListener('click', () => addMany([...S.chosen].map((clip) => ({ clip, kind: 'reject' })), t('material.saved_reject')));
$('m-sel-clear').addEventListener('click', () => { S.chosen.clear(); S.anchor = null; render(); });
document.addEventListener('keydown', (e) => {
  if (mon.open || !S.chosen.size || e.key !== 'Escape' || !pop.hidden) return;
  S.chosen.clear(); S.anchor = null; render();
});

// ------------------------------------------------------------------ list

function table(clips) {
  const tb = el('table', 'material-table');
  const head = tb.createTHead().insertRow();
  for (const k of ['time', 'file', 'duration', 'camera', 'speech', 'people', 'tags', 'used', 'mark']) head.append(el('th', null, t(`material.col.${k}`)));
  const body = tb.createTBody();
  const latest = latestCut();
  for (const c of clips) {
    const r = body.insertRow();
    if (S.chosen.has(c.id)) r.className = 'chosen';
    r.tabIndex = 0;
    r.dataset.id = c.id;
    const kinds = new Set(marksOf(c.id).map((m) => m.kind));
    const used = c.used.filter((u) => u.cut === latest).map((u) => u.nr);
    for (const v of [timeOf(c), c.file, mmss(c.duration, false), c.camera, c.words ? String(c.words) : '',
      c.people.map((p) => S.people.get(p) || p).join(', '),
      [...new Set(userTagsOf(c.id).map((m) => m.tag)), ...(c.tags || []).slice(0, 3).map(([n]) => tagLabel('c', n))].join(', '),
      used.length ? `${latest} · ${used.join(', ')}` : '',
      `${kinds.has('favorite') ? '★' : ''}${kinds.has('reject') ? '⊘' : ''}`]) r.insertCell().textContent = v;
    r.addEventListener('click', (e) => {
      if (e.shiftKey) choose(c.id, 'range');
      else if (e.ctrlKey || e.metaKey || S.chosen.size) choose(c.id, 'toggle');
      else openMonitor(c.id);
    });
    r.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') openMonitor(c.id);
      if (e.key === ' ') { e.preventDefault(); choose(c.id, 'toggle'); }
    });
  }
  const wrap = el('div', 'table-wrap');
  wrap.append(tb);
  return wrap;
}

// ------------------------------------------------------------------ source monitor

const mon = $('monitor'), video = $('mon-video');
function current() { return S.open ? S.byId.get(S.open) : null; }

async function openMonitor(cid) {
  scrubEnd();
  const c = S.byId.get(cid);
  if (!c) return;
  S.open = cid; S.rIn = S.rOut = null; S.words = []; S.detail = null;
  stopShuttle();
  $('mon-title').textContent = c.file;
  const meta = [c.created ? `${dayTitle(c.day)}, ${timeOf(c)}` : t('material.no_date'), mmss(c.duration), c.camera,
    `${c.width}×${c.height}`, c.id];
  if (c.people.length) meta.push(`👤 ${c.people.map((p) => S.people.get(p) || p).join(', ')}`);
  $('mon-meta').textContent = meta.join(' · ');
  const used = $('mon-used');
  used.textContent = '';
  for (const u of c.used) {
    const li = el('li');
    const a = el('a', null, t(u.cutaway ? 'material.used_cutaway' : 'material.used_shot', { cut: u.cut, nr: u.nr, a: mmss(u.in), b: mmss(u.out) }));
    a.href = `/projects/${project}/review?cut=${encodeURIComponent(u.cut)}`;
    li.append(a);
    used.append(li);
  }
  video.src = clipUrl(cid);
  video.currentTime = 0;
  $('mon-note').value = '';
  if (!mon.open) mon.showModal();
  showMarks(); showRange(); track(); showTags();
  reloadDetail();
  const tr = $('mon-transcript');
  tr.textContent = '';
  tr.append(el('p', 'small muted', t('material.loading')));
  try {
    const d = await api(`/api/projects/${project}/material/${encodeURIComponent(cid)}/transcript`);
    if (S.open !== cid) return;
    transcript(d);
  } catch (e) { tr.textContent = e.message; }
}
function closeMonitor() {
  video.pause(); stopShuttle();
  video.removeAttribute('src'); video.load();
  S.open = null;
  if (mon.open) mon.close();
  render();
}
$('mon-close').addEventListener('click', closeMonitor);
mon.addEventListener('cancel', (e) => {
  e.preventDefault();                                    // Esc: first the range, then the monitor
  if (S.rIn != null || S.rOut != null) { S.rIn = S.rOut = null; showRange(); } else closeMonitor();
});

function transcript(d) {
  const tr = $('mon-transcript');
  tr.textContent = '';
  S.words = [];
  if (!d.segments.length) { tr.append(el('p', 'small muted', t('material.no_speech'))); return; }
  for (const s of d.segments) {
    const p = el('p', s.unsure ? 'seg unsure' : 'seg');
    const at = el('button', 'seg-time mono', mmss(s.start));
    at.type = 'button';
    at.addEventListener('click', () => seek(s.start));
    p.append(at, ' ');
    if (s.words.length) {
      for (const [a, b, w] of s.words) {
        const span = el('span', 'word', w);
        span.dataset.t = a;
        span.addEventListener('click', () => seek(a));
        p.append(span);
        S.words.push({ a, b, span });
      }
    } else p.append(document.createTextNode(s.text));
    if (s.unsure) p.title = t('material.unsure');
    tr.append(p);
  }
}

// marks of this clip: list with delete and a note that can be changed
function showMarks() {
  const ul = $('mon-marks');
  ul.textContent = '';
  for (const m of marksOf(S.open).filter((x) => x.kind !== 'tag')) {
    const li = el('li', `mark ${m.kind}`);
    const label = el('button', 'mark-label', `${m.kind === 'favorite' ? '★' : '⊘'} ${m.in == null ? t('material.whole_clip') : `${mmss(m.in)}–${mmss(m.out)}`}`);
    label.type = 'button';
    label.title = t('material.mark_jump');
    label.addEventListener('click', () => seek(m.in ?? 0));
    const note = el('input');
    note.type = 'text'; note.maxLength = 500; note.value = m.note || ''; note.placeholder = t('material.note_placeholder');
    note.setAttribute('aria-label', t('material.note'));
    note.addEventListener('change', async () => {
      try { Object.assign(m, await api(`/api/projects/${project}/selects/${m.id}`, { method: 'PATCH', body: JSON.stringify({ note: note.value }) })); } catch (e) { alert(e.message); }
    });
    const del = el('button', 'mark-del', '×');
    del.type = 'button';
    del.title = t('material.mark_delete');
    del.addEventListener('click', async () => {
      try {
        await api(`/api/projects/${project}/selects/${m.id}`, { method: 'DELETE' });
        S.selects = S.selects.filter((x) => x.id !== m.id);
        showMarks(); track();
      } catch (e) { alert(e.message); }
    });
    li.append(label, note, del);
    ul.append(li);
  }
}
function reloadDetail() {
  const cid = S.open;
  if (!cid) return;
  api(`/api/projects/${project}/material/${encodeURIComponent(cid)}/tags`).then((d) => {
    if (S.open !== cid) return;
    S.detail = d;
    showTags();
  }).catch(() => {});
}

// tags of this clip: the user's own (with range, ×), the field to add one, and what the analysis found; pointing at
// a tag shows where it is on the bar under the picture, a click jumps there
function showTags() {
  const c = current();
  if (!c) return;
  const own = $('mon-utags');
  own.textContent = '';
  for (const m of userTagsOf(c.id)) {
    const chip = el('span', 'tag-chip k-u on');
    const go = el('button', 'tag-go', `${m.tag}${m.in == null ? '' : ` · ${mmss(m.in)}–${mmss(m.out)}`}`);
    go.type = 'button';
    go.title = m.in == null ? t('material.whole_clip') : t('material.mark_jump');
    go.addEventListener('click', () => seek(m.in ?? 0));
    hover(go, m.in == null ? [[0, c.duration]] : [[m.in, m.out]]);
    const del = el('button', 'x', '×');
    del.type = 'button';
    del.title = t('material.tag_delete', { tag: m.tag });
    del.setAttribute('aria-label', del.title);
    del.addEventListener('click', async () => {
      try {
        await api(`/api/projects/${project}/selects/${m.id}`, { method: 'DELETE' });
        S.selects = S.selects.filter((x) => x.id !== m.id);
        showTags(); track(); tagNames();
      } catch (e) { alert(e.message); }
    });
    chip.append(go, del);
    own.append(chip);
  }
  if (!own.children.length) own.append(el('span', 'small muted', t('material.tags_own_none')));
  scope();
  const box = $('mon-atags');
  box.textContent = '';
  const d = S.detail;
  if (!d) return;
  for (const [kind, title] of KINDS.slice(1)) {
    const items = Object.entries(d[FIELD[kind]] || {}).map(([name, rs]) => [name, rs, rs.reduce((a, [x, y]) => a + y - x, 0)])
      .sort((a, b) => b[2] - a[2]);
    if (!items.length) continue;
    box.append(el('h4', 'tag-group-title', t(title)));
    const row = el('div', 'tag-chips');
    for (const [name, rs, secs] of items) {
      const b = el('button', `tag-chip k-${kind}`);
      b.type = 'button';
      b.append(el('span', null, tagLabel(kind, name)), el('span', 'n', `${Math.round(secs)} s`));
      b.title = rs.map(([x, y]) => `${mmss(x)}–${mmss(y)}`).join(', ');
      b.addEventListener('click', () => {           // to the next place after the playhead, then round again
        const next = rs.find(([x]) => x > video.currentTime + 0.2) || rs[0];
        seek(next[0]);
      });
      hover(b, rs);
      const wrap = el('span', 'tag-pair');
      const x = el('button', 'tag-hide', '×');
      x.type = 'button';
      x.title = t('material.tag_hide_clip', { tag: tagLabel(kind, name) });
      x.setAttribute('aria-label', x.title);
      x.addEventListener('click', () => hideTag(`${kind}:${name}`, c.id));
      wrap.append(b, x);
      row.append(wrap);
    }
    box.append(row);
  }
}
function hover(button, ranges) {
  const show = (on) => {
    const hl = $('mon-hl'), c = current();
    hl.textContent = '';
    if (!on || !c) return;
    const d = c.duration || 1;
    for (const [a, b] of ranges) {
      const s = el('span', 'hl');
      s.style.left = `${(a / d) * 100}%`;
      s.style.width = `${Math.max(0.4, ((b - a) / d) * 100)}%`;
      hl.append(s);
    }
  };
  button.addEventListener('mouseenter', () => show(true));
  button.addEventListener('mouseleave', () => show(false));
  button.addEventListener('focus', () => show(true));
  button.addEventListener('blur', () => show(false));
}
function scope() {
  const c = current();
  if (!c) return;
  $('mon-tag-scope').textContent = S.rIn != null || S.rOut != null
    ? t('material.tag_scope_range', { a: mmss(S.rIn ?? 0), b: mmss(S.rOut ?? c.duration) })
    : t('material.tag_scope_clip');
}
$('mon-tag-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const c = current(), input = $('mon-tag'), tag = input.value.trim();
  if (!c || !tag) return;
  const body = { clip: c.id, kind: 'tag', tag };
  if (S.rIn != null || S.rOut != null) {
    const a = S.rIn ?? 0, b = S.rOut ?? c.duration;
    if (b > a) Object.assign(body, { in: a, out: b });
  }
  try {
    const s = await api(`/api/projects/${project}/selects`, { method: 'POST', body: JSON.stringify(body) });
    if (!S.selects.some((x) => x.id === s.id)) S.selects.push(s);
    input.value = '';
    S.rIn = S.rOut = null;
    showRange(); showTags(); track(); tagNames();
    flash(t('material.tagged', { tag }));
    input.blur();
  } catch (err) { alert(err.message); }
});

async function mark(kind) {
  const c = current();
  if (!c) return;
  const body = { clip: c.id, kind, note: $('mon-note').value };
  if (S.rIn != null || S.rOut != null) {
    const a = S.rIn ?? 0, b = S.rOut ?? c.duration;
    if (b > a) Object.assign(body, { in: a, out: b });
  }
  try {
    const s = await api(`/api/projects/${project}/selects`, { method: 'POST', body: JSON.stringify(body) });
    S.selects.push(s);
    $('mon-note').value = '';
    S.rIn = S.rOut = null;
    showRange(); showMarks(); track();
    flash(kind === 'favorite' ? t('material.saved_favorite') : t('material.saved_reject'));
  } catch (e) { alert(e.message); }
}
$('mon-fav').addEventListener('click', () => mark('favorite'));
$('mon-rej').addEventListener('click', () => mark('reject'));
function flash(text) {
  const k = $('mon-keys'), old = k.dataset.text || k.textContent;
  k.dataset.text = old;
  k.textContent = text;
  k.classList.add('flash');
  setTimeout(() => { k.textContent = old; k.classList.remove('flash'); }, 1800);
}

// the clip's bar under the picture: usable/steady (green), flaws (red), marks (gold / grey), range, playhead
function track() {
  const c = current();
  if (!c) return;
  const q = $('mon-quality');
  q.textContent = '';
  q.append(qualityBar(c, 'qbar big'));
  const sl = $('mon-selects');
  sl.textContent = '';
  const d = c.duration || 1;
  const tl = $('mon-tagl');
  tl.textContent = '';
  for (const m of userTagsOf(c.id)) {
    const s = el('span', 'msel tag');
    const a = m.in ?? 0, b = m.out ?? d;
    s.style.left = `${(a / d) * 100}%`;
    s.style.width = `${((b - a) / d) * 100}%`;
    s.title = m.tag;
    tl.append(s);
  }
  for (const m of marksOf(c.id).filter((x) => x.kind !== 'tag')) {
    const s = el('span', `msel ${m.kind}`);
    const a = m.in ?? 0, b = m.out ?? d;
    s.style.left = `${(a / d) * 100}%`;
    s.style.width = `${((b - a) / d) * 100}%`;
    s.title = m.note || (m.kind === 'favorite' ? t('material.favorite') : t('material.reject'));
    sl.append(s);
  }
  head();
}
function head() {
  const c = current();
  if (!c) return;
  const d = c.duration || video.duration || 1;
  $('mon-head').style.left = `${(clamp(video.currentTime, 0, d) / d) * 100}%`;
  $('mon-time').textContent = `${mmss(video.currentTime)} / ${mmss(d)}`;
  $('mon-play').textContent = video.paused ? '▶' : '❚❚';
  const x = video.currentTime;
  for (const w of S.words) w.span.classList.toggle('now', x >= w.a && x < w.b);
}
function showRange() {
  const c = current(), r = $('mon-range');
  scope();
  if (!c || (S.rIn == null && S.rOut == null)) { r.hidden = true; $('mon-range-text').textContent = ''; return; }
  const d = c.duration || 1, a = S.rIn ?? 0, b = S.rOut ?? d;
  r.hidden = false;
  r.style.left = `${(a / d) * 100}%`;
  r.style.width = `${(Math.max(0, b - a) / d) * 100}%`;
  $('mon-range-text').textContent = t('material.range', { a: mmss(a), b: mmss(b) });
}
const setIn = () => { S.rIn = video.currentTime; if (S.rOut != null && S.rOut <= S.rIn) S.rOut = null; showRange(); };
const setOut = () => { S.rOut = video.currentTime; if (S.rIn != null && S.rIn >= S.rOut) S.rIn = null; showRange(); };
$('mon-in').addEventListener('click', setIn);
$('mon-out').addEventListener('click', setOut);

const trackEl = $('mon-track');
function seekAt(e) {
  const c = current(), r = trackEl.getBoundingClientRect();
  seek(clamp((e.clientX - r.left) / r.width, 0, 1) * (c.duration || video.duration || 0));
}
trackEl.addEventListener('pointerdown', (e) => { trackEl.setPointerCapture(e.pointerId); trackEl.dragging = true; seekAt(e); });
trackEl.addEventListener('pointermove', (e) => { if (trackEl.dragging) seekAt(e); });
trackEl.addEventListener('pointerup', () => { trackEl.dragging = false; });

function seek(x) {
  const c = current();
  video.currentTime = clamp(x, 0, Math.max(0, (c?.duration || video.duration || 0) - 0.5 / FPS));
  head();
}
function toggle() { stopShuttle(); if (video.paused) video.play().catch(() => {}); else video.pause(); }
function frame(n) { video.pause(); stopShuttle(); seek(video.currentTime + n / FPS); }
// J K L: backwards, stop, forwards; pressed again: faster
let shuttle = null;
function stopShuttle() { if (shuttle) { clearInterval(shuttle); shuttle = null; } S.speed = 0; video.playbackRate = 1; }
function forwards() {
  if (S.speed < 0) stopShuttle();
  S.speed = S.speed > 0 ? Math.min(8, S.speed * 2) : 1;
  video.playbackRate = S.speed;
  video.play().catch(() => {});
}
function backwards() {
  video.pause();
  if (S.speed > 0) stopShuttle();
  S.speed = S.speed < 0 ? Math.max(-8, S.speed * 2) : -1;
  if (shuttle) clearInterval(shuttle);
  shuttle = setInterval(() => {
    const x = video.currentTime + S.speed * 0.05;
    if (x <= 0) { seek(0); stopShuttle(); } else seek(x);
  }, 50);
}
function stop() { stopShuttle(); video.pause(); }
function step(d) {
  const i = S.shown.findIndex((c) => c.id === S.open);
  const next = S.shown[i + d];
  if (next) openMonitor(next.id);
}
$('mon-play').addEventListener('click', toggle);
$('mon-prev').addEventListener('click', () => step(-1));
$('mon-next').addEventListener('click', () => step(1));
video.addEventListener('timeupdate', head);
video.addEventListener('play', head);
video.addEventListener('pause', head);
video.addEventListener('seeked', head);
video.addEventListener('click', toggle);

document.addEventListener('keydown', (e) => {
  if (!mon.open) return;
  const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName);
  if (typing) {
    if (e.key === 'Enter' && e.target.id === 'mon-note') { e.preventDefault(); mark('favorite'); }
    if (e.key === 'Escape' && e.target.id === 'mon-tag') { e.preventDefault(); e.target.value = ''; e.target.blur(); }
    return;
  }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const fn = {
    ' ': toggle, k: stop, l: forwards, j: backwards, i: setIn, o: setOut, f: () => mark('favorite'), x: () => mark('reject'),
    n: () => $('mon-note').focus(), t: () => $('mon-tag').focus(), ArrowLeft: () => frame(e.shiftKey ? -5 : -1), ArrowRight: () => frame(e.shiftKey ? 5 : 1),
    ArrowUp: () => step(-1), ArrowDown: () => step(1), Home: () => seek(0), End: () => seek(current()?.duration || 0),
  }[e.key.length === 1 ? e.key.toLowerCase() : e.key];
  if (!fn) return;
  e.preventDefault();
  fn();
});

// ------------------------------------------------------------------ start

refresh().catch((e) => { $('m-loading').textContent = e.message; });
window.cutleryMaterial = { state: S, openMonitor };

// Analysis view: filter the clips by ID, path or spoken words; play a clip's 360p copy; contact sheets and overview
// pages in a lightbox (arrow keys go through the visible ones, Escape closes).
import { load, t } from './i18n.js';

// ------------------------------------------------------------------ player

document.addEventListener('click', (e) => {
  const b = e.target.closest('.js-play');
  if (!b) return;
  const box = document.getElementById(b.dataset.target);
  const open = box.querySelector('video');
  if (open) {
    open.pause();
    open.remove();
    b.setAttribute('aria-pressed', 'false');
    return;
  }
  for (const v of document.querySelectorAll('.clip-media video')) v.pause();     // one at a time
  const v = document.createElement('video');
  v.controls = true;
  v.autoplay = true;
  v.preload = 'metadata';
  v.playsInline = true;
  v.src = b.dataset.src;
  box.append(v);
  b.setAttribute('aria-pressed', 'true');
});

// ------------------------------------------------------------------ delete

document.addEventListener('click', async (e) => {
  const b = e.target.closest('.js-delete');
  if (!b) return;
  await load();
  if (!confirm(t('footage.delete_confirm', { name: b.dataset.name }))) return;
  const pid = document.getElementById('clip-cards').dataset.project;
  const res = await fetch(`/api/projects/${pid}/clips/${b.dataset.clip}`, { method: 'DELETE', credentials: 'same-origin' });
  const err = document.getElementById('analysis-error');
  if (!res.ok) {
    err.textContent = (await res.json().catch(() => ({}))).error || res.statusText;
    err.hidden = false;
    return;
  }
  err.hidden = true;
  b.closest('.clip-card').remove();
  const n = document.querySelectorAll('.clip-card:not([hidden])').length;
  document.getElementById('filter-count').textContent = t('analysis.count', { n });
});

// ------------------------------------------------------------------ lightbox

const box = document.getElementById('lightbox');
let group = [], index = 0;
function show(i) {
  index = (i + group.length) % group.length;
  const a = group[index];
  document.getElementById('lightbox-img').src = a.href;
  document.getElementById('lightbox-img').alt = a.querySelector('img')?.alt || '';
  document.getElementById('lightbox-caption').textContent = a.dataset.caption || a.querySelector('img')?.alt || '';
  const many = group.length > 1;
  document.getElementById('lightbox-prev').hidden = !many;
  document.getElementById('lightbox-next').hidden = !many;
}
if (box) {
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a.js-lightbox');
    if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.button) return;     // a new tab stays possible
    e.preventDefault();
    group = [...document.querySelectorAll(`a.js-lightbox[data-group="${a.dataset.group}"]`)]
      .filter((x) => !x.closest('[hidden]'));
    show(group.indexOf(a));
    box.showModal();
  });
  document.getElementById('lightbox-prev').addEventListener('click', () => show(index - 1));
  document.getElementById('lightbox-next').addEventListener('click', () => show(index + 1));
  document.getElementById('lightbox-close').addEventListener('click', () => box.close());
  box.addEventListener('click', (e) => { if (e.target === box) box.close(); });         // the backdrop
  box.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft') show(index - 1);
    else if (e.key === 'ArrowRight') show(index + 1);
  });
}

const input = document.getElementById('filter');
if (input) {
  await load();
  input.addEventListener('input', () => {
    const q = input.value.trim().toLowerCase();
    let n = 0;
    for (const c of document.querySelectorAll('.clip-card')) {     // live: deleted cards are gone
      const show = !q || c.dataset.search.includes(q);
      c.hidden = !show;
      n += show;
    }
    document.getElementById('filter-count').textContent = t('analysis.count', { n });
  });
}

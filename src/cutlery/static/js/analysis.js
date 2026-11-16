// Analysis view: filter the clips by ID, path or spoken words.
import { load, t } from './i18n.js';

const input = document.getElementById('filter');
if (input) {
  await load();
  const cards = [...document.querySelectorAll('.clip-card')];
  input.addEventListener('input', () => {
    const q = input.value.trim().toLowerCase();
    let n = 0;
    for (const c of cards) {
      const show = !q || c.dataset.search.includes(q);
      c.hidden = !show;
      n += show;
    }
    document.getElementById('filter-count').textContent = t('analysis.count', { n });
  });
}

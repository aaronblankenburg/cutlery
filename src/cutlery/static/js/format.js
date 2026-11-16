// Locale-aware formatting of sizes, durations and numbers (Intl), applied to .js-size and .js-duration.
const lang = document.documentElement.lang || 'en';

export function number(n, digits = 0) {
  return new Intl.NumberFormat(lang, { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(n);
}

export function size(bytes) {
  const units = ['byte', 'kilobyte', 'megabyte', 'gigabyte', 'terabyte'];
  let i = 0, v = bytes;
  while (v >= 1000 && i < units.length - 1) { v /= 1000; i++; }
  return new Intl.NumberFormat(lang, { style: 'unit', unit: units[i], unitDisplay: 'short',
    maximumFractionDigits: v < 10 && i > 0 ? 1 : 0 }).format(v);
}

// 5 h 12 min, 3 min 20 s, 42 s
export function duration(seconds) {
  const s = Math.round(seconds || 0);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const unit = (v, u) => new Intl.NumberFormat(lang, { style: 'unit', unit: u, unitDisplay: 'short' }).format(v);
  if (h) return `${unit(h, 'hour')} ${unit(m, 'minute')}`;
  if (m) return `${unit(m, 'minute')}${sec ? ' ' + unit(sec, 'second') : ''}`;
  return unit(sec, 'second');
}

export function apply(root = document) {
  for (const el of root.querySelectorAll('.js-size[data-bytes]')) el.textContent = size(Number(el.dataset.bytes));
  for (const el of root.querySelectorAll('.js-duration[data-seconds]')) el.textContent = duration(Number(el.dataset.seconds));
}

apply();

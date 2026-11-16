// UI strings for scripts: the same catalogue as the server's (/i18n/<lang>.json).
export const lang = document.documentElement.lang || 'en';
let strings = {};
let fallback = {};

export async function load() {
  const get = async (code) => (await fetch(`/i18n/${code}.json`, { credentials: 'same-origin' })).json();
  [strings, fallback] = await Promise.all([get(lang), lang === 'en' ? Promise.resolve({}) : get('en')]);
}

export function t(key, values = {}) {
  const text = strings[key] ?? fallback[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (m, name) => (name in values ? String(values[name]) : m));
}

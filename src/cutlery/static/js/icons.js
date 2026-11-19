// The UI icons (static/img/icons.svg) as inline SVG: same size and line as the text next to them, centred by .ico.
const NS = 'http://www.w3.org/2000/svg';

export function icon(name, cls = '') {
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('class', `ico${cls ? ` ${cls}` : ''}`);
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS(NS, 'use');
  use.setAttribute('href', `/static/img/icons.svg#${name}`);
  svg.append(use);
  return svg;
}

// a text with " → " in it ("Done → Export") on a button: the arrows as icons, the words in spans
export function arrowText(e, text) {
  const parts = String(text).split(' → ');
  e.replaceChildren();
  parts.forEach((p, i) => {
    if (i) e.append(icon('arrow-right'));
    e.append(Object.assign(document.createElement('span'), { textContent: p }));
  });
}

// an element with an icon and a text after it: <tag class=cls><svg/><span>text</span></tag>
export function withIcon(tag, cls, name, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  e.append(icon(name));
  if (text != null && text !== '') e.append(Object.assign(document.createElement('span'), { textContent: text }));
  return e;
}

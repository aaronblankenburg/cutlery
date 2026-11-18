// A small Markdown renderer for Claude's messages in the job log: paragraphs, headings, lists, tables, code blocks,
// **bold**, *italic*, `code` and [links](https://…). It builds DOM nodes from text only (never innerHTML), so
// whatever the text contains is shown, not run.

const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};

// inline: `code`, **bold**, *italic* / _italic_, [text](http…)
const INLINE = /`([^`]+)`|\*\*([^*]+?)\*\*|__([^_]+?)__|\*([^*\s][^*]*?)\*|(?<![\w])_([^_\s][^_]*?)_(?![\w])|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g;

function inline(parent, text) {
  let last = 0;
  for (const m of text.matchAll(INLINE)) {
    if (m.index > last) parent.append(text.slice(last, m.index));
    if (m[1] != null) parent.append(el('code', null, m[1]));
    else if (m[2] != null || m[3] != null) inline(parent.appendChild(el('strong')), m[2] ?? m[3]);
    else if (m[4] != null || m[5] != null) inline(parent.appendChild(el('em')), m[4] ?? m[5]);
    else {
      const a = el('a', null, m[6]);
      a.href = m[7];
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      parent.append(a);
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) parent.append(text.slice(last));
  return parent;
}

const cells = (line) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
const isRule = (line) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);
const LIST = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;

export function markdown(text) {
  const root = el('div', 'md');
  const rows = String(text || '').split('\n');
  let i = 0;
  while (i < rows.length) {
    const line = rows[i];
    if (!line.trim()) { i++; continue; }
    // fenced code
    if (/^\s*```/.test(line)) {
      const code = [];
      i++;
      while (i < rows.length && !/^\s*```/.test(rows[i])) code.push(rows[i++]);
      i++;
      root.append(el('pre', null, code.join('\n')));
      continue;
    }
    // heading
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      root.append(inline(el(`h${Math.min(6, h[1].length + 2)}`), h[2]));
      i++;
      continue;
    }
    // table: a row of cells followed by a rule
    if (line.includes('|') && i + 1 < rows.length && isRule(rows[i + 1])) {
      const table = el('table');
      const head = table.appendChild(el('thead')).appendChild(el('tr'));
      for (const c of cells(line)) head.append(inline(el('th'), c));
      const body = table.appendChild(el('tbody'));
      i += 2;
      while (i < rows.length && rows[i].includes('|') && rows[i].trim()) {
        const tr = body.appendChild(el('tr'));
        for (const c of cells(rows[i])) tr.append(inline(el('td'), c));
        i++;
      }
      const wrap = el('div', 'md-table');
      wrap.append(table);
      root.append(wrap);
      continue;
    }
    // list (one level; deeper items are indented)
    if (LIST.test(line)) {
      const ordered = /\d/.test(line.match(LIST)[2]);
      const list = el(ordered ? 'ol' : 'ul');
      while (i < rows.length && rows[i].trim()) {
        const m = rows[i].match(LIST);
        if (m) {
          const li = inline(el('li'), m[3]);
          if (m[1].length >= 2) li.className = 'md-sub';
          list.append(li);
        } else if (list.lastChild) {
          list.lastChild.append(' ');
          inline(list.lastChild, rows[i].trim());          // a continued item
        }
        i++;
      }
      root.append(list);
      continue;
    }
    // paragraph: up to a blank line or another block; single line breaks kept
    const p = el('p');
    let first = true;
    while (i < rows.length && rows[i].trim() && !/^\s*```/.test(rows[i]) && !/^#{1,6}\s/.test(rows[i])
      && !(LIST.test(rows[i]) && !first) && !(rows[i].includes('|') && i + 1 < rows.length && isRule(rows[i + 1]))) {
      if (!first) p.append(el('br'));
      inline(p, rows[i].trim());
      first = false;
      i++;
    }
    root.append(p);
  }
  return root;
}

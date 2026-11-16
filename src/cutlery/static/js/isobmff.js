// Minimal ISO-BMFF / QuickTime reader for what Mediabunny does not expose: frame-rate rationals as ffprobe
// reports them, the start timecode (tmcd track), the raw GoPro telemetry track (gpmd), the bit depth and the
// QuickTime user data (device, ISO 6709 location). Reads the file in small slices; never loads it whole.

const MAC_EPOCH = Date.UTC(1904, 0, 1) / 1000;
const CONTAINERS = new Set(['moov', 'trak', 'mdia', 'minf', 'stbl', 'udta', 'edts', 'dinf']);
const MAX_MOOV = 256 * 1024 * 1024;

async function read(file, start, length) {
  const end = Math.min(file.size, start + length);
  return new DataView(await file.slice(start, end).arrayBuffer());
}

function fourcc(view, off) {
  return String.fromCharCode(view.getUint8(off), view.getUint8(off + 1), view.getUint8(off + 2), view.getUint8(off + 3));
}

function u64(view, off) {
  return view.getUint32(off) * 2 ** 32 + view.getUint32(off + 4);
}

// Top-level boxes, found by reading only their headers.
export async function topLevel(file) {
  const boxes = [];
  let pos = 0;
  while (pos + 8 <= file.size && boxes.length < 10000) {
    const h = await read(file, pos, 16);
    let size = h.getUint32(0);
    const type = fourcc(h, 4);
    let header = 8;
    if (size === 1) { size = u64(h, 8); header = 16; }
    else if (size === 0) size = file.size - pos;
    if (size < header || !/^[\x20-\x7e©]{4}$/.test(type)) break;
    boxes.push({ type, start: pos, size, header });
    pos += size;
  }
  return boxes;
}

// Child boxes of a box held in memory.
function children(view, start, end) {
  const out = [];
  let pos = start;
  while (pos + 8 <= end) {
    let size = view.getUint32(pos);
    const type = fourcc(view, pos + 4);
    let header = 8;
    if (size === 1) { size = u64(view, pos + 8); header = 16; }
    else if (size === 0) size = end - pos;
    if (size < header || pos + size > end) break;
    out.push({ type, start: pos, size, header, body: pos + header, end: pos + size });
    pos += size;
  }
  return out;
}

function find(view, box, path) {
  let cur = [box];
  for (const name of path) {
    const next = [];
    for (const b of cur) for (const c of children(view, b.body, b.end)) if (c.type === name) next.push(c);
    cur = next;
  }
  return cur;
}

function gcd(a, b) { while (b) [a, b] = [b, a % b]; return a; }
export function rational(num, den) {
  if (!den) return '0/0';
  const g = gcd(Math.round(num), Math.round(den)) || 1;
  return `${Math.round(num) / g}/${Math.round(den) / g}`;
}

function macDate(seconds) {
  if (!seconds) return null;
  const ms = (seconds + MAC_EPOCH) * 1000;
  if (ms < Date.UTC(1990, 0, 1)) return null;
  return new Date(ms).toISOString();
}

function fullBoxTime(view, b) {
  const version = view.getUint8(b.body);
  if (version === 1) {
    return { created: u64(view, b.body + 4), timescale: view.getUint32(b.body + 20), duration: u64(view, b.body + 24) };
  }
  return { created: view.getUint32(b.body + 4), timescale: view.getUint32(b.body + 12), duration: view.getUint32(b.body + 16) };
}

function sampleTable(view, stbl) {
  const t = {};
  const get = (name) => find(view, stbl, [name])[0];
  const stts = get('stts');
  if (stts) {
    const n = view.getUint32(stts.body + 4);
    t.stts = [];
    for (let i = 0; i < n && stts.body + 8 + i * 8 + 8 <= stts.end; i++) {
      t.stts.push([view.getUint32(stts.body + 8 + i * 8), view.getUint32(stts.body + 12 + i * 8)]);
    }
  }
  const stsz = get('stsz');
  if (stsz) {
    const fixed = view.getUint32(stsz.body + 4), n = view.getUint32(stsz.body + 8);
    t.count = n;
    t.sizes = fixed ? null : (i) => view.getUint32(stsz.body + 12 + i * 4);
    t.fixedSize = fixed;
  }
  const stsc = get('stsc');
  if (stsc) {
    const n = view.getUint32(stsc.body + 4);
    t.stsc = [];
    for (let i = 0; i < n; i++) t.stsc.push([view.getUint32(stsc.body + 8 + i * 12), view.getUint32(stsc.body + 12 + i * 12)]);
  }
  const stco = get('stco'), co64 = get('co64');
  if (stco) {
    const n = view.getUint32(stco.body + 4);
    t.chunks = Array.from({ length: n }, (_, i) => view.getUint32(stco.body + 8 + i * 4));
  } else if (co64) {
    const n = view.getUint32(co64.body + 4);
    t.chunks = Array.from({ length: n }, (_, i) => u64(view, co64.body + 8 + i * 8));
  }
  return t;
}

// Byte ranges of every sample of a track, in decode order.
function sampleRanges(t) {
  if (!t.chunks || !t.stsc || !t.count) return [];
  const ranges = [];
  let sample = 0;
  for (let c = 0; c < t.chunks.length && sample < t.count; c++) {
    let per = 0;
    for (const [first, n] of t.stsc) if (first <= c + 1) per = n;
    let off = t.chunks[c];
    for (let k = 0; k < per && sample < t.count; k++, sample++) {
      const size = t.sizes ? t.sizes(sample) : t.fixedSize;
      ranges.push([off, size]);
      off += size;
    }
  }
  return ranges;
}

// QuickTime international text in udta (©xyz, ©mak …): 2 bytes length, 2 bytes language, text.
function udtaText(view, b) {
  if (b.size < 12) return null;
  const len = view.getUint16(b.body);
  if (len === 0 || b.body + 4 + len > b.end) return null;
  const bytes = new Uint8Array(view.buffer, view.byteOffset + b.body + 4, len);
  return new TextDecoder().decode(bytes).replace(/\0+$/, '');
}

const UDTA_NAMES = { '©xyz': 'location', '©mak': 'make', '©mod': 'model', '©swr': 'encoder', '©day': 'date',
  '©inf': 'information', '©nam': 'title', '©cmt': 'comment', '©too': 'encoder' };

export function timecodeString(frame, fps, dropFrame) {
  let f = frame;
  if (dropFrame) {
    const drop = Math.round(fps * 0.066666);           // 2 at 29.97, 4 at 59.94
    const perTen = Math.round(fps * 600) - 9 * drop;   // frames per 10 minutes
    const perMin = Math.round(fps) * 60 - drop;
    const d = Math.floor(f / perTen), m = f % perTen;
    f += 9 * drop * d + (m > drop ? drop * Math.floor((m - drop) / perMin) : 0);
  }
  const nf = Math.round(fps);
  const ff = f % nf, s = Math.floor(f / nf) % 60, mi = Math.floor(f / (nf * 60)) % 60, h = Math.floor(f / (nf * 3600)) % 24;
  const p = (x, n = 2) => String(x).padStart(n, '0');
  return `${p(h)}:${p(mi)}:${p(s)}${dropFrame ? ';' : ':'}${p(ff, nf > 99 ? 3 : 2)}`;
}

// Everything this module knows about a file. Returns null if it is not an ISO-BMFF / QuickTime file.
export async function inspect(file) {
  const boxes = await topLevel(file);
  const moov = boxes.find((b) => b.type === 'moov');
  const ftyp = boxes.find((b) => b.type === 'ftyp');
  if (!moov || moov.size > MAX_MOOV) return null;
  const view = await read(file, moov.start, moov.size);
  const root = { body: moov.header, end: moov.size };
  const out = { brand: null, created: null, tags: {}, tracks: [] };
  if (ftyp) {
    const fv = await read(file, ftyp.start, Math.min(ftyp.size, 64));
    out.brand = fourcc(fv, 8).trim();
  }
  const mvhd = find(view, root, ['mvhd'])[0];
  if (mvhd) {
    const t = fullBoxTime(view, mvhd);
    out.created = macDate(t.created);
    out.duration = t.timescale ? t.duration / t.timescale : null;
  }
  for (const b of find(view, root, ['udta']).flatMap((u) => children(view, u.body, u.end))) {
    if (b.type.startsWith('©')) {
      const text = udtaText(view, b);
      if (text) out.tags[UDTA_NAMES[b.type] || b.type] = text;
    }
  }
  for (const trak of find(view, root, ['trak'])) {
    const tr = {};
    const tkhd = find(view, trak, ['tkhd'])[0];
    if (tkhd) tr.id = view.getUint32(tkhd.body + (view.getUint8(tkhd.body) === 1 ? 20 : 12));
    const mdhd = find(view, trak, ['mdia', 'mdhd'])[0];
    if (mdhd) {
      const t = fullBoxTime(view, mdhd);
      tr.timescale = t.timescale;
      tr.mediaDuration = t.duration;
    }
    const hdlr = find(view, trak, ['mdia', 'hdlr'])[0];
    if (hdlr) tr.handler = fourcc(view, hdlr.body + 8);
    const stbl = find(view, trak, ['mdia', 'minf', 'stbl'])[0];
    if (!stbl) continue;
    const stsd = find(view, stbl, ['stsd'])[0];
    if (stsd) {
      const entry = stsd.body + 8;
      tr.format = fourcc(view, entry + 4);
      if (tr.handler === 'vide') {
        tr.width = view.getUint16(entry + 32);
        tr.height = view.getUint16(entry + 34);
        const config = children(view, entry + 86, entry + view.getUint32(entry));
        const hvcC = config.find((c) => c.type === 'hvcC'), avcC = config.find((c) => c.type === 'avcC');
        if (hvcC && hvcC.body + 18 <= hvcC.end) tr.bitDepth = (view.getUint8(hvcC.body + 17) & 7) + 8;
        if (avcC) {
          const profile = view.getUint8(avcC.body + 1);
          tr.bitDepth = profile === 110 || profile === 122 || profile === 244 ? 10 : 8;
          let p = avcC.body + 5;
          const nsps = view.getUint8(p) & 31; p += 1;
          for (let i = 0; i < nsps && p + 2 <= avcC.end; i++) p += 2 + view.getUint16(p);
          if (p < avcC.end) {
            const npps = view.getUint8(p); p += 1;
            for (let i = 0; i < npps && p + 2 <= avcC.end; i++) p += 2 + view.getUint16(p);
            if ([100, 110, 122, 244].includes(profile) && p + 3 <= avcC.end) tr.bitDepth = (view.getUint8(p + 1) & 7) + 8;
          }
        }
      }
      if (tr.format === 'tmcd' && entry + 34 <= stsd.end) {
        // sample entry header (16 bytes), reserved (4), flags, timescale, frame duration, frames per second
        tr.tmcd = { flags: view.getUint32(entry + 20), timescale: view.getUint32(entry + 24),
          frameDuration: view.getUint32(entry + 28), frames: view.getUint8(entry + 32) };
      }
    }
    tr.table = sampleTable(view, stbl);
    out.tracks.push(tr);
  }
  return out;
}

// avg_frame_rate and r_frame_rate as ffprobe derives them for MP4/MOV, plus the frame count and VFR flag.
export function frameRates(tr) {
  const stts = tr.table?.stts || [];
  const frames = stts.reduce((n, [count]) => n + count, 0);
  const total = stts.reduce((n, [count, delta]) => n + count * delta, 0) || tr.mediaDuration;
  let mode = 0, best = -1;
  const byDelta = new Map();
  for (const [count, delta] of stts) byDelta.set(delta, (byDelta.get(delta) || 0) + count);
  for (const [delta, count] of byDelta) if (count > best && delta > 0) { best = count; mode = delta; }
  return {
    nb_frames: frames,
    duration: tr.timescale ? total / tr.timescale : null,
    avg_frame_rate: total ? rational(frames * tr.timescale, total) : '0/0',
    r_frame_rate: mode ? rational(tr.timescale, mode) : '0/0',
    vfr: byDelta.size > 1 && [...byDelta.values()].filter((c) => c > 1).length > 1,
  };
}

// The start timecode of the file, from its tmcd track.
export async function startTimecode(file, info) {
  const tr = info?.tracks.find((t) => t.format === 'tmcd' && t.tmcd);
  if (!tr) return null;
  const [range] = sampleRanges(tr.table);
  if (!range || range[1] < 4) return null;
  const v = await read(file, range[0], 4);
  const { flags, timescale, frameDuration, frames } = tr.tmcd;
  const fps = frameDuration ? timescale / frameDuration : frames;
  return timecodeString(v.getUint32(0), fps || frames, Boolean(flags & 1));
}

// The raw GoPro metadata track (GPMF payloads back to back), or null.
export async function gpmdTrack(file, info) {
  const tr = info?.tracks.find((t) => t.format === 'gpmd');
  if (!tr) return null;
  const ranges = sampleRanges(tr.table);
  const total = ranges.reduce((n, [, s]) => n + s, 0);
  if (!total || total > 256 * 1024 * 1024) return null;
  const out = new Uint8Array(total);
  let pos = 0;
  for (const [off, size] of ranges) {
    out.set(new Uint8Array(await file.slice(off, off + size).arrayBuffer()), pos);
    pos += size;
  }
  return out;
}

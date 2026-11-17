// Reads the ORIGINAL file of a clip and writes its sidecar (format cutlery.sidecar/1, docs/sidecar.md).
// Runs in the pipeline worker; only small slices of the file are read.
import * as MB from '../vendor/mediabunny-1.64.0.min.mjs';
import { inspect, frameRates, trackDuration, startTimecode, gpmdTrack } from './isobmff.js';
import { fingerprint } from './fingerprint.js';

export { fingerprint };

export const FORMAT = 'cutlery.sidecar/1';
function textTags(raw) {
  const out = {};
  for (const [k, v] of Object.entries(raw || {})) {
    if (typeof v === 'string' && v.length <= 4096) out[k] = v;
    else if (typeof v === 'number' || typeof v === 'boolean') out[k] = String(v);
    else if (v instanceof Date) out[k] = v.toISOString();
  }
  return out;
}

const ISO6709 = /^[+-]\d+(\.\d+)?[+-]\d+(\.\d+)?([+-]\d+(\.\d+)?)?(CRS[^/]*)?\/?$/;

function findGps(tags) {
  for (const key of ['com.apple.quicktime.location.ISO6709', 'location', '©xyz', 'location-eng']) {
    const v = tags[key];
    if (v && ISO6709.test(v.trim())) return v.trim();
  }
  return null;
}

// A track's length as ffprobe gives it: last end minus first timestamp.
async function span(track) {
  return (await track.computeDuration()) - Math.max(0, (await track.getFirstTimestamp().catch(() => 0)) || 0);
}
// The file's length (ffprobe's format duration): from the earliest start to the latest end of its tracks.
async function spanAll(tracks) {
  if (!tracks.length) return null;
  const starts = await Promise.all(tracks.map((t) => t.getFirstTimestamp().catch(() => 0)));
  const ends = await Promise.all(tracks.map((t) => t.computeDuration()));
  return Math.max(...ends) - Math.max(0, Math.min(...starts));
}

// AC-3 / E-AC-3 sound (AVCHD camcorders, .MTS) has no decoder in any browser: libavcodec's as WebAssembly, loaded only
// when a clip needs it. Registered per worker; the transcoding worker calls this too.
let ac3Ready = null;
export function ensureAc3() {
  ac3Ready ??= import('../vendor/mediabunny-ac3-1.64.0.min.mjs').then(({ registerAc3Decoder }) => registerAc3Decoder());
  return ac3Ready;
}
export const isAc3 = (codec) => codec === 'ac3' || codec === 'eac3';

// Why a clip cannot be used in this browser, or null.
async function decodeProblem(input, video, audio) {
  if (!video) return { reason: 'no_video' };
  if (!video.codec) return { reason: 'video_codec', detail: String(await video.getInternalCodecId?.() ?? '?') };
  if (!(await video.canDecode())) return { reason: 'video_codec', detail: (await video.getCodecParameterString()) || video.codec };
  if (audio && isAc3(audio.codec)) await ensureAc3().catch(() => {});
  if (audio && !(await audio.canDecode())) return { reason: 'audio_codec', detail: (await audio.getCodecParameterString()) || audio.codec || '?' };
  return null;
}

export async function probe(file, relPath) {
  const sc = {
    format: FORMAT,
    file: file.name,
    rel_path: relPath,
    size: file.size,
    mtime: new Date(file.lastModified).toISOString(),
    container: null,
    duration: null,
    format_duration: null,
    creation_time: null,
    timecode: null,
    gps: null,
    video: null,
    audio: null,
    tags: {},
    telemetry: { gpmd: false, srt: null },
    fingerprint: await fingerprint(file),
    tool: { name: 'cutlery', user_agent: (self.navigator && navigator.userAgent) || '' },
  };
  let gpmd = null;
  const input = new MB.Input({ source: new MB.BlobSource(file), formats: MB.ALL_FORMATS });
  try {
    let format;
    try {
      format = await input.getFormat();
    } catch {
      sc.unsupported = { reason: 'container', detail: (file.name.split('.').pop() || '').toUpperCase() };
      return { sidecar: sc, gpmd };
    }
    sc.container = format.name;
    const video = await input.getPrimaryVideoTrack();
    const audio = await input.getPrimaryAudioTrack();
    const tags = await input.getMetadataTags().catch(() => ({}));
    Object.assign(sc.tags, textTags(tags.raw));

    const box = await inspect(file).catch(() => null);
    if (box) {
      Object.assign(sc.tags, box.tags);
      sc.creation_time = box.created;
      if (box.created) sc.tags.creation_time = box.created;
      sc.timecode = await startTimecode(file, box).catch(() => null);
      gpmd = await gpmdTrack(file, box).catch(() => null);
      sc.telemetry.gpmd = Boolean(gpmd && gpmd.length);
    }
    if (!sc.creation_time && tags.date instanceof Date) sc.creation_time = tags.date.toISOString();
    sc.gps = findGps(sc.tags);

    if (video) {
      const vt = box?.tracks.find((t) => t.handler === 'vide' && t.id === video.id) || box?.tracks.find((t) => t.handler === 'vide');
      const rates = vt ? frameRates(vt) : null;
      // like ffprobe's video stream duration: the sum of the sample durations, shortened by an edit list
      const shown = vt ? trackDuration(vt, box?.timescale) : null;
      let stats = null;
      if (!rates) stats = await video.computePacketStats(1000).catch(() => null);
      const fr = stats ? `${Math.round(stats.averagePacketRate * 1000)}/1000` : null;
      sc.video = {
        codec: video.codec || 'unknown',
        codec_string: (await video.getCodecParameterString().catch(() => null)) || null,
        width: vt?.width || video.codedWidth,
        height: vt?.height || video.codedHeight,
        display_width: video.displayWidth,
        display_height: video.displayHeight,
        rotation: ((video.rotation % 360) + 360) % 360,
        r_frame_rate: rates?.r_frame_rate || fr,
        avg_frame_rate: rates?.avg_frame_rate || fr,
        nb_frames: rates?.nb_frames ?? null,
        vfr: rates?.vfr ?? false,
        edit_list: shown?.editList ?? false,
        bit_depth: vt?.bitDepth ?? null,
        hdr: await video.hasHighDynamicRange().catch(() => false),
        time_base: vt?.timescale ? `1/${vt.timescale}` : null,
      };
      // without an ISO BMFF box (transport streams, Matroska …) timestamps need not start at 0 (AVCHD: about 1.4 s);
      // ffprobe's stream duration, and the stand-in's, are end minus start
      sc.duration = shown?.seconds || await span(video);
    }
    if (audio) {
      sc.audio = { codec: audio.codec || 'unknown', channels: audio.numberOfChannels, sample_rate: audio.sampleRate };
    }
    if (!sc.duration) sc.duration = await input.computeDuration().catch(() => null);
    // the duration of the whole file as ffprobe reports it: the longest of the tracks as they are shown
    const sound = box?.tracks.find((t) => t.handler === 'soun');
    const longest = Math.max(sc.duration || 0, sound ? trackDuration(sound, box.timescale).seconds : 0);
    sc.format_duration = box ? (longest || box.duration) : await spanAll([video, audio].filter(Boolean)).catch(() => null);
    const problem = await decodeProblem(input, video, audio);
    if (problem) sc.unsupported = problem;
  } finally {
    input.dispose?.();
  }
  return { sidecar: sc, gpmd };
}

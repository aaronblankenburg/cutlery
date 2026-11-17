// Worker: probes originals and transcodes them to 360p stand-ins in the origin private file system (OPFS).
//
// Stand-in contract (cutter's proxies.py): same frames at the same timestamps (no frame-rate change, VFR kept),
// picture turned upright (rotation baked in), long side at most 640 px, H.264 at about 1.2 Mbit/s with a key frame
// every half second, AAC 48 kHz at about 160 kbit/s with the original channel count.
import * as MB from '../vendor/mediabunny-1.64.0.min.mjs';
import { probe, ensureAc3, isAc3 } from './probe.js';

export const LONG_SIDE = 640;
const VIDEO_BITRATE = 1_200_000;
const AUDIO_BITRATE = 160_000;
const KEYFRAME_S = 0.5;

const running = new Map();   // job id -> Conversion
let aacReady = null;

// Browsers without a native AAC encoder (Chrome on Linux, Firefox) get libavcodec's encoder as WebAssembly.
function ensureAac() {
  aacReady ??= (async () => {
    if (await MB.canEncodeAudio('aac', { numberOfChannels: 2, sampleRate: 48000, bitrate: AUDIO_BITRATE }).catch(() => false)) {
      return 'native';
    }
    const { registerAacEncoder } = await import('../vendor/mediabunny-aac-encoder-1.64.0.min.mjs');
    registerAacEncoder();
    return 'wasm';
  })();
  return aacReady;
}

function even(n) { return Math.max(2, Math.round(n / 2) * 2); }

// Output size: long side 640 (never upscaled), aspect kept, even numbers.
export function targetSize(displayWidth, displayHeight) {
  const scale = Math.min(1, LONG_SIDE / Math.max(displayWidth, displayHeight));
  return { width: even(displayWidth * scale), height: even(displayHeight * scale) };
}

async function capabilities() {
  const avc = await MB.canEncodeVideo('avc', { width: 640, height: 360, bitrate: VIDEO_BITRATE }).catch(() => false);
  const aac = await ensureAac().catch(() => false);
  let opfs = false;
  try {
    const root = await navigator.storage.getDirectory();
    const h = await root.getFileHandle('.cutlery-probe', { create: true });
    const access = await h.createSyncAccessHandle();
    access.close();
    await root.removeEntry('.cutlery-probe');
    opfs = true;
  } catch { /* no OPFS sync access */ }
  return { webcodecs: typeof VideoEncoder !== 'undefined', avc, aac, opfs };
}

async function opfsFile(name) {
  const root = await navigator.storage.getDirectory();
  const dir = await root.getDirectoryHandle('cutlery', { create: true });
  return dir.getFileHandle(name, { create: true });
}

async function transcode(id, file, outName, report) {
  const input = new MB.Input({ source: new MB.BlobSource(file), formats: MB.ALL_FORMATS });
  if (isAc3((await input.getPrimaryAudioTrack())?.codec)) await ensureAc3();
  const handle = await opfsFile(outName);
  const access = await handle.createSyncAccessHandle();
  access.truncate(0);
  let written = 0;
  const target = new MB.StreamTarget(new WritableStream({
    write(chunk) {
      access.write(chunk.data, { at: chunk.position });
      written = Math.max(written, chunk.position + chunk.data.byteLength);
    },
  }), { chunked: true, chunkSize: 4 * 1024 * 1024 });
  const output = new MB.Output({ format: new MB.Mp4OutputFormat({ fastStart: false }), target });
  await ensureAac();
  try {
    const conversion = await MB.Conversion.init({
      input, output,
      tracks: 'primary',
      showWarnings: false,
      tags: {},
      video: (track) => {
        // Scale with createImageBitmap instead of Mediabunny's canvas path: as fast with a GPU and about three times
        // faster without one. Rotation is cancelled in Mediabunny and applied to the small bitmap instead.
        const rotation = ((track.rotation % 360) + 360) % 360;
        const turned = rotation % 180 !== 0;
        const w = track.squarePixelWidth, h = track.squarePixelHeight;          // stored picture, square pixels
        const out = targetSize(turned ? h : w, turned ? w : h);                  // upright result
        const pre = turned ? { width: out.height, height: out.width } : out;    // before turning
        const canvas = rotation ? new OffscreenCanvas(out.width, out.height) : null;
        const ctx = canvas?.getContext('2d');
        return {
          codec: 'avc', quality: new MB.Quality({ bitrate: VIDEO_BITRATE }), keyFrameInterval: KEYFRAME_S,
          forceTranscode: true, rotate: (360 - rotation) % 360, allowTransformationMetadata: false,
          processedWidth: out.width, processedHeight: out.height,
          process: async (sample) => {
            const frame = sample.toVideoFrame();
            let bmp;
            try {
              bmp = await createImageBitmap(frame, { resizeWidth: pre.width, resizeHeight: pre.height, resizeQuality: 'medium' });
            } finally {
              frame.close();
            }
            if (!rotation) return bmp;
            ctx.setTransform(1, 0, 0, 1, 0, 0);
            ctx.translate(out.width / 2, out.height / 2);
            ctx.rotate((rotation * Math.PI) / 180);
            ctx.drawImage(bmp, -pre.width / 2, -pre.height / 2);
            bmp.close();
            return canvas.transferToImageBitmap();
          },
        };
      },
      audio: (track) => ({
        codec: 'aac', sampleRate: 48000, numberOfChannels: track.numberOfChannels,
        quality: new MB.Quality({ bitrate: AUDIO_BITRATE }),
      }),
    });
    if (!conversion.isValid) {
      const reasons = conversion.discardedTracks.map((d) => `${d.track.type}: ${d.reason}`).join(', ');
      throw Object.assign(new Error(reasons || 'cannot convert'), { code: 'convert' });
    }
    const lost = conversion.discardedTracks.filter((d) => d.track.type === 'audio' || d.track.type === 'video');
    if (lost.length) {
      const reasons = lost.map((d) => `${d.track.type}: ${d.reason}`).join(', ');
      throw Object.assign(new Error(reasons), { code: lost[0].reason });
    }
    running.set(id, conversion);
    conversion.onProgress = (p) => report(p);
    await conversion.execute();
    access.flush();
    return { name: outName, size: written };
  } finally {
    running.delete(id);
    access.close();
    input.dispose?.();
  }
}

self.onmessage = async (event) => {
  const { id, type } = event.data;
  const reply = (msg, transfer) => self.postMessage({ id, ...msg }, transfer || []);
  try {
    if (type === 'capabilities') {
      reply({ ok: true, result: await capabilities() });
    } else if (type === 'probe') {
      const { sidecar, gpmd } = await probe(event.data.file, event.data.relPath);
      reply({ ok: true, result: { sidecar, gpmd } }, gpmd ? [gpmd.buffer] : []);
    } else if (type === 'transcode') {
      const result = await transcode(id, event.data.file, event.data.outName,
        (p) => self.postMessage({ id, progress: p }));
      reply({ ok: true, result });
    } else if (type === 'cancel') {
      await running.get(event.data.job)?.cancel();
      reply({ ok: true });
    } else if (type === 'remove') {
      const root = await navigator.storage.getDirectory();
      const dir = await root.getDirectoryHandle('cutlery', { create: true });
      await dir.removeEntry(event.data.name).catch(() => {});
      reply({ ok: true });
    }
  } catch (e) {
    reply({ ok: false, error: String(e?.message || e), code: e?.code || e?.name || 'error' });
  }
};

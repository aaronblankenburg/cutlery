# M1 feasibility: transcoding in the browser

Measured on 2026-11-16 on a laptop with a 12th-gen Intel Core i5 and Intel Iris Xe graphics (Linux, Google
Chrome 143), against a local Cutlery server. The pipeline is the one in `static/js/pipeline-worker.js`:
Mediabunny 1.64.0 decodes with WebCodecs, the picture is scaled with `createImageBitmap`, H.264 is encoded with
WebCodecs, AAC with the browser's encoder or, where there is none, with Mediabunny's libavcodec AAC encoder in
WebAssembly.

## Results

| Material | Speed (incl. reading, metadata, upload) | Notes |
|---|---|---|
| 8 real clips, 720p H.264 (GoPro HD and a compact camera, 2011), 17.5 min, 1.35 GB | 5.7 × real time overall (3 min 6 s) | 156 MB of stand-ins; all passed the server check |
| of these: 720p25 GoPro | 5.6–6.7 × real time | stand-ins ≈ 1.0 Mbit/s |
| of these: 720p29.97 compact camera | ≈ 4.9 × real time | |
| 2160p29.97 H.264, DJI drone, 100 Mbit/s, no sound (20 s excerpt) | 1.7–2.1 × real time | software decoding |
| 720p, headless Chrome without GPU | 1.3 × real time | with Mediabunny's canvas scaling only 0.46 × |

For a sense of scale: 5 h of 4K drone footage takes about 3 h; 5 h of 720p/1080p phone or action-cam footage
about an hour. Upload is not the bottleneck: the stand-ins are ≈ 1.2 Mbit/s video + 160 kbit/s sound.

## Findings

- **Scaling** dominates the cost without a GPU. Mediabunny's built-in resize draws through a canvas, which in a
  browser without GPU acceleration falls back to software rendering (10 × slower). Cutlery therefore scales with
  `createImageBitmap` and turns rotated clips on the already small bitmap. With a GPU both paths are equally fast.
- **Chrome on Linux** has no AAC encoder and cannot decode HEVC (it would need hardware decoding through VA-API).
  AAC works through the WebAssembly encoder (loaded only when needed, ≈ 1 MB). HEVC clips are reported as not
  readable, with the recommendation to use Safari on a Mac or Chrome/Edge on Windows/macOS.
- **Timestamps** are preserved exactly: every stand-in has the same number of frames as the original and the
  same presentation times (maximum difference below 0.01 ms in the tests, including VFR).
- **Metadata** read in the browser matches ffprobe for all test clips: frame rates as rationals (`30000/1001`),
  frame count, start timecode including drop-frame, bit depth, rotation, audio channels and sample rate.
- **The server check** (ffprobe on every finished upload: codec, size, same length within 2 frames + 0.02 s)
  works as a safety net.

## Not yet measured

- HEVC (4K phone and drone clips, 10 bit): no such footage was available, and Chrome on Linux cannot decode it.
  Needs a run in Safari on a Mac or Chrome on Windows.
- Portrait phone clips with rotation and real VFR, GoPro clips with a GPS metadata track (`gpmd`), DJI `.SRT`
  files: covered by synthetic test clips, not yet by real footage.
- Safari: the Playwright WebKit tests need `libavif16` on the test machine; real Safari only on a Mac.

## Parallel transcoding (2026-11-17)

Does transcoding several clips at the same time (one pipeline worker each) make the upload faster? Measured with
`tools/bench_transcode.py` (the real pipeline worker, N at a time, largest clips first) on a laptop with Intel Iris
Xe (Alder Lake, 16 logical cores), Chrome 152:

| Footage | Chrome | 1 worker | 2 workers | 3 workers | 4 workers |
|---|---|---|---|---|---|
| 8 clips 720p H.264, 17.2 min, 1.35 GB | with GPU | 91.2 s (11.3 × real time) | 87.0 s (1.05 ×) | 89.6 s (1.02 ×) | 88.1 s (1.04 ×) |
| 5 clips 2160p H.264 (DJI), 1.1 min, 0.91 GB | with GPU | 26.4 s (2.5 ×) | 25.3 s (1.05 ×) | 24.1 s (1.10 ×) | – |
| 2 clips 720p H.264, 2.4 min | headless, no GPU | 380 s (0.37 ×) | 438 s (0.87 ×) | – | – |

More workers share the same hardware decoder and encoder, and Chrome's decoders already use several cores; without a
GPU the software paths compete for the same cores. Cutlery therefore transcodes one clip at a time (`PARALLEL` in
`static/js/footage.js`; `?parallel=N` in the address tries more, for example on a Mac with several media engines).
Uploading runs alongside the transcoding already, and the stand-ins (≈ 1.4 Mbit/s) never make it the bottleneck.

# Sidecar metadata (`cutlery.sidecar/1`)

The browser writes one sidecar per clip. It describes the **original** file on the user's computer. The server
only keeps a 360p stand-in, so everything that the export needs about the original comes from here: frame rate,
start timecode, size, rotation, duration. The sidecar is also the input for cutter change (a), see
[plan.md](plan.md).

The server stores it at `CUTLERY_DATA_DIR/projects/<id>/sidecars/<rel_path>.json` and adds `clip_id`. Raw
telemetry sits next to it: `<rel_path>.gpmd` (GoPro) and `<rel_path>.srt` (DJI). The stand-in is
`CUTLERY_WORK_DIR/projects/<id>/media/<rel_path>.mp4`.

## Fields

| Field | Meaning |
|---|---|
| `format` | always `"cutlery.sidecar/1"` |
| `file` | file name of the original |
| `rel_path` | path inside the chosen footage folder, `/`-separated, as the browser reports it (`webkitRelativePath` without the folder itself). Kept exactly, including Unicode normalisation (macOS may report NFD). |
| `size`, `mtime` | bytes; last modification as ISO 8601 UTC |
| `container` | e.g. `MP4`, `QuickTime`, `Matroska` |
| `duration` | seconds; the video stream's duration as ffprobe reports it (sum of the sample durations) |
| `format_duration` | seconds; the movie duration (`mvhd`), which includes edit lists |
| `creation_time` | ISO 8601 UTC from the movie header (`mvhd`), or `null` |
| `timecode` | start timecode from the `tmcd` track, `HH:MM:SS:FF`, or `HH:MM:SS;FF` for drop-frame; `null` if none |
| `gps` | ISO 6709 location from the container tags (`©xyz`, `com.apple.quicktime.location.ISO6709`), or `null` |
| `video.codec` | `avc`, `hevc`, `vp9`, `av1`, `prores` … |
| `video.codec_string` | the full codec string, e.g. `avc1.4d401f`, `hvc1.2.4.L93.90` |
| `video.width`, `video.height` | stored picture size (before rotation), as ffprobe reports `width`/`height` |
| `video.display_width`, `video.display_height` | size as displayed (after rotation and pixel aspect ratio) |
| `video.rotation` | degrees **clockwise** to display the picture upright: 0, 90, 180 or 270. ffprobe reports the same as a counter-clockwise `rotation` side datum (−90 ⇔ 90). |
| `video.r_frame_rate`, `video.avg_frame_rate` | rationals exactly as ffprobe reports them (`30000/1001`) |
| `video.nb_frames` | number of frames |
| `video.vfr` | `true` if the frame durations vary |
| `video.bit_depth` | 8, 10 … from `avcC`/`hvcC` |
| `video.hdr` | `true` for HDR transfer characteristics |
| `video.time_base` | `1/<timescale>` of the video track |
| `audio` | `{codec, channels, sample_rate}` of the first audio track, or `null` |
| `tags` | all text tags of the container (device, encoder, location …) |
| `telemetry.gpmd` | `true` if the file has a GoPro metadata track; its samples are uploaded back to back as `.gpmd` |
| `telemetry.srt` | name of a DJI `.SRT` next to the clip (same name, any case), uploaded as `.srt` |
| `fingerprint` | `{size, head_sha256, tail_sha256}`: SHA-256 of the first and of the last MiB. Identifies the original again at export, even after renaming or moving. |
| `unsupported` | only for clips this browser cannot use: `{reason, detail}` with `reason` one of `container`, `video_codec`, `audio_codec`, `no_video` |
| `tool` | `{name: "cutlery", user_agent}` |
| `clip_id` | added by the server |

## The 360p stand-in

- H.264, long side 640 px (never upscaled; portrait stays portrait), picture turned upright (no rotation
  metadata), about 1.2 Mbit/s, a key frame every half second.
- Same frames at the same timestamps as the original: no frame-rate change, VFR kept.
- AAC, 48 kHz, about 160 kbit/s, the original's channel count.
- The server checks every upload with ffprobe: H.264 + AAC, at most 640 px, and the same length as the original
  within 2 frames + 0.02 s (cutter's proxy contract). Otherwise the clip is marked failed with the reason.

## Manifest

`CUTLERY_DATA_DIR/projects/<id>/manifest.json` (`cutlery.manifest/1`) lists every clip with `id`, `rel_path`,
`file`, `size`, `mtime`, `fingerprint` and `duration`. At export the browser compares the folder the user picks
again with it.

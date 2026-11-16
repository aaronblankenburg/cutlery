# Analysis on 360p stand-ins: do cutter's thresholds still hold?

Question (PROMPT.md): do the thresholds in `quality.py` and `image_analysis.py`, above all the Laplacian sharpness,
behave noticeably differently on 360p H.264 than on the originals? Measured on 2026-11-16 with cutter's image
analysis (`cutter analyze --no-transcripts`, cutter branch `cutlery-support`), once on the original files and once on
the stand-ins the browser made from them, with `tools/compare_analysis.py`.

## Material

| Set | Clips | Original | Stand-in |
|---|---|---|---|
| Action cam and compact camera, 2011 | 8, 17 min, with people | 1280×720 H.264 | 640×360 |
| Drone, 30 s excerpts | 5, 65 s, no people | 3840×2160 H.264, 100 Mbit/s | 640×360 |

## Result

| | 720p set | 4K set |
|---|---|---|
| Median sharpness, stand-in ÷ original | 0.55 | 0.41 |
| Usable ranges: share of seconds on which both agree | 96 % | 97 % |
| Flag *soft*: agreement | 97 % | 95 % |
| Flag *blurred*: agreement | 100 % | 100 % |
| Camera move (static, pan, push-in …): agreement | 98 % | 87 % |
| Seconds flagged soft (original → stand-in) | 60 → 45 | 14 → 13 |
| Usable seconds (original → stand-in) | 897.5 → 898.7 | 54.3 → 54.9 |
| Clips with the same number of scenes | 5 of 8 | 5 of 5 |
| Most faces in one clip, summed (original → stand-in) | 13 → 11 | 0 → 0 |
| Most people in one clip, summed | 14 → 14 | 0 → 0 |

## Findings

- **The absolute sharpness number drops to 40–55 %.** Nothing in cutter uses it as an absolute value: soft and blurred
  are shares of the sharp frames nearby and of the clip (`SOFT`, `BLURRED`), `retime.py` compares with the clip's
  median, and `low_detail` compares with clips of the same size. The *decisions* therefore agree (95–100 %).
- **`low_detail` must compare stand-ins with stand-ins.** Done: it groups by the analysed size (`standin`), so a
  project that mixes 640×360 and already-small 512×288 stand-ins does not put them in one group, and the original's
  4K size does not make every stand-in a "4K" peer.
- **Slightly less strict on softness:** 45 instead of 60 s flagged soft in the 720p set. Soft frames at 720p are
  sometimes just average at 360p. The usable seconds hardly change (898.7 vs 897.5 s).
- **Scene detection finds fewer cuts in clips with many fast cuts** (45 → 31, 7 → 5 in two GoPro clips). Scenes are a
  hint for the agent, not used for cutting.
- **Faces:** 2 of 13 per-clip maxima were lost at 360p (a face must be at least `FACE_PX` = 48 px of the picture
  that is read, which is 13 % of the height at 360p instead of 7 % at 720p). People (bodies) are unaffected. In
  this footage the people were close enough; small faces far from the camera are not recognised, so *people.json*
  clusters (who is who) are built from larger faces only.
- **Camera moves:** 98 % agreement at 720p, 87 % at 4K, where the original has far more detail to track: the
  differences are at the boundary between *static* and *handheld* in slowly moving drone footage.

## Decision

No threshold changes, no `[analysis]` section in cutter.toml. The one change that was needed, grouping `low_detail`
by the analysed size, is in cutter (`image_analysis.analysed_size`). Revisit if face recognition turns out to matter
for a film (a higher stand-in resolution for clips with people would be the lever: `LONG_SIDE` in the worker).

## Not measured

Speech: cutter transcribes the stand-in's AAC track (48 kHz, 160 kbit/s). The Whisper model (1.6 GB) was not
downloaded for these tests. Phone footage with HEVC and 10-bit colour (no such clips were available).

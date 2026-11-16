# NLE round trip: manual checklist

Automatic tests check what Cutlery writes (golden files, path encoding, metadata). Whether an editing application
reads it as intended can only be checked in that application. Run this checklist after changes to the export
(cutter's `export.py`, `fcp7.py`, `fcpxml.py`, `paths.py`, `timeline.py`, `filmcraft.py`) and before a release.
Note the application version, the result and every deviation in the table at the end.

## Material

Use a project whose footage covers the hard cases, all with a few seconds of speech or ambient sound:

| Case | Example | What it tests |
|---|---|---|
| 4K HEVC drone clip, 29.97 fps | DJI | NTSC rate, 50 % scale, HEVC relink |
| Portrait phone clip, HEVC, rotation 90° | iPhone | rotation, upright stand-in, 10 bit |
| Phone clip with variable frame rate | Android | VFR timing |
| GoPro clip with GPS | HERO 8 or later | telemetry, 50/59.94 fps |
| Clip with a start timecode | camera with timecode, or `ffmpeg -timecode 10:00:00:00` | Premiere's Link Media compares the media start |
| Clip cut out with stream copy | `ffmpeg -ss 5 -i in.mp4 -t 20 -c copy cut.mp4` | edit list (see docs/progress.md, known limit) |
| A file renamed and one moved after the upload | | relink by fingerprint |
| File and folder names with spaces, umlauts, `#`, `%`, `&` | `Tag 3/Straße #1 50%.MOV` | URL encoding, NFC/NFD on macOS |

Upload them, let a cut be made, then export with the export page (check the originals, choose the folder again
after renaming one file and moving another).

## Premiere Pro (FCP 7 XML + SRT)

1. Unzip the package into `<footage folder>/_cutlery/`, on Windows and on macOS (one run each).
2. File → Import → the `.xml`. Expected: a new sequence, 1920×1080, the cut's frame rate.
3. **All media linked**: no clip offline. If clips are offline: is the path in the XML right
   (`<pathurl>`)? Does Link Media find them when you point at one?
4. **Frame-accurate cuts**: compare five cut points with the review video (same timecode in the sequence, same
   picture). Especially after a 29.97 fps clip and after the stream-copy clip.
5. **Start timecode**: in the Project panel, the "Media Start" of the timecode clip is 10:00:00:00.
6. **Scaling**: 4K clips fill the frame at 50 %, 1080p at 100 %, the portrait clip upright.
7. **Levels**: dialogue clips have their level; the music track has volume keyframes that duck under speech
   (check the keyframe positions against the dialogue: if they are shifted by the clip's in point, the `<when>`
   convention in `fcp7.py` is wrong – note it).
8. **Titles**: the PNG titles on V6 fade in and out; transparent background.
9. **Markers**: sequence markers for the edit list's markers, "Stabilise …", "Audio cleanup …" and "Decision: …"
   with their comments.
10. **Subtitles**: import the `.srt`, add it as a caption track; it matches the speech.
11. **Music licences**: `music/LICENSES.txt` (or `LIZENZEN.txt`) lists every track with its licence.
12. Export a few seconds (File → Export → Media) to see that the originals render at full quality.

## What the automatic tests already cover

- FCP 7 XML and FCPXML against golden files of the demo cut (cutter `tests/golden/demo/expected`).
- FCPXML against Apple's DTD of version 1.10 (cutter `tests/test_fcpxml.py`, with `CUTTER_FCPXML_DTD` set to a saved
  copy of the DTD from Apple's FCPXML reference; the DTD is Apple's and not in either repository).
- FilmCraft: a project exported for another computer is opened with filmcraft-cli on a computer where the originals
  are at the given root; it finds every file and renders the same frames as the project built on the server
  (cutter `tests/test_filmcraft_headless.py`).
- Cutlery with the real cutter on stand-ins (`tests/integration/test_cutter_standins.py`): every format names the
  originals with their timecode and size, never the server's copies; the server's own timeline scales the stand-ins.

What only an editing application can tell is listed below.

## Final Cut Pro (FCPXML 1.10 + SRT)

1. Unzip into `<footage folder>/_cutlery/` on a Mac. File → Import → XML… → the `.fcpxml`. Expected: a new event
   with the project, 1920×1080, the cut's frame rate; no warnings in the import log (if there are: note them).
2. **All media linked**; if not, is `media-rep src` right (`file:///…`, percent-encoded, NFD names)?
3. **Frame-accurate cuts** (five cut points against the review video), as for Premiere.
4. **Timecode**: the clips show the camera's start timecode (Clip inspector), drop-frame for the 29.97 fps clip.
5. **Picture**: every clip fills the frame (Spatial Conform: Fill); the portrait clip is upright and cropped like in
   the review.
6. **Stabilisation**: the shots with a stabilisation note have Stabilization on (Video inspector); Final Cut analyses
   them in the background.
7. **Audio**: dialogue levels; the music clip has volume keyframes that duck under speech; Noise Removal is on for
   the dialogue shots with noise reduction (Audio inspector → Audio Enhancements).
8. **Titles**: PNG titles on a connected lane, fading in and out (Compositing → Opacity keyframes).
9. **Captions**: an iTT caption role in the project's language with one caption per subtitle.
10. **Markers**: "Audio cleanup …" and "Decision: …" markers with their notes, on the clips where they begin.
11. Share a few seconds to see that the originals render at full quality.

## DaVinci Resolve (FCPXML 1.10 + SRT)

Resolve gets FCPXML rather than FCP 7 XML or OTIO: it keeps start timecode, frame rates, volume keyframes, markers
and captions, and Blackmagic maintains the FCPXML import for round trips with Final Cut. Check whether that holds:

1. File → Import → Timeline… → the `.fcpxml`, with "Automatically import source clips into media pool" on.
2. **All media linked**; if not, does Relink Selected Clips find them?
3. **Cuts, timecode, scaling** as for Final Cut (Resolve shows the conform as "Scale to Fill" or as Zoom).
4. **Volume keyframes** on the music clip; **opacity keyframes** on the titles. If Resolve drops either, note it:
   then the FCP 7 XML (`--to premiere`) may be the better file for Resolve, and the export should say so.
5. **Markers**: also "Stabilise …" (Resolve gets those as markers, since it may not take `adjust-stabilization`).
   Does Resolve apply the stabilisation anyway? Note it.
6. **Subtitles**: does a subtitle track come with the timeline? Otherwise File → Import → Subtitle… → the `.srt`.
7. Render a few seconds on the Deliver page.

## FilmCraft (.fcproj)

The package carries the project cutter builds on the server, made from a timeline that points to the originals on
your computer (offline on the server). FilmCraft keeps the paths and finds the files when you open it.

1. Unzip into `<footage folder>/_cutlery/` and open the `.fcproj` in FilmCraft on the editing computer (Windows,
   macOS or Linux; one run each if you can).
2. **All media online**, including the music in `_cutlery/music`. If not: is the path in the media's properties the
   one you typed on the export page? Does Relink Media find the rest after you point at one clip?
3. **Same film as in the review**: titles, credits, map, end text, captions, music ducking, stabilisation.
4. **Picture**: 4K and portrait originals fill the frame like the review's stand-ins (the project is sized for the
   originals: 4K clips at 50 %). If a rotated phone clip is turned twice or not at all, note it: FilmCraft reads the
   rotation from the file, the server built the project without the file.
5. Export a few seconds at full quality.

## Results

| Date | Application, version, OS | Package from | Result | Deviations |
|---|---|---|---|---|
| | | | | |

# NLE round trip: manual checklist

Automatic tests check what Cutlery writes (golden files, path encoding, metadata). Whether an editing application
reads it as intended can only be checked in that application. Run this checklist after changes to the export
(cutter's `export.py`, `fcp7.py`, `paths.py`, later `fcpxml.py`) and before a release. Note the application version,
the result and every deviation in the table at the end.

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

## Final Cut Pro, DaVinci Resolve, FilmCraft

Follow in milestone M6 (FCPXML writer, Resolve, FilmCraft relink).

## Results

| Date | Application, version, OS | Package from | Result | Deviations |
|---|---|---|---|---|
| | | | | |

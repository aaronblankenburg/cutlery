"""Generate the small test clips for the browser tests (needs ffmpeg with libx264 and libx265).

    python tests/fixtures/make_clips.py            # writes tests/fixtures/generated/footage/…

The clips are synthetic (test pattern and a sine tone), 2 seconds each, so the tests stay fast and need no real
footage.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

OUT = pathlib.Path(__file__).parent / "generated" / "footage"

SRC_V = "testsrc2=size={w}x{h}:rate={rate}:duration={d}"
SRC_A = "sine=frequency=440:sample_rate={sr}:duration={d}"


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def make(name, *, w=1280, h=720, rate="25", d=2, vcodec=("-c:v", "libx264", "-pix_fmt", "yuv420p"),
         sr=48000, channels=2, extra=(), pre=(), vf=None):
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path
    args = ["-f", "lavfi", "-i", SRC_V.format(w=w, h=h, rate=rate, d=d),
            "-f", "lavfi", "-i", SRC_A.format(sr=sr, d=d), *pre]
    if vf:
        args += ["-vf", vf]
    args += [*vcodec, "-c:a", "aac", "-b:a", "128k", "-ac", str(channels), "-shortest", *extra, str(path)]
    ff(*args)
    return path


def rotated(name, src, degrees):
    """A copy of SRC whose display matrix turns it by DEGREES (counter-clockwise, as ffmpeg counts)."""
    path = OUT / name
    if not path.exists():
        ff("-display_rotation", str(degrees), "-i", str(src), "-c", "copy", str(path))
    return path


def main():
    made = [
        make("Day 1/2024-06-01 10-15-00 h264.mp4"),
        make("Day 1/hevc main10.mov", vcodec=("-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1",
                                              "-x265-params", "log-level=error")),
        make("Day 2/ntsc 29.97.mov", rate="30000/1001", extra=("-timecode", "01:02:03;04")),
        make("Day 2/start timecode.mov", extra=("-timecode", "10:00:00:00")),
        rotated("Phone/portrait rotated.mp4", make("Phone/.landscape source.mp4"), 90),
        make("Phone/vfr.mp4", rate="30", vf="setpts='if(lt(N,30),N/30/TB,(30+(N-30)*2)/30/TB)'",
             extra=("-fps_mode", "passthrough", "-video_track_timescale", "30000")),
        make("Mono 44k/mono 44.1 kHz.mp4", sr=44100, channels=1),
        make("Small/already small 512x288.mp4", w=512, h=288),
    ]
    bad = OUT / "Broken" / "not a video.mts"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"\x47" + bytes(187) * 4)
    srt = OUT / "Day 1" / "2024-06-01 10-15-00 h264.SRT"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n[latitude: 52.1] [longitude: 13.2]\n\n", encoding="utf-8")
    for p in made:
        print(p.relative_to(OUT))


if __name__ == "__main__":
    sys.exit(main())

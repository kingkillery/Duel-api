"""Generate the punchy, viral-calibrated narration for duel-api.

Paced with high retention in mind:
- 3-second hook (pattern interrupt + contradiction)
- The technical gauntlet (DevTools CDP, bundle drift, four-deep money gates)
- The simulation reveal (10,000 Monte Carlo runs across Martingale/Fibonacci)
- The mathematical verdict (Kelly f* = 0, invariant fails closed)
- The loop closure & call to action

Produces:
- docs/demo.mp3 (interleaved audio master)
- tools/narration-timing.json (exact beat boundaries for the video renderer)
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import edge_tts
import imageio_ffmpeg

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
TIMING = REPO / "tools" / "narration-timing.json"

VOICE = "en-US-AndrewMultilingualNeural"
RATE = "+6%"
GAP_MS = 350  # milliseconds of breathing room between scenes

SEGMENTS: list[tuple[str, str]] = [
    ("hook",
     "Every gambling bot on GitHub promises to print money. "
     "I reverse-engineered an undocumented crypto casino API to test them all... "
     "and the backtester proved you shouldn't bet."),
    ("recon",
     "This is duel-api. Reverse-engineered directly from Chrome DevTools protocol traces. "
     "It tracks JavaScript bundle drift, manages thirty-minute token lifetimes, "
     "and locks live money execution behind four mandatory safety gates."),
    ("gauntlet",
     "Then came the backtester everyone actually wanted. "
     "Ten thousand simulated sessions across every system traders swear by. "
     "Flat stake. Martingale. Fibonacci."),
    ("verdict",
     "Here is the math: sizing changes variance, but never the sign. "
     "Expected net equals negative edge times total wagered. "
     "At every observed tier, fractional Kelly returns exactly zero. "
     "The tool's most useful feature is that it fails closed."),
    ("cta",
     "Three hundred thirty-one tests. Zero dependencies for the engine. "
     "Inspect the math for yourself on GitHub."),
]


def ffmpeg() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def duration_s(path: Path) -> float:
    out = subprocess.run([ffmpeg(), "-i", str(path)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", out)
    if not m:
        raise RuntimeError(f"could not read duration of {path}")
    h, mm, s = m.groups()
    return int(h) * 3600 + int(mm) * 60 + float(s)


async def synth_segment(text: str, out: Path) -> list[dict]:
    comm = edge_tts.Communicate(text, VOICE, rate=RATE)
    submaker = edge_tts.SubMaker()
    with open(out, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                submaker.feed(chunk)
    # Parse cue points if available
    cues = []
    for cue in submaker.cues:
        cues.append({
            "start": cue.start.total_seconds(),
            "end": cue.end.total_seconds(),
            "text": cue.content
        })
    return cues


def main() -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="duel_narr_"))
    silence = work / "silence.mp3"
    subprocess.run(
        [ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "anullsrc=r=24000:cl=mono", "-t", str(GAP_MS / 1000), str(silence)],
        check=True,
    )

    beats: dict[str, dict] = {}
    concat_lines: list[str] = []
    cursor = 0.0
    last_idx = len(SEGMENTS) - 1

    for idx, (key, text) in enumerate(SEGMENTS):
        seg = work / f"{key}.mp3"
        cues = asyncio.run(synth_segment(text, seg))
        dur = duration_s(seg)
        span = dur + (0.0 if idx == last_idx else GAP_MS / 1000)
        beats[key] = {
            "dur": round(dur, 3),
            "span": round(span, 3),
            "start": round(cursor, 3),
            "text": text,
            "cues": cues,
        }
        cursor += span
        concat_lines.append(f"file '{seg.as_posix()}'")
        if idx != last_idx:
            concat_lines.append(f"file '{silence.as_posix()}'")

    list_file = work / "concat.txt"
    list_file.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
    target = DOCS / "demo.mp3"

    subprocess.run(
        [ffmpeg(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(list_file), "-c:a", "libmp3lame", "-b:a", "128k",
         "-ar", "48000", "-ac", "2", str(target)],
        check=True,
    )

    total = duration_s(target)
    TIMING.parent.mkdir(parents=True, exist_ok=True)
    TIMING.write_text(
        json.dumps(
            {"beats": beats, "_total": round(total, 3), "_gap_ms": GAP_MS}, indent=2
        ) + "\n",
        encoding="utf-8",
    )

    print(f"Voice: {VOICE} | Rate: {RATE}")
    print(f"{'Scene':>10} {'Duration':>10} {'Span':>10} {'Start':>10}")
    for key, _ in SEGMENTS:
        b = beats[key]
        print(f"{key:>10} {b['dur']:>10.3f}s {b['span']:>10.3f}s {b['start']:>10.3f}s")
    print(f"{'TOTAL':>10} {'':>10} {cursor:>10.3f}s (file {total:.3f}s)")
    print(f"\nWrote narration to {target} ({target.stat().st_size / 1000:.1f} kB)")
    print(f"Wrote timings to {TIMING}")


if __name__ == "__main__":
    main()

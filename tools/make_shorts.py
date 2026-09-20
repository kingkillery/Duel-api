"""Native 9:16 shorts + stills for duel-api EP02 (code-as-video, Windows).

Diffusion Studio (starred) is a Mac desktop MCP. This script is the local analog:
edits live in this file; running it renders mp4/gif/png into content/media/.
"""
from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "content" / "media"
FPS = 30
W, H = 1080, 1920

BG = (13, 17, 23)
CARD = (22, 27, 34)
BORDER = (48, 54, 61)
FG = (201, 209, 216)
DIM = (139, 148, 158)
GREEN = (63, 185, 80)
WHITE = (240, 246, 252)
BLUE = (88, 166, 255)
RED = (248, 81, 73)
YELLOW = (227, 179, 81)

VOICE = "en-US-AndrewMultilingualNeural"
RATE = "+8%"

# One line per still; keep under ~5s spoken at +8%.
VO_LINES = [
    "Verdict: halt. The bot that refuses to bet.",
    "Three gates: afford the entry, stay above the floor, fund the sum of eleven slots.",
    "A tool that says don't is a tool you can trust when it says do.",
    "Install duel-api. Run doctor. Run demo. Three hundred thirty one tests. Inspect the math.",
]


def font(names: list[str], size: int) -> ImageFont.FreeTypeFont:
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    return ImageFont.load_default()


F_HUGE = font(["consolab.ttf", "consola.ttf", "arialbd.ttf"], 92)
F_BIG = font(["consolab.ttf", "consola.ttf", "arialbd.ttf"], 56)
F_MED = font(["consola.ttf", "CascadiaCode.ttf", "arial.ttf"], 36)
F_SMALL = font(["consola.ttf", "arial.ttf"], 28)


def wrap(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=f) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [text]


def card(lines: list[tuple[str, tuple[int, int, int], ImageFont.FreeTypeFont]],
         accent: tuple[int, int, int] | None = None) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((48, 280, W - 48, H - 280), radius=28, fill=CARD, outline=accent or BORDER, width=4)
    y = 420
    for text, color, fnt in lines:
        if not text:
            y += 28
            continue
        for line in wrap(d, text, fnt, W - 160):
            tw = d.textlength(line, font=fnt)
            d.text(((W - tw) / 2, y), line, fill=color, font=fnt)
            y += int(fnt.size * 1.25)
        y += 18
    d.text((64, 120), "duel-api", fill=DIM, font=F_SMALL)
    d.text((64, H - 160), "@pkslots", fill=DIM, font=F_SMALL)
    return img


def stills() -> dict[str, Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    specs = {
        "halt-card.png": [
            ("VERDICT: HALT", RED, F_HUGE),
            ("", FG, F_SMALL),
            ("the bot that refuses to bet", WHITE, F_BIG),
            ("", FG, F_SMALL),
            ("naming an unrunnable command", DIM, F_MED),
            ("would be a lie", DIM, F_MED),
        ],
        "gates-card.png": [
            ("THREE GATES", YELLOW, F_BIG),
            ("", FG, F_SMALL),
            ("1. family can afford entry", FG, F_MED),
            ("2. worst case stays above floor", FG, F_MED),
            ("3. SUM of 11 slots is fundable", FG, F_MED),
            ("", FG, F_SMALL),
            ("balance - exposure >= floor", BLUE, F_MED),
        ],
        "trust-line.png": [
            ("A tool that says DON'T", WHITE, F_BIG),
            ("is a tool you can trust", WHITE, F_BIG),
            ("when it says DO", GREEN, F_BIG),
            ("", FG, F_SMALL),
            ("331 tests. MIT licensed.", GREEN, F_MED),
        ],
        "install-card.png": [
            ("INSTALL", BLUE, F_BIG),
            ("", FG, F_SMALL),
            ("pip install --extra-index-url", FG, F_MED),
            ("https://kingkillery.github.io/Duel-api/simple/", BLUE, F_SMALL),
            ("duel-api", WHITE, F_BIG),
            ("", FG, F_SMALL),
            ("then: duel-api doctor && duel-api demo", DIM, F_MED),
        ],
    }
    paths = {}
    for name, lines in specs.items():
        dest = OUT / name
        accent = RED if "halt" in name else (GREEN if "trust" in name else BORDER)
        card(lines, accent=accent).save(dest, "PNG")
        paths[name] = dest
        print("still", dest, dest.stat().st_size)
    return paths


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def duration_s(path: Path) -> float:
    out = subprocess.run([ffmpeg_exe(), "-i", str(path)], capture_output=True, text=True).stderr
    import re
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", out)
    if not m:
        return 5.0
    h, mm, s = m.groups()
    return int(h) * 3600 + int(mm) * 60 + float(s)


async def synth_vo(lines: list[str], dest: Path) -> list[float]:
    import edge_tts

    dest.parent.mkdir(parents=True, exist_ok=True)
    durs: list[float] = []
    with tempfile.TemporaryDirectory() as td:
        parts: list[Path] = []
        for i, text in enumerate(lines):
            part = Path(td) / f"vo{i}.mp3"
            await edge_tts.Communicate(text, VOICE, rate=RATE).save(str(part))
            durs.append(max(duration_s(part), 3.5) + 0.35)
            parts.append(part)
        concat = Path(td) / "list.txt"
        concat.write_text("\n".join(f"file '{p.as_posix()}'" for p in parts), encoding="utf-8")
        subprocess.run(
            [ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", str(concat), "-c:a", "libmp3lame", "-q:a", "4", str(dest)],
            check=True,
        )
    return durs


def render_short(stills_map: dict[str, Path], durations: list[float] | None = None) -> Path:
    dest = OUT / "ep02-short-vertical.mp4"
    names = ["halt-card.png", "gates-card.png", "trust-line.png", "install-card.png"]
    fallback = [5.0, 5.5, 5.5, 6.0]
    durs = durations or fallback
    order = list(zip(names, durs))
    with tempfile.TemporaryDirectory() as td:
        concat = Path(td) / "list.txt"
        lines = []
        ff = ffmpeg_exe()
        for i, (name, dur) in enumerate(order):
            clip = Path(td) / f"{i}.mp4"
            fade = min(0.25, max(dur * 0.08, 0.12))
            subprocess.run(
                [ff, "-y", "-loglevel", "error",
                 "-loop", "1", "-i", str(stills_map[name]),
                 "-t", f"{dur:.3f}",
                 "-vf", f"fade=t=in:st=0:d={fade:.2f},fade=t=out:st={dur - fade:.2f}:d={fade:.2f}",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
                 str(clip)],
                check=True,
            )
            lines.append(f"file '{clip.as_posix()}'")
        concat.write_text("\n".join(lines), encoding="utf-8")
        silent = Path(td) / "silent.mp4"
        subprocess.run(
            [ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", str(concat), "-c:v", "libx264", "-pix_fmt", "yuv420p",
             str(silent)],
            check=True,
        )
        vo = OUT / "ep02-short-vo.mp3"
        if vo.exists():
            subprocess.run(
                [ff, "-y", "-loglevel", "error", "-i", str(silent), "-i", str(vo),
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest",
                 "-movflags", "+faststart", str(dest)],
                check=True,
            )
        else:
            subprocess.run(
                [ff, "-y", "-loglevel", "error", "-i", str(silent),
                 "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-movflags", "+faststart", str(dest)],
                check=True,
            )
    gif = OUT / "ep02-short.gif"
    subprocess.run(
        [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(dest),
         "-vf", "fps=12,scale=540:-1:flags=lanczos", "-loop", "0", str(gif)],
        check=True,
    )
    print("short", dest, dest.stat().st_size)
    print("gif", gif, gif.stat().st_size)
    return dest


def main() -> None:
    paths = stills()
    vo = OUT / "ep02-short-vo.mp3"
    durs = None
    try:
        durs = asyncio.run(synth_vo(VO_LINES, vo))
        print("vo", vo, vo.stat().st_size, durs)
    except Exception as exc:
        print("vo skipped:", type(exc).__name__, exc)
    render_short(paths, durs)
    print("done", OUT)


if __name__ == "__main__":
    main()

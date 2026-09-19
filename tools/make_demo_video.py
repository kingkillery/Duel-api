"""make_demo_video.py - render the viral, high-retention product video for duel-api.

Pacing & Architecture:
  1. Hook       (0-11s):  Kinetic typographic slam & contrast reveal ("OPTIMAL STAKE = 0")
  2. Recon      (11-26s): Animated DevTools CDP packet flow + 30-min token TTL + SHA256 tripwire
  3. Gauntlet   (26-37s): Fast-paced Monte Carlo simulations (Martingale, Fibonacci, Flat)
  4. Verdict    (37-54s): Math invariant stamp: E[net] = -edge x E[wagered], Kelly f* = -0.001000
  5. CTA        (54-61s): Clean command install + GitHub repo card

Produces:
  docs/demo.mp4           1760x990 @ 60fps silent clean master
  docs/demo-voiced.mp4    1760x990 @ 60fps voiced master with burned-in kinetic captions & SFX
  docs/demo.gif           README hero animation
  docs/demo-vertical.mp4  1080x1920 (9:16) vertical cut for X / TikTok / Shorts
  docs/poster.png         Full-res visual poster
"""

from __future__ import annotations

import array
import json
import math
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Callable, Generator, Optional

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
MP4 = DOCS / "demo.mp4"
VOICED = DOCS / "demo-voiced.mp4"
GIF = DOCS / "demo.gif"
VERTICAL = DOCS / "demo-vertical.mp4"
POSTER = DOCS / "poster.png"
NARRATION = DOCS / "demo.mp3"
TIMING = REPO / "tools" / "narration-timing.json"

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

W, H = 1760, 990
VW, VH = 1080, 1920          # 9:16 vertical cut
TITLE_H = 64
MARGIN_X = 56
LINE_H = 44
FPS = 60
CHAR_FRAMES = 2
FADE_N = 10
STATE_HOLD = 8
XFADE_N = 16                 # scene-to-scene crossfade (0.27s)
ZOOM = 0.025                 # Ken Burns drift
RISE_N = 8
SR = 48000                   # SFX sample rate

# Palette (high-contrast dark mode)
BG = (13, 17, 23)            # GitHub canvas dark #0d1117
TITLE_BG = (22, 27, 34)      # Card bg #161b22
BORDER = (48, 54, 61)        # Border #30363d
FG = (201, 209, 216)         # Primary text #c9d1d9
DIM = (139, 148, 158)        # Subdued #8b949e
GREEN = (63, 185, 80)        # Accent green #3fb950
WHITE = (240, 246, 252)      # Bright white #f0f6fc
BLUE = (88, 166, 255)        # Accent blue #58a6ff
YELLOW = (227, 179, 81)      # Gold warning #e3b341
RED = (248, 81, 73)          # Accent red #f85149
ORANGE = (255, 123, 114)     # Warm coral
BOX_BG = (22, 27, 34)
BOX_BORDER = (63, 66, 74)


def _truetype(names: list[str], size: int) -> ImageFont.FreeTypeFont:
    for name in names:
        try:
            return ImageFont.truetype(f"C:/Windows/Fonts/{name}", size)
        except OSError:
            continue
    return ImageFont.load_default()


F_TERM = _truetype(["consola.ttf", "CascadiaCode.ttf"], 30)
F_BIG = _truetype(["consolab.ttf", "consola.ttf"], 58)
F_MED = _truetype(["consola.ttf", "CascadiaCode.ttf"], 40)
F_SMALL = _truetype(["consola.ttf", "CascadiaCode.ttf"], 26)
F_CAP = _truetype(["consolab.ttf", "consola.ttf"], 34)

VF_BIG = _truetype(["consolab.ttf", "consola.ttf"], 60)
VF_MED = _truetype(["consola.ttf", "CascadiaCode.ttf"], 42)
VF_SMALL = _truetype(["consola.ttf", "CascadiaCode.ttf"], 28)
VF_CAP = _truetype(["consolab.ttf", "consola.ttf"], 36)


def text_width(font: ImageFont.FreeTypeFont, s: str) -> int:
    return font.getbbox(s)[2] if s else 0


Line = tuple[str, tuple[int, int, int]]
CardLine = tuple[str, tuple[int, int, int], ImageFont.FreeTypeFont]
Frames = Generator[Image.Image, None, None]

FALLBACK_SECONDS = {
    "hook": 11.0, "recon": 15.3, "gauntlet": 11.0, "verdict": 17.3, "cta": 6.5
}


def load_timings() -> dict[str, float]:
    if TIMING.exists():
        try:
            data = json.loads(TIMING.read_text(encoding="utf-8"))
            beats = data.get("beats", {})
            return {k: float(beats[k]["span"]) for k in beats}
        except Exception:
            pass
    return FALLBACK_SECONDS


def load_segments() -> list[tuple[str, str]]:
    if TIMING.exists():
        try:
            data = json.loads(TIMING.read_text(encoding="utf-8"))
            beats = data.get("beats", {})
            return [(k, beats[k].get("text", "")) for k in beats]
        except Exception:
            pass
    return []


# ---------------------------------------------------------------- captions --

def split_clauses(text: str) -> list[str]:
    parts = [p.strip() for p in text.replace(";", ",").replace("...", ",").split(",") if p.strip()]
    out: list[str] = []
    for p in parts:
        words = p.split()
        if len(words) > 10:
            mid = len(words) // 2
            out.append(" ".join(words[:mid]))
            out.append(" ".join(words[mid:]))
        else:
            out.append(p)
    return out or [text]


def caption_track(segments: list[tuple[str, str]],
                  timings: dict[str, float]) -> list[tuple[int, int, str]]:
    track: list[tuple[int, int, str]] = []
    cursor = 0.0
    for key, text in segments:
        span = timings.get(key, FALLBACK_SECONDS.get(key, 10.0))
        clauses = split_clauses(text)
        total_chars = sum(len(c) for c in clauses) or 1
        sub_cursor = cursor
        for c in clauses:
            dur = max(1.2, span * (len(c) / total_chars))
            f0 = int(round(sub_cursor * FPS))
            f1 = int(round((sub_cursor + dur) * FPS))
            track.append((f0, f1, c))
            sub_cursor += dur
        cursor += span
    return track


def overlay_caption(img: Image.Image, text: str,
                    font: ImageFont.FreeTypeFont, y: int) -> Image.Image:
    if not text:
        return img
    im = img.convert("RGBA")
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    tw = text_width(font, text)
    pad_x, pad_y = 22, 10
    cx = im.size[0] // 2
    x0, y0 = cx - tw // 2 - pad_x, y - pad_y
    x1, y1 = cx + tw // 2 + pad_x, y + font.size + pad_y
    d.rounded_rectangle([x0, y0, x1, y1], radius=8, fill=(13, 17, 23, 235),
                        outline=(48, 54, 61, 235), width=1)
    d.text((cx - tw // 2, y), text, font=font, fill=(240, 246, 252, 255))
    return Image.alpha_composite(im, ov).convert("RGB")


# ------------------------------------------------------------- Ken Burns ---

def ken_burns(img: Image.Image, frame: int, total_frames: int,
              zoom: float = ZOOM) -> Image.Image:
    if total_frames <= 1:
        return img
    progress = frame / (total_frames - 1)
    s = 1.0 + zoom * (1.0 - progress)
    w, h = img.size
    crop_w, crop_h = w / s, h / s
    x0, y0 = (w - crop_w) / 2.0, (h - crop_h) / 2.0
    cropped = img.crop((int(x0), int(y0), int(x0 + crop_w), int(y0 + crop_h)))
    return cropped.resize((w, h), Image.Resampling.LANCZOS)


# ---------------------------------------------------------------- Scene 1: Hook ----

def hook_stream(slot: int, marks: Optional[list[tuple[int, str]]] = None) -> Frames:
    """Pattern interrupt hook: kinetic slam with tension and contradictory reveal."""
    lines: list[tuple[str, tuple[int, int, int], ImageFont.FreeTypeFont]] = [
        ("Every gambling bot on GitHub", FG, F_MED),
        ("promises to print money.", WHITE, F_MED),
        ("", FG, F_SMALL),
        ("I reverse-engineered the API to test them all...", DIM, F_MED),
        ("", FG, F_SMALL),
        ("OPTIMAL STAKE = 0", RED, F_BIG),
        ("The backtester proved you shouldn't bet.", YELLOW, F_MED),
    ]

    heights = [f.size for _, _, f in lines]
    total_h = sum(f.size + 24 for _, _, f in lines[:-1]) + heights[-1]
    y = (H - total_h) // 2
    layout = []
    for (text, colour, font), hgt in zip(lines, heights):
        if text:
            layout.append((text, colour, font, W // 2 - text_width(font, text) // 2, y))
        y += hgt + 24

    holds = [20, 20, 36, 44, None]
    n = 0

    def frame(shown: int, rise_alpha: float, dy: int) -> Image.Image:
        base = Image.new("RGB", (W, H), BG)
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        for j, (text, colour, font, x, ly) in enumerate(layout):
            if j > shown:
                break
            if j == shown and rise_alpha < 1.0:
                od.text((x, ly + dy), text, font=font,
                        fill=(*colour, int(255 * rise_alpha)))
            else:
                od.text((x, ly), text, font=font, fill=(*colour, 255))
        return Image.alpha_composite(base.convert("RGBA"), ov).convert("RGB")

    for shown in range(len(layout)):
        for i in range(RISE_N):
            a = (i + 1) / RISE_N
            yield frame(shown, a, int((1 - a) * 18) + 1)
            n += 1
        if marks is not None:
            if shown == 1:
                marks.append((n, "tick"))
            elif shown == 3:
                marks.append((n, "whoosh"))
            elif shown == 4:
                marks.append((n, "stamp"))  # impact slam on OPTIMAL STAKE = 0
        hold = holds[shown]
        if hold is None:
            while n < slot:
                yield frame(shown, 1.0, 0)
                n += 1
            return
        for _ in range(hold):
            yield frame(shown, 1.0, 0)
            n += 1


# ---------------------------------------------------------------- Scene 2: Recon ----

def render_recon_diagram(stage: int, pulse: float = 0.0) -> Image.Image:
    """Render the architecture flow: Browser DevTools -> Drift Detection -> 4 Safety Gates."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Header
    title = "DUEL-API ARCHITECTURE"
    d.text((W // 2 - text_width(F_BIG, title) // 2, 80), title, font=F_BIG, fill=WHITE)
    sub = "Reverse-engineered protocol pipeline with failsafe invariants"
    d.text((W // 2 - text_width(F_SMALL, sub) // 2, 155), sub, font=F_SMALL, fill=DIM)

    # 3 Pipeline Cards
    cards = [
        {"title": "1. CDP CAPTURE",
         "items": ["Chrome DevTools Protocol", "Cookie TTL auto-refresh (30m)", "Tri-state session staleness"],
         "color": BLUE},
        {"title": "2. SPEC & DRIFT",
         "items": ["21 typed endpoints mapped", "Bundle hash tripwire (sha256)", "Silent UI drift detection"],
         "color": YELLOW},
        {"title": "3. 4-DEEP MONEY GATES",
         "items": ["Layer 1: Offline backtest first", "Layer 2: Explicit --yes intent", "Layer 3: Browser-minted token", "Layer 4: Zero Kelly hard fail"],
         "color": GREEN},
    ]

    card_w, card_h = 480, 480
    total_w = len(cards) * card_w + (len(cards) - 1) * 60
    start_x = (W - total_w) // 2
    card_y = 260

    for i, c in enumerate(cards):
        if i > stage:
            break
        cx = start_x + i * (card_w + 60)
        # Glow border for active stage
        border_c = c["color"] if i == stage else BOX_BORDER
        d.rounded_rectangle([cx, card_y, cx + card_w, card_y + card_h],
                            radius=12, fill=BOX_BG, outline=border_c, width=2)
        # Title banner
        d.text((cx + 32, card_y + 36), c["title"], font=F_MED, fill=c["color"])
        d.line([cx + 32, card_y + 88, cx + card_w - 32, card_y + 88], fill=BORDER, width=1)
        # Bullet items
        iy = card_y + 120
        for item in c["items"]:
            d.text((cx + 32, iy), f"- {item}", font=F_SMALL, fill=FG)
            iy += 54

        # Connective arrows between boxes
        if i < len(cards) - 1 and i < stage:
            arrow_x = cx + card_w + 12
            arrow_y = card_y + card_h // 2
            d.text((arrow_x, arrow_y - 20), "->", font=F_MED, fill=WHITE)

    return img


def recon_stream(slot: int, marks: Optional[list[tuple[int, str]]] = None) -> Frames:
    stages = 3
    hold_per = (slot - 20) // stages
    n = 0
    for s in range(stages):
        if marks is not None:
            marks.append((n, "tick" if s < 2 else "gate"))
        for _ in range(hold_per):
            yield render_recon_diagram(s)
            n += 1
    while n < slot:
        yield render_recon_diagram(stages - 1)
        n += 1


# ------------------------------------------------------------- Terminal helper ----

def viewport_rows() -> int:
    return (H - TITLE_H - 40) // LINE_H


class Terminal:
    def __init__(self, title_text: str = "duel-api") -> None:
        self.lines: list[Line] = []
        self.scroll_px = 0.0
        self.title = title_text

    def append(self, line: Line) -> None:
        self.lines.append(line)

    def scroll_target(self) -> float:
        overflow = max(0, len(self.lines) - viewport_rows())
        return overflow * LINE_H

    def ease_scroll(self, fraction: float) -> bool:
        target = self.scroll_target()
        gap = target - self.scroll_px
        if gap <= 0.5:
            self.scroll_px = target
            return False
        self.scroll_px += gap * fraction
        return True


def draw_terminal(term: Terminal, cursor_line: Line | None = None) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, W - 1, TITLE_H - 1], radius=10, fill=TITLE_BG)
    d.rectangle([0, TITLE_H - 12, W - 1, TITLE_H - 1], fill=TITLE_BG)
    for i, colour in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        cx = 34 + i * 44
        d.ellipse([cx, 22, cx + 20, 42], fill=colour)
    d.text((W // 2 - text_width(F_TERM, term.title) // 2, 20), term.title, font=F_TERM, fill=DIM)
    d.line([0, TITLE_H, W, TITLE_H], fill=BORDER)

    base_y = TITLE_H + 26
    offset = term.scroll_px
    start_idx = int(offset // LINE_H)
    sub = offset - start_idx * LINE_H
    y = base_y - sub
    for text, colour in term.lines[start_idx:]:
        if TITLE_H <= y and y + LINE_H <= H:
            d.text((MARGIN_X, y), text, font=F_TERM, fill=colour)
        y += LINE_H
    if cursor_line is not None:
        text, colour = cursor_line
        if y + LINE_H <= H:
            d.rectangle([MARGIN_X + text_width(F_TERM, text) + 6, y + 6,
                         MARGIN_X + text_width(F_TERM, text) + 23, y + LINE_H - 10],
                        fill=colour)
    return img


# ---------------------------------------------------------------- Scene 3: Gauntlet ----

GAUNTLET_EVENTS = [
    ("type", "duel-api demo --sessions 10000"),
    ("gap", None),
    ("line", ("duel-api backtester v0.1 - offline AST guarded", BLUE)),
    ("line", ("Deterministic Monte Carlo engine (10,000 sessions / schedule)", DIM)),
    ("gap", None),
    ("line", ("+--------------+------------+-----------------+-----------+-----------+", DIM)),
    ("line", ("| schedule     | EV/session |    implied edge | P(target) | P(stop)   |", WHITE)),
    ("line", ("+--------------+------------+-----------------+-----------+-----------+", DIM)),
    ("line", ("| flat         |    -0.0391 | +0.098% +/-0.1% |     0.440 |   0.560   |", FG)),
    ("line", ("| martingale-3 |    +0.0184 | -0.095% +/-0.2% |     0.470 |   0.530   |", FG)),
    ("line", ("| fibonacci-5  |    -0.0775 | +0.305% +/-0.2% |     0.481 |   0.519   |", FG)),
    ("line", ("+--------------+------------+-----------------+-----------+-----------+", DIM)),
    ("gap", None),
    ("line", ("Pooled implied edge across all schedules: +0.116% (true house edge: 0.100%)", YELLOW)),
]


def gauntlet_stream(slot: int, marks: Optional[list[tuple[int, str]]] = None) -> Frames:
    term = Terminal("duel-api demo - backtest simulation")
    n = 0

    def settle(steps: int = 6) -> Frames:
        nonlocal n
        for _ in range(steps):
            if not term.ease_scroll(0.34):
                break
            yield draw_terminal(term)

    for kind, payload in GAUNTLET_EVENTS:
        if kind == "type":
            for i in range(1, len(payload) + 1):
                typed = "$ " + payload[:i]
                for _ in range(CHAR_FRAMES):
                    if marks is not None and i % 2 == 0:
                        marks.append((n, "key"))
                    yield draw_terminal(term, cursor_line=(typed, WHITE))
                    n += 1
            term.append(("$ " + payload, WHITE))
            yield from settle()
        elif kind == "line":
            term.append(payload)
            if marks is not None:
                if payload[1] == YELLOW:
                    marks.append((n, "tick"))
                elif payload[1] == BLUE:
                    marks.append((n, "tick"))
            yield draw_terminal(term)
            n += 1
            yield draw_terminal(term)
            n += 1
            yield from settle(2)
        elif kind == "gap":
            term.append(("", FG))
            yield draw_terminal(term)
            n += 1
            yield from settle(2)

    last = draw_terminal(term)
    while n < slot:
        yield last
        n += 1


# ---------------------------------------------------------------- Scene 4: Verdict ----

VERDICT_STATES: list[list[CardLine]] = [
    [("THE MATHEMATICAL INVARIANT", WHITE, F_BIG)],
    [("THE MATHEMATICAL INVARIANT", WHITE, F_BIG),
     ("E[net]  =  -edge  x  E[total wagered]", RED, F_BIG)],
    [("THE MATHEMATICAL INVARIANT", WHITE, F_BIG),
     ("E[net]  =  -edge  x  E[total wagered]", RED, F_BIG),
     ("Sizing changes variance, never the sign.", FG, F_MED)],
    [("THE MATHEMATICAL INVARIANT", WHITE, F_BIG),
     ("E[net]  =  -edge  x  E[total wagered]", RED, F_BIG),
     ("Sizing changes variance, never the sign.", FG, F_MED),
     ("", FG, F_SMALL),
     ("Fractional Kelly:  f* = -0.001000", YELLOW, F_MED),
     ("OPTIMAL STAKE:  0.00000000", RED, F_BIG)],
    [("THE MATHEMATICAL INVARIANT", WHITE, F_BIG),
     ("E[net]  =  -edge  x  E[total wagered]", RED, F_BIG),
     ("Sizing changes variance, never the sign.", FG, F_MED),
     ("", FG, F_SMALL),
     ("Fractional Kelly:  f* = -0.001000", YELLOW, F_MED),
     ("OPTIMAL STAKE:  0.00000000", RED, F_BIG),
     ("", FG, F_SMALL),
     ("VERDICT: FAILS CLOSED. REFUSES TO BET.", GREEN, F_BIG)],
]


def render_card_state(lines: list[CardLine], w: int = W, h: int = H) -> Image.Image:
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    heights = [f.size for _, _, f in lines]
    total_h = sum(hgt + 24 for hgt in heights[:-1]) + heights[-1]
    y = (h - total_h) // 2
    for (text, colour, font), hgt in zip(lines, heights):
        if text:
            tw = text_width(font, text)
            d.text(((w - tw) // 2, y), text, font=font, fill=colour)
        y += hgt + 24
    return img


def verdict_stream(slot: int, marks: Optional[list[tuple[int, str]]] = None) -> Frames:
    imgs = [render_card_state(st) for st in VERDICT_STATES]
    n = 0

    def emit(image: Image.Image) -> Image.Image:
        nonlocal n
        n += 1
        return image

    for _ in range(STATE_HOLD):
        yield emit(imgs[0])

    for s, (prev, cur) in enumerate(zip(imgs, imgs[1:]), start=1):
        for i in range(1, FADE_N + 1):
            yield emit(Image.blend(prev, cur, i / (FADE_N + 1)))
        if marks is not None:
            if s in (1, 3):
                marks.append((n, "stamp"))  # heavy stamp for formula and Kelly 0
            elif s == 4:
                marks.append((n, "gate"))   # gate drop for FAILS CLOSED
        for _ in range(STATE_HOLD):
            yield emit(cur)

    while n < slot:
        yield emit(imgs[-1])


# ---------------------------------------------------------------- Scene 5: CTA ----

CTA_STATES: list[list[CardLine]] = [
    [("297 TESTS. MIT LICENSED.", GREEN, F_BIG)],
    [("297 TESTS. MIT LICENSED.", GREEN, F_BIG),
     ("Zero network dependencies for the engine.", FG, F_MED)],
    [("297 TESTS. MIT LICENSED.", GREEN, F_BIG),
     ("Zero network dependencies for the engine.", FG, F_MED),
     ("", FG, F_SMALL),
     ("uvx --from git+https://github.com/kingkillery/Duel-api duel-api doctor", BLUE, F_MED)],
    [("297 TESTS. MIT LICENSED.", GREEN, F_BIG),
     ("Zero network dependencies for the engine.", FG, F_MED),
     ("", FG, F_SMALL),
     ("uvx --from git+https://github.com/kingkillery/Duel-api duel-api doctor", BLUE, F_MED),
     ("", FG, F_SMALL),
     ("Inspect the math. Star the repo on GitHub.", WHITE, F_BIG)],
]


def cta_stream(slot: int, marks: Optional[list[tuple[int, str]]] = None) -> Frames:
    imgs = [render_card_state(st) for st in CTA_STATES]
    n = 0

    def emit(image: Image.Image) -> Image.Image:
        nonlocal n
        n += 1
        return image

    for _ in range(STATE_HOLD):
        yield emit(imgs[0])
    for s, (prev, cur) in enumerate(zip(imgs, imgs[1:]), start=1):
        for i in range(1, FADE_N + 1):
            yield emit(Image.blend(prev, cur, i / (FADE_N + 1)))
        if marks is not None and s in (2, 3):
            marks.append((n, "tick"))
        for _ in range(STATE_HOLD):
            yield emit(cur)
    while n < slot:
        yield emit(imgs[-1])


# ---------------------------------------------------------------- Master chaining ----

SceneDef = tuple[str, int, Callable[[], Frames], list[tuple[int, str]]]


def build_scenes(timings: dict[str, float]) -> list[SceneDef]:
    def slot(key: str) -> int:
        return int(round(timings.get(key, FALLBACK_SECONDS.get(key, 10.0)) * FPS))

    m_hook: list[tuple[int, str]] = []
    m_recon: list[tuple[int, str]] = []
    m_gaunt: list[tuple[int, str]] = []
    m_verd: list[tuple[int, str]] = []
    m_cta: list[tuple[int, str]] = []

    return [
        ("hook", slot("hook"), lambda: hook_stream(slot("hook"), m_hook), m_hook),
        ("recon", slot("recon"), lambda: recon_stream(slot("recon"), m_recon), m_recon),
        ("gauntlet", slot("gauntlet"), lambda: gauntlet_stream(slot("gauntlet"), m_gaunt), m_gaunt),
        ("verdict", slot("verdict"), lambda: verdict_stream(slot("verdict"), m_verd), m_verd),
        ("cta", slot("cta"), lambda: cta_stream(slot("cta"), m_cta), m_cta),
    ]


def master_stream(scenes: list[SceneDef],
                  zoom_scenes: set[str] = {"verdict", "cta"},
                  captions: Optional[list[tuple[int, int, str]]] = None) -> Frames:
    cap_map: dict[int, str] = {}
    if captions:
        for f0, f1, text in captions:
            for f in range(f0, f1):
                cap_map[f] = text

    gi = 0
    prev_last: Optional[Image.Image] = None
    for name, slot, gen, _marks in scenes:
        count = 0
        last: Optional[Image.Image] = None
        for si, img in enumerate(gen()):
            if name in zoom_scenes:
                img = ken_burns(img, si, slot)
            if prev_last is not None and si < XFADE_N:
                img = Image.blend(prev_last, img, (si + 1) / XFADE_N)
            if gi in cap_map:
                img = overlay_caption(img, cap_map[gi], F_CAP, H - 150)
            yield img
            last = img
            gi += 1
            count += 1
        prev_last = last


# ---------------------------------------------------------------- SFX Synthesis ----

def _fract(x: float) -> float:
    return x - math.floor(x)


def _noise(i: int) -> float:
    return _fract(math.sin((i + 1) * 12.9898) * 43758.5453) * 2.0 - 1.0


_BURSTS: dict[str, array.array] = {}


def _burst(kind: str) -> array.array:
    if kind in _BURSTS:
        return _BURSTS[kind]
    samples: list[float] = []
    if kind in ("key", "tick"):
        n = 300 if kind == "key" else 180
        decay = 70.0 if kind == "key" else 55.0
        amp = 0.40 if kind == "key" else 0.22
        raw = [_noise(i) for i in range(n)]
        hp = [raw[0]] + [raw[i] - raw[i - 1] for i in range(1, n)]
        samples = [hp[i] * math.exp(-i / decay) * amp for i in range(n)]
    elif kind == "stamp":
        n = int(0.18 * SR)
        for i in range(n):
            t = i / SR
            v = math.sin(2 * math.pi * 85 * t) * math.exp(-t / 0.055) * 0.85
            if i < 120:
                v += _noise(i) * 0.35 * math.exp(-i / 40)
            samples.append(v)
    elif kind == "gate":
        n = int(0.24 * SR)
        for i in range(n):
            t = i / SR
            v = math.sin(2 * math.pi * 60 * t) * math.exp(-t / 0.09) * 0.95
            if i < 140:
                v += _noise(i) * 0.40 * math.exp(-i / 45)
            samples.append(v)
    elif kind == "whoosh":
        n = int(0.30 * SR)
        raw = [_noise(i) for i in range(n)]
        acc = 0.0
        lp: list[float] = []
        for i in range(n):
            acc += raw[i]
            if i >= 96:
                acc -= raw[i - 96]
            lp.append(acc / 96.0)
        samples = [lp[i] * (math.sin(math.pi * i / n) ** 2) * 2.2 for i in range(n)]
    else:
        samples = [0.0]

    out = array.array("h", [max(-32767, min(32767, int(v * 32767))) for v in samples])
    _BURSTS[kind] = out
    return out


def write_sfx(path: Path, total_frames: int, events: list[tuple[int, str]]) -> int:
    total = int(total_frames / FPS * SR) + SR // 2
    buf = array.array("h", bytes(4 * total))
    for frame_idx, kind in events:
        burst = _burst(kind)
        base = int(round(frame_idx / FPS * SR)) * 2
        for j, v in enumerate(burst):
            idx = base + 2 * j
            if idx + 1 >= len(buf):
                break
            buf[idx] = max(-32767, min(32767, buf[idx] + v))
            buf[idx + 1] = max(-32767, min(32767, buf[idx + 1] + v))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(buf.tobytes())
    return len(events)


def global_marks(scenes: list[SceneDef]) -> list[tuple[int, str]]:
    events: list[tuple[int, str]] = []
    offset = 0
    for _name, slot, _gen, marks in scenes:
        for f, kind in marks:
            events.append((offset + f, kind))
        offset += slot
    return sorted(events, key=lambda p: p[0])


# ---------------------------------------------------------------- Codec / Mux ----

def _read_exact(stream, n: int) -> Optional[bytes]:
    data = bytearray()
    while len(data) < n:
        chunk = stream.read(n - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data) if len(data) == n else None


def encode_stream(frames: Frames, dest: Path, w: int, h: int) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(FPS),
        "-i", "-",
        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(dest),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    count = 0
    for frame in frames:
        proc.stdin.write(frame.tobytes())
        count += 1
    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0:
        sys.exit(f"ffmpeg encode failed with {proc.returncode}")
    print(f"Master MP4: {count} frames, {count / FPS:.1f}s -> {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
    return count


def caption_pass(src: Path, dest: Path, w: int, h: int,
                 track: list[tuple[int, int, str]]) -> int:
    cap_map: dict[int, str] = {}
    for f0, f1, text in track:
        for f in range(f0, f1):
            cap_map[f] = text
    dec = subprocess.Popen(
        [FFMPEG, "-loglevel", "error", "-i", str(src),
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE)
    encode_cmd = [
        FFMPEG, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(FPS),
        "-i", "-",
        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(dest),
    ]
    enc = subprocess.Popen(encode_cmd, stdin=subprocess.PIPE)
    framebytes = w * h * 3
    i = 0
    while True:
        raw = _read_exact(dec.stdout, framebytes)
        if raw is None:
            break
        img = Image.frombytes("RGB", (w, h), raw)
        if i in cap_map:
            img = overlay_caption(img, cap_map[i], F_CAP, h - 150)
        enc.stdin.write(img.tobytes())
        i += 1
    dec.stdout.close()
    dec.wait()
    enc.stdin.close()
    enc.wait()
    return i


def mux_voiced(video: Path, narration: Path, sfx: Path, dest: Path) -> None:
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error",
         "-i", str(video), "-i", str(narration), "-i", str(sfx),
         "-filter_complex",
         "[1:a]aformat=sample_rates=48000:channel_layouts=stereo[vo];"
         "[2:a]aformat=sample_rates=48000:channel_layouts=stereo[sx];"
         "[vo][sx]amix=inputs=2:duration=first:normalize=0[m]",
         "-map", "0:v:0", "-map", "[m]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
         "-shortest", "-movflags", "+faststart", str(dest)],
        check=True,
    )
    print(f"Voiced MP4: -> {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")


def encode_gif(starts: dict[str, float]) -> None:
    begin = starts.get("gauntlet", 0.0) + 0.3
    end = starts.get("verdict", begin + 10.0) + 5.0
    cmd = [
        FFMPEG, "-y", "-loglevel", "error",
        "-ss", f"{begin:.2f}", "-i", str(MP4),
        "-to", f"{end:.2f}",
        "-vf",
        "fps=12,scale=880:-1:flags=lanczos,split[s0][s1];"
        "[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer:bayer_scale=4",
        str(GIF),
    ]
    subprocess.run(cmd, check=True)
    print(f"GIF hero: -> {GIF.name} ({GIF.stat().st_size / 1e6:.1f} MB)")


# ---------------------------------------------------------------- Vertical 9:16 ----

def render_vertical(timings: dict[str, float], starts: dict[str, float],
                    segments: list[tuple[str, str]], tmpdir: Path) -> None:
    """Fast-paced 9:16 vertical cut for X / TikTok / Shorts."""
    # Compose 1080x1920: scale 16:9 frame to 1080 width and center vertically with top/bottom bars
    vert_cap = tmpdir / "vert_cap.mp4"
    vert_sfx = tmpdir / "vert_sfx.wav"

    cmd = [
        FFMPEG, "-y", "-loglevel", "error",
        "-i", str(VOICED),
        "-vf",
        "scale=1080:607,pad=1080:1920:0:656:color=0x0d1117",
        "-c:v", "libx264", "-crf", "19", "-preset", "fast",
        "-c:a", "copy",
        str(VERTICAL),
    ]
    subprocess.run(cmd, check=True)
    print(f"Vertical 9:16: -> {VERTICAL.name} ({VERTICAL.stat().st_size / 1e6:.1f} MB)")


def main() -> None:
    timings = load_timings()
    segments = load_segments()

    cursor = 0.0
    starts: dict[str, float] = {}
    for key in ("hook", "recon", "gauntlet", "verdict", "cta"):
        starts[key] = cursor
        cursor += timings.get(key, FALLBACK_SECONDS.get(key, 10.0))

    scenes = build_scenes(timings)
    total_frames = sum(slot for _, slot, _, _ in scenes)

    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)

        # 1. Silent clean master
        print("Rendering visual master...")
        encode_stream(master_stream(scenes, {"verdict", "cta"}), MP4, W, H)

        # 2. Gather SFX marks
        events = global_marks(scenes)
        sfx_path = tmpdir / "demo-sfx.wav"
        write_sfx(sfx_path, total_frames, events)
        print(f"SFX: {len(events)} events written.")

        # 3. Burned-in captions + Voiced Mix
        if NARRATION.exists():
            track = caption_track(segments, timings)
            cap_tmp = tmpdir / "demo-captioned.mp4"
            print("Encoding kinetic captioned pass...")
            caption_pass(MP4, cap_tmp, W, H, track)
            mux_voiced(cap_tmp, NARRATION, sfx_path, VOICED)
        else:
            print("Warning: demo.mp3 narration not found.")

        # 4. Hero GIF
        encode_gif(starts)

        # 5. Vertical cut
        if VOICED.exists():
            render_vertical(timings, starts, segments, tmpdir)

    # 6. Poster snapshot
    poster_img = render_card_state(VERDICT_STATES[-1])
    poster_img.save(POSTER)
    print(f"Poster: -> {POSTER.name}")
    print("\nAll video artifacts rendered successfully!")


if __name__ == "__main__":
    main()

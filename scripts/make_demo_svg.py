"""Generate docs/assets/demo.svg - an animated terminal rendering of the real
``duel-api doctor`` output.

The demo is generated from live output rather than mocked, so it cannot drift
from what the tool actually prints. Local filesystem paths are masked before
rendering. SMIL animations are used because they survive GitHub's image proxy.

Usage:  py -3.13 scripts/make_demo_svg.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "assets" / "demo.svg"

FONT = 13.0
LINE_H = 17.0
CHAR_W = 7.8
PAD = 24.0
BAR = 34.0
MAX_LINES = 34
CYCLE = 14.0  # seconds per loop

BG = "#0d1117"
BAR_BG = "#161b22"
BORDER = "#30363d"
DEFAULT = "#c9d1d9"

ANSI = re.compile(r"\x1b\[([0-9;]*)m")
WINPATH = re.compile(r"[A-Za-z]:\\[^\s\"']+")

COLORS = {
    "30": "#8b949e", "31": "#f85149", "32": "#3fb950", "33": "#d29922",
    "34": "#58a6ff", "35": "#bc8cff", "36": "#39c5cf", "37": DEFAULT,
    "90": "#8b949e", "91": "#ffa198", "92": "#56d364", "93": "#e3b341",
    "94": "#79c0ff", "95": "#d2a8ff", "96": "#56d4dd", "97": "#f0f6fc",
}


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def parse_ansi(line: str):
    """Yield (text, fill, weight) segments for one ANSI-styled line."""
    segments: list[tuple[str, str, str]] = []
    color, weight = DEFAULT, "normal"
    pos = 0
    for m in ANSI.finditer(line):
        chunk = line[pos : m.start()]
        if chunk:
            segments.append((chunk, color, weight))
        for code in (m.group(1) or "0").split(";"):
            if code == "0":
                color, weight = DEFAULT, "normal"
            elif code == "1":
                weight = "bold"
            elif code == "2":
                weight = "dim"
            elif code in COLORS:
                color = COLORS[code]
        pos = m.end()
    tail = line[pos:]
    if tail:
        segments.append((tail, color, weight))
    return segments


def collect_output() -> list[str]:
    env = dict(os.environ, FORCE_COLOR="1")
    env.pop("NO_COLOR", None)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "automation_cli.py"), "doctor"],
        capture_output=True, text=True, env=env, cwd=str(ROOT), encoding="utf-8",
    )
    if proc.returncode != 0:
        raise SystemExit(f"doctor failed: {proc.stderr[:400]}")
    lines = [WINPATH.sub("…", ln).rstrip() for ln in proc.stdout.splitlines()]
    return [ln for ln in lines if ln.strip() != "" or True][:MAX_LINES]


def line_element(index: int, line: str, width: float) -> str:
    y = BAR + 8 + index * LINE_H
    t0 = 0.4 + index * 0.28
    f0 = min(t0 / CYCLE, 0.98)
    f1 = min((t0 + 0.15) / CYCLE, 0.995)
    spans, x = [], PAD
    for text, fill, weight in parse_ansi(line):
        fw = "bold" if weight == "bold" else "normal"
        ff = "#8b949e" if weight == "dim" else fill
        spans.append(
            f'<tspan x="{x:.1f}" fill="{ff}" font-weight="{fw}">{esc(text)}</tspan>'
        )
        x += len(text) * CHAR_W
    cursor_x = min(x + 2, width - PAD)
    return (
        f'<g opacity="0">'
        f'<animate attributeName="opacity" values="0;0;1;1" '
        f'keyTimes="0;{f0:.4f};{f1:.4f};1" dur="{CYCLE}s" repeatCount="indefinite"/>'
        f'<text class="l">{"".join(spans)}</text>'
        f'<rect x="{cursor_x:.1f}" y="{y - FONT + 2:.1f}" width="{CHAR_W:.1f}" height="{FONT - 1:.0f}" fill="{DEFAULT}">'
        f'<animate attributeName="opacity" values="0;0;1;0" keyTimes="0;{f0:.4f};{f1:.4f};{f1 + 0.03:.4f}" dur="{CYCLE}s" repeatCount="indefinite"/>'
        f'</rect></g>'
    )


def render(lines: list[str]) -> str:
    longest = max((len(ANSI.sub("", ln)) for ln in lines), default=60)
    width = max(PAD * 2 + longest * CHAR_W + 20, 640)
    height = BAR + 16 + len(lines) * LINE_H + 14
    body = "\n".join(line_element(i, ln, width) for i, ln in enumerate(lines))
    dots = "".join(
        f'<circle cx="{PAD + 10 + i * 18:.0f}" cy="{BAR / 2:.0f}" r="5" fill="{c}"/>'
        for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840"))
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}" role="img" aria-label="duel-api doctor animated terminal demo">
  <style>
    .l {{ font: {FONT}px ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; dominant-baseline: text-before-edge; }}
    .t {{ font: 12px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; fill: #8b949e; }}
  </style>
  <rect x="1" y="1" width="{width - 2:.0f}" height="{height - 2:.0f}" rx="8" fill="{BG}" stroke="{BORDER}" stroke-width="1.5"/>
  <path d="M1 {BAR} h {width - 2:.0f} v -{BAR - 9:.0f} a 8 8 0 0 0 -8 -8 h {-(width - 20):.0f} a 8 8 0 0 0 -8 8 z" fill="{BAR_BG}"/>
  {dots}
  <text class="t" x="{width / 2:.0f}" y="{BAR / 2 - 6:.0f}" text-anchor="middle">duel-api — doctor</text>
{body}
</svg>
'''


def main() -> None:
    lines = collect_output()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(lines), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {len(lines)} lines)")


if __name__ == "__main__":
    main()

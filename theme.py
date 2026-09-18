"""Terminal presentation helpers, deliberately free of third-party imports.

The offline tier (``doctor``, and the planned ``demo``) has to run from ``uvx``
with nothing extra installed, so this module writes ANSI escapes directly rather
than depending on a rendering library.

Colour is opt-out via ``NO_COLOR`` and force-able via ``FORCE_COLOR``; off a TTY
output degrades to plain text so a piped run stays byte-comparable. Box drawing
falls back to ASCII when the stream encoding cannot carry it.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Sequence

__all__ = [
    "configure",
    "color_enabled",
    "unicode_enabled",
    "style",
    "bold",
    "dim",
    "red",
    "green",
    "yellow",
    "cyan",
    "grey",
    "sym",
    "visible_len",
    "pad",
    "rule",
    "kv",
    "ok",
    "fail",
    "warn",
    "table",
]

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_CODES = {
    "bold": "1",
    "dim": "2",
    "italic": "3",
    "underline": "4",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "white": "37",
    "grey": "90",
}

_BOX = {
    True: dict(tl="\u250c", tr="\u2510", bl="\u2514", br="\u2518", h="\u2500", v="\u2502", lm="\u251c", rm="\u2524"),
    False: dict(tl="+", tr="+", bl="+", br="+", h="-", v="|", lm="+", rm="+"),
}

_SYM = {
    True: dict(ok="\u2713", fail="\u2717", warn="!", bullet="\u2022", arrow="\u2192"),
    False: dict(ok="ok", fail="x", warn="!", bullet="-", arrow="->"),
}

_COLOR: bool | None = None
_UNICODE: bool | None = None


def _enable_vt() -> bool:
    """Turn on ANSI processing for legacy Windows consoles."""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def _detect_color(stream) -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    try:
        if not stream.isatty():
            return False
    except Exception:
        return False
    return _enable_vt()


def _detect_unicode(stream) -> bool:
    encoding = (getattr(stream, "encoding", None) or "").lower()
    if "utf" not in encoding:
        return False
    try:
        "\u2500\u2713".encode(encoding)
    except Exception:
        return False
    return True


def configure(*, color: bool | None = None, unicode: bool | None = None, stream=None) -> None:
    """Resolve presentation flags once; auto-detected unless overridden."""
    global _COLOR, _UNICODE
    stream = stream if stream is not None else sys.stdout
    _COLOR = _detect_color(stream) if color is None else bool(color)
    _UNICODE = _detect_unicode(stream) if unicode is None else bool(unicode)


def color_enabled() -> bool:
    if _COLOR is None:
        configure()
    return bool(_COLOR)


def unicode_enabled() -> bool:
    if _UNICODE is None:
        configure()
    return bool(_UNICODE)


def style(text: object, *names: str) -> str:
    text = str(text)
    codes = [_CODES[n] for n in names if n in _CODES]
    if not codes or not color_enabled():
        return text
    return f"\x1b[{';'.join(codes)}m{text}\x1b[0m"


def bold(text: object) -> str:
    return style(text, "bold")


def dim(text: object) -> str:
    return style(text, "dim")


def red(text: object) -> str:
    return style(text, "red")


def green(text: object) -> str:
    return style(text, "green")


def yellow(text: object) -> str:
    return style(text, "yellow")


def cyan(text: object) -> str:
    return style(text, "cyan")


def grey(text: object) -> str:
    return style(text, "grey")


def sym(name: str) -> str:
    return _SYM[unicode_enabled()][name]


def visible_len(text: str) -> int:
    return len(_ANSI_RE.sub("", text))


def pad(text: str, width: int, align: str = "left") -> str:
    gap = max(0, width - visible_len(text))
    if align == "right":
        return " " * gap + text
    if align == "center":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def rule(width: int = 66, char: str | None = None) -> str:
    return (char or _BOX[unicode_enabled()]["h"]) * width


def kv(key: str, value: object, *, key_width: int = 18) -> str:
    return f"  {pad(dim(key), key_width)}{value}"


def ok(text: object) -> str:
    return f"{green(sym('ok'))} {text}"


def fail(text: object) -> str:
    return f"{red(sym('fail'))} {text}"


def warn(text: object) -> str:
    return f"{yellow(sym('warn'))} {text}"


def table(
    headers: Sequence[str],
    rows: Sequence[Sequence[object]],
    *,
    aligns: Sequence[str] | None = None,
) -> str:
    """Render a bordered table; widths account for ANSI escapes."""
    heads = [str(h) for h in headers]
    body = [[str(c) for c in row] for row in rows]
    cols = len(heads)
    align = list(aligns or ["left"] * cols)

    widths = [visible_len(h) for h in heads]
    for row in body:
        for i in range(cols):
            if i < len(row):
                widths[i] = max(widths[i], visible_len(row[i]))

    box = _BOX[unicode_enabled()]
    bar = lambda l, m, r: l + m.join(box["h"] * (w + 2) for w in widths) + r  # noqa: E731
    head = box["v"] + box["v"].join(
        f" {pad(bold(heads[i]), widths[i], align[i])} " for i in range(cols)
    ) + box["v"]
    lines = [bar(box["tl"], box["h"], box["tr"]), head, bar(box["lm"], box["h"], box["rm"])]
    for row in body:
        cells = [row[i] if i < len(row) else "" for i in range(cols)]
        lines.append(
            box["v"] + box["v"].join(f" {pad(cells[i], widths[i], align[i])} " for i in range(cols)) + box["v"]
        )
    lines.append(bar(box["bl"], box["h"], box["br"]))
    return "\n".join(lines)
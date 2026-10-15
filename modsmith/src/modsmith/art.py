"""Pixel art for the desktop app: the anvil scene, the blocky MODSMITH logo,
and the window / .exe icon. Everything is drawn from code (no image files),
and PNG/ICO encoding uses only the standard library.

    python -m modsmith.art modsmith.ico     # write the Windows icon
"""
from __future__ import annotations

import base64
import struct
import sys
import zlib
from pathlib import Path

# Forge palette, shared with the GUI theme.
PALETTE = {
    "K": "#141214",   # outline / soot
    "D": "#3a3f47",   # dark iron
    "M": "#5c636d",   # iron
    "L": "#8c949e",   # light iron
    "W": "#c9d1d9",   # polished edge
    "R": "#b5380c",   # cooling metal
    "O": "#ff7b1c",   # ember
    "Y": "#ffd34d",   # white-hot
    "B": "#80502a",   # handle wood
    "b": "#53321a",   # handle shadow
}

Pixels = dict  # {(x, y): palette key}


def _outline(px: Pixels) -> Pixels:
    out = dict(px)
    for (x, y) in px:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            out.setdefault((x + dx, y + dy), "K")
    return out


def _rows(spans: dict[int, tuple[int, int]], shade) -> Pixels:
    px = {}
    for y, (x0, x1) in spans.items():
        for x in range(x0, x1 + 1):
            px[(x, y)] = shade(x, y, x0, x1)
    return px


def anvil() -> Pixels:
    """A side-on anvil, horn to the left, 32 px wide grid."""
    spans = {14: (8, 28), 15: (4, 28), 16: (1, 28), 17: (4, 28), 18: (8, 27),
             19: (11, 24), 20: (12, 23), 21: (12, 23), 22: (11, 24),
             23: (9, 26), 24: (7, 28), 25: (6, 29), 26: (6, 29)}

    def shade(x, y, x0, x1):
        if y == 14:
            return "W"
        if y in (25, 26):
            return "D" if x < x1 - 1 else "K"
        if x >= x1 - 1:
            return "D"
        if y == 15 or (y in (23, 24) and x < x0 + 3):
            return "L"
        if y == 18 or y == 22:
            return "D"
        return "M"
    return _outline(_rows(spans, shade))


def ingot() -> Pixels:
    """The glowing work piece resting on the anvil face."""
    spans = {11: (15, 22), 12: (14, 23), 13: (14, 23)}

    def shade(x, y, x0, x1):
        if y == 11:
            return "Y"
        if y == 13:
            return "R" if x in (x0, x1) else "O"
        return "O" if x in (x0, x1) else "Y"
    return _outline(_rows(spans, shade))


def hammer(dy: int = 0) -> Pixels:
    """A smithing hammer raised over the ingot; ``dy`` lowers it for a strike."""
    px = {}
    for y in range(2, 9):            # head
        for x in range(12, 18):
            px[(x, y + dy)] = "W" if y == 2 else ("L" if x == 12 else ("D" if x == 17 else "M"))
    for x in range(18, 30):          # handle
        px[(x, 4 + dy)] = "B"
        px[(x, 5 + dy)] = "b"
    return _outline(px)


def scene(hammer_dy: int = 0, with_hammer: bool = True) -> Pixels:
    px = anvil()
    px.update(ingot())
    if with_hammer:
        px.update(hammer(hammer_dy))
    for p in ((9, 9), (26, 8), (7, 12), (28, 11)):   # resting sparks
        px.setdefault(p, "O")
    return px


# --------------------------------------------------------------------- logo

GLYPHS = {
    "M": ["X...X", "XX.XX", "X.X.X", "X.X.X", "X...X", "X...X", "X...X"],
    "O": [".XXX.", "X...X", "X...X", "X...X", "X...X", "X...X", ".XXX."],
    "D": ["XXXX.", "X...X", "X...X", "X...X", "X...X", "X...X", "XXXX."],
    "S": [".XXXX", "X....", "X....", ".XXX.", "....X", "....X", "XXXX."],
    "I": ["XXX", ".X.", ".X.", ".X.", ".X.", ".X.", "XXX"],
    "T": ["XXXXX", "..X..", "..X..", "..X..", "..X..", "..X..", "..X.."],
    "H": ["X...X", "X...X", "X...X", "XXXXX", "X...X", "X...X", "X...X"],
    " ": ["...", "...", "...", "...", "...", "...", "..."],
}
LOGO_ROW_SHADES = ["W", "W", "L", "L", "M", "M", "D"]


def logo(text: str = "MODSMITH") -> tuple[Pixels, int]:
    """Blocky stone/iron lettering with a drop shadow. Returns (pixels, width)."""
    body, x = {}, 0
    for ch in text.upper():
        g = GLYPHS[ch]
        for y, row in enumerate(g):
            for i, c in enumerate(row):
                if c == "X":
                    body[(x + i, y)] = LOGO_ROW_SHADES[y]
        x += len(g[0]) + 1
    px = {(x + 1, y + 1): "K" for (x, y) in body}
    px.update(body)
    return px, x


# --------------------------------------------------------------------- images

def _rgba(key: str) -> tuple[int, int, int, int]:
    h = PALETTE[key]
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16), 255


def render(px: Pixels, size: int = 32, grid: int = 32) -> list[list[tuple]]:
    """Nearest-neighbour render of a ``grid``-sized pixel map to ``size`` x ``size``."""
    clear = (0, 0, 0, 0)
    return [[_rgba(px[(x * grid // size, y * grid // size)]) if (x * grid // size, y * grid // size) in px
             else clear for x in range(size)] for y in range(size)]


def png(image: list[list[tuple]]) -> bytes:
    h, w = len(image), len(image[0])
    raw = b"".join(b"\0" + bytes(c for p in row for c in p) for row in image)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico(sizes=(16, 24, 32, 48, 64, 128, 256)) -> bytes:
    """A Windows .ico holding PNG-compressed images of the anvil scene."""
    px = scene()
    images = [png(render(px, s)) for s in sizes]
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for s, data in zip(sizes, images):
        out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return out + b"".join(images)


def icon_png_base64(size: int = 64) -> str:
    """For Tk's ``PhotoImage(data=...)``."""
    return base64.b64encode(png(render(scene(), size))).decode("ascii")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    out = Path(argv[0] if argv else "modsmith.ico")
    out.write_bytes(ico() if out.suffix.lower() == ".ico" else png(render(scene(), 256)))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

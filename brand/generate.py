#!/usr/bin/env python3
"""Regenerate every Yeliztli brand asset from the single source SVG.

Source of truth: ``brand/logo-source.svg`` — a 1254x1254 square with a white
rounded-rect background and a *single* dark-navy (#001B3E) path that draws the
DNA/geometric emblem plus a "YELIZTLI" wordmark along the bottom row.

Because the artwork is one fill path made only of ``M``/``L``/``Z`` commands, we
can split it into sub-paths, drop the wordmark glyphs (bottom row) to obtain an
emblem-only mark, recolour by swapping the fill, and crop via a computed
viewBox. Rasters are produced with CairoSVG + Pillow.

Run manually (not wired into CI):  python brand/generate.py
Requires:  cairosvg, pillow

Colour system (see brand/README.md):
  emblem teal   #0D9488  — matches the app --color-primary; tab favicon + docs header
  emblem white  #FFFFFF  — on the teal docs header bar
  emblem navy   #001B3E  — the artwork's native colour; standalone install icons
  background    #FFFFFF  — opaque white for install icons (Apple/maskable ignore alpha)
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import cairosvg
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = Path(__file__).resolve().parent / "logo-source.svg"

TEAL = "#0D9488"   # app --color-primary (light); matches existing favicon
WHITE = "#FFFFFF"
NAVY = "#001B3E"   # artwork native colour

# Sub-paths whose starting Y is at/below this line are the "YELIZTLI" wordmark.
# The emblem's lowest sub-path starts at y~887; the wordmark row starts at y=1078.
WORDMARK_Y = 1000

NUM = re.compile(r"-?\d+(?:\.\d+)?")


def load_source() -> tuple[str, str]:
    """Return (svg_text, path_d) from the source file."""
    text = SOURCE.read_text(encoding="utf-8")
    m = re.search(r'<path\b[^>]*\bd="([^"]*)"', text, re.DOTALL)
    if not m:
        raise SystemExit("no <path d=...> found in source SVG")
    return text, m.group(1)


def split_subpaths(d: str) -> list[str]:
    """Split a path 'd' (M/L/Z only) into sub-path strings, each starting 'M'."""
    parts = re.split(r"(?=M)", d.strip())
    return [p.strip() for p in parts if p.strip()]


def start_xy(subpath: str) -> tuple[float, float]:
    nums = NUM.findall(subpath)
    return float(nums[0]), float(nums[1])


def bbox(subpaths: list[str]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for sp in subpaths:
        nums = [float(n) for n in NUM.findall(sp)]
        xs.extend(nums[0::2])
        ys.extend(nums[1::2])
    return min(xs), min(ys), max(xs), max(ys)


def emblem_svg(path_d: str, fill: str, *, pad_frac: float = 0.06) -> str:
    """Emblem-only SVG (wordmark removed), tightly cropped, transparent bg."""
    subs = split_subpaths(path_d)
    emblem = [sp for sp in subs if start_xy(sp)[1] < WORDMARK_Y]
    x0, y0, x1, y1 = bbox(emblem)
    w, h = x1 - x0, y1 - y0
    side = max(w, h)
    pad = side * pad_frac
    # centre the emblem inside a square viewBox with uniform padding
    vb_side = side + 2 * pad
    vb_x = x0 - (vb_side - w) / 2
    vb_y = y0 - (vb_side - h) / 2
    d = " ".join(emblem)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{vb_x:.2f} {vb_y:.2f} {vb_side:.2f} {vb_side:.2f}" '
        'role="img" aria-label="Yeliztli">'
        "<title>Yeliztli</title>"
        f'<path fill="{fill}" fill-rule="evenodd" d="{d}"/>'
        "</svg>\n"
    )


def lockup_svg(svg_text: str, path_fill: str, rect_fill: str | None) -> str:
    """Full emblem+wordmark lockup, recoloured. rect_fill=None drops the bg."""
    out = re.sub(r'fill="#001B3E"', f'fill="{path_fill}"', svg_text)
    if rect_fill is None:
        out = re.sub(r"<rect\b[^>]*/>", "", out, count=1)
    else:
        out = re.sub(
            r'(<rect\b[^>]*\bfill=")#FFFFFF(")', rf"\g<1>{rect_fill}\g<2>", out, count=1
        )
    return out


def rasterize(svg: str, size: int, background: str | None = None) -> Image.Image:
    png = cairosvg.svg2png(
        bytestring=svg.encode("utf-8"),
        output_width=size,
        output_height=size,
        background_color=background,
    )
    return Image.open(io.BytesIO(png)).convert("RGBA")


def icon_on_white(emblem: str, size: int, inner_frac: float) -> Image.Image:
    """Navy emblem centred on an opaque white square (for install icons)."""
    inner = round(size * inner_frac)
    mark = rasterize(emblem, inner)
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    off = (size - inner) // 2
    canvas.alpha_composite(mark, (off, off))
    return canvas.convert("RGB")


def write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)
    print(f"  wrote {path.relative_to(ROOT)}")


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def main() -> None:
    svg_text, path_d = load_source()

    emblem_teal = emblem_svg(path_d, TEAL)
    emblem_white = emblem_svg(path_d, WHITE)
    emblem_navy = emblem_svg(path_d, NAVY)
    emblem_cc = emblem_svg(path_d, "currentColor")
    lockup_navy = lockup_svg(svg_text, NAVY, WHITE)          # native (navy on white)
    lockup_white = lockup_svg(svg_text, WHITE, None)         # white, transparent

    print("SVG variants (brand/):")
    write(ROOT / "brand" / "emblem-teal.svg", emblem_teal)
    write(ROOT / "brand" / "emblem-white.svg", emblem_white)
    write(ROOT / "brand" / "emblem-navy.svg", emblem_navy)
    write(ROOT / "brand" / "emblem-currentcolor.svg", emblem_cc)
    write(ROOT / "brand" / "lockup-navy-on-white.svg", lockup_navy)
    write(ROOT / "brand" / "lockup-white.svg", lockup_white)

    print("App assets (frontend/public/):")
    pub = ROOT / "frontend" / "public"
    write(pub / "favicon.svg", emblem_teal)  # replaces the old lucide DNA favicon
    write(pub / "favicon-32.png", png_bytes(rasterize(emblem_teal, 32)))
    # multi-resolution .ico from the teal emblem
    ico = io.BytesIO()
    rasterize(emblem_teal, 64).save(ico, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    write(pub / "favicon.ico", ico.getvalue())
    # opaque navy-on-white install icons (Apple + Android/PWA ignore alpha)
    write(pub / "apple-touch-icon.png", png_bytes(icon_on_white(emblem_navy, 180, 0.82)))
    write(pub / "icon-192.png", png_bytes(icon_on_white(emblem_navy, 192, 0.82)))
    write(pub / "icon-512.png", png_bytes(icon_on_white(emblem_navy, 512, 0.82)))
    # maskable: content must sit inside the central 80% safe zone
    write(pub / "icon-512-maskable.png", png_bytes(icon_on_white(emblem_navy, 512, 0.66)))

    print("Docs assets (docs/assets/img/):")
    img = ROOT / "docs" / "assets" / "img"
    write(img / "logo.svg", emblem_white)                 # on the teal docs header bar
    write(img / "favicon.svg", emblem_teal)               # docs browser tab
    write(img / "logo-lockup.svg", lockup_navy)           # README banner + docs hero (light)
    write(img / "logo-lockup-dark.svg", lockup_white)     # docs hero (dark backgrounds)

    print("done.")


if __name__ == "__main__":
    main()

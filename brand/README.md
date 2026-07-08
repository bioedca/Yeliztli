# Yeliztli brand assets

Single source of truth for the Yeliztli logo. Everything in this directory and
the served copies under `frontend/public/` and `docs/assets/img/` is **generated**
from [`logo-source.svg`](logo-source.svg) by [`generate.py`](generate.py) — edit
the source (or the script), never the derivatives by hand.

## The mark

`logo-source.svg` is a 1254×1254 square: a white rounded-rect background with a
single dark-navy path drawing a *chakana* (Andean stepped cross) framing a DNA
double-helix, above a **YELIZTLI** wordmark. Because the artwork is one fill path
of straight-line (`M`/`L`/`Z`) commands, the generator can split it into
sub-paths, drop the bottom wordmark row to get an emblem-only mark, recolour by
swapping the fill, and crop with a computed `viewBox`.

## Palette

The logo adapts to the palette of wherever it lands (the app + docs identity is
**teal**, not the artwork's native navy):

| Use | Colour | Where |
| --- | --- | --- |
| App / docs primary | teal `#0D9488` | in-app marks, tab favicon, docs header (as white on the teal bar) |
| App primary (dark) | teal `#14B8A6` | auto via `text-primary` / `currentColor` in dark mode |
| On teal surfaces | white `#FFFFFF` | docs header bar, solid-teal badges |
| Standalone icons | navy `#001B3E` on white | apple-touch / PWA install / maskable / README banner |

App palette source: `frontend/src/index.css` (`--color-primary`).

## Generated files

Variants (this dir): `emblem-{teal,white,navy,currentcolor}.svg`,
`lockup-navy-on-white.svg`, `lockup-white.svg`.

Served app assets (`frontend/public/`): `favicon.svg` (teal emblem),
`favicon.ico`, `favicon-32.png`, `apple-touch-icon.png`, `icon-192.png`,
`icon-512.png`, `icon-512-maskable.png` (navy-on-white, opaque).

Served docs assets (`docs/assets/img/`): `logo.svg` (white emblem, docs header),
`favicon.svg` (teal emblem, docs tab), `logo-lockup.svg` (navy-on-white full
lockup — README banner / docs hero), `logo-lockup-dark.svg` (white lockup).

## Regenerate

```bash
pip install cairosvg pillow      # dev-only; not a runtime/CI dependency
python brand/generate.py
```

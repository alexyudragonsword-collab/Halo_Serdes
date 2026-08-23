#!/usr/bin/env python3
"""Generate the Android launcher icons from the desktop master art.

The desktop builds (PyInstaller and both Nuitka variants) all point at
``packaging/icon.ico``, which is rendered from ``packaging/icon.png``. This
script derives the Android resources from that same PNG so the phone and the
Windows exe cannot drift apart — the same reasoning as pointing Chaquopy at the
repo's own ``src/`` rather than a copy.

Run it after changing the master art, then commit the output:

    python android/tools/gen_launcher_icons.py

The generated files are committed rather than built by Gradle. A CI app build
should not need Pillow, and an icon changes about once a project.

Why the art cannot simply be dropped in as the adaptive foreground
------------------------------------------------------------------
An adaptive icon hands the launcher two 108dp layers and lets it apply its own
mask — circle, squircle, teardrop, whatever the device uses. Only the middle
66dp is guaranteed to survive; the outer ring is cropped and used for parallax.

The master art is a *finished* icon: a rounded square, edge to edge, with the
glowing eye diagram spanning 84% of its width. Used directly as a foreground
layer it would be cropped twice over — the rounded corners cut off by the
launcher's own mask, and the eye diagram itself clipped at the sides. So the
art is scaled down to sit inside the safe circle, and the flat background it
was drawn on becomes the background layer, which is exactly the split adaptive
icons ask for.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
MASTER = ROOT / "packaging" / "icon.png"
RES = ROOT / "android" / "app" / "src" / "main" / "res"

#: Adaptive layers are 108dp; legacy launcher icons are 48dp.
ADAPTIVE_DP, LEGACY_DP = 108, 48
DENSITIES = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}

#: Fraction of the 108dp layer the artwork's bright ring may occupy.
#:
#: Two different numbers get called "the safe zone" and they are not the same.
#: 72dp is where content is actually *clipped*: the system reserves the outer
#: 18dp on each side, and the circular mask — the tightest of the standard
#: shapes — inscribes a 72dp circle in what is left. 66dp is Material's
#: *keyline* recommendation for where a full-bleed circular icon should sit,
#: which is advice, not a boundary.
#:
#: This art is a neon glow, so it has no edge — it fades. At 0.60 the bright
#: ring lands at 0.625 of the layer radius and the faint halo at 0.645, both
#: comfortably under the 0.667 that clips and both slightly over the 0.611
#: keyline. That overshoot is deliberate: the keyline is advice for hard-edged
#: full-bleed art, and shrinking ~7% to tuck a fade behind it would cost
#: visible size against neighbouring icons to protect nothing.
#:
#: (Those two figures are measured on the output, not predicted from this
#: constant — the ring is found here by a mid-row scan of the master and there
#: by a 2-D threshold, which disagree by a few percent.)
SAFE_FRACTION = 0.60


def glow_span(img: Image.Image) -> float:
    """Fraction of the master's width taken up by the bright ring.

    Measured rather than assumed: the scale factor below depends on it, and a
    redraw of the art that changes the margin would otherwise silently produce
    an icon that is too big or lost in white space.
    """
    lum = np.array(img.convert("RGB"), dtype=float).mean(axis=2)
    n = lum.shape[0]
    bright = lum > 90
    row, col = bright[n // 2], bright[:, n // 2]
    spans = []
    for v in (row, col):
        idx = np.nonzero(v)[0]
        if idx.size == 0:
            raise SystemExit("no bright ring found in the master art")
        spans.append((idx.max() - idx.min() + 1) / n)
    return max(spans)


def background_color(img: Image.Image) -> tuple[int, int, int]:
    """The flat field the art sits on, as the adaptive background layer.

    Sampled from inside the rounded rectangle but outside the glow, and taken
    as a median so the faint grid ticks drawn in that region do not pull it.
    """
    a = np.array(img.convert("RGB"))
    n = a.shape[0]
    m = n // 16
    patches = [a[m:2 * m, m:2 * m], a[m:2 * m, -2 * m:-m],
               a[-2 * m:-m, m:2 * m], a[-2 * m:-m, -2 * m:-m]]
    med = np.median(np.concatenate([p.reshape(-1, 3) for p in patches]), axis=0)
    return tuple(int(round(c)) for c in med)


def _disc_alpha(n: int, r_in: float, r_out: float) -> np.ndarray:
    """A soft-edged disc: opaque inside ``r_in``, gone by ``r_out``."""
    yy, xx = np.mgrid[0:n, 0:n]
    c = (n - 1) / 2
    d = np.hypot(xx - c, yy - c)
    return np.clip((r_out - d) / max(r_out - r_in, 1e-6), 0.0, 1.0)


def foreground(img: Image.Image, size: int, scale: float,
               span: float) -> Image.Image:
    """The art, shrunk into the safe zone, on a transparent layer.

    Only the *disc* of artwork is kept. The master is a finished icon, so it
    carries its own rounded-square frame and black corners — and a foreground
    layer containing that frame draws a second, smaller rounded square inside
    whatever shape the launcher masks to. Cutting to a disc a little wider
    than the glow removes it.

    The cut is feathered rather than hard: the art's own field is the same
    colour as the background layer underneath, so a soft edge disappears,
    while a hard one would leave a visible rim where the outer glow stops.
    """
    art = img.resize((round(size * scale),) * 2, Image.LANCZOS)
    n = art.width
    glow_r = span * n / 2
    a = np.array(art)
    # 1.06, not 1.14. The wider cut still reached the thin blue stroke the
    # master draws around its rounded rectangle, which showed up as a faint
    # square outline on launchers that mask close to the full 108dp. The glow
    # sits at 0.425 of the art's width and that stroke at ~0.5, so stopping at
    # 0.45 clears it with room to spare.
    a[..., 3] = (a[..., 3] * _disc_alpha(n, glow_r * 1.02, glow_r * 1.06)
                 ).astype(np.uint8)
    art = Image.fromarray(a, "RGBA")

    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    off = (size - n) // 2
    layer.paste(art, (off, off), art)
    return layer


def monochrome(img: Image.Image, size: int, scale: float) -> Image.Image:
    """A single-colour silhouette for Android 13+ themed icons.

    The launcher tints this layer and drops all colour, so what has to survive
    is the *shape*. This art is line work — a ring and the eye traces — so its
    luminance is already the silhouette; alpha follows brightness rather than
    a hard threshold, which keeps the crossings from filling in.
    """
    art = img.convert("RGB").resize((round(size * scale),) * 2, Image.LANCZOS)
    lum = np.array(art, dtype=float).mean(axis=2)
    # Cut well above the field, not just above it. The first attempt used a
    # floor of 30 against a field of ~10 and produced a filled coin: the grid
    # ticks and the glow bleeding across the ring's interior all cleared it,
    # so the inside of the eye came out as solid grey and the design was gone.
    # The traces themselves run past 150, so there is plenty of room.
    lo, hi = 70.0, 200.0
    alpha = np.clip((lum - lo) / (hi - lo), 0.0, 1.0) ** 0.75
    rgba = np.zeros(lum.shape + (4,), dtype=np.uint8)
    rgba[..., :3] = 255
    rgba[..., 3] = (alpha * 255).astype(np.uint8)
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    art_l = Image.fromarray(rgba, "RGBA")
    off = (size - art_l.width) // 2
    layer.paste(art_l, (off, off), art_l)
    return layer


def circular(img: Image.Image, size: int) -> Image.Image:
    """Legacy round icon: the finished art, masked to a circle.

    Only a fallback — minSdk 26 means every supported device uses the adaptive
    icon above. Kept because packaging tools and some launchers still read it.
    """
    art = img.resize((size, size), Image.LANCZOS).convert("RGBA")
    yy, xx = np.mgrid[0:size, 0:size]
    r = (size - 1) / 2
    inside = ((xx - r) ** 2 + (yy - r) ** 2) <= r ** 2
    a = np.array(art)
    a[..., 3] = np.where(inside, a[..., 3], 0)
    return Image.fromarray(a, "RGBA")


def check_safe_zone(layer: Image.Image, bg: tuple[int, int, int],
                    label: str) -> None:
    """Fail if anything *visible* sits outside the 66dp guaranteed circle.

    This is the property the whole script exists to get right and the one I
    got wrong twice — first by leaving the art at full size, then by cutting
    the disc wide enough to keep the master's border stroke. Both looked fine
    in the file and wrong under a launcher mask, which is a check no build
    step performs.

    "Visible" means composited: the foreground is checked against the
    background layer it will actually sit on, not against transparency. A
    first version tested alpha alone and failed on the feathered edge, where
    the art's own near-black field fades out — pixels that are technically
    opaque and indistinguishable from the background behind them. Clipping
    those changes nothing, and a check that forbids them only shrinks the
    icon for no gain.
    """
    a = np.array(layer).astype(float)
    alpha = a[..., 3:4] / 255.0
    over = a[..., :3] * alpha + np.array(bg, dtype=float) * (1 - alpha)
    seen = np.abs(over - np.array(bg, dtype=float)).max(axis=2) > 12

    n = a.shape[0]
    yy, xx = np.mgrid[0:n, 0:n]
    r = np.hypot(xx - (n - 1) / 2, yy - (n - 1) / 2)
    # The clipping boundary, not the keyline: 72dp of the 108dp layer, which
    # is what the tightest standard mask (the circle) actually cuts at.
    outside = r > (72 / 108) * n / 2
    stray = outside & seen
    if stray.any():
        worst = r[stray].max() / (n / 2)
        raise SystemExit(
            f"{label}: {int(stray.sum())} visible pixels outside the safe "
            f"zone (reaching {worst:.3f} of the layer radius, limit "
            f"{72 / 108:.3f}) — a launcher mask would clip them")


def write(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", optimize=True)
    print(f"  {path.relative_to(ROOT)}  {path.stat().st_size // 1024} KB")


def main() -> int:
    if not MASTER.exists():
        raise SystemExit(f"master art not found: {MASTER}")
    master = Image.open(MASTER).convert("RGBA")
    if master.width != master.height:
        raise SystemExit(f"master art is not square: {master.size}")

    span = glow_span(master)
    scale = SAFE_FRACTION / span
    bg = background_color(master)
    print(f"master {master.width}px, glow spans {span:.3f} of it")
    print(f"foreground scale {scale:.3f}, background #{bg[0]:02X}{bg[1]:02X}{bg[2]:02X}")

    for name, factor in DENSITIES.items():
        adaptive = round(ADAPTIVE_DP * factor)
        legacy = round(LEGACY_DP * factor)
        d = RES / f"mipmap-{name}"
        fg = foreground(master, adaptive, scale, span)
        mono = monochrome(master, adaptive, scale)
        check_safe_zone(fg, bg, f"{name} foreground")
        # The themed layer is tinted, so it is checked against transparency:
        # anything with alpha out there is ink the launcher would clip.
        check_safe_zone(mono, (0, 0, 0), f"{name} monochrome")
        write(fg, d / "ic_launcher_foreground.png")
        write(mono, d / "ic_launcher_monochrome.png")
        write(master.resize((legacy, legacy), Image.LANCZOS), d / "ic_launcher.png")
        write(circular(master, legacy), d / "ic_launcher_round.png")

    colors = RES / "values" / "ic_launcher_background.xml"
    colors.parent.mkdir(parents=True, exist_ok=True)
    colors.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<!-- Generated by android/tools/gen_launcher_icons.py; the flat field\n"
        "     the master art is drawn on. Do not hand-edit. -->\n"
        "<resources>\n"
        f'    <color name="ic_launcher_background">'
        f"#{bg[0]:02X}{bg[1]:02X}{bg[2]:02X}</color>\n"
        "</resources>\n"
    )
    print(f"  {colors.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

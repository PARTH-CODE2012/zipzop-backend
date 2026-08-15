#!/usr/bin/env python3
"""Write a 33x33x33 .cube LUT used by the M1 compositor spike.

The grade itself does not matter much — what matters is that it is *obviously*
visible, so that a strength slider moving from 0 to 1 is unmistakable on screen,
and that the same file can later be handed to FFmpeg's `lut3d` filter so the
browser and the export renderer can be compared frame by frame
(docs/04-frontend-architecture.md 4.4).

Generated rather than committed: it is 33^3 lines of text that nothing reviews.
Regenerate with `make spike-media`.

Format follows the Adobe Cube specification: the RED component varies fastest,
then green, then blue. That order maps directly onto a WebGL `TEXTURE_3D`
upload, where x is the fastest-varying axis.
"""

from __future__ import annotations

import sys
from pathlib import Path

SIZE = 33
TITLE = "ZipZop cinematic_warm (M1 spike)"

# Rec. 709 luma weights — the same ones the fragment shader uses.
LUMA = (0.2126, 0.7152, 0.0722)

SHADOW_TINT = (0.00, 0.36, 0.46)  # teal
HIGHLIGHT_TINT = (1.00, 0.72, 0.34)  # warm amber

# Pushed harder than anything shippable would be. The spike has to answer
# "is the LUT actually reaching the shader, and does `strength` interpolate?",
# and a tasteful grade answers that ambiguously.
CONTRAST = 1.28
SHADOW_WEIGHT = 0.34
HIGHLIGHT_WEIGHT = 0.30
SATURATION = 1.18


def clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def grade(r: float, g: float, b: float) -> tuple[float, float, float]:
    # 1. Contrast around mid grey, softened by an S-curve so the ends do not clip
    #    into flat black and flat white.
    rgb = [clamp01(0.5 + (c - 0.5) * CONTRAST) for c in (r, g, b)]
    rgb = [c * c * (3.0 - 2.0 * c) * 0.35 + c * 0.65 for c in rgb]

    luma = sum(w * c for w, c in zip(LUMA, rgb))

    # 2. Split tone: teal into the shadows, amber into the highlights. Squaring
    #    the weights keeps the mid-tones close to neutral, which is what makes
    #    this read as a grade rather than a colour cast.
    s = (1.0 - luma) ** 2 * SHADOW_WEIGHT
    h = luma**2 * HIGHLIGHT_WEIGHT
    rgb = [c * (1.0 - s - h) + SHADOW_TINT[i] * s + HIGHLIGHT_TINT[i] * h for i, c in enumerate(rgb)]

    # 3. Saturation, around the tinted luma.
    luma = sum(w * c for w, c in zip(LUMA, rgb))
    rgb = [clamp01(luma + (c - luma) * SATURATION) for c in rgb]

    return rgb[0], rgb[1], rgb[2]


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: make_spike_lut.py <output.cube>", file=sys.stderr)
        return 2

    out = Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        f'TITLE "{TITLE}"',
        f"LUT_3D_SIZE {SIZE}",
        "DOMAIN_MIN 0.0 0.0 0.0",
        "DOMAIN_MAX 1.0 1.0 1.0",
        "",
    ]

    last = SIZE - 1
    for bi in range(SIZE):
        for gi in range(SIZE):
            for ri in range(SIZE):
                r, g, b = grade(ri / last, gi / last, bi / last)
                lines.append(f"{r:.6f} {g:.6f} {b:.6f}")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out} ({SIZE}^3 = {SIZE**3} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

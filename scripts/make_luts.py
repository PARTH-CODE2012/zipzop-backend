#!/usr/bin/env python3
"""The five looks colour analysis can recommend, as `.cube` files.

**Shared by both renderers, which is the whole point.** The browser uploads
these into a WebGL `TEXTURE_3D` for the preview and the export renderer hands
the same file to FFmpeg's `lut3d` — one grade, one set of numbers, two
implementations that cannot drift because there is nothing for them to disagree
about (docs/04-frontend-architecture.md §4.4, contract §4.4).

⚠️ **This is what unblocked colour analysis.** Until these existed, four of the
five looks the tool recommends had no file behind them: the recommendation
arrived, the browser looked for a LUT that was not there, and the picture did
not change. A recommendation nothing can render is worse than no recommendation.

Generated rather than committed as data: five files of 4,913 lines each is
25,000 lines nothing reviews. The *grades* are reviewed here, in thirty lines of
arithmetic each, which is the part a person can actually judge.

Written to `backend/app/assets/luts/`, which is the source of truth: the export
renderer hands FFmpeg a path on disk and cannot fetch one over HTTP, and the
container is built from the `backend/` context so anything outside it is not in
the image. The browser needs the same bytes under `frontend/public/luts/`, and
gets them by a copy — `make luts` does it, and so does every `pnpm` script that
needs them, so the two can never be different files.

Regenerate with `make luts`.

Format follows the Adobe Cube specification: RED varies fastest, then green,
then blue — the order a `TEXTURE_3D` upload expects, where x is the
fastest-varying axis.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: 17 rather than 33. A 33³ table is 970 kB of text per look; at five looks that
#: is five megabytes the browser fetches to grade a preview. 17³ is 133 kB, is a
#: standard shipping size, and the difference is invisible on grades this
#: smooth. Nothing depends on the size — it is read from the file.
SIZE = 17

#: Rec. 709, the same weights the fragment shader uses. A LUT built on different
#: luma weights than the shader assumes produces a different picture in the two
#: renderers, which is exactly the drift these files exist to prevent.
LUMA = (0.2126, 0.7152, 0.0722)


def clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def luma_of(rgb: list[float]) -> float:
    return sum(w * c for w, c in zip(LUMA, rgb, strict=True))


def contrast(rgb: list[float], amount: float) -> list[float]:
    """Around mid grey, then softened by an S-curve.

    Without the curve the ends clip into flat black and flat white, and a clipped
    highlight is the one grading artefact that cannot be undone downstream.
    """
    out = [clamp01(0.5 + (c - 0.5) * amount) for c in rgb]
    return [c * c * (3.0 - 2.0 * c) * 0.35 + c * 0.65 for c in out]


def split_tone(
    rgb: list[float], shadow: tuple[float, float, float], high: tuple[float, float, float],
    shadow_weight: float, high_weight: float,
) -> list[float]:
    """Tint the shadows one way and the highlights another.

    The weights are **squared** so the mid-tones stay close to neutral. That is
    what makes a split tone read as a grade rather than as a colour cast over
    the whole picture — and skin sits in the mid-tones.
    """
    y = luma_of(rgb)
    s = (1.0 - y) ** 2 * shadow_weight
    h = y**2 * high_weight
    return [c * (1.0 - s - h) + shadow[i] * s + high[i] * h for i, c in enumerate(rgb)]


def saturate(rgb: list[float], amount: float) -> list[float]:
    y = luma_of(rgb)
    return [clamp01(y + (c - y) * amount) for c in rgb]


# --------------------------------------------------------------------------
# The five looks. Each one is named for the scene it suits, and
# `app/services/color_analysis.py` recommends it from measured exposure, white
# balance and contrast — so a look that does not match its description would
# make the tool recommend the wrong thing for the right reason.
# --------------------------------------------------------------------------


def cinematic_warm(rgb: list[float]) -> list[float]:
    """Teal shadows, amber highlights. The default for cool, flat footage."""
    out = contrast(rgb, 1.22)
    out = split_tone(out, (0.00, 0.36, 0.46), (1.00, 0.72, 0.34), 0.30, 0.26)
    return saturate(out, 1.14)


def vlog_clean(rgb: list[float]) -> list[float]:
    """Barely a grade: a little contrast, a little warmth, nothing stylised.

    The one to reach for when the footage is already fine — which is why the
    analyser picks it for a neutral, normally-exposed scene.
    """
    out = contrast(rgb, 1.10)
    out = split_tone(out, (0.02, 0.05, 0.10), (1.00, 0.94, 0.86), 0.10, 0.14)
    return saturate(out, 1.06)


def cyberpunk(rgb: list[float]) -> list[float]:
    """Magenta highlights over deep cyan shadows, pushed hard.

    For dark, cool, flat footage — the case where there is headroom to be
    stylised because there was nothing else in the picture to protect.
    """
    out = contrast(rgb, 1.34)
    out = split_tone(out, (0.00, 0.30, 0.52), (0.96, 0.28, 0.78), 0.42, 0.34)
    return saturate(out, 1.28)


def sun_kissed(rgb: list[float]) -> list[float]:
    """Golden-hour warmth, lifted shadows, gentle roll-off.

    For bright, already-warm footage: adding contrast to it would clip the
    highlights it already has, so this lifts instead.
    """
    out = contrast(rgb, 1.06)
    out = [clamp01(c * 0.94 + 0.06) for c in out]  # lift the toe
    out = split_tone(out, (0.16, 0.10, 0.04), (1.00, 0.84, 0.56), 0.18, 0.34)
    return saturate(out, 1.12)


def mono_contrast(rgb: list[float]) -> list[float]:
    """Black and white, with a cool bias in the shadows.

    Fully desaturated on purpose: a "nearly mono" look is the one that reads as
    a mistake rather than a decision.
    """
    out = contrast(rgb, 1.30)
    y = luma_of(out)
    out = [y, y, y]
    return split_tone(out, (0.04, 0.06, 0.12), (1.00, 0.99, 0.96), 0.22, 0.18)


LOOKS = {
    "cinematic_warm": cinematic_warm,
    "vlog_clean": vlog_clean,
    "cyberpunk": cyberpunk,
    "sun_kissed": sun_kissed,
    "mono_contrast": mono_contrast,
}


def write_lut(name: str, out_dir: Path) -> Path:
    grade = LOOKS[name]
    lines = [
        f'TITLE "ZipZop {name}"',
        f"LUT_3D_SIZE {SIZE}",
        "DOMAIN_MIN 0.0 0.0 0.0",
        "DOMAIN_MAX 1.0 1.0 1.0",
        "",
    ]
    last = SIZE - 1
    for bi in range(SIZE):
        for gi in range(SIZE):
            for ri in range(SIZE):
                r, g, b = grade([ri / last, gi / last, bi / last])
                lines.append(f"{clamp01(r):.6f} {clamp01(g):.6f} {clamp01(b):.6f}")

    path = out_dir / f"{name}.cube"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    # The backend, not the frontend. The renderer is the side that cannot work
    # without these — `lut3d=file=…` needs a path on the worker's disk — and
    # the browser's copy is made from this one by `make luts`.
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("backend/app/assets/luts")
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in LOOKS:
        path = write_lut(name, out_dir)
        print(f"wrote {path} ({SIZE}^3 = {SIZE**3} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

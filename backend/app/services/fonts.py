"""Finding a font file the renderer can actually draw with.

**This exists because of the same mistake the LUTs made, caught the same way.**
`drawtext` — which the watermark uses, and which anything drawn onto a frame
will use — resolves fonts through fontconfig when it is given a family name.
Fontconfig is not a given: on the Windows development machine there is no
configuration at all and FFmpeg dies with

    Fontconfig error: Cannot load default config file: File not found

and an exit code that is not an error message. Like `lut3d=file=…` pointing at
nothing, it fails **at render time on a job the user has already paid for**,
rather than at build time.

So the renderer names a file rather than a family, and this resolves it.

⚠️ **The durable fix is to bundle a font in `app/assets/fonts/`**, exactly as
the grades are bundled in `app/assets/luts/` — one file in the image, no
dependency on what the host happens to have installed, and identical output on
every machine. That is a licensing choice (a font has to be redistributable)
and is left as a follow-up rather than made silently here. Until then this
falls back to the platforms' own fonts, and `available()` returning nothing is
a failure the tests catch rather than something a customer discovers.
"""

from pathlib import Path

#: Bundled first, always. When a font lands here it wins on every platform and
#: the fallbacks below stop mattering.
BUNDLED_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

#: Where the platforms keep something plain and always present. Ordered by
#: preference: DejaVu is what Debian-based images have — including the backend
#: image, which installs `ffmpeg` and gets it as a dependency — and the rest
#: are development machines.
_FALLBACKS = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    Path("/Library/Fonts/Arial.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/segoeui.ttf"),
)

# --------------------------------------------------------------------------
# Devanagari
#
# **Captions in Hindi are the product's stated edge**, and none of the fonts
# above can draw them. DejaVu, Liberation and Arial have no Devanagari block at
# all: the shaping can be perfect and the output is still empty boxes, because
# there are no glyphs to shape. Correct rendering needs two independent things
# and it is worth keeping them apart in your head —
#
#   1. **Shaping**, which reorders `ि` to the left of its consonant and joins
#      `न` + `्` + `द` into one conjunct. libass does this through HarfBuzz;
#      `drawtext` does not do it at all. That is why captions go through
#      `render_text.py` and never through `drawtext`.
#   2. **Coverage**, which is this list.
#
# Neither is visible in a Latin test, which is how this ships broken.
# --------------------------------------------------------------------------

#: What an ASS style names when nothing on this machine can draw Devanagari.
#: **Not a Devanagari font** — it is here so a Latin-only deployment still
#: renders Latin captions rather than failing outright. `has_devanagari()` is
#: what tells you which case you are in.
LATIN_FALLBACK_FAMILY = "Sans"

#: Paired, not two parallel tuples. They were parallel for about ten minutes and
#: the pairing was off by one, so a machine with Nirmala installed reported
#: "Mangal" — a family it does not have, which fontconfig would have resolved to
#: something Latin without complaining.
_DEVANAGARI_FONTS: tuple[tuple[Path, str], ...] = (
    # Debian/Ubuntu, from `fonts-noto-core` — what the backend image installs.
    # Open Font Licence, so shipping it costs nothing.
    (Path("/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf"), "Noto Sans Devanagari"),
    (Path("/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf"), "Lohit Devanagari"),
    # Windows ships Nirmala UI, which covers every Indic script.
    (Path("C:/Windows/Fonts/Nirmala.ttc"), "Nirmala UI"),
    (Path("/Library/Fonts/Kohinoor.ttc"), "Kohinoor Devanagari"),
)


def devanagari_family() -> str:
    """The family name to put in an ASS style.

    Returns the first family whose file is actually present, so the script names
    something fontconfig can resolve rather than a name that silently falls back
    to a Latin face — which is the failure this whole module exists to make
    visible. Falls through to `LATIN_FALLBACK_FAMILY` when nothing Indic is
    installed; `has_devanagari()` is what tells the caller which case it is in.
    """
    bundled = sorted(BUNDLED_DIR.glob("NotoSansDevanagari*")) + sorted(
        BUNDLED_DIR.glob("*Devanagari*")
    )
    if bundled:
        return "Noto Sans Devanagari"
    for path, family in _DEVANAGARI_FONTS:
        if path.is_file():
            return family
    return LATIN_FALLBACK_FAMILY


def has_devanagari() -> bool:
    """Whether anything on this machine can draw Hindi.

    Checked by the tests rather than assumed, because the failure is silent:
    a font without the glyphs renders boxes, and a Latin caption test passes
    either way.
    """
    if sorted(BUNDLED_DIR.glob("*Devanagari*")):
        return True
    return any(path.is_file() for path, _ in _DEVANAGARI_FONTS)


class NoFontError(Exception):
    """Nothing to draw text with.

    Raised rather than falling back to a family name: letting FFmpeg try
    fontconfig is what produced the unreadable failure this module exists to
    avoid, and a render that dies half way through has already cost the user
    their credits and the wait.
    """


def default_font() -> Path:
    """A font file `drawtext` can use, or raise.

    Checked at call time rather than cached at import, so a font added to the
    image after a worker started is picked up without a restart — the same
    reasoning as `luts.available()`.
    """
    for path in sorted(BUNDLED_DIR.glob("*.ttf")) + sorted(BUNDLED_DIR.glob("*.otf")):
        return path
    for path in _FALLBACKS:
        if path.is_file():
            return path
    raise NoFontError(
        "no font available for drawtext — bundle one in app/assets/fonts/ "
        "or install a system font package in the image"
    )


def available() -> bool:
    """Whether `default_font()` would succeed. For diagnostics and tests."""
    try:
        default_font()
    except NoFontError:
        return False
    return True

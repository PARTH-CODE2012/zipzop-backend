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

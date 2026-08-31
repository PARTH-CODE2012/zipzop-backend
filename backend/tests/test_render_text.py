"""Caption rendering, and Devanagari in particular.

**Hindi captions are the product's stated edge**, and getting them right needs
two independent things that a Latin test cannot see either of:

1. **Shaping.** `ि` is written to the *left* of the consonant it follows, and
   `न` + `्` + `द` is one conjunct glyph rather than three. libass shapes
   through HarfBuzz; `drawtext` goes through libfreetype and does not shape at
   all. `test_shaping_actually_changes_the_picture` proves the difference by
   rendering the same string both ways and measuring that they differ, rather
   than asserting a configure flag and hoping.
2. **Coverage.** Neither Arial nor DejaVu has a Devanagari block, so the
   shaping can be perfect and the output is still empty boxes.

Both fail silently. That is the whole reason this file is longer than the
feature.
"""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.api.schemas.project import TextTrack, TimelineDocument
from app.services import fonts
from app.services.ffmpeg_filters import escape_path
from app.services.render_text import build_ass, write_ass

pytestmark = pytest.mark.ffmpeg

#: "hindi" in Hindi. Chosen because it exercises both hard parts in five
#: characters: `ि` after `ह` must be drawn *before* it, and `न` + `्` + `द`
#: must join into a conjunct.
HINDI = "हिन्दी"


def _text_clip(**over: Any) -> dict[str, Any]:
    return {
        "id": "clp_t1",
        "kind": "caption",
        "startMs": 0,
        "durationMs": 1000,
        "text": "Hello",
        "styleId": "kinetic_bold",
        **over,
    }


def _document(*clips: dict[str, Any]) -> TimelineDocument:
    return TimelineDocument(
        schema_version=1,
        tracks=[TextTrack.model_validate({"id": "trk_t", "kind": "text", "clips": list(clips)})],
    )


# --------------------------------------------------------------------------
# The script itself
# --------------------------------------------------------------------------


def test_no_text_track_means_no_file_rather_than_an_empty_one() -> None:
    """The caller then leaves the `subtitles` filter out altogether, instead of
    compositing a filter over a script with no events in it."""
    assert (
        build_ass(
            TimelineDocument(schema_version=1, tracks=[]), width=1080, height=1920, font_name="Sans"
        )
        is None
    )


def test_the_canvas_is_declared_as_the_output_frame() -> None:
    """Normalised coordinates times `PlayRes` is one multiplication, and it is
    what makes a 720p and a 2160p export lay out identically."""
    script = build_ass(_document(_text_clip()), width=1080, height=1920, font_name="Sans")
    assert script is not None
    assert "PlayResX: 1080" in script
    assert "PlayResY: 1920" in script


def test_a_position_becomes_pixels_against_that_canvas() -> None:
    script = build_ass(
        _document(_text_clip(position={"x": 0.5, "y": 0.78, "anchor": "center"})),
        width=1080,
        height=1920,
        font_name="Sans",
    )
    assert script is not None
    assert "\\pos(540,1498)" in script or "\\pos(540,1497)" in script
    assert "\\an5" in script


def test_colours_are_converted_to_ass_byte_order() -> None:
    """ASS is `&HAABBGGRR` — **BGR**, and the alpha byte is inverted. Getting
    either wrong gives a colour plausibly close to the one asked for, which is
    the kind of mistake that survives review."""
    script = build_ass(
        _document(_text_clip(style={"color": "#FF8000"})),
        width=1080,
        height=1920,
        font_name="Sans",
    )
    assert script is not None
    assert "\\1c&H000080FF" in script


def test_a_caption_containing_braces_cannot_open_an_override_block() -> None:
    """Caption text is user-editable, so `{` is reachable — and unescaped it
    would swallow the rest of the line into a style override."""
    script = build_ass(
        _document(_text_clip(text="use {\\an8} carefully")),
        width=1080,
        height=1920,
        font_name="Sans",
    )
    assert script is not None
    body = script.rsplit(",,", 1)[-1]
    assert "\\{" in body and "\\}" in body


def test_a_newline_becomes_a_break_and_not_a_truncated_event() -> None:
    """A literal newline ends the `Dialogue:` line, so half the caption would
    disappear and the other half would be parsed as a malformed event."""
    script = build_ass(
        _document(_text_clip(text="two\nlines")), width=1080, height=1920, font_name="Sans"
    )
    assert script is not None
    assert "two\\Nlines" in script
    assert len([line for line in script.splitlines() if line.startswith("Dialogue:")]) == 1


def test_timestamps_are_centiseconds() -> None:
    script = build_ass(
        _document(_text_clip(startMs=3_661_230, durationMs=500)),
        width=1080,
        height=1920,
        font_name="Sans",
    )
    assert script is not None
    assert "1:01:01.23" in script


# --------------------------------------------------------------------------
# Devanagari
# --------------------------------------------------------------------------


def test_this_machine_can_draw_devanagari_at_all() -> None:
    """Not a property of the code — a property of the box it runs on, asserted
    so a green suite on a machine that cannot render Hindi is impossible.

    The backend image installs `fonts-noto-core` for exactly this.
    """
    assert fonts.has_devanagari(), (
        "no Devanagari font found. Hindi captions would render as empty boxes. "
        "Install fonts-noto-core, or bundle one in app/assets/fonts/"
    )


def test_the_script_names_a_family_that_is_actually_installed() -> None:
    """Naming a family fontconfig cannot resolve is how a Hindi caption ends up
    silently drawn in a Latin face."""
    family = fonts.devanagari_family()
    assert family != fonts.LATIN_FALLBACK_FAMILY
    script = build_ass(_document(_text_clip(text=HINDI)), width=1080, height=1920, font_name=family)
    assert script is not None
    assert family in script


def _ink(path: Path) -> float:
    """Mean luminance of a rendered frame. Text on black, so more ink is a
    higher number — and a frame with no glyphs at all reads as 0."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-f",
            "lavfi",
            "-i",
            f"movie={escape_path(path)},signalstats",
            "-show_entries",
            "frame_tags=lavfi.signalstats.YAVG",
            "-of",
            "default=nw=1:nk=1",
            "-read_intervals",
            "%+#1",
        ],
        capture_output=True,
        timeout=60,
        check=True,
    )
    return float(result.stdout.split()[0])


def _blank_ink(tmp_path: Path) -> float:
    """The same frame with nothing drawn on it.

    Every "did it render" assertion below compares against this rather than a
    threshold: how much luminance a string of text adds depends on the font's
    metrics, and a number picked to pass today is a number that fails on a
    machine with a different font.
    """
    out = tmp_path / "_blank.png"
    _render("null", out)
    return _ink(out)


def _render(filters: str, out: Path) -> None:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=640x360:d=0.1",
            "-vf",
            filters,
            "-frames:v",
            "1",
            str(out),
        ],
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")[-1500:]


def test_hindi_captions_reach_the_frame_as_glyphs(tmp_path: Path) -> None:
    """The blunt one: does anything get drawn at all?

    A missing font draws nothing, so an empty frame is the failure this
    catches — the exact way "Hindi support" ships broken while every test is
    green.
    """
    script = write_ass(
        _document(
            _text_clip(
                text=HINDI,
                position={"x": 0.5, "y": 0.5, "anchor": "center"},
                style={"fontSize": 0.3},
            )
        ),
        width=640,
        height=360,
        font_name=fonts.devanagari_family(),
        into=tmp_path / "captions.ass",
    )
    assert script is not None

    out = tmp_path / "hindi.png"
    _render(f"subtitles={escape_path(script)}", out)
    assert _ink(out) > _blank_ink(tmp_path) + 0.5, (
        "the frame is no brighter than an empty one — the Devanagari glyphs did not render"
    )


def test_shaping_actually_changes_the_picture(tmp_path: Path) -> None:
    """**The test the whole design rests on.**

    `drawtext` and `subtitles` draw the same string with the same font at the
    same size. For Latin they agree closely. For Devanagari they cannot: libass
    reorders the `ि` before its consonant and joins the conjunct through
    HarfBuzz, and `drawtext` does neither — so a measurable difference is
    evidence that shaping happened, and its absence would mean captions are
    going out unshaped.
    """
    ass = write_ass(
        _document(
            _text_clip(
                text=HINDI,
                position={"x": 0.5, "y": 0.5, "anchor": "center"},
                style={"fontSize": 0.3},
            )
        ),
        width=640,
        height=360,
        font_name=fonts.devanagari_family(),
        into=tmp_path / "shaped.ass",
    )
    assert ass is not None

    shaped = tmp_path / "shaped.png"
    unshaped = tmp_path / "unshaped.png"
    _render(f"subtitles={escape_path(ass)}", shaped)
    _render(
        f"drawtext=fontfile={escape_path(_devanagari_file())}:text='{HINDI}':"
        f"fontsize=108:fontcolor=white:x=(w-tw)/2:y=(h-th)/2",
        unshaped,
    )

    blank = _blank_ink(tmp_path)
    shaped_ink, unshaped_ink = _ink(shaped), _ink(unshaped)
    # Both drew something — otherwise the comparison is between two blanks and
    # "they differ" would be meaningless.
    assert shaped_ink > blank + 0.5, f"nothing shaped ({shaped_ink} vs blank {blank})"
    assert unshaped_ink > blank + 0.5, f"nothing unshaped ({unshaped_ink} vs blank {blank})"
    assert abs(shaped_ink - unshaped_ink) > 0.4, (
        f"shaped and unshaped Devanagari rendered identically "
        f"({shaped_ink} vs {unshaped_ink}) — libass may not be shaping, "
        "which means matras and conjuncts are wrong in every Hindi export"
    )


def _devanagari_file() -> Path:
    from app.services.fonts import _DEVANAGARI_FONTS

    for path, _ in _DEVANAGARI_FONTS:
        if path.is_file():
            return path
    pytest.skip("no Devanagari font file to compare against")


def test_latin_captions_still_render(tmp_path: Path) -> None:
    """The regression guard for all of the above: nothing done for Devanagari
    may cost the language most captions are in."""
    script = write_ass(
        _document(
            _text_clip(
                text="Hello",
                position={"x": 0.5, "y": 0.5, "anchor": "center"},
                style={"fontSize": 0.3},
            )
        ),
        width=640,
        height=360,
        font_name=fonts.devanagari_family(),
        into=tmp_path / "latin.ass",
    )
    assert script is not None
    out = tmp_path / "latin.png"
    _render(f"subtitles={escape_path(script)}", out)
    assert _ink(out) > _blank_ink(tmp_path) + 0.5

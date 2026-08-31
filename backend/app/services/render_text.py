"""Text clips into an ASS subtitle file, for libass to draw.

## Why libass and not `drawtext`, which is the obvious choice

Two reasons, and the second one is the product's stated edge.

**Scale.** Captions land one clip per word (`docs/04` §7), so a 60-minute
recording is on the order of ten thousand text clips. Ten thousand `drawtext`
filters is a filtergraph FFmpeg will not parse, let alone composite per frame.
libass reads one file and draws every event.

**Shaping — and this is the one that matters.** `drawtext` goes through
libfreetype and nothing else: it maps characters to glyphs one at a time and
draws them left to right. That is fine for Latin and **wrong for Devanagari**,
where `ि` is written *before* the consonant it follows, and where `न` + `्` +
`द` is one conjunct glyph rather than three. Rendered without shaping, Hindi
captions come out with matras on the wrong side of their consonants and
dotted-circle placeholders where conjuncts should be — legible enough to ship
by accident and wrong to anyone who reads the language.

libass shapes through HarfBuzz. This build has it:

    --enable-libass --enable-libharfbuzz --enable-libfribidi

`tests/test_render_text.py` proves the difference rather than asserting the
flag, by rendering the same Hindi string both ways and measuring that they
differ.

⚠️ **Shaping is necessary and not sufficient: the font has to have the
glyphs.** Neither Arial nor DejaVu covers Devanagari, and a font without the
glyphs produces empty boxes no amount of shaping fixes. See
`app/services/fonts.py`.

## Coordinates

The document is normalised 0-1 against the canvas (contract §4.3), and ASS is
in pixels against a declared `PlayRes`. Declaring `PlayResX/Y` as the output
frame makes the conversion one multiplication and keeps a 720p and a 2160p
export identical in layout — which is the same property the normalised
coordinates exist to give the preview.
"""

from pathlib import Path

from app.api.schemas.project import TextClip, TextTrack, TimelineDocument

#: Fallback when a clip carries no `style.fontSize`. A fraction of the frame
#: height, matching the document's units — 5.6% of 1920 is about 107px, which
#: is roughly what a caption is in the mockups.
DEFAULT_FONT_SCALE = 0.056

DEFAULT_COLOR = "#FFFFFF"
DEFAULT_STROKE = "#000000"
#: Also a fraction of the frame height. Captions are watched over moving
#: pictures and an outline is what keeps them readable over a bright frame.
DEFAULT_STROKE_SCALE = 0.004


def _ass_colour(hex_rgb: str) -> str:
    """`#RRGGBB` to ASS's `&HAABBGGRR`.

    ASS is **BGR**, not RGB, and its alpha byte is inverted — `00` is opaque.
    Getting either wrong produces a colour that is plausibly close to the one
    asked for, which is the kind of bug that survives review.
    """
    value = hex_rgb.lstrip("#")
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H00{blue}{green}{red}".upper()


def _timestamp(ms: int) -> str:
    """ASS wants `H:MM:SS.cc` — centiseconds, and one digit of hours."""
    ms = max(0, ms)
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{millis // 10:02d}"


def _escape(text: str) -> str:
    """ASS event text is a small markup language.

    `{` opens an override block and a literal newline ends the event, so an
    unescaped caption containing either would silently swallow the rest of the
    line — and a caption is user-editable text, so both are reachable.
    """
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\r\n", "\\N")
        .replace("\n", "\\N")
        .replace("\r", "\\N")
    )


#: ASS alignment numbers for the document's three anchors, at the vertical
#: middle: 4 is middle-left, 5 middle-centre, 6 middle-right. Paired with
#: `\pos`, the alignment is what the position is measured *from*.
_ALIGNMENT = {"center": 5, "left": 4, "right": 6}


def _text_track(document: TimelineDocument) -> TextTrack | None:
    for track in document.tracks:
        if track.kind == "text":
            return track
    return None


def build_ass(document: TimelineDocument, *, width: int, height: int, font_name: str) -> str | None:
    """Every text clip in the document as one ASS script, or `None` if there
    are none — the caller then leaves the `subtitles` filter out entirely
    rather than adding a filter over an empty file.
    """
    track = _text_track(document)
    if track is None or not track.clips or track.muted:
        return None

    header = "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            # The canvas the coordinates below are in. Declaring it as the
            # output frame is what makes a 720p and a 2160p export lay out
            # identically.
            f"PlayResX: {width}",
            f"PlayResY: {height}",
            # No automatic line breaking: the client decided where the words
            # are, and libass rewrapping them would move captions the user
            # positioned by hand.
            "WrapStyle: 2",
            # Outlines scale with PlayRes rather than with the video, so a
            # stroke stays the same fraction of the frame at every resolution.
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour,"
            " Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle,"
            " BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            # A base style only. Every event overrides what it needs inline,
            # because the document's styling is per clip and a style table
            # would have to be as long as the caption list anyway.
            f"Style: Default,{font_name},{round(height * DEFAULT_FONT_SCALE)},"
            f"{_ass_colour(DEFAULT_COLOR)},{_ass_colour(DEFAULT_STROKE)},&H00000000,"
            f"0,0,0,0,100,100,0,0,1,{max(1, round(height * DEFAULT_STROKE_SCALE))},0,5,0,0,0,1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
    )

    events = [_event(clip, width=width, height=height) for clip in track.clips]
    return header + "\n" + "\n".join(events) + "\n"


def _event(clip: TextClip, *, width: int, height: int) -> str:
    style = clip.style
    position = clip.position

    x = round((position.x if position else 0.5) * width)
    y = round((position.y if position else 0.78) * height)
    alignment = _ALIGNMENT[position.anchor if position else "center"]

    overrides = [f"\\an{alignment}", f"\\pos({x},{y})"]

    scale = style.font_size if style and style.font_size else DEFAULT_FONT_SCALE
    font_size = round(scale * height)
    overrides.append(f"\\fs{font_size}")

    if style and style.color:
        overrides.append(f"\\1c{_ass_colour(style.color)}")
    if style and style.stroke_color:
        overrides.append(f"\\3c{_ass_colour(style.stroke_color)}")
    if style and style.stroke_width is not None:
        overrides.append(f"\\bord{max(0, round(style.stroke_width * height))}")

    start = _timestamp(clip.start_ms)
    end = _timestamp(clip.start_ms + max(0, clip.duration_ms))
    body = "{" + "".join(overrides) + "}" + _escape(clip.text)
    return f"Dialogue: 0,{start},{end},Default,,0,0,0,,{body}"


def write_ass(
    document: TimelineDocument, *, width: int, height: int, font_name: str, into: Path
) -> Path | None:
    """`build_ass` to a file, or `None` when there is no text.

    UTF-8 without a BOM: libass reads UTF-8, and a BOM ends up parsed as part
    of the first section header — the script loads with no styles and every
    caption silently renders in libass's default.
    """
    script = build_ass(document, width=width, height=height, font_name=font_name)
    if script is None:
        return None
    into.write_text(script, encoding="utf-8", newline="\n")
    return into

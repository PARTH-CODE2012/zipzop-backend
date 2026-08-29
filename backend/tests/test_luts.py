"""Every look the tool can recommend has a grade the renderer can actually use.

**This is the check `docs/15-m5-readiness.md` §3 asks for, and it runs from the
backend on purpose.** The frontend has its own catalogue test, but it compares
the client's list against `SERVER_LOOKS` — a copy of the server's names kept in
step by hand, with a comment saying so. It never reads `color_analysis.py`, so
a look added on the server passes it. This is the direction that was not
covered: from the catalogue that can be recommended, to a file on the disk the
worker will render on.

The last test is the one that matters most. Parsing a `.cube` ourselves proves
our parser agrees with our writer, which is a closed loop and proves nothing
about export. **Handing each file to FFmpeg's `lut3d` proves the thing the
milestone rests on**: that the grade the browser previewed is one the renderer
can apply.
"""

import subprocess

import pytest

from app.services import luts
from app.services.color_analysis import LOOKS
from app.services.ffmpeg_filters import escape_path

pytestmark = pytest.mark.ffmpeg


def test_every_look_the_tool_can_recommend_has_a_grade() -> None:
    """A name in `LOOKS` with no file is a recommendation that arrives, gets
    written into the document, and changes nothing — which reads as the tool
    being broken rather than as a missing file."""
    missing = sorted(set(LOOKS) - luts.available())
    assert missing == [], f"no .cube for {missing} — run: make luts"


def test_the_directory_holds_exactly_the_catalogue() -> None:
    """The other direction. A stray grade on disk is dead weight in the image
    and, more to the point, a look somebody meant to add to `LOOKS` and did
    not."""
    assert luts.available() == frozenset(LOOKS)


@pytest.mark.parametrize("look", sorted(LOOKS))
def test_each_grade_is_a_well_formed_cube(look: str) -> None:
    """Structural, so a broken file fails with a line number rather than with
    an FFmpeg exit code."""
    text = luts.path_for(look).read_text(encoding="utf-8")

    size = next(
        int(line.split()[1]) for line in text.splitlines() if line.startswith("LUT_3D_SIZE")
    )
    assert size >= 2

    entries = [
        line
        for line in text.splitlines()
        if line and not line[0].isalpha() and not line.startswith("#")
    ]
    assert len(entries) == size**3, f"{look}: {len(entries)} entries for a {size}³ table"

    for line in entries:
        channels = [float(value) for value in line.split()]
        assert len(channels) == 3
        # Outside 0-1 is not clipped by `lut3d`, it wraps — a value of 1.2 comes
        # back as a dark pixel, and the grade would look wrong rather than fail.
        assert all(0.0 <= channel <= 1.0 for channel in channels), f"{look}: {line}"


def test_the_grades_are_actually_different_from_one_another() -> None:
    """Five names over one table would recommend a difference nobody can see."""
    fingerprints = {luts.path_for(look).read_text(encoding="utf-8")[:4000] for look in LOOKS}
    assert len(fingerprints) == len(LOOKS)


def test_an_unknown_name_is_refused_rather_than_resolved() -> None:
    with pytest.raises(luts.LutMissingError):
        luts.path_for("not_a_look")


def test_a_traversal_cannot_reach_outside_the_grade_directory() -> None:
    """`look` arrives from a timeline document, which is client input. The name
    is checked against what is on disk rather than escaped, so the set of
    reachable files is exactly the set of files that exist."""
    for attempt in ("../../../etc/passwd", "..\\..\\secrets", "/etc/passwd", ""):
        with pytest.raises(luts.LutMissingError):
            luts.path_for(attempt)


@pytest.mark.parametrize("look", sorted(LOOKS))
def test_ffmpeg_accepts_each_grade(look: str) -> None:
    """The one that makes the milestone possible.

    `lut3d=file=…` is exactly what the export renderer will run, and a `.cube`
    our own parser likes is not evidence FFmpeg does. The path is escaped
    through `ffmpeg_filters.escape_path` because a filter option is unescaped
    twice — the same trap `movie=` fell into, and the reason that function is
    shared rather than private to `color_analysis`.
    """
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=32x32:d=0.1",
            "-vf",
            f"lut3d=file={escape_path(luts.path_for(look))}",
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, (
        f"ffmpeg refused {look}: {result.stderr.decode(errors='replace').strip()}"
    )


def test_a_grade_actually_changes_the_picture() -> None:
    """`lut3d` accepting a file is not the same as the file doing anything.

    An identity table would pass every test above and produce an export
    indistinguishable from an ungraded one — the failure mode that made this
    milestone's predecessor worth writing up, where a recommendation arrived
    and the picture did not change.
    """

    def mean_of(filters: str) -> float:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-f",
                "lavfi",
                "-i",
                f"color=c=#3a6ea5:s=32x32:d=0.1,{filters}signalstats",
                "-show_entries",
                "frame_tags=lavfi.signalstats.YAVG",
                "-of",
                "default=nw=1:nk=1",
                "-read_intervals",
                "%+#1",
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        return float(result.stdout.split()[0])

    plain = mean_of("")
    graded = {
        look: mean_of(f"lut3d=file={escape_path(luts.path_for(look))},") for look in sorted(LOOKS)
    }

    moved = {look for look, value in graded.items() if abs(value - plain) > 0.5}
    assert moved, f"no grade changed a mid-blue at all (plain YAVG {plain}, graded {graded})"

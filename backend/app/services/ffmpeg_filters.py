"""Building filter-graph arguments that survive FFmpeg's parsers.

One function so far, and it is here rather than private to a caller because it
was already wrong once in `color_analysis` and the export renderer is about to
need the same thing for `lut3d=file=…`. A second private copy is how the first
one's bug gets reintroduced somewhere it has not been found yet.
"""

from pathlib import Path


def escape_path(path: Path | str) -> str:
    """A filesystem path, safe to interpolate into a filter argument.

    `movie=…`, `lut3d=file=…` and their relatives take a path as a filter
    *option*, so a colon or a backslash in it is syntax. **It is unescaped
    twice on the way in, not once**: the filtergraph parser strips one level
    before the filter's own option parser ever sees the string, so a single
    `\\:` arrives as a bare `:` and the path splits at it.

    One level is what `color_analysis` did until 27 August, and nothing caught
    it — every scratch path on Linux is colon-free, so the escaping was dead
    code that happened to be wrong. The first path with a colon in it, which is
    every Windows path, proved it.

    Both levels are applied to the path as it is, with no platform branch. An
    earlier fix rewrote Windows separators to forward slashes — which FFmpeg
    accepts, and which spares a drive path four backslashes per separator — but
    a backslash is a legal character in a POSIX *filename*, so that rewrite
    turned `/tmp/od\\d/a.mp4` into a path to somewhere else. Guarding it behind
    `os.name` fixed the corruption and left a branch that could not be
    exercised on the machine running the tests, which is how the original bug
    got in. Plain escaping is correct on both; verified against ffmpeg 9.0.1 on
    Windows and by `tests/test_analysis.py` on the POSIX cases.

    A path needing no escaping comes out byte-identical to what went in.
    """
    text = str(path)
    # Level 1 — the filter's own option parser.
    text = text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
    # Level 2 — the filtergraph parser, which unescapes before level 1 runs.
    return text.replace("\\", "\\\\")

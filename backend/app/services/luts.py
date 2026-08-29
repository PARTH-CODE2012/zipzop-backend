"""Where the colour grades live, from the renderer's side.

**One file, two renderers, and until 28 August only one of them could reach
it.** The browser uploads a `.cube` into a WebGL `TEXTURE_3D` for the preview
and the export renderer hands the same file to FFmpeg's `lut3d`. That is the
whole basis for trusting that an export matches what the user was shown
(contract §4.4: *"the LUT files are shared assets, not two implementations"*).

The files used to live only under `frontend/public/luts/`, which the browser
could fetch and the worker could not open — and the container is built from the
`backend/` context, so they were not even in the image. `lut3d=file=…` had
nothing to point at, and it would have failed at the first graded export rather
than at build time. Named as M5's first problem in `docs/15-m5-readiness.md` §3
and fixed by moving the source of truth here; the browser's copy under
`frontend/public/luts/` is made from this one and is gitignored.

**The check that matters is in the tests, not here.** `color_analysis.LOOKS` is
the catalogue of what the tool may recommend, and a name in it with no readable
file is a recommendation that cannot be rendered — the failure this module
exists to make impossible. `tests/test_luts.py` asserts the two agree, from the
backend, which is the direction the frontend's own catalogue test cannot cover.
"""

from pathlib import Path

#: `app/assets/luts/`, resolved from this file rather than from the working
#: directory: a Celery worker's cwd is whatever the process was started in, and
#: a relative path here would work in tests and fail in the container.
LUT_DIR = Path(__file__).resolve().parent.parent / "assets" / "luts"

SUFFIX = ".cube"


class LutMissingError(Exception):
    """A look with no file behind it.

    Raised rather than returning `None` because every caller is a render that
    cannot proceed: a silent fallback to no grade would produce an export that
    does not match the preview the user approved, which is the one failure this
    milestone is defined against.
    """


def path_for(look: str) -> Path:
    """The `.cube` for a look, or raise.

    The name is validated against the directory rather than interpolated into a
    path: `look` reaches here from a timeline document, which is client input,
    and `../../etc/passwd` is a path traversal. Comparing against what is
    actually on disk makes the set of reachable files exactly the set of files
    that exist here, with no escaping to get right.
    """
    if look not in available():
        raise LutMissingError(f"no colour grade named {look!r}")
    return LUT_DIR / f"{look}{SUFFIX}"


def available() -> frozenset[str]:
    """Every grade on disk, by name.

    Read at call time, not cached at import: `make luts` rewrites the directory
    and a long-lived worker holding a stale set would refuse a grade that is
    sitting right there. Five `stat` calls against a directory the OS has in
    cache is not worth a cache of our own.
    """
    if not LUT_DIR.is_dir():
        return frozenset()
    return frozenset(path.stem for path in LUT_DIR.glob(f"*{SUFFIX}"))

"""Building filter-graph arguments that survive FFmpeg's parsers, and confining
what those parsers are allowed to reach.

Two concerns, one module, and both here for the same reason: they were each a
copy-paste away from being wrong in a different file. `escape_path` was already
wrong once in `color_analysis`; the allowlists below have to be identical at ten
call sites, and ten private copies is ten chances to forget one.
"""

from pathlib import Path
from typing import Final

#: What an FFmpeg/FFprobe invocation on user-supplied media is allowed to open.
#:
#: **The sharpest edge in the product** (docs/07-security.md §6.4): a file chosen
#: entirely by an attacker is handed to a C demuxer, and some container formats
#: — HLS playlists, the concat demuxer, a `.mov` with an external data reference
#: — name a *URL* for the demuxer to follow. Left unrestricted that is an SSRF
#: primitive: an uploaded playlist pointing a segment at
#: `http://169.254.169.254/…` reads the instance's IAM credentials from inside
#: the worker.
#:
#: `file` and nothing else. Every network protocol (http, https, tcp, tls,
#: rtmp, …) is refused, so no reference inside a user file can leave the host.
#: The residual — a reference to another *local* file — is closed by naming the
#: demuxers too (`USER_MEDIA_FORMATS`, M7-22).
#:
#: **This is pinned rather than assumed.** FFmpeg *does* ship a secure-ish
#: default (9.0.1 here defaults the demuxer whitelist to `file,crypto,data`),
#: but that default is build- and version-dependent — the Debian image the
#: worker runs is a different build from any developer's — and it once was
#: permissive. A one-argument control that says exactly what we allow is worth
#: more than a default we have to re-verify on every base-image bump. It is
#: applied as an **input** option, before each `-i`, so it governs the demuxer
#: and its sub-resources without touching where output is written.
USER_MEDIA_PROTOCOLS: Final = "file"

#: The demuxers user media may be opened with — one per container the upload
#: route accepts (`SUPPORTED_CONTENT_TYPES`), named as FFmpeg names them:
#: `mov` reads mp4, mov and m4a; `matroska` reads mkv and webm.
#:
#: **What the protocol allowlist could not close (M7-22).** `file` has to stay
#: allowed to open the upload at all, and two demuxers turn a file into a list
#: of *other* files: `concat` and `hls`. FFprobe picks the demuxer from the
#: bytes, not the name — an `ffconcat` text uploaded as `clip.mp4` was opened as
#: concat by FFmpeg 9.0.1 and its `file …` line followed, reading the file next
#: to it. Its `-safe` default kept that to relative paths without `..`, and each
#: job's scratch is a private directory, so it reached nothing but the job's own
#: files; but that was a property of where files happened to be, not a control.
#: Naming the formats closes the class: concat, HLS, DASH, image sequences, and
#: any reference-following demuxer a future FFmpeg adds are refused by name
#: before they parse anything. A container we do not accept is now refused by
#: FFmpeg too, which is the same answer the upload route already gives.
USER_MEDIA_FORMATS: Final = "mov,matroska,avi,mp3,aac,wav,flac,ogg"


def user_media_input_args() -> list[str]:
    """The allowlists, to place immediately before an `-i` on user media.

    A function rather than a bare constant so a call site reads as
    ``[*user_media_input_args(), "-i", path]`` and cannot accidentally share or
    mutate one list, and so there is exactly one spelling of each flag.
    """
    return [
        "-protocol_whitelist",
        USER_MEDIA_PROTOCOLS,
        "-format_whitelist",
        USER_MEDIA_FORMATS,
    ]


def movie_source_options() -> str:
    """The same two allowlists for a lavfi `movie=` source, as its `format_opts`.

    `movie=` opens the file through a demuxer of its own, which top-level flags
    do not reach. `format_opts` is a dictionary parsed **three** times on the way
    in, so each separator is escaped for the parser that must not consume it:

    * level 3, the dictionary itself: `key=value` pairs joined by `:`;
    * level 2, the filter's option parser, which ends a value at a bare `:` — so
      the pair separator arrives as `\\:`;
    * level 1, the filtergraph parser, which unescapes once and splits filters
      at a bare `,` — so backslashes are doubled and the format list's commas
      escaped.

    Verified against the real ffprobe: a valid upload is analysed, an
    `ffconcat` one is refused with "Format not on whitelist".
    """
    pairs = (("protocol_whitelist", USER_MEDIA_PROTOCOLS), ("format_whitelist", USER_MEDIA_FORMATS))
    level2 = "\\:".join(f"{key}={value}" for key, value in pairs)
    level1 = level2.replace("\\", "\\\\").replace(",", "\\,")
    return f"format_opts={level1}"


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

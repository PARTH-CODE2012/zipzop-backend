"""Building filter-graph arguments that survive FFmpeg's parsers, and confining
what those parsers are allowed to reach.

Two concerns, one module, and both here for the same reason: they were each a
copy-paste away from being wrong in a different file. `escape_path` was already
wrong once in `color_analysis`; the protocol allowlist below has to be identical
at ten call sites, and ten private copies is ten chances to forget one.
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
#: rtmp, …) is refused, so no reference inside a user file can leave the host;
#: the residual — a `file:///etc/…` reference — is what worker egress
#: restriction and running ingest with no ambient credentials are for (§6.4).
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


def input_protocol_args() -> list[str]:
    """The allowlist flag, to place immediately before an `-i` on user media.

    A function rather than a bare constant so a call site reads as
    ``[*input_protocol_args(), "-i", path]`` and cannot accidentally share or
    mutate one list, and so there is exactly one spelling of the flag name.
    """
    return ["-protocol_whitelist", USER_MEDIA_PROTOCOLS]


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

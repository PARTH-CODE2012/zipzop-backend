"""FFmpeg is handed attacker-chosen bytes; this pins what it may reach.

docs/07-security.md §6.4 calls the ingest worker the sharpest boundary in the
product: a file chosen entirely by an attacker goes to a C demuxer with no human
in between, and some container formats — an HLS playlist, the concat demuxer, a
`.mov` with an external data reference — name a *URL* for the demuxer to follow.
Left open that is an SSRF primitive against the instance metadata endpoint.

Two kinds of test here, and both are needed:

* **the flag is present** at every call site that touches user media — a unit
  assertion that fails the moment someone adds a new `ffmpeg`/`ffprobe` call and
  forgets it, which is exactly how the first copy of a bug like this survives;
* **the flag bites** — a crafted HLS playlist that references a URL is refused by
  the real `ffprobe`, and does not reach out.

The readiness note (docs/22-m7-readiness.md §2.1) ranked this first. Running it
refined the finding rather than confirming a live hole: FFmpeg 9.0.1 already
defaults the demuxer allowlist to `file,crypto,data`, so http is refused on this
build without us. The value of pinning `file` explicitly is that the guarantee
stops depending on which FFmpeg the image happens to ship — which is the same
reason the LUTs and the font are resolved from files rather than trusted to be
configured.
"""

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from app.services import color_analysis, ingest, render_graph, smart_trim, transcription
from app.services.ffmpeg_filters import USER_MEDIA_PROTOCOLS, input_protocol_args
from app.services.smart_trim import THRESHOLDS


def _assert_allowlisted_before_input(cmd: list[str]) -> None:
    """The allowlist is present, is exactly `file`, and precedes every input.

    `-protocol_whitelist` is an *input* option: placed after an `-i` it governs
    nothing. So the assertion is not merely that the flag exists but that it sits
    ahead of the first input the command opens.
    """
    assert "-protocol_whitelist" in cmd, cmd
    idx = cmd.index("-protocol_whitelist")
    assert cmd[idx + 1] == USER_MEDIA_PROTOCOLS == "file", cmd
    # ffmpeg uses `-i`; ffprobe takes the input positionally as the last arg. In
    # either case the flag must come before the thing being opened.
    if "-i" in cmd:
        assert idx < cmd.index("-i"), cmd
    else:
        assert idx < len(cmd) - 1, cmd


@pytest.fixture
def capture(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Record the command lists a module hands to `subprocess.run`.

    Returns a benign failed result so the caller raises its own domain error
    rather than parsing empty output; the test only cares about the command.
    """

    def _make(module: Any) -> list[list[str]]:
        calls: list[list[str]] = []

        def _fake_run(args: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess[bytes]:
            calls.append(list(args))
            return subprocess.CompletedProcess(args, returncode=1, stdout=b"", stderr=b"")

        monkeypatch.setattr(module.subprocess, "run", _fake_run)
        return calls

    return _make


# --------------------------------------------------------------------------
# The flag is present at every call site
# --------------------------------------------------------------------------


def test_probe_pins_the_protocol_allowlist(capture: Any) -> None:
    calls = capture(ingest)
    with pytest.raises(ingest.UnreadableMediaError):
        ingest.probe(Path("/tmp/whatever.mp4"))
    assert calls
    _assert_allowlisted_before_input(calls[0])


def test_proxy_pins_the_protocol_allowlist(capture: Any, tmp_path: Path) -> None:
    calls = capture(ingest)
    with pytest.raises(ingest.UnreadableMediaError):
        ingest.make_proxy(Path("/tmp/in.mp4"), tmp_path / "out.mp4")
    _assert_allowlisted_before_input(calls[0])


def test_audio_proxy_pins_the_protocol_allowlist(capture: Any, tmp_path: Path) -> None:
    calls = capture(ingest)
    with pytest.raises(ingest.UnreadableMediaError):
        ingest.make_audio_proxy(Path("/tmp/in.m4a"), tmp_path / "out.m4a")
    _assert_allowlisted_before_input(calls[0])


def test_thumbnail_pins_the_allowlist_on_both_attempts(capture: Any, tmp_path: Path) -> None:
    calls = capture(ingest)
    with pytest.raises(ingest.UnreadableMediaError):
        ingest.make_thumbnail(Path("/tmp/in.mp4"), tmp_path / "t.jpg", duration_ms=5000)
    # The seek attempt and the first-frame retry are both on user media.
    assert len(calls) == 2
    for cmd in calls:
        _assert_allowlisted_before_input(cmd)


def test_peaks_pins_the_protocol_allowlist(capture: Any) -> None:
    calls = capture(ingest)
    ingest.make_peaks(Path("/tmp/in.mp4"), duration_ms=1000, has_audio=True)
    _assert_allowlisted_before_input(calls[0])


def test_color_analysis_pins_the_allowlist_on_the_movie_source(capture: Any) -> None:
    calls = capture(color_analysis)
    with pytest.raises(color_analysis.AnalysisFailedError):
        color_analysis.sample_frames(Path("/tmp/in.mp4"), duration_ms=4000)
    cmd = calls[0]
    _assert_allowlisted_before_input(cmd)
    # The lavfi `movie=` source opens the file through its own demuxer, which the
    # top-level flag does not reach — so it carries the allowlist inline. Without
    # this the movie source silently falls back to FFmpeg's build default.
    movie = next(part for part in cmd if part.startswith("movie="))
    assert f"protocol_whitelist\\={USER_MEDIA_PROTOCOLS}" in movie, movie


def test_smart_trim_silence_pins_the_protocol_allowlist(capture: Any) -> None:
    calls = capture(smart_trim)
    smart_trim.detect_silence(Path("/tmp/in.mp4"), THRESHOLDS["medium"], duration_ms=4000)
    _assert_allowlisted_before_input(calls[0])


def test_transcription_envelope_pins_the_protocol_allowlist(capture: Any) -> None:
    calls = capture(transcription)
    transcription._rms_envelope(Path("/tmp/in.mp4"))
    _assert_allowlisted_before_input(calls[0])


def test_export_graph_pins_the_allowlist_before_every_input() -> None:
    """Every `-i` in the render command is a user upload, so every one of them
    carries the flag — not just the first."""
    from app.api.schemas.project import MediaClip, MediaTrack, TimelineDocument
    from app.services import luts

    clips = [
        MediaClip.model_validate(
            {"id": f"clp_{n}", "assetId": f"ast_{n}", "startMs": n * 1000, "durationMs": 1000}
        )
        for n in range(2)
    ]
    document = TimelineDocument(
        schema_version=1, tracks=[MediaTrack(id="trk_v", kind="video", index=0, clips=clips)]
    )
    plan = render_graph.build_command(
        document,
        sources={"ast_0": Path("/tmp/a.mp4"), "ast_1": Path("/tmp/b.mp4")},
        settings=render_graph.RenderSettings.for_preset(
            aspect_ratio="9:16", height=480, crf=28, fps=24, watermark=False
        ),
        output=Path("/tmp/out.mp4"),
        lut_path_for=luts.path_for,
    )
    # One `-protocol_whitelist file` for each of the two inputs, each before its
    # own `-i`.
    args = plan.args
    input_positions = [i for i, a in enumerate(args) if a == "-i"]
    assert len(input_positions) == 2
    for i in input_positions:
        window = args[max(0, i - 6) : i]
        assert "-protocol_whitelist" in window and USER_MEDIA_PROTOCOLS in window, args


def test_input_protocol_args_is_a_fresh_list_each_call() -> None:
    """A shared mutable default is how one call's args leak into another's."""
    a = input_protocol_args()
    a.append("mutated")
    assert input_protocol_args() == ["-protocol_whitelist", "file"]


# --------------------------------------------------------------------------
# The flag bites — real ffprobe refuses a URL-referencing upload
# --------------------------------------------------------------------------


@pytest.mark.ffmpeg
def test_probe_refuses_an_hls_upload_that_points_at_a_url(tmp_path: Path) -> None:
    """A media playlist naming an http segment must be refused, not fetched.

    The segment points at a closed loopback port: if the allowlist failed open,
    ffprobe would try to connect (and this asserts it does not, by finishing
    well inside the connect timeout as a clean refusal). On a build whose default
    already blocks http this still passes — it pins the behaviour against a build
    whose default does not.
    """
    evil = tmp_path / "evil.mp4"  # a video content-type, to look like an upload
    evil.write_text(
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#EXT-X-MEDIA-SEQUENCE:0\n"
        "#EXTINF:10.0,\n"
        "http://127.0.0.1:1/segment0.ts\n"
        "#EXT-X-ENDLIST\n",
        encoding="utf-8",
    )
    started = time.monotonic()
    with pytest.raises(ingest.UnreadableMediaError):
        ingest.probe(evil)
    # A refusal is immediate; a fetch attempt against a dead port would burn the
    # connect timeout. Ten seconds is far under ffprobe's 60s probe budget and
    # far over a clean refusal.
    assert time.monotonic() - started < 10.0


# --------------------------------------------------------------------------
# The watermark is a constant, not a sink — docs/07-security.md §6.5
# --------------------------------------------------------------------------


def test_the_watermark_text_cannot_be_passed_in() -> None:
    """`build_command` has no `watermark_text` parameter.

    `drawtext` expands `%{…}`, and `'` and `:` are field delimiters, so a
    user-controlled string interpolated into it is a filter-graph injection. The
    text is a module constant precisely so nothing — a plan name, a display name,
    a future custom-watermark field — can ever reach that sink by being threaded
    through here.
    """
    import inspect

    params = inspect.signature(render_graph.build_command).parameters
    assert "watermark_text" not in params
    assert render_graph.WATERMARK_TEXT == "ZipZop"

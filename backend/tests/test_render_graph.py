"""The export filter graph.

Two kinds of test, and both are needed. Asserting on the command string catches
the decisions — scale-and-pad rather than stretch, the grade mixed at its
strength, `atempo` chained past 2x — with a failure that names the filter.
**Rendering real media through it catches whether FFmpeg agrees**, which no
amount of string comparison can, and that is the half that matters: the
milestone's closing condition is a file, not a filtergraph.
"""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.api.schemas.project import MediaClip, MediaTrack, TimelineDocument
from app.services import luts
from app.services.render_graph import RenderSettings, build_command, source_window

pytestmark = pytest.mark.ffmpeg


def _settings(**over: Any) -> RenderSettings:
    base: dict[str, Any] = {
        "aspect_ratio": "9:16",
        "height": 480,
        "crf": 28,
        "fps": 24,
        "watermark": False,
    }
    return RenderSettings.for_preset(**{**base, **over})


def _clip(**over: Any) -> MediaClip:
    base: dict[str, Any] = {
        "id": "clp_1",
        "assetId": "ast_1",
        "startMs": 0,
        "durationMs": 1000,
        "sourceInMs": 0,
    }
    return MediaClip.model_validate({**base, **over})


def _document(*clips: MediaClip, music: list[MediaClip] | None = None) -> TimelineDocument:
    tracks: list[Any] = [
        MediaTrack(id="trk_v", kind="video", index=0, clips=list(clips)),
    ]
    if music:
        tracks.append(MediaTrack(id="trk_a", kind="audio", index=0, clips=music))
    return TimelineDocument(schema_version=1, tracks=tracks)


def _graph(document: TimelineDocument, sources: dict[str, Path], **over: Any) -> Any:
    return build_command(
        document,
        sources=sources,
        settings=_settings(**over.pop("settings", {})),
        output=Path("out.mp4"),
        lut_path_for=luts.path_for,
        **over,
    )


# --------------------------------------------------------------------------
# The decisions, read off the command
# --------------------------------------------------------------------------


def test_the_frame_is_padded_and_never_stretched() -> None:
    """A 16:9 source in a 9:16 frame keeps its geometry and gets bars.

    Squeezing is the one transformation the user cannot undo by re-editing.
    """
    plan = _graph(_document(_clip()), {"ast_1": Path("a.mp4")})

    assert "force_original_aspect_ratio=decrease" in plan.filtergraph
    assert "pad=270:480" in plan.filtergraph
    # 9:16 at 480 high is 270 wide, and even — H.264 cannot encode an odd one.
    assert plan.args[plan.args.index("-filter_complex") + 1] == plan.filtergraph


def test_an_odd_width_is_rounded_down_rather_than_failing_at_the_encoder() -> None:
    """`yuv420p` cannot encode an odd dimension, and the error it gives names
    the pixel format rather than the aspect ratio that caused it."""
    settings = RenderSettings.for_preset(
        aspect_ratio="9:16", height=721, crf=20, fps=30, watermark=False
    )
    assert settings.width % 2 == 0
    assert settings.height % 2 == 0


def test_the_grade_is_mixed_at_its_strength_and_not_applied_flat() -> None:
    """`lut3d` has no opacity. Applying it fully and calling 0.6 close enough
    would make every graded export differ from the preview, which composites
    the same LUT at the same fraction in a shader."""
    clip = _clip(effects=[{"type": "color_grade", "lut": "cyberpunk", "strength": 0.6}])
    plan = _graph(_document(clip), {"ast_1": Path("a.mp4")})

    assert "split=2" in plan.filtergraph
    assert "blend=all_mode=normal:all_opacity=0.6000" in plan.filtergraph
    assert "lut3d=file=" in plan.filtergraph


def test_a_full_strength_grade_skips_the_blend() -> None:
    """One fewer filter to composite on every frame, and the result is
    identical — a blend at opacity 1 is the graded copy."""
    clip = _clip(effects=[{"type": "color_grade", "lut": "cyberpunk", "strength": 1.0}])
    plan = _graph(_document(clip), {"ast_1": Path("a.mp4")})

    assert "lut3d=file=" in plan.filtergraph
    assert "blend=" not in plan.filtergraph


def test_a_zero_strength_grade_is_not_applied_at_all() -> None:
    clip = _clip(effects=[{"type": "color_grade", "lut": "cyberpunk", "strength": 0.0}])
    plan = _graph(_document(clip), {"ast_1": Path("a.mp4")})

    assert "lut3d" not in plan.filtergraph


def test_speed_beyond_2x_chains_atempo_instead_of_clamping() -> None:
    """`atempo` accepts 0.5-2.0 and the document allows 0.25-4.0. Clamping
    would run a 4x clip's audio at half the speed of its picture — obvious in
    the file, invisible in the code."""
    plan = _graph(_document(_clip(speed=4.0)), {"ast_1": Path("a.mp4")})

    # Two of them: 4x is 2x twice, which is the whole point of chaining.
    assert plan.filtergraph.count("atempo=2.0") == 2
    assert "atempo=2.0000" in plan.filtergraph
    assert "setpts=PTS/4.0000" in plan.filtergraph


def test_a_quarter_speed_clip_chains_the_other_way() -> None:
    plan = _graph(_document(_clip(speed=0.25)), {"ast_1": Path("a.mp4")})
    assert plan.filtergraph.count("atempo=0.5") >= 1


def test_the_source_window_is_derived_and_not_stored() -> None:
    """There is no `sourceOutMs` in the document on purpose — storing a
    derivable value invites the two to disagree (contract §4.2)."""
    start, take = source_window(_clip(sourceInMs=2000, durationMs=4000, speed=2.0))
    assert start == 2.0
    assert take == 8.0  # four seconds of timeline at 2x eats eight of source


def test_a_muted_video_track_is_silenced_and_not_dropped() -> None:
    """Dropping it would change the number of concat inputs and put the video
    out of step with its own audio."""
    document = TimelineDocument(
        schema_version=1,
        tracks=[MediaTrack(id="trk_v", kind="video", index=0, muted=True, clips=[_clip()])],
    )
    plan = _graph(document, {"ast_1": Path("a.mp4")})

    assert "volume=0.0000" in plan.filtergraph
    assert "concat=n=1:v=1:a=1" in plan.filtergraph


def test_the_watermark_is_drawn_when_the_plan_says_so() -> None:
    """Server-side and not a parameter — contract §6.2."""
    plan = _graph(_document(_clip()), {"ast_1": Path("a.mp4")}, settings={"watermark": True})
    assert "text='ZipZop'" in plan.filtergraph
    # A font *file*, never a family: a family goes through fontconfig, which is
    # not configured on every machine that runs this, and the failure lands on a
    # job the user has already paid for.
    assert "fontfile=" in plan.filtergraph

    clean = _graph(_document(_clip()), {"ast_1": Path("a.mp4")})
    assert "drawtext" not in clean.filtergraph


def test_music_is_delayed_to_where_the_user_put_it() -> None:
    """`-ss` says where to start *in the file*; `adelay` says where to start
    *in the project*, and the two are not the same number."""
    music = [_clip(id="clp_m", assetId="ast_m", startMs=5000, durationMs=3000)]
    plan = _graph(_document(_clip(), music=music), {"ast_1": Path("a.mp4"), "ast_m": Path("m.m4a")})

    assert "adelay=5000:all=1" in plan.filtergraph
    assert "amix=inputs=2" in plan.filtergraph
    # Not normalised: `amix` otherwise ducks every input when one ends, so the
    # voice would jump in level the moment the music stopped.
    assert "normalize=0" in plan.filtergraph


def test_an_empty_timeline_is_refused_rather_than_rendered_black() -> None:
    with pytest.raises(ValueError, match="no video clips"):
        _graph(_document(), {})


def test_a_missing_source_names_the_asset() -> None:
    with pytest.raises(ValueError, match="ast_1"):
        _graph(_document(_clip()), {})


def test_the_plans_duration_is_the_timeline_and_not_the_media() -> None:
    """Progress is measured against this, so it comes from the plan rather than
    from probing a file that does not exist yet."""
    plan = _graph(
        _document(_clip(durationMs=1500), _clip(id="clp_2", startMs=1500, durationMs=2500)),
        {"ast_1": Path("a.mp4")},
    )
    assert plan.duration_ms == 4000


# --------------------------------------------------------------------------
# What FFmpeg makes of it
# --------------------------------------------------------------------------


def _probe(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        timeout=60,
        check=True,
    )
    return json.loads(result.stdout)  # type: ignore[no-any-return]


def test_the_graph_renders_a_real_file(sample_video: Path, tmp_path: Path) -> None:
    """The half that matters. Two clips of real media, one graded, one sped up,
    concatenated into a vertical frame — and the file is probed afterwards,
    because an exit code of 0 only says FFmpeg did not crash."""
    output = tmp_path / "export.mp4"
    document = _document(
        _clip(id="clp_1", durationMs=1000, sourceInMs=0),
        _clip(
            id="clp_2",
            startMs=1000,
            durationMs=1000,
            sourceInMs=1000,
            speed=2.0,
            effects=[{"type": "color_grade", "lut": "cyberpunk", "strength": 0.5}],
        ),
    )
    plan = _graph(document, {"ast_1": sample_video})
    plan.args[-1] = str(output)

    result = subprocess.run(plan.args, capture_output=True, timeout=300, check=False)
    assert result.returncode == 0, result.stderr.decode(errors="replace")[-2000:]

    probed = _probe(output)
    video = next(s for s in probed["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (270, 480)
    assert video["codec_name"] == "h264"
    # Both clips are in it: two seconds of timeline, within a frame either way.
    assert abs(float(probed["format"]["duration"]) - 2.0) < 0.25
    assert any(s["codec_type"] == "audio" for s in probed["streams"])


def test_a_watermarked_render_still_produces_a_playable_file(
    sample_video: Path, tmp_path: Path
) -> None:
    """`drawtext` needs a font, and a container without one fails at render
    time rather than at build time — which is exactly the class of problem the
    LUT files had."""
    output = tmp_path / "marked.mp4"
    plan = _graph(
        _document(_clip(durationMs=500)),
        {"ast_1": sample_video},
        settings={"watermark": True},
    )
    plan.args[-1] = str(output)

    result = subprocess.run(plan.args, capture_output=True, timeout=300, check=False)
    assert result.returncode == 0, result.stderr.decode(errors="replace")[-2000:]
    assert output.stat().st_size > 0

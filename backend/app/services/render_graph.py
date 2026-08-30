"""The export filter graph: a timeline document in, an FFmpeg command out.

**A pure function, and that is the point.** Building the command and running it
are separate so the graph can be tested without a worker, a bucket or a
database — `tests/test_render_graph.py` asserts on the string, and
`tests/test_render.py` renders real media through it. The milestone's closing
condition is that the file matches the preview, and neither half of that is
checkable if the only way to see the graph is to run a job.

## What the graph does, in order

Per video clip: seek and trim, speed, crop, flip, rotate, the colour grade at
its strength, then scale-and-pad into the output frame. Then concat, then the
text overlay, then the watermark. Audio runs alongside: per-clip volume and
fades, the video track's own audio concatenated, the music track delayed to its
start and mixed under it.

## Three decisions worth knowing before reading the code

**Scale-and-pad, never stretch.** A 16:9 source in a 9:16 frame keeps its
geometry and gets bars. Squeezing it would be the one transformation the user
cannot undo by re-editing, and `docs/03` §7 is explicit that the canvas is a
frame the picture sits inside.

**The grade is `split` + `lut3d` + `blend`, not `lut3d` alone.** `strength` is a
number between 0 and 1 and `lut3d` has no opacity: applying it at full strength
and calling 0.75 close enough would make every graded export differ from the
preview, which composites the same LUT at the same fraction in a shader. The
graph mixes the graded copy back over the original at exactly `strength`.

**Text is one `subtitles` filter over a generated ASS file, not `drawtext` per
clip.** Captions land one clip per word, so a 60-minute recording is on the
order of ten thousand text clips — ten thousand `drawtext` filters is a
filtergraph FFmpeg will not parse, let alone run. See `render_text.py`.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.api.schemas.project import (
    ASPECT_RATIOS,
    MediaClip,
    MediaTrack,
    TimelineDocument,
    Transform,
)
from app.services import fonts
from app.services.ffmpeg_filters import escape_path

#: Frames per second of the output. Taken from the project rather than guessed
#: — `projects.fps` exists and defaults to 30 — but pinned here as the fallback
#: for a document that predates it.
DEFAULT_FPS = 30

#: How the graph gets from a look name to a `.cube` on disk. Injected rather
#: than imported so a test can hand it a stub without a grade directory, and so
#: this module never has to know where the grades live.
LutResolver = Callable[[str], Path]


@dataclass(frozen=True, slots=True)
class RenderSettings:
    """Everything about the output that is not in the timeline."""

    width: int
    height: int
    crf: int
    fps: int = DEFAULT_FPS
    watermark: bool = False

    @classmethod
    def for_preset(
        cls, *, aspect_ratio: str, height: int, crf: int, fps: int, watermark: bool
    ) -> "RenderSettings":
        """Width comes from the aspect ratio and the height, and is forced even.

        H.264 with `yuv420p` chroma subsampling cannot encode an odd dimension;
        an odd width fails at the encoder with a message about the pixel format
        that says nothing about the aspect ratio that caused it.
        """
        ref_w, ref_h = ASPECT_RATIOS[aspect_ratio]
        width = round(height * ref_w / ref_h)
        return cls(
            width=width - (width % 2),
            height=height - (height % 2),
            crf=crf,
            fps=fps,
            watermark=watermark,
        )


@dataclass(slots=True)
class GraphPlan:
    """The command, and what a caller needs to know about it afterwards."""

    args: list[str]
    #: Wall-clock length of the output, in milliseconds. Progress is measured
    #: against this, so it comes from the plan rather than from probing the
    #: file that does not exist yet.
    duration_ms: int
    filtergraph: str = ""
    inputs: list[Path] = field(default_factory=list)


def _crop_filter(transform: Transform | None) -> str | None:
    """Normalised 0-1 rectangle to pixels, in the source's own dimensions.

    `iw`/`ih` rather than numbers: the crop is expressed against the *source*
    frame, and the source is whatever the user uploaded. Resolving it here to
    pixels would need the probe, and would be wrong the moment two clips on the
    timeline come from files of different sizes.
    """
    if transform is None or transform.crop is None:
        return None
    crop = transform.crop
    return f"crop=iw*{crop.width:.6f}:ih*{crop.height:.6f}:iw*{crop.x:.6f}:ih*{crop.y:.6f}"


def _transform_filters(transform: Transform | None) -> list[str]:
    """Flip, rotate, scale and offset — in that order, and the order matters.

    Rotation before scaling, because `transpose` swaps the dimensions and a
    scale applied first would be scaling the wrong axis. Offset last, because it
    is a position in the *output* frame and everything before it changes what
    that frame contains.
    """
    if transform is None:
        return []
    filters: list[str] = []
    if transform.flip_h:
        filters.append("hflip")
    if transform.flip_v:
        filters.append("vflip")
    # `transpose` is a quarter turn; 180 is two of them. Free rotation is
    # deliberately not offered (see `Transform.rotation`).
    if transform.rotation == 90:
        filters.append("transpose=1")
    elif transform.rotation == 180:
        filters.append("transpose=1,transpose=1")
    elif transform.rotation == 270:
        filters.append("transpose=2")
    if transform.scale != 1.0:
        filters.append(f"scale=iw*{transform.scale:.4f}:ih*{transform.scale:.4f}")
    return filters


def _grade_filters(clip: MediaClip, lut_path_for: "LutResolver") -> list[str]:
    """The colour grade, mixed at its strength.

    Returns a *chain fragment* rather than a single filter because the mix
    needs a split and a blend, and expressing that as one string keeps the
    caller from having to know it is three filters.
    """
    grade = next((effect for effect in clip.effects if effect.type == "color_grade"), None)
    if grade is None or grade.strength <= 0:
        return []

    path = escape_path(lut_path_for(grade.lut))

    if grade.strength >= 1.0:
        # No blend needed, and one fewer filter to composite on every frame.
        return [f"lut3d=file={path}"]

    # `split` the stream, grade one copy, lay it back over the original at the
    # requested opacity. This is what the browser's shader does with the same
    # number, which is the only reason the two agree.
    return [
        f"split=2[grade_a][grade_b];[grade_b]lut3d=file={path}[grade_g];"
        f"[grade_a][grade_g]blend=all_mode=normal:all_opacity={grade.strength:.4f}"
    ]


def _fit_filters(settings: RenderSettings) -> list[str]:
    """Into the output frame, letterboxed rather than stretched."""
    return [
        f"scale={settings.width}:{settings.height}:force_original_aspect_ratio=decrease",
        f"pad={settings.width}:{settings.height}:(ow-iw)/2:(oh-ih)/2:color=black",
        # Square pixels. Without this a source with a non-1 sample aspect ratio
        # comes out geometrically wrong in a way that looks like a bad crop.
        "setsar=1",
        f"fps={settings.fps}",
        # Every concat input must share a pixel format or the filter refuses.
        "format=yuv420p",
    ]


def video_chain(
    clip: MediaClip, index: int, settings: RenderSettings, lut_path_for: LutResolver
) -> str:
    """One clip's video, from decoded input to a frame ready to concatenate."""
    steps: list[str] = []
    crop = _crop_filter(clip.transform)
    if crop:
        steps.append(crop)
    steps.extend(_transform_filters(clip.transform))
    if clip.speed != 1.0:
        # `setpts` retimes; the trim above already took `duration * speed`
        # worth of source, so this lands on exactly `duration_ms`.
        steps.append(f"setpts=PTS/{clip.speed:.4f}")
    steps.extend(_grade_filters(clip, lut_path_for))
    steps.extend(_fit_filters(settings))
    return f"[{index}:v]" + ",".join(steps) + f"[v{index}]"


def audio_chain(clip: MediaClip, index: int, *, muted: bool) -> str:
    """One clip's audio: speed, level, fades.

    A muted track is silenced here rather than dropped, because dropping it
    would change the number of concat inputs and put the video out of step with
    its own audio.
    """
    steps: list[str] = []
    if clip.speed != 1.0:
        steps.extend(_atempo_chain(clip.speed))
    volume = 0.0 if muted else clip.volume
    steps.append(f"volume={volume:.4f}")
    if clip.audio_fade_in_ms > 0:
        steps.append(f"afade=t=in:st=0:d={clip.audio_fade_in_ms / 1000:.3f}")
    if clip.audio_fade_out_ms > 0:
        start = max(0.0, (clip.duration_ms - clip.audio_fade_out_ms) / 1000)
        steps.append(f"afade=t=out:st={start:.3f}:d={clip.audio_fade_out_ms / 1000:.3f}")
    # Resampled to one rate so `concat` and `amix` will accept them together;
    # sources arrive at whatever the phone recorded at.
    steps.append("aresample=48000")
    steps.append("asetpts=N/SR/TB")
    return f"[{index}:a]" + ",".join(steps) + f"[a{index}]"


def _atempo_chain(speed: float) -> list[str]:
    """`atempo` only accepts 0.5-2.0, and the document allows 0.25-4.0.

    Chained rather than clamped: 4x is `atempo=2,atempo=2`. Clamping would make
    a 4x clip's audio run at half the speed of its picture, which is the kind of
    defect that is obvious in the file and invisible in the code.
    """
    steps: list[str] = []
    remaining = speed
    while remaining > 2.0:
        steps.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        steps.append("atempo=0.5")
        remaining *= 2.0
    steps.append(f"atempo={remaining:.4f}")
    return steps


def _video_track(document: TimelineDocument) -> MediaTrack | None:
    for track in document.tracks:
        if track.kind == "video":
            return track
    return None


def _audio_track(document: TimelineDocument) -> MediaTrack | None:
    for track in document.tracks:
        if track.kind == "audio":
            return track
    return None


def source_window(clip: MediaClip) -> tuple[float, float]:
    """Where to seek in the source, and how much of it to take.

    `durationMs * speed`, because there is deliberately no `sourceOutMs` in the
    document — storing a derivable value invites the two to disagree and the
    renderer would have no way to know which is right (contract §4.2).
    """
    start_s = clip.source_in_ms / 1000
    take_s = (clip.duration_ms * clip.speed) / 1000
    return start_s, take_s


def build_command(
    document: TimelineDocument,
    *,
    sources: dict[str, Path],
    settings: RenderSettings,
    output: Path,
    lut_path_for: LutResolver,
    subtitles: Path | None = None,
    watermark_text: str = "ZipZop",
    progress_to: str | None = None,
) -> GraphPlan:
    """The whole render, as one FFmpeg invocation.

    One pass, not a temporary file per clip. Intermediate files would mean
    encoding every clip twice — once to scratch and once into the output — and
    a generation of H.264 loss for nothing.

    `sources` maps `assetId` to the downloaded original. **Originals, not
    proxies**: the proxy is 480p and exists so the browser can scrub, and
    exporting from it would cap every render at the preview's resolution.
    """
    video_track = _video_track(document)
    audio_track = _audio_track(document)
    video_clips = list(video_track.clips) if video_track else []
    music_clips = list(audio_track.clips) if audio_track else []

    if not video_clips:
        raise ValueError("nothing to render: the timeline has no video clips")

    args: list[str] = ["ffmpeg", "-hide_banner", "-v", "error", "-y"]
    inputs: list[Path] = []
    chains: list[str] = []

    # ---- inputs -----------------------------------------------------------
    # `-ss` before `-i` so the seek is done by the demuxer rather than by
    # decoding and discarding: on a 2 GB source that is the difference between
    # a render and a render that takes an hour.
    for clip in [*video_clips, *music_clips]:
        source = sources.get(clip.asset_id)
        if source is None:
            raise ValueError(f"no source file for asset {clip.asset_id}")
        start_s, take_s = source_window(clip)
        args += ["-ss", f"{start_s:.3f}", "-t", f"{take_s:.3f}", "-i", str(source)]
        inputs.append(source)

    # ---- per-clip chains --------------------------------------------------
    for index, clip in enumerate(video_clips):
        chains.append(video_chain(clip, index, settings, lut_path_for))
        chains.append(audio_chain(clip, index, muted=bool(video_track and video_track.muted)))

    concat_in = "".join(f"[v{i}][a{i}]" for i in range(len(video_clips)))
    chains.append(f"{concat_in}concat=n={len(video_clips)}:v=1:a=1[vcat][acat]")

    video_label = "vcat"

    # ---- text -------------------------------------------------------------
    if subtitles is not None:
        # One filter for every caption in the project. `drawtext` per clip is
        # the obvious shape and it does not survive contact with a caption run:
        # one clip per word means ten thousand filters in a graph FFmpeg will
        # not parse.
        chains.append(f"[{video_label}]subtitles={escape_path(subtitles)}[vtext]")
        video_label = "vtext"

    # ---- watermark --------------------------------------------------------
    if settings.watermark:
        # Server-side and not a parameter: contract §6.2 is explicit that the
        # client cannot ask for it to be left off. Sized against the frame so
        # it is the same size relative to the picture at every resolution.
        size = max(12, round(settings.height * 0.028))
        margin = round(settings.height * 0.02)
        # `fontfile=`, never a family name. A family goes through fontconfig,
        # which is not configured on every machine that runs this — the Windows
        # development one dies with "Cannot load default config file" and an
        # exit code that is not an error message. Naming a file makes a missing
        # font a startup problem instead of a render that fails after the user
        # has paid for it. See `app/services/fonts.py`.
        chains.append(
            f"[{video_label}]drawtext=fontfile={escape_path(fonts.default_font())}:"
            f"text='{watermark_text}':"
            f"fontsize={size}:fontcolor=white@0.75:"
            f"borderw={max(1, size // 12)}:bordercolor=black@0.45:"
            f"x=w-tw-{margin}:y=h-th-{margin}[vout]"
        )
        video_label = "vout"

    # ---- music ------------------------------------------------------------
    audio_label = "acat"
    if music_clips:
        offset = len(video_clips)
        for position, clip in enumerate(music_clips):
            index = offset + position
            chains.append(audio_chain(clip, index, muted=bool(audio_track and audio_track.muted)))
            # The music sits where the user put it on the timeline, which the
            # trim above cannot express — `-ss` says where to start *in the
            # file*, `adelay` says where to start *in the project*.
            if clip.start_ms > 0:
                chains.append(f"[a{index}]adelay={clip.start_ms}:all=1[am{position}]")
            else:
                chains.append(f"[a{index}]anull[am{position}]")
        mix_in = "[acat]" + "".join(f"[am{p}]" for p in range(len(music_clips)))
        # `dropout_transition=0` and `normalize=0`: `amix` otherwise ducks every
        # input when one ends, so the voice would jump in level the moment the
        # music stopped.
        chains.append(
            f"{mix_in}amix=inputs={len(music_clips) + 1}:duration=first:"
            f"dropout_transition=0:normalize=0[amix]"
        )
        audio_label = "amix"

    filtergraph = ";".join(chains)
    args += ["-filter_complex", filtergraph, "-map", f"[{video_label}]", "-map", f"[{audio_label}]"]

    args += [
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(settings.crf),
        "-pix_fmt",
        "yuv420p",
        # Faststart: the moov atom moves to the front so the file plays while
        # it downloads. A social upload that has to buffer the whole file first
        # is a file people give up on.
        "-movflags",
        "+faststart",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
    ]
    if progress_to:
        # FFmpeg's own machine-readable progress, which is what turns a render
        # into a number the user can watch instead of a spinner.
        args += ["-progress", progress_to, "-nostats"]
    args.append(str(output))

    duration_ms = sum(clip.duration_ms for clip in video_clips)
    return GraphPlan(args=args, duration_ms=duration_ms, filtergraph=filtergraph, inputs=inputs)

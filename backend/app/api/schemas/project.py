"""Request and response shapes for /projects — contract §4 and §5.

**The timeline document is typed here, field by field, and that is the point of
this module.** `docs/04-frontend-architecture.md` §3.1 requires the client's
timeline type to be *generated* from this schema rather than hand-written, so
anything left as `dict[str, Any]` arrives in the frontend as
`Record<string, unknown>` and the two sides drift on the first field anyone
adds. The cost is that every contract change touches this file. That is the
intended cost.

Bounds that appear in the contract's invariant list (§4.3) are deliberately
**not** enforced by pydantic here — they are checked in `app/services/timeline.py`
so the caller gets `INVALID_TIMELINE` naming the offending clip, rather than a
generic `VALIDATION_ERROR` pointing at an array index. Bounds that are not
invariants (speed, volume, colour format) stay here, where pydantic reports
them precisely and for free.
"""

from datetime import datetime
from typing import Annotated, Final, Literal

from pydantic import Field

from app.api.schemas.common import ApiModel

#: `Field(default=[])` rather than `default_factory=list` throughout this
#: module, and the reason is the generated client. A factory cannot be shown
#: in a JSON schema, so the field emits no `default` and openapi-typescript
#: marks it optional — the frontend then gets `clips?: MediaClip[]` and every
#: caller needs a `?? []`, which is exactly the fresh-array-per-render bug M2
#: spent an end-to-end run finding. Pydantic deep-copies mutable defaults, so
#: the list is not shared between instances.
#:
#: Bumped only when the document's shape changes incompatibly. A client sending
#: anything else is refused rather than guessed at.
SCHEMA_VERSION: Final[Literal[1]] = 1

#: contract §5: canvas dimensions are derived from the aspect ratio, never sent
#: by the client. A client that could choose them could ask for 4K on a free
#: plan by writing the numbers itself.
ASPECT_RATIOS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
}

AspectRatio = Literal["9:16", "16:9", "1:1"]

#: Captions land one clip per word (`docs/04-frontend-architecture.md` §7), so a
#: 60-minute recording — the longest phase 1 accepts — is on the order of ten
#: thousand text clips. The ceiling is generous rather than tight because the
#: real limit is the performance budget, not this number; what it exists to stop
#: is a payload with ten million clips in it.
MAX_CLIPS_PER_TRACK = 20_000


# --------------------------------------------------------------------------
# Clip parts
# --------------------------------------------------------------------------


class Crop(ApiModel):
    """A normalised rectangle of the source frame to keep.

    contract §4.3: *"All spatial values are normalised 0-1 relative to the
    canvas, never pixels."* This is what lets a 480p preview and a 1080p export
    agree; pixels would put every reframe in the wrong place at export, and
    nobody would notice until they watched the file.
    """

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)


class Transform(ApiModel):
    scale: float = Field(default=1.0, gt=0.0, le=10.0)
    offset_x: float = Field(default=0.0, ge=-1.0, le=1.0)
    offset_y: float = Field(default=0.0, ge=-1.0, le=1.0)
    #: Quarter turns only. Phase 1 ships "rotate", not free rotation — arbitrary
    #: angles need interpolation the renderer does not do, and the scope's
    #: non-goals rule out keyframes and motion paths. The contract's example
    #: shows `0` and does not pin the range down, so it is pinned here.
    rotation: Literal[0, 90, 180, 270] = 0
    flip_h: bool = False
    flip_v: bool = False
    crop: Crop | None = None


class ColorGradeEffect(ApiModel):
    """contract §4.4. Phase 1 defines exactly one effect type.

    When a second arrives this becomes a discriminated union on `type`, which is
    why `type` is a Literal rather than absent — the field is already carrying
    the discriminator's weight before there is anything to discriminate.
    """

    type: Literal["color_grade"] = "color_grade"
    lut: str = Field(min_length=1, max_length=64)
    strength: float = Field(ge=0.0, le=1.0)
    source_job_id: str | None = Field(default=None, max_length=64)


class Transition(ApiModel):
    type: Literal["cut", "fade", "dissolve"]
    duration_ms: int = Field(ge=0)


# --------------------------------------------------------------------------
# Clips
# --------------------------------------------------------------------------


class MediaClip(ApiModel):
    """A clip on a video or audio track — contract §4.2.

    There is deliberately **no `sourceOutMs`**: it is
    `sourceInMs + durationMs * speed`. Storing a derivable value invites the two
    to disagree, and the renderer would have no way to know which is right.
    """

    id: str = Field(min_length=1, max_length=64)
    asset_id: str = Field(min_length=1, max_length=64)
    start_ms: int = Field(ge=0)
    #: Not `gt=0` — invariant 3 owns that, so the failure names the clip.
    duration_ms: int
    source_in_ms: int = Field(default=0, ge=0)
    speed: float = Field(default=1.0, ge=0.25, le=4.0)
    volume: float = Field(default=1.0, ge=0.0, le=2.0)
    audio_fade_in_ms: int = Field(default=0, ge=0)
    audio_fade_out_ms: int = Field(default=0, ge=0)
    transform: Transform | None = None
    effects: list[ColorGradeEffect] = Field(default=[], max_length=8)
    transition_in: Transition | None = None
    transition_out: Transition | None = None


class TextStyle(ApiModel):
    """Overrides on top of `styleId`. Every field optional: the catalogue entry
    supplies the rest, so a caption that only changes colour carries one key."""

    font_size: float | None = Field(default=None, gt=0.0, le=1.0)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    stroke_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    stroke_width: float | None = Field(default=None, ge=0.0, le=1.0)


class TextPosition(ApiModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    anchor: Literal["center", "left", "right"] = "center"


class TextClip(ApiModel):
    """A caption word or a typed title — contract §4.2.

    `text` is editable and that is the whole point of the editor model: a
    misheard name is corrected here, not by re-running the tool.
    """

    id: str = Field(min_length=1, max_length=64)
    kind: Literal["caption", "title"]
    start_ms: int = Field(ge=0)
    duration_ms: int
    text: str = Field(max_length=2_000)
    style_id: str = Field(min_length=1, max_length=64)
    style: TextStyle | None = None
    position: TextPosition | None = None
    emphasis: float = Field(default=0.0, ge=0.0, le=1.0)
    source_job_id: str | None = Field(default=None, max_length=64)


# --------------------------------------------------------------------------
# Tracks and the document
# --------------------------------------------------------------------------


class MediaTrack(ApiModel):
    id: str = Field(min_length=1, max_length=64)
    kind: Literal["video", "audio"]
    index: int = Field(default=0, ge=0, le=8)
    muted: bool = False
    locked: bool = False
    clips: list[MediaClip] = Field(default=[], max_length=MAX_CLIPS_PER_TRACK)


class TextTrack(ApiModel):
    id: str = Field(min_length=1, max_length=64)
    kind: Literal["text"]
    index: int = Field(default=0, ge=0, le=8)
    muted: bool = False
    locked: bool = False
    clips: list[TextClip] = Field(default=[], max_length=MAX_CLIPS_PER_TRACK)


#: Discriminated on `kind` so the generated TypeScript is a union a `switch`
#: narrows, rather than one wide object with every field optional.
Track = Annotated[MediaTrack | TextTrack, Field(discriminator="kind")]


class TimelineDocument(ApiModel):
    schema_version: Literal[1] = 1
    #: Phase 1 allows one track of each kind (invariant 8), so three is the
    #: ceiling; the invariant itself is checked where it can name the offender.
    tracks: list[Track] = Field(default=[], max_length=3)


def empty_timeline() -> TimelineDocument:
    return TimelineDocument(schema_version=SCHEMA_VERSION, tracks=[])


# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------


class CreateProjectRequest(ApiModel):
    title: str = Field(default="Untitled project", min_length=1, max_length=200)
    aspect_ratio: AspectRatio = "9:16"


class UpdateProjectRequest(ApiModel):
    """PATCH — contract §5.

    Two different edits share this shape, and they must not be confused:

    - `{timeline, version}` is the autosave. It validates, bumps `version` and
      rebuilds `project_assets`.
    - `{title}` or `{aspectRatio}` is a metadata edit. It **does not touch the
      timeline and does not bump `version`**, because bumping it would make
      every other tab's next autosave a spurious 409.
    """

    timeline: TimelineDocument | None = None
    version: int | None = Field(default=None, ge=0)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    aspect_ratio: AspectRatio | None = None


class ProjectAssetRef(ApiModel):
    """One asset the timeline references, with URLs signed for an hour.

    *"`assets` is a convenience: every asset the timeline references, with fresh
    signed URLs, so opening a project is one request rather than one per clip."*

    `thumbnailUrl` is an addition to the contract's example, which lists `id`,
    `proxyUrl`, `peaksUrl` and `durationMs`. Without it the media bin has to
    call `/media` immediately after opening a project, which is the second
    request this field exists to avoid. Additive, so no client breaks.
    """

    id: str
    proxy_url: str | None = None
    peaks_url: str | None = None
    thumbnail_url: str | None = None
    duration_ms: int | None = None


class ProjectResponse(ApiModel):
    id: str
    title: str
    aspect_ratio: str
    width: int
    height: int
    fps: int
    duration_ms: int
    version: int
    timeline: TimelineDocument
    assets: list[ProjectAssetRef]
    created_at: datetime
    updated_at: datetime


class ProjectSummary(ApiModel):
    """What the projects list returns — **no timeline**.

    A list of twenty projects carrying twenty timelines would be megabytes to
    render a page of titles.
    """

    id: str
    title: str
    aspect_ratio: str
    duration_ms: int
    thumbnail_url: str | None = None
    updated_at: datetime


class ProjectSaveResponse(ApiModel):
    """What a PATCH returns — contract §5. Deliberately small: the client
    already has the timeline it just sent, and echoing it back doubles the
    bytes of every two-second autosave."""

    version: int
    duration_ms: int
    updated_at: datetime

"""Job payloads — contract §6.

**Every per-tool input is spelled out**, for the same reason the timeline
document is (docs/09-m3-notes.md §1): anything left as a free-form object
arrives in the frontend as `Record<string, unknown>`, and the two sides drift on
the first field anyone adds. `input` is a union of three named shapes, and the
one that applies is chosen by `tool` rather than guessed from the keys — an
untagged union that happens to match is a union that will one day match the
wrong member.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.schemas.common import ApiModel
from app.models import JobFamily, JobStatus, JobTool
from app.services import languages

#: The tools `POST /jobs` accepts today. The phase-2 tools are phase 2 —
#: listing them here would put endpoints in the contract that answer 500, and a
#: client generated from that schema would offer buttons that cannot work.
#:
#: `export` joined on 28 August, in M5.
PHASE_1_TOOLS = (
    JobTool.CAPTIONS,
    JobTool.SMART_TRIM,
    JobTool.COLOR_ANALYSIS,
    JobTool.EXPORT,
)

TRIM_STRENGTHS = ("light", "medium", "aggressive")

#: Export heights, by the name a user sees. Exactly the ladder the seeded plan
#: ceilings name (`plans.max_export_height` is 720, 1080 or 2160) — offering a
#: 1440p that no plan's ceiling refers to would be a tier invented in a schema.
EXPORT_HEIGHTS: dict[str, int] = {"720p": 720, "1080p": 1080, "2160p": 2160}

#: Constant Rate Factor for x264, lower being better. Not exposed as a number:
#: "18" means nothing to the person choosing, and pinning the mapping here keeps
#: a preset's meaning the same across every render rather than travelling in the
#: job payload where an old queued job would render at the old meaning.
EXPORT_QUALITY_CRF: dict[str, int] = {"draft": 26, "standard": 21, "high": 18}


class RangeMs(ApiModel):
    """A window inside the asset, in **asset** time — never timeline time.

    The distinction is the one that makes captions land in the wrong place when
    it is got wrong: a clip trimmed to start at 4 s and played at 2x has a
    different clock from the timeline it sits on (contract §6.2).
    """

    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> "RangeMs":
        if self.end_ms <= self.start_ms:
            raise ValueError("endMs must be after startMs")
        return self


class _AssetInput(ApiModel):
    asset_id: str
    #: Which clip on the timeline the result is for. The server never reads it —
    #: it is echoed back so the client can route a result to the clip that asked
    #: for it without keeping a map of its own in-flight jobs.
    clip_id: str | None = None
    range_ms: RangeMs | None = None


class CaptionsInput(_AssetInput):
    #: `auto` detects; otherwise one of `app.services.languages.SUPPORTED` —
    #: English, French or Hindi, decided 21 August.
    #:
    #: Rejected rather than silently detected, because a user who names a
    #: language is telling us something they know and we do not: which one is
    #: being spoken under the music, or which of two in the same recording they
    #: want. Falling back to `auto` would throw that away and be right often
    #: enough that nobody would notice it was ignored.
    language: str = languages.AUTO

    @field_validator("language")
    @classmethod
    def _known_language(cls, value: str) -> str:
        if not languages.is_supported(value):
            offered = ", ".join(sorted(languages.SUPPORTED))
            raise ValueError(f"we do not caption {value!r} yet; try one of {offered}, or 'auto'")
        return languages.normalise(value)


class SmartTrimInput(_AssetInput):
    strength: Literal["light", "medium", "aggressive"] = "medium"


class ColorAnalysisInput(_AssetInput):
    preferred_look: str | None = None


class ExportPreset(ApiModel):
    """What the file should be — contract §6.2.

    Names rather than numbers throughout. A preset is a promise about the
    output, and `resolution: "1080p"` still means the same thing next year
    while `height: 1080` invites a client to send 1081.
    """

    resolution: Literal["720p", "1080p", "2160p"] = "1080p"
    #: Vertical first: the product is short-form. Nothing here is squeezed —
    #: the renderer scales to fit and pads, so a 16:9 source in a 9:16 frame
    #: keeps its geometry (docs/03 §7).
    aspect_ratio: Literal["9:16", "1:1", "16:9"] = "9:16"
    quality: Literal["draft", "standard", "high"] = "high"
    #: One format in phase 1. H.264 in MP4 is the only thing that plays
    #: everywhere the audience is, and a second container is a second matrix of
    #: codec support to test.
    format: Literal["mp4"] = "mp4"

    @property
    def height(self) -> int:
        return EXPORT_HEIGHTS[self.resolution]

    @property
    def crf(self) -> int:
        return EXPORT_QUALITY_CRF[self.quality]


class ExportInput(ApiModel):
    """The one input with no `assetId`: an export is of a *project*, not of a
    clip. `projectId` on the request body carries it.

    **`timelineVersion` is not optimistic locking, it is a refusal to guess.**
    Every other tool reads an asset that cannot change underneath it. An export
    reads the timeline, and a timeline the user has edited since they pressed
    the button is a render of something they are no longer looking at — so a
    mismatch is `409 VERSION_CONFLICT` rather than a render of the current
    version (contract §6.2).
    """

    timeline_version: int = Field(ge=1)
    preset: ExportPreset = Field(default_factory=ExportPreset)


#: Smart union, deliberately: the members are structurally close enough that
#: `left_to_right` would coerce a `SmartTrimInput` into the first one that
#: accepts it and drop `strength` on the way. Smart mode keeps an exact instance
#: — which is what `_parse_input_for_tool` below hands it — as itself.
JobInput = CaptionsInput | SmartTrimInput | ColorAnalysisInput | ExportInput

#: `ApiModel` and not `_AssetInput`: `ExportInput` deliberately has no asset.
_INPUT_MODEL: dict[JobTool, type[ApiModel]] = {
    JobTool.CAPTIONS: CaptionsInput,
    JobTool.SMART_TRIM: SmartTrimInput,
    JobTool.COLOR_ANALYSIS: ColorAnalysisInput,
    JobTool.EXPORT: ExportInput,
}


class CreateJobRequest(ApiModel):
    tool: JobTool
    project_id: str | None = None
    input: JobInput

    @model_validator(mode="before")
    @classmethod
    def _parse_input_for_tool(cls, data: Any) -> Any:
        """Parse `input` with the model `tool` names, not with whichever union
        member happens to accept it.

        Pydantic would otherwise resolve the union by trying members in order,
        and all three accept `{assetId}` — so a `smart_trim` job sent with a
        `strength` would silently parse as `CaptionsInput`, drop the field, and
        run at the default. Choosing explicitly makes an unknown field an error
        the caller can read instead of a setting that quietly did nothing.
        """
        if not isinstance(data, dict):
            return data
        raw_tool = data.get("tool")
        raw_input = data.get("input")
        if not isinstance(raw_input, dict):
            return data
        try:
            tool = JobTool(str(raw_tool))
        except ValueError:
            return data  # let the field validator report the bad tool
        model = _INPUT_MODEL.get(tool)
        if model is None:
            return data
        return {**data, "input": model.model_validate(raw_input)}

    @model_validator(mode="after")
    def _tool_is_available(self) -> "CreateJobRequest":
        if self.tool not in PHASE_1_TOOLS:
            raise ValueError(
                f"{self.tool.value} is not available yet; "
                f"phase 1 ships {', '.join(t.value for t in PHASE_1_TOOLS)}"
            )
        return self

    @model_validator(mode="after")
    def _export_names_its_project(self) -> "CreateJobRequest":
        """An export with no project has no timeline to render.

        Caught here rather than in the handler so the client gets a 422 naming
        the field, instead of a 404 for a project it never mentioned.
        """
        if self.tool is JobTool.EXPORT and not self.project_id:
            raise ValueError("export needs a projectId — it renders a project, not a clip")
        return self


class ReservedFrom(ApiModel):
    """Which buckets a job drew from. A job can span two — contract §6."""

    plan: int = 0
    topup: int = 0
    facemap_seconds: int = 0


class JobError(ApiModel):
    code: str
    message: str


class JobResponse(ApiModel):
    id: str
    tool: JobTool
    family: JobFamily
    status: JobStatus
    progress: int
    priority: int
    credits_reserved: int
    reserved_from: ReservedFrom
    estimated_seconds: int
    project_id: str | None = None
    clip_id: str | None = None

    #: Exactly one of these carries an analysis result. Over 256 KB the payload
    #: goes to S3 and `result` is null — **a client must handle both**, and a
    #: caption run on anything over about twenty minutes of speech takes the
    #: second path (contract §6.3).
    result: dict[str, Any] | None = None
    result_url: str | None = None
    output_asset_id: str | None = None

    error: JobError | None = None

    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class EstimateResponse(ApiModel):
    """What `POST /jobs` would charge, without creating anything.

    `blockedBy` carries the error code the real call would return, so the client
    can put "Not enough credits" on the button itself rather than after a failed
    click (contract §6.1).
    """

    credits: int
    would_reserve_from: ReservedFrom
    estimated_seconds: int
    sufficient_balance: bool
    blocked_by: str | None = None


class JobProgressEvent(BaseModel):
    """What goes onto the user's Redis channel and out over the WebSocket.

    The socket is an **optimisation, not the source of truth** — every field
    here is also readable from `GET /jobs/{id}`, which is what a client falls
    back to when the connection drops (docs/10-m4-readiness.md §3).
    """

    type: Literal["job.progress", "job.succeeded", "job.failed", "job.cancelled"]
    job_id: str
    status: JobStatus
    progress: int
    tool: JobTool
    clip_id: str | None = None
    error: JobError | None = None

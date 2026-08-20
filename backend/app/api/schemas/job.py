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

from pydantic import BaseModel, Field, model_validator

from app.api.schemas.common import ApiModel
from app.models import JobFamily, JobStatus, JobTool

#: The tools `POST /jobs` accepts today. `export` is M5 and the phase-2 tools
#: are phase 2 — listing them here would put endpoints in the contract that
#: answer 500, and a client generated from that schema would offer buttons that
#: cannot work.
PHASE_1_TOOLS = (JobTool.CAPTIONS, JobTool.SMART_TRIM, JobTool.COLOR_ANALYSIS)

TRIM_STRENGTHS = ("light", "medium", "aggressive")


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
    #: `auto` detects. The accepted list is still open — see
    #: docs/10-m4-readiness.md §1, "the language list" — and `auto` is enough to
    #: ship the pipeline without pre-empting that decision.
    language: str = "auto"


class SmartTrimInput(_AssetInput):
    strength: Literal["light", "medium", "aggressive"] = "medium"


class ColorAnalysisInput(_AssetInput):
    preferred_look: str | None = None


#: Smart union, deliberately: the members are structurally close enough that
#: `left_to_right` would coerce a `SmartTrimInput` into the first one that
#: accepts it and drop `strength` on the way. Smart mode keeps an exact instance
#: — which is what `_parse_input_for_tool` below hands it — as itself.
JobInput = CaptionsInput | SmartTrimInput | ColorAnalysisInput

_INPUT_MODEL: dict[JobTool, type[_AssetInput]] = {
    JobTool.CAPTIONS: CaptionsInput,
    JobTool.SMART_TRIM: SmartTrimInput,
    JobTool.COLOR_ANALYSIS: ColorAnalysisInput,
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

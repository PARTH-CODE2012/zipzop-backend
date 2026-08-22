"""Request and response shapes for /media — contract §3."""

from datetime import datetime
from typing import Any

from pydantic import Field

from app.api.schemas.common import ApiModel

#: What the ingest pipeline can actually read. Anything outside this list is
#: rejected before a presigned URL is handed out, so the user is told in a
#: round trip rather than after uploading two gigabytes.
SUPPORTED_CONTENT_TYPES: dict[str, str] = {
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/x-matroska": "mkv",
    "video/webm": "webm",
    "video/x-msvideo": "avi",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/aac": "aac",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/flac": "flac",
    "audio/ogg": "ogg",
}


class UploadRequest(ApiModel):
    filename: str = Field(min_length=1, max_length=512)
    size_bytes: int = Field(gt=0)
    content_type: str


class MultipartPart(ApiModel):
    part_number: int
    url: str


class MultipartPlan(ApiModel):
    upload_id: str
    part_size_bytes: int
    parts: list[MultipartPart]


class UploadResponse(ApiModel):
    """*"The file itself never passes through this API."*"""

    asset_id: str
    upload_url: str
    method: str
    headers: dict[str, str]
    expires_at: datetime
    multipart: MultipartPlan | None = None


class CompletedPart(ApiModel):
    part_number: int
    etag: str


class CompleteUploadRequest(ApiModel):
    etag: str | None = None
    parts: list[CompletedPart] | None = None


class AssetResponse(ApiModel):
    """contract §3.

    **The three URLs are signed and expire in one hour.** Re-fetch the asset to
    renew them; never persist one in the timeline document or in client
    storage, or a saved project will come back with dead links.
    """

    id: str
    kind: str
    status: str
    original_filename: str | None = None
    size_bytes: int | None = None
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    audio_channels: int | None = None
    proxy_url: str | None = None
    thumbnail_url: str | None = None
    peaks_url: str | None = None
    derived_from_asset_id: str | None = None
    failure_reason: str | None = None
    created_at: datetime


class PeaksResponse(ApiModel):
    version: int
    buckets_per_second: int
    channels: int
    duration_ms: int
    peaks: list[float]


def peaks_from_document(document: dict[str, Any]) -> PeaksResponse:
    return PeaksResponse(
        version=int(document["version"]),
        buckets_per_second=int(document["bucketsPerSecond"]),
        channels=int(document["channels"]),
        duration_ms=int(document.get("durationMs", 0)),
        peaks=[float(p) for p in document["peaks"]],
    )

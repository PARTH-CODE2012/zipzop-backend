"""Progress, out to whoever is watching.

One Redis channel per user (`user:{id}`), fire-and-forget. The API replica
holding that user's WebSocket subscribes and forwards; a replica holding no
socket for them ignores it. That is the whole fan-out
(docs/03-backend-architecture.md §9).

**The socket is an optimisation and not the source of truth.** Every event here
describes a change that has *already been written to the jobs row*, so a client
that misses one — disconnected, backgrounded, on a train — recovers completely
by calling `GET /jobs?status=running`. Nothing about a job is knowable only
through this channel, and nothing here is allowed to become the only place
something is recorded.

Publishing therefore never fails a job. A Redis blip must not turn a completed
analysis into a failure the user gets refunded for.
"""

import json
import uuid
from typing import Any

from app.logging import get_logger
from app.models import JobStatus, JobTool
from app.services.redis_client import get_redis, user_channel

log = get_logger(__name__)


async def publish(
    *,
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    tool: JobTool,
    status: JobStatus,
    progress: int,
    clip_id: str | None = None,
    error: dict[str, str] | None = None,
) -> None:
    event: dict[str, Any] = {
        "type": _EVENT_TYPES.get(status, "job.progress"),
        "jobId": f"job_{job_id}",
        "status": status.value,
        "progress": progress,
        "tool": tool.value,
    }
    if clip_id:
        event["clipId"] = clip_id
    if error:
        event["error"] = error

    try:
        await get_redis().publish(user_channel(str(user_id)), json.dumps(event))
    except Exception as exc:
        # Deliberately swallowed. The row is already written; the client's
        # fallback poll will find it. Losing the notification is a slower
        # interface, losing the job would be a refund and a lost result.
        log.warning("job_event_publish_failed", job_id=str(job_id), error=type(exc).__name__)


_EVENT_TYPES = {
    JobStatus.SUCCEEDED: "job.succeeded",
    JobStatus.FAILED: "job.failed",
    JobStatus.CANCELLED: "job.cancelled",
    JobStatus.RUNNING: "job.progress",
    JobStatus.QUEUED: "job.progress",
}

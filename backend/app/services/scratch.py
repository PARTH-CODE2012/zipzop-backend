"""Scratch directories a killed worker left behind.

Every pipeline works in a `tempfile.TemporaryDirectory(prefix="zipzop-…")`,
which removes itself when the task ends — unless the process is killed, which
is the one way a worker reliably ends in production (an OOM, a deploy, a scaled-
in instance). Found on the local staging stack in M7: three `docker kill`s
during an ingest left three directories behind, each holding the user's
original upload, and nothing ever removed them. The disk fills, and a worker's
disk keeps copies of files their owners may since have deleted.

**Orphaned by age, never by guess.** A directory is removed only when nothing
in it has been written for `ORPHANED_AFTER`. The longest a live task goes
without writing is a transcription — `TRANSCRIBE_TIMEOUT_SECONDS`, 30 minutes,
reading audio it already extracted; a render rewrites its progress file every
couple of seconds. Six hours is therefore safe whatever the process topology:
two workers sharing one `/tmp`, a restart, a task still finishing after its
parent died. Nothing here needs to know which process owns what.
"""

import shutil
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from app.logging import get_logger

log = get_logger(__name__)

PREFIX = "zipzop-"
ORPHANED_AFTER = timedelta(hours=6)


def purge_orphaned(root: Path | None = None, *, now: float | None = None) -> list[str]:
    """Remove `zipzop-*` directories nothing has written to for six hours.

    Returns the names removed. Called by the pipeline sweep every five
    minutes, on whichever worker runs it — so every worker's disk is visited
    before long, without any of them coordinating.
    """
    base = root or Path(tempfile.gettempdir())
    cutoff = (now or time.time()) - ORPHANED_AFTER.total_seconds()
    removed: list[str] = []
    for candidate in base.glob(f"{PREFIX}*"):
        if not candidate.is_dir() or candidate.is_symlink():
            continue
        try:
            newest = max(
                [candidate.stat().st_mtime]
                + [child.stat().st_mtime for child in candidate.rglob("*")]
            )
        except OSError:
            continue  # vanished while looking: its task finished
        if newest < cutoff:
            shutil.rmtree(candidate, ignore_errors=True)
            removed.append(candidate.name)
    if removed:
        log.warning("scratch_orphans_removed", count=len(removed), names=removed[:20])
    return removed

"""Scratch a killed worker left behind — M7, found by `docker kill` on staging."""

import os
import time
from pathlib import Path

from app.services import scratch

HOUR = 3600


def _aged(path: Path, hours: float) -> None:
    then = time.time() - hours * HOUR
    os.utime(path, (then, then))


def test_a_long_abandoned_scratch_directory_is_removed(tmp_path: Path) -> None:
    orphan = tmp_path / "zipzop-ingest-abc123"
    orphan.mkdir()
    (orphan / "source").write_bytes(b"someone's upload")
    _aged(orphan / "source", 7)
    _aged(orphan, 7)

    assert scratch.purge_orphaned(tmp_path) == ["zipzop-ingest-abc123"]
    assert not orphan.exists()


def test_a_directory_still_being_written_is_kept_however_old_it_is(tmp_path: Path) -> None:
    """A render's directory is hours old and its progress file seconds old:
    the newest write inside is what counts, not the directory's own date."""
    live = tmp_path / "zipzop-export-def456"
    live.mkdir()
    (live / "progress.txt").write_text("frame=1200")
    _aged(live, 20)

    assert scratch.purge_orphaned(tmp_path) == []
    assert live.exists()


def test_a_quiet_but_recent_directory_is_kept(tmp_path: Path) -> None:
    """A transcription writes nothing for up to 30 minutes."""
    quiet = tmp_path / "zipzop-captions-ghi789"
    quiet.mkdir()
    (quiet / "audio.wav").write_bytes(b"\x00" * 16)
    _aged(quiet / "audio.wav", 0.75)
    _aged(quiet, 0.75)

    assert scratch.purge_orphaned(tmp_path) == []


def test_nothing_that_is_not_ours_is_touched(tmp_path: Path) -> None:
    other = tmp_path / "somebody-elses-dir"
    other.mkdir()
    _aged(other, 100)
    stray_file = tmp_path / "zipzop-not-a-directory"
    stray_file.write_text("x")
    _aged(stray_file, 100)

    assert scratch.purge_orphaned(tmp_path) == []
    assert other.exists() and stray_file.exists()

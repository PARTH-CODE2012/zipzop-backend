"""Timeline validation — contract §4.3, all eight invariants.

**This module is the only thing standing between a malformed timeline and a
renderer that fails at export.** The document is stored as JSONB, which
Postgres will accept in any shape whatsoever, so the guarantee has to be made
here or not at all. `docs/03-backend-architecture.md` §4.1 states the trade
plainly: a document buys the client its undo model, and the price is that the
application validates on every write.

Every failure raises `INVALID_TIMELINE` **naming the offending clip**, because
the contract promises that and because "the timeline is invalid" is useless to
a frontend that has just autosaved eighteen hundred caption clips.

The order of the checks is not arbitrary. Structural checks run first and need
no database; the two that need assets run afterwards on one batched lookup, so
a document that is malformed on its face costs no query at all.
"""

import uuid
from collections.abc import Iterable, Sequence
from typing import NoReturn

from app.api.errors import InvalidTimelineError
from app.api.schemas.project import (
    MediaClip,
    MediaTrack,
    TextClip,
    TextTrack,
    TimelineDocument,
)
from app.models import AssetStatus, MediaAsset

#: Invariant 4 compares `sourceInMs + durationMs * speed` against the asset's
#: duration. That product is a float, and a frame at 30 fps is 33.33 ms, so an
#: edit trimmed exactly to the end of its media can land a fraction of a
#: millisecond over. One millisecond of slack costs nothing at export and
#: prevents a class of rejection the user cannot act on.
SOURCE_TOLERANCE_MS = 1

_ASSET_PREFIX = "ast_"


def _reject(reason: str, *, track_id: str, clip_id: str | None = None, **extra: object) -> NoReturn:
    details: dict[str, object] = {"trackId": track_id, "reason": reason, **extra}
    if clip_id is not None:
        details["clipId"] = clip_id
    raise InvalidTimelineError(reason, details=details)


def _clips(track: MediaTrack | TextTrack) -> Sequence[MediaClip | TextClip]:
    return track.clips


# --------------------------------------------------------------------------
# Structure — invariants 1, 2, 3, 6, 7, 8
# --------------------------------------------------------------------------


def validate_structure(document: TimelineDocument) -> None:
    _one_track_per_kind(document)

    seen_ids: set[str] = set()
    for track in document.tracks:
        if track.id in seen_ids:
            _reject("Two tracks share the same id.", track_id=track.id)
        seen_ids.add(track.id)

        previous: MediaClip | TextClip | None = None
        for clip in _clips(track):
            # Invariant 6 — unique across the *whole* document, tracks included.
            # Undo/redo addresses clips by id, so a duplicate makes one of them
            # unreachable and the other undoable twice.
            if clip.id in seen_ids:
                _reject("Two clips share the same id.", track_id=track.id, clip_id=clip.id)
            seen_ids.add(clip.id)

            # Invariant 3.
            if clip.duration_ms <= 0:
                _reject(
                    "A clip must last longer than zero.",
                    track_id=track.id,
                    clip_id=clip.id,
                    durationMs=clip.duration_ms,
                )

            if previous is not None:
                # Invariant 2 — ascending order. Checked before overlap so a
                # document sent out of order is told that, rather than being
                # told two clips collide.
                if clip.start_ms < previous.start_ms:
                    _reject(
                        "Clips are out of order on this track.",
                        track_id=track.id,
                        clip_id=clip.id,
                        startMs=clip.start_ms,
                        previousStartMs=previous.start_ms,
                    )
                # Invariant 1 — no overlap. Transitions overlap at *render*
                # time; the document itself never does, which is what keeps
                # "what is on screen at time t" answerable by a binary search.
                end = previous.start_ms + previous.duration_ms
                if end > clip.start_ms:
                    _reject(
                        "Two clips overlap on this track.",
                        track_id=track.id,
                        clip_id=clip.id,
                        overlapsClipId=previous.id,
                        overlapMs=end - clip.start_ms,
                    )
            previous = clip

        _validate_transitions(track)


def _one_track_per_kind(document: TimelineDocument) -> None:
    """Invariant 8. Phase 1 is one video, one audio, one text track.

    Multiple video tracks are phase 2 (`docs/02-scope-v1.md` §2). Accepting a
    second one here would store a document the export renderer cannot draw.
    """
    kinds: set[str] = set()
    for track in document.tracks:
        if track.kind in kinds:
            _reject(
                f"Phase 1 allows one {track.kind} track.",
                track_id=track.id,
                kind=track.kind,
            )
        kinds.add(track.kind)


def _validate_transitions(track: MediaTrack | TextTrack) -> None:
    """Invariant 7 — a transition may not exceed half the shorter clip it joins.

    Longer than that and the two overlaps meet in the middle: the renderer
    would be asked to read past the end of one clip while still inside the
    other, and the picture it produced would depend on which side it evaluated
    first.
    """
    if not isinstance(track, MediaTrack):
        return

    clips = track.clips
    for position, clip in enumerate(clips):
        for label, transition, neighbour in (
            ("transitionIn", clip.transition_in, clips[position - 1] if position else None),
            (
                "transitionOut",
                clip.transition_out,
                clips[position + 1] if position + 1 < len(clips) else None,
            ),
        ):
            if transition is None or transition.duration_ms == 0:
                continue
            shortest = clip.duration_ms
            if neighbour is not None:
                shortest = min(shortest, neighbour.duration_ms)
            limit = shortest // 2
            if transition.duration_ms > limit:
                _reject(
                    "This transition is longer than half the clip it joins.",
                    track_id=track.id,
                    clip_id=clip.id,
                    transition=label,
                    durationMs=transition.duration_ms,
                    maximumMs=limit,
                )


# --------------------------------------------------------------------------
# Assets — invariants 4 and 5
# --------------------------------------------------------------------------


def media_clips(document: TimelineDocument) -> Iterable[tuple[MediaTrack, MediaClip]]:
    for track in document.tracks:
        if isinstance(track, MediaTrack):
            for clip in track.clips:
                yield track, clip


def referenced_asset_ids(document: TimelineDocument) -> set[uuid.UUID]:
    """Every asset the document points at, as database ids.

    A malformed `assetId` is `INVALID_TIMELINE` rather than the 404 that
    `app.api.ids.decode` would raise: the caller is saving a document, not
    fetching an asset, and telling them "not found" for a string that was never
    an id sends them looking in the wrong place.
    """
    found: set[uuid.UUID] = set()
    for track, clip in media_clips(document):
        raw = clip.asset_id
        if not raw.startswith(_ASSET_PREFIX):
            _reject(
                "That is not an asset id.",
                track_id=track.id,
                clip_id=clip.id,
                assetId=raw,
            )
        try:
            found.add(uuid.UUID(raw[len(_ASSET_PREFIX) :]))
        except ValueError:
            _reject(
                "That is not an asset id.",
                track_id=track.id,
                clip_id=clip.id,
                assetId=raw,
            )
    return found


def validate_against_assets(
    document: TimelineDocument, assets: dict[uuid.UUID, MediaAsset]
) -> None:
    """Invariants 4 and 5, against assets already loaded and already scoped.

    `assets` must come from a repository bound to the caller — this function
    checks that every referenced asset is *present in the mapping*, and
    presence is what carries the ownership guarantee. Passing an unscoped
    lookup here would turn invariant 5 into a decoration.
    """
    for track, clip in media_clips(document):
        asset_id = uuid.UUID(clip.asset_id[len(_ASSET_PREFIX) :])
        asset = assets.get(asset_id)

        # Invariant 5. Absent covers three cases at once — no such asset,
        # somebody else's asset, deleted asset — and they are deliberately
        # indistinguishable to the caller.
        if asset is None:
            _reject(
                "This clip points at a file that is not available.",
                track_id=track.id,
                clip_id=clip.id,
                assetId=clip.asset_id,
            )
        if asset.status is not AssetStatus.READY:
            _reject(
                "This file is still being processed.",
                track_id=track.id,
                clip_id=clip.id,
                assetId=clip.asset_id,
                status=asset.status.value,
            )

        # Invariant 4 — a clip cannot read past the end of its media. `speed`
        # is what makes this worth checking on the server: at 2x a clip
        # consumes twice its timeline duration from the source, and a client
        # that forgets produces a timeline that plays fine in preview and runs
        # out of frames at export.
        consumed = clip.source_in_ms + clip.duration_ms * clip.speed
        available = (asset.duration_ms or 0) + SOURCE_TOLERANCE_MS
        if consumed > available:
            _reject(
                "This clip reads past the end of its file.",
                track_id=track.id,
                clip_id=clip.id,
                assetId=clip.asset_id,
                needsMs=int(consumed),
                availableMs=asset.duration_ms or 0,
            )


# --------------------------------------------------------------------------
# Derived
# --------------------------------------------------------------------------


def duration_ms(document: TimelineDocument) -> int:
    """Where the last clip on any track ends.

    Derived on save and stored on the row, so the projects list can show a
    duration without deserialising every timeline it lists.
    """
    end = 0
    for track in document.tracks:
        for clip in _clips(track):
            end = max(end, clip.start_ms + clip.duration_ms)
    return end

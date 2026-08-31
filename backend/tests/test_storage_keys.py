"""The key layout, against the one place it is specified.

`docs/03-backend-architecture.md` §6.3 draws the bucket as a tree, and every
prefix in it carries an operational promise: `originals/` is never
auto-deleted, `exports/` expires after 30 days, `scratch/` after one. Those
promises get implemented as lifecycle rules on prefixes, so a key that drifts
from the drawing does not fail — it quietly acquires the wrong retention, and
the way you find out is a bill or a missing file.

Nothing tested these until M5 needed `export_key`, which is exactly the kind of
gap worth closing while adding to it.
"""

from app.services import storage

USER = "11111111-1111-4111-8111-111111111111"
ASSET = "22222222-2222-4222-8222-222222222222"
JOB = "33333333-3333-4333-8333-333333333333"


def test_the_keys_match_the_bucket_layout_in_the_architecture_doc() -> None:
    assert storage.original_key(USER, ASSET, "mp4") == f"originals/{USER}/{ASSET}/source.mp4"
    assert storage.proxy_key(USER, ASSET) == f"proxies/{USER}/{ASSET}/proxy.mp4"
    assert storage.thumbnail_key(USER, ASSET) == f"thumbs/{USER}/{ASSET}/thumb.jpg"
    assert storage.peaks_key(USER, ASSET) == f"peaks/{USER}/{ASSET}/peaks.json"
    assert storage.export_key(USER, JOB) == f"exports/{USER}/{JOB}/final.mp4"


def test_an_export_is_keyed_by_job_and_not_by_asset() -> None:
    """Exporting the same project twice must give two files.

    Every other prefix is keyed by the asset, because there the file *is* the
    asset. An export is one render of one timeline at one moment, and an
    asset-keyed path would have the second export silently overwrite the first
    — a user losing a render they already downloaded a link to.
    """
    first = storage.export_key(USER, "job-a")
    second = storage.export_key(USER, "job-b")
    assert first != second
    assert first.startswith(f"exports/{USER}/")


def test_every_key_is_scoped_to_its_owner() -> None:
    """The prefix is the only thing separating two accounts' objects in a
    single bucket, so a key that forgets the user id is a cross-account read
    waiting for someone to guess a UUID."""
    keys = (
        storage.original_key(USER, ASSET, "mp4"),
        storage.proxy_key(USER, ASSET),
        storage.thumbnail_key(USER, ASSET),
        storage.peaks_key(USER, ASSET),
        storage.export_key(USER, JOB),
    )
    for key in keys:
        assert f"/{USER}/" in key, key


def test_the_extension_is_normalised_either_way() -> None:
    """Callers pass `mp4` from a MIME lookup and `.mp4` from a filename, and
    `originals/…/source..mp4` is a real key that a signed URL would happily
    serve and nobody would ever find again."""
    assert storage.original_key(USER, ASSET, ".mov").endswith("/source.mov")
    assert storage.original_key(USER, ASSET, "mov").endswith("/source.mov")
    assert storage.export_key(USER, JOB, ".webm").endswith("/final.webm")
    # No extension at all leaves the name bare rather than trailing a dot.
    assert storage.original_key(USER, ASSET, "").endswith("/source")

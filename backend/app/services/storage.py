"""Object storage.

Files never pass through this API. The browser is handed a presigned URL and
uploads straight to S3, because bandwidth through a request handler is wasted
money and a needless failure mode (docs/03-backend-architecture.md §6.1).

MinIO locally, S3 in production, same protocol either way — which is what makes
an AWS account unnecessary to start building.

**Everything is private.** Reads go through a signed URL that expires in an
hour, so a link that leaks stops working on its own (§6.3). Nothing in this
module ever makes an object public.
"""

import contextlib
import functools
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from app.config import settings

# --------------------------------------------------------------------------
# Key layout — docs/03-backend-architecture.md §6.3.
# One bucket, prefixed by purpose so lifecycle rules can differ: `originals/`
# is never auto-deleted, everything else is regenerable.
# --------------------------------------------------------------------------


def original_key(user_id: str, asset_id: str, extension: str) -> str:
    return f"originals/{user_id}/{asset_id}/source{_dot(extension)}"


def proxy_key(user_id: str, asset_id: str) -> str:
    return f"proxies/{user_id}/{asset_id}/proxy.mp4"


def thumbnail_key(user_id: str, asset_id: str) -> str:
    return f"thumbs/{user_id}/{asset_id}/thumb.jpg"


def peaks_key(user_id: str, asset_id: str) -> str:
    return f"peaks/{user_id}/{asset_id}/peaks.json"


def _dot(extension: str) -> str:
    if not extension:
        return ""
    return extension if extension.startswith(".") else f".{extension}"


# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PresignedUpload:
    url: str
    method: str
    headers: dict[str, str]
    expires_at: datetime


@dataclass(frozen=True)
class MultipartUpload:
    upload_id: str
    part_size_bytes: int
    parts: list[dict[str, Any]]


@functools.lru_cache(maxsize=1)
def client() -> Any:
    """One boto3 client for the process.

    Path-style addressing against MinIO: it does not serve virtual-host style
    on `localhost`, and a signature computed for the wrong style comes back as
    a 403 that reads exactly like bad credentials.
    """
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        region_name=settings.s3_region,
        config=BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"},
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def presign_put(key: str, content_type: str) -> PresignedUpload:
    """A 15-minute window to upload one object.

    `Content-Type` is part of the signature, so the browser must send exactly
    the value returned in `headers` — a different one is a 403. That is why the
    contract returns the headers rather than leaving the client to guess.
    """
    ttl = settings.upload_url_ttl_seconds
    url = client().generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.s3_bucket, "Key": key, "ContentType": content_type},
        ExpiresIn=ttl,
    )
    return PresignedUpload(
        url=url,
        method="PUT",
        headers={"Content-Type": content_type},
        expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
    )


def presign_get(key: str | None) -> str | None:
    """A one-hour read link. `None` in, `None` out, so callers can pass an
    optional key straight through."""
    if not key:
        return None
    return client().generate_presigned_url(  # type: ignore[no-any-return]
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=settings.download_url_ttl_seconds,
    )


def start_multipart(key: str, content_type: str, size_bytes: int) -> MultipartUpload:
    """Split an upload over 100 MB into parts.

    A single PUT of a 2 GB file over a phone connection is one timeout away
    from starting again from zero; a failed part is a retry of 8 MB.

    **Creating the upload and signing its parts are separate on purpose** —
    `presign_parts` below. An upload id lives until the upload is completed or
    aborted, but the URLs signed against it expire in fifteen minutes, so a
    client that comes back for fresh URLs must get them for the upload it
    already started rather than a new one. Calling this twice for the same
    asset leaves the first upload's parts in the bucket with nothing pointing
    at them, billed until something aborts them.
    """
    created = client().create_multipart_upload(
        Bucket=settings.s3_bucket, Key=key, ContentType=content_type
    )
    return presign_parts(key, str(created["UploadId"]), size_bytes)


def presign_parts(key: str, upload_id: str, size_bytes: int) -> MultipartUpload:
    """Fresh part URLs for an upload that already exists.

    The part size is derived from `size_bytes` rather than stored, so this has
    to be called with the same size the upload was created for — otherwise the
    part boundaries move and the parts already uploaded no longer line up.
    `size_bytes` is on the asset row from the reservation, which is where the
    caller gets it.
    """
    part_size = _part_size_for(size_bytes)
    count = max(1, -(-size_bytes // part_size))  # ceiling division
    parts = [
        {
            "partNumber": number,
            "url": client().generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": settings.s3_bucket,
                    "Key": key,
                    "UploadId": upload_id,
                    "PartNumber": number,
                },
                ExpiresIn=settings.upload_url_ttl_seconds,
            ),
        }
        for number in range(1, count + 1)
    ]
    return MultipartUpload(upload_id=upload_id, part_size_bytes=part_size, parts=parts)


def _part_size_for(size_bytes: int) -> int:
    """8 MB parts, raised if the file would need more than 10 000 of them.

    S3's hard limits: at most 10 000 parts, and every part except the last at
    least 5 MB. A 2 GB file is 250 parts at 8 MB, so the ceiling only matters
    if the size limit is ever raised — which is exactly when nobody would
    remember this constraint.
    """
    minimum = 8 * 1024 * 1024
    needed = -(-size_bytes // 10_000)
    return max(minimum, ((needed + minimum - 1) // minimum) * minimum)


def complete_multipart(key: str, upload_id: str, parts: list[dict[str, Any]]) -> None:
    client().complete_multipart_upload(
        Bucket=settings.s3_bucket,
        Key=key,
        UploadId=upload_id,
        MultipartUpload={
            "Parts": [
                {"PartNumber": int(p["partNumber"]), "ETag": str(p["etag"])}
                for p in sorted(parts, key=lambda p: int(p["partNumber"]))
            ]
        },
    )


def abort_multipart(key: str, upload_id: str) -> None:
    # Aborting is cleanup. If it fails, the bucket's lifecycle rule collects the
    # orphaned parts; raising here would fail a request that already went wrong
    # for another reason and bury the real cause.
    with contextlib.suppress(ClientError):
        client().abort_multipart_upload(Bucket=settings.s3_bucket, Key=key, UploadId=upload_id)


@dataclass(frozen=True)
class ObjectInfo:
    size_bytes: int
    etag: str
    content_type: str | None


def head(key: str) -> ObjectInfo | None:
    """What is actually in the bucket, or `None` if nothing is.

    The client claiming it finished an upload is not evidence that it did.
    """
    try:
        response = client().head_object(Bucket=settings.s3_bucket, Key=key)
    except ClientError:
        return None
    return ObjectInfo(
        size_bytes=int(response["ContentLength"]),
        etag=str(response.get("ETag", "")).strip('"'),
        content_type=response.get("ContentType"),
    )


def download(key: str, destination: str) -> None:
    client().download_file(settings.s3_bucket, key, destination)


def upload(path: str, key: str, content_type: str) -> None:
    client().upload_file(path, settings.s3_bucket, key, ExtraArgs={"ContentType": content_type})


def put_bytes(key: str, payload: bytes, content_type: str) -> None:
    client().put_object(Bucket=settings.s3_bucket, Key=key, Body=payload, ContentType=content_type)


def delete_prefix(prefix: str) -> int:
    """Remove every object under a prefix. Returns how many were deleted."""
    paginator = client().get_paginator("list_objects_v2")
    removed = 0
    for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix=prefix):
        keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if not keys:
            continue
        client().delete_objects(Bucket=settings.s3_bucket, Delete={"Objects": keys})
        removed += len(keys)
    return removed


#: There is deliberately no `public_url()` helper. Every read goes through
#: `presign_get`, because the bucket is private (docs/03 §6.3) and a function
#: that builds an unsigned link is one import away from being handed to a
#: client. `CDN_BASE_URL` is declared in .env.example for the CloudFront
#: signing that replaces `presign_get` in production; nothing reads it yet, and
#: note that it embeds a bucket name which must be kept in step with S3_BUCKET.

"""Part B of docs/07-security.md, against the local staging stack.

Runs in the `checks` container, on the `public` network only — where somebody
on the internet would be. It knows the hostnames everyone knows, holds the
proxy's root certificate the way a browser holds a public CA's, and uses the
storage identity's keys only for the checks that are *about* that identity.

Every check prints PASS or FAIL with the evidence, and the exit status is the
number of failures. `check.sh` runs this and then the checks that need to be
inside the stack (worker egress, Redis authentication, the proxy's logs).
"""

import asyncio
import json
import os
import random
import re
import socket
import ssl
import sys
import time
import traceback
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import boto3
import httpx
import websockets
from botocore.config import Config
from botocore.exceptions import ClientError

APP = "https://app.zipzop.test"
API = "https://api.zipzop.test"
MEDIA = "https://media.zipzop.test"
BUCKET = "zipzop-media"
CA = os.environ["CA_BUNDLE"]

RESULTS: list[tuple[str, bool, str]] = []
CHECKS: list[Callable[[], str]] = []
STATE: dict[str, Any] = {}


def check(fn: Callable[[], str]) -> Callable[[], str]:
    CHECKS.append(fn)
    return fn


def client(**kwargs: Any) -> httpx.Client:
    return httpx.Client(verify=CA, timeout=30, **kwargs)


def register(http: httpx.Client) -> tuple[dict[str, str], dict[str, Any], httpx.Response]:
    email = f"staging-{uuid.uuid4().hex[:10]}@example.com"
    response = http.post(
        f"{API}/v1/auth/register", json={"email": email, "password": "staging-pass-1234"}
    )
    assert response.status_code == 201, f"register: {response.status_code} {response.text[:200]}"
    body = response.json()
    return {"Authorization": f"Bearer {body['accessToken']}"}, body, response


def storage(**kwargs: Any) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=MEDIA,
        region_name="eu-west-1",
        aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
        verify=CA,
        config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
        **kwargs,
    )


# --------------------------------------------------------------------------
# The edge: TLS, HSTS, headers, what is and is not reachable
# --------------------------------------------------------------------------


@check
def tls_everywhere_and_no_plaintext() -> str:
    """§5.2: TLS on every hostname; plain http only ever redirects."""
    evidence = []
    with client() as http:
        for origin in (APP, API, MEDIA):
            host = urlsplit(origin).netloc
            response = http.get(f"http://{host}/", follow_redirects=False)
            assert response.status_code in (301, 308), (
                f"{host} answered http with {response.status_code}"
            )
            assert response.headers["location"].startswith("https://"), response.headers["location"]
            evidence.append(f"{host}: http→{response.status_code}")
            # The certificate verifies against the proxy's root, by name.
            context = ssl.create_default_context(cafile=CA)
            with (
                socket.create_connection((host, 443), timeout=10) as raw,
                context.wrap_socket(raw, server_hostname=host) as tls,
            ):
                evidence.append(f"{tls.version()}")
    return ", ".join(evidence)


@check
def hsts_on_every_origin() -> str:
    found = []
    with client() as http:
        for url in (f"{APP}/", f"{API}/health/live", f"{MEDIA}/minio/health/live"):
            value = http.get(url).headers.get("strict-transport-security", "")
            assert "max-age=31536000" in value, f"{url}: {value!r}"
            found.append(urlsplit(url).netloc)
    return "max-age=31536000 on " + ", ".join(found)


@check
def app_headers_and_nonce_csp() -> str:
    """M7-10 and M7-20, through the real edge."""
    with client() as http:
        first = http.get(f"{APP}/pricing")
        second = http.get(f"{APP}/pricing")
    assert first.status_code == 200, first.status_code
    csp = first.headers["content-security-policy"]
    script_src = next(d for d in csp.split(";") if d.strip().startswith("script-src"))
    assert "'nonce-" in script_src and "'strict-dynamic'" in script_src, script_src
    assert "'unsafe-inline'" not in script_src and "'unsafe-eval'" not in script_src, script_src
    assert "frame-ancestors 'none'" in csp
    assert first.headers.get("x-frame-options") == "DENY"
    assert first.headers.get("x-content-type-options") == "nosniff"
    # ZAP's two findings on the app, closed: no framework banner, and a
    # Permissions-Policy that refuses what the editor never asks for.
    assert "x-powered-by" not in first.headers, first.headers.get("x-powered-by")
    assert "camera=()" in first.headers.get("permissions-policy", ""), "no Permissions-Policy"
    assert "https://media.zipzop.test" in csp, "the CSP must name the media origin"
    assert csp != second.headers["content-security-policy"], "the nonce did not change"
    tags = re.findall(r"<script\b[^>]*>", first.text)
    scripts = len(tags)
    with_nonce = sum(1 for tag in tags if re.search(r'\snonce="[^"]+"', tag))
    assert scripts and scripts == with_nonce, f"{with_nonce}/{scripts} scripts carry the nonce"
    return f"{script_src.strip()} · {scripts}/{scripts} scripts nonced · fresh nonce per response"


@check
def api_documentation_is_not_served() -> str:
    """M7-17, with ENVIRONMENT=production — and every API answer, an error
    included, is `nosniff` and `no-store` (ZAP's two findings on the API)."""
    with client() as http:
        responses = {
            path: http.get(f"{API}{path}") for path in ("/docs", "/redoc", "/openapi.json")
        }
        live = http.get(f"{API}/health/live")
    codes = {path: response.status_code for path, response in responses.items()}
    assert all(code == 404 for code in codes.values()), codes
    for response in (*responses.values(), live):
        assert response.headers.get("x-content-type-options") == "nosniff", response.url
        assert response.headers.get("cache-control") == "no-store", response.url
    return f"{codes}; every answer nosniff + no-store"


@check
def data_network_is_unreachable_from_outside() -> str:
    """Postgres, Redis, storage and the API itself answer nobody but the edge."""
    outcomes = []
    for host, port in (
        ("postgres", 5432),
        ("redis", 6379),
        ("minio", 9000),
        ("api", 8000),
        ("worker", 8000),
    ):
        try:
            with socket.create_connection((host, port), timeout=3):
                raise AssertionError(f"{host}:{port} accepted a connection from the public network")
        except (socket.gaierror, TimeoutError, ConnectionRefusedError, OSError) as exc:
            outcomes.append(f"{host}:{port} {type(exc).__name__}")
    return ", ".join(outcomes)


# --------------------------------------------------------------------------
# Accounts, cookies, the socket
# --------------------------------------------------------------------------


@check
def refresh_cookie_is_locked_down() -> str:
    with client() as http:
        headers, body, response = register(http)
        STATE["a"] = (headers, body)
    cookie = response.headers["set-cookie"]
    for attribute in ("HttpOnly", "Secure", "SameSite=lax", "Path=/v1/auth"):
        assert attribute.lower() in cookie.lower(), f"{attribute} missing: {cookie}"
    assert "refreshToken" not in response.text
    return "HttpOnly; Secure; SameSite=Lax; Path=/v1/auth — and not in the body"


@check
def websocket_opens_with_a_ticket_and_only_once() -> str:
    """M7-19 through the edge: a ticket opens one socket; the access token
    opens none. The ticket value is printed for `check.sh`, which looks for it
    in the proxy's access log."""
    headers, _ = STATE["a"]
    token = headers["Authorization"].removeprefix("Bearer ")
    with client() as http:
        ticket = http.post(f"{API}/v1/ws/ticket", headers=headers).json()["ticket"]
    context = ssl.create_default_context(cafile=CA)

    async def run() -> list[str]:
        seen = []
        async with websockets.connect(
            f"wss://api.zipzop.test/v1/ws?ticket={ticket}", ssl=context
        ) as ws:
            seen.append(f"open:{ws.state.name}")
            for url in (
                f"wss://api.zipzop.test/v1/ws?ticket={ticket}",
                f"wss://api.zipzop.test/v1/ws?token={token}",
            ):
                async with websockets.connect(url, ssl=context) as replay:
                    try:
                        await asyncio.wait_for(replay.recv(), timeout=10)
                    except websockets.ConnectionClosed as closed:
                        seen.append(f"refused:{closed.rcvd.code if closed.rcvd else None}")
        return seen

    seen = asyncio.run(run())
    assert seen == ["open:OPEN", "refused:1008", "refused:1008"], seen
    print(f"TICKET_FOR_LOG_CHECK={ticket}")
    return ", ".join(seen)


# --------------------------------------------------------------------------
# Storage: the real presigned flow, through TLS, against a private bucket
# --------------------------------------------------------------------------


@check
def presigned_upload_through_the_edge() -> str:
    headers, _ = STATE["a"]
    payload = os.urandom(2048)
    with client() as http:
        reserved = http.post(
            f"{API}/v1/media/uploads",
            headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
            json={"filename": "clip.mp4", "contentType": "video/mp4", "sizeBytes": len(payload)},
        )
        assert reserved.status_code == 201, f"{reserved.status_code} {reserved.text[:300]}"
        body = reserved.json()
        url = body["uploadUrl"]
        assert url.startswith(f"{MEDIA}/"), url
        # The headers the API says to send, less the length httpx sets itself.
        sent = {k: v for k, v in body["headers"].items() if k.lower() != "content-length"}
        put = http.put(url, content=payload, headers=sent)
        assert put.status_code == 200, f"PUT {put.status_code} {put.text[:300]}"
        # M7-04 through the edge: a byte more than was reserved is refused.
        stuffed = http.put(url, content=payload + b"x", headers=sent)
        assert stuffed.status_code == 403, f"stuffed PUT {stuffed.status_code}"
    STATE["upload_url"] = url
    STATE["asset_id"] = body["assetId"]
    STATE["object_path"] = urlsplit(url).path
    return f"PUT 200 to {urlsplit(url).netloc}; one byte over the reservation 403"


@check
def a_real_video_is_ingested_end_to_end() -> str:
    """The whole pipeline through the edge: upload, complete, the worker reads
    the original from storage over TLS, writes the proxy back, and the browser
    can fetch it by its presigned URL. The first run of the bomb measurement is
    what showed this needed its own check: a PUT had passed, and the
    *completion* 500'd on a certificate the API could not read."""
    import subprocess
    import tempfile

    headers, _ = STATE["a"]
    with tempfile.TemporaryDirectory() as work:
        clip = os.path.join(work, "clip.mp4")
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc=d=2:s=320x240:r=24",
                "-f",
                "lavfi",
                "-i",
                "sine=d=2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                clip,
            ],
            check=True,
        )
        with open(clip, "rb") as fh:
            payload = fh.read()
    with client() as http:
        reserved = http.post(
            f"{API}/v1/media/uploads",
            headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
            json={"filename": "clip.mp4", "contentType": "video/mp4", "sizeBytes": len(payload)},
        ).json()
        sent = {k: v for k, v in reserved["headers"].items() if k.lower() != "content-length"}
        assert http.put(reserved["uploadUrl"], content=payload, headers=sent).status_code == 200
        done = http.post(
            f"{API}/v1/media/{reserved['assetId']}/complete", headers=headers, json={"etag": None}
        )
        assert done.status_code in (200, 202), f"complete: {done.status_code} {done.text[:200]}"
        started = time.monotonic()
        while time.monotonic() - started < 180:
            asset = http.get(f"{API}/v1/media/{reserved['assetId']}", headers=headers).json()
            if asset["status"] in ("ready", "failed"):
                break
            time.sleep(2)
        assert asset["status"] == "ready", f"{asset['status']}: {asset.get('failureReason')}"
        proxy = http.get(asset["proxyUrl"])
        thumb = http.get(asset["thumbnailUrl"])
    assert proxy.status_code == 200 and proxy.content[4:8] == b"ftyp", proxy.status_code
    assert thumb.status_code == 200, thumb.status_code
    return (
        f"ready in {time.monotonic() - started:.0f}s; proxy {len(proxy.content)} bytes and "
        "thumbnail fetched by presigned URL through the edge"
    )


@check
def bucket_is_private() -> str:
    path = STATE["object_path"]
    with client() as http:
        codes = {
            "anonymous GET of the object": http.get(f"{MEDIA}{path}").status_code,
            "anonymous listing": http.get(f"{MEDIA}/{BUCKET}?list-type=2").status_code,
            "anonymous PUT": http.put(
                f"{MEDIA}/{BUCKET}/originals/anon.mp4", content=b"x"
            ).status_code,
            "listing all buckets": http.get(f"{MEDIA}/").status_code,
        }
    assert all(code == 403 for code in codes.values()), codes
    return str(codes)


@check
def presigned_urls_cannot_be_bent() -> str:
    url = STATE["upload_url"]
    parts = urlsplit(url)
    other_key = parts.path.replace("originals/", "originals/x", 1)
    tampered = urlunsplit(parts._replace(path=other_key))

    s3 = storage()
    expired = s3.generate_presigned_url(
        "put_object", Params={"Bucket": BUCKET, "Key": "originals/expiry-check.bin"}, ExpiresIn=1
    )
    time.sleep(2)
    with client() as http:
        moved = http.put(tampered, content=b"x" * 2048, headers={"Content-Type": "video/mp4"})
        late = http.put(expired, content=b"x")
    assert moved.status_code == 403, f"another key: {moved.status_code}"
    assert late.status_code == 403 and "expired" in late.text.lower(), (
        f"expired: {late.status_code}"
    )
    return "URL for one key refused for another: 403 · expired URL: 403 Request has expired"


@check
def cors_names_the_app_and_exposes_etag() -> str:
    """§5.2/§6.3: the multipart upload has to read `ETag`; nobody else's page
    may read anything."""
    url = STATE["upload_url"]
    with client() as http:
        own = http.request(
            "OPTIONS",
            url,
            headers={
                "Origin": APP,
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        actual = http.get(url.split("?")[0], headers={"Origin": APP})
        foreign = http.request(
            "OPTIONS",
            url,
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "PUT"},
        )
    assert own.headers.get("access-control-allow-origin") == APP, dict(own.headers)
    exposed = actual.headers.get("access-control-expose-headers", "")
    assert "etag" in exposed.lower(), f"ETag not exposed: {exposed!r}"
    assert foreign.headers.get("access-control-allow-origin") in (None, ""), dict(foreign.headers)
    return "app origin allowed, ETag exposed; a foreign origin gets no Access-Control-Allow-Origin"


@check
def storage_identity_is_scoped() -> str:
    """The IAM stand-in (§5.3): objects in one bucket, nothing else."""
    s3 = storage()
    key = f"originals/scope-check-{uuid.uuid4().hex[:8]}.bin"
    s3.put_object(Bucket=BUCKET, Key=key, Body=b"ok")
    assert s3.get_object(Bucket=BUCKET, Key=key)["Body"].read() == b"ok"
    s3.delete_object(Bucket=BUCKET, Key=key)

    # MinIO answers ListBuckets with the buckets an identity may use rather
    # than an error, so the proof is the other bucket `minio-init` made: this
    # identity can neither see it nor touch it.
    visible = [b["Name"] for b in s3.list_buckets()["Buckets"]]
    assert visible == [BUCKET], f"the application identity sees {visible}"

    refused = {}
    attempts = {
        "read another bucket": lambda: s3.get_object(Bucket="zipzop-other", Key="canary.txt"),
        "write another bucket": lambda: s3.put_object(Bucket="zipzop-other", Key="x", Body=b"x"),
        "create a bucket": lambda: s3.create_bucket(Bucket="zipzop-third"),
        "delete the bucket": lambda: s3.delete_bucket(Bucket=BUCKET),
        "make the bucket public": lambda: s3.put_bucket_policy(
            Bucket=BUCKET,
            Policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": "*",
                            "Action": "s3:GetObject",
                            "Resource": f"arn:aws:s3:::{BUCKET}/*",
                        }
                    ],
                }
            ),
        ),
    }
    for name, attempt in attempts.items():
        try:
            attempt()
        except ClientError as exc:
            refused[name] = exc.response["Error"]["Code"]
        else:
            raise AssertionError(f"the application identity could {name}")
    assert all(code == "AccessDenied" for code in refused.values()), refused
    return f"own objects: read/write/delete · sees only {visible} · " + ", ".join(
        f"{k}: {v}" for k, v in refused.items()
    )


@check
def one_account_cannot_reach_anothers_upload() -> str:
    with client() as http:
        other, _, _ = register(http)
        asset = STATE["asset_id"]
        read = http.get(f"{API}/v1/media/{asset}", headers=other).status_code
        complete = http.post(
            f"{API}/v1/media/{asset}/complete", headers=other, json={"etag": None}
        ).status_code
        delete = http.delete(f"{API}/v1/media/{asset}", headers=other).status_code
    assert (read, complete, delete) == (404, 404, 404), (read, complete, delete)
    return "read, complete, delete: 404, 404, 404"


# --------------------------------------------------------------------------
# Rate limits behind the real proxy, and what an abusive account costs
# --------------------------------------------------------------------------


@check
def forged_forwarded_for_buys_nothing() -> str:
    """M7-03 behind the edge, with TRUSTED_PROXY_HOPS=1: every request names a
    different made-up client, and the limiter still sees one."""
    with client() as http:
        for attempt in range(1, 46):
            forged = f"203.0.113.{random.randint(1, 254)}, 198.51.100.{random.randint(1, 254)}"
            response = http.post(
                f"{API}/v1/auth/login",
                json={"email": "nobody@example.com", "password": "wrong-password"},
                headers={"X-Forwarded-For": forged},
            )
            if response.status_code == 429:
                return f"429 after {attempt} logins, each with a different forged X-Forwarded-For"
    raise AssertionError("45 logins with forged X-Forwarded-For were never throttled")


#: `REGISTER_LIMIT_PER_HOUR` in the API's settings.
REGISTER_LIMIT_PER_HOUR = 10


@check
def one_address_cannot_farm_accounts() -> str:
    """§6.10's measurement. Before M7 closed, one address opened 20 accounts a
    minute here — 1,200 an hour, each with 300 credits and 5 GB, all on one
    disposable domain. The hourly signup limit is what stops that; this counts
    what still gets through. `check.sh` resets the counters first, and this
    run has already opened accounts of its own, which count."""
    time.sleep(61)  # past the 20-a-minute auth window, so only the hourly one can answer
    opened = 0
    with client() as http:
        for _ in range(REGISTER_LIMIT_PER_HOUR + 5):
            email = f"farm-{uuid.uuid4().hex[:10]}@mailinator.com"
            response = http.post(
                f"{API}/v1/auth/register", json={"email": email, "password": "staging-pass-1234"}
            )
            if response.status_code == 429:
                break
            assert response.status_code == 201, response.status_code
            opened += 1
    assert response.status_code == 429, "never refused"
    retry_after = int(response.headers["retry-after"])
    assert retry_after > 60, f"refused by the minute window ({retry_after}s), not the hourly one"
    assert opened < REGISTER_LIMIT_PER_HOUR, opened
    return (
        f"{opened} more accounts, then 429 for {retry_after}s — at most "
        f"{REGISTER_LIMIT_PER_HOUR} an hour from one address (was 1,200)"
    )


def main() -> int:
    for fn in CHECKS:
        try:
            evidence = fn()
            RESULTS.append((fn.__name__, True, evidence))
            print(f"PASS  {fn.__name__}: {evidence}", flush=True)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            RESULTS.append((fn.__name__, False, detail))
            print(f"FAIL  {fn.__name__}: {detail}", flush=True)
            traceback.print_exc(limit=2)
    failed = [name for name, ok, _ in RESULTS if not ok]
    print(
        f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed"
        + (f" — failed: {failed}" if failed else "")
    )
    return len(failed)


if __name__ == "__main__":
    sys.exit(main())

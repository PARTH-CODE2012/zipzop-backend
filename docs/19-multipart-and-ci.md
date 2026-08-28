# Multipart uploads, and the MinIO the CI never started

*28 August 2026 · a second outside audit, checked point by point before anything was changed*

| | |
|---|---|
| **Trigger** | A code audit delivered as screenshots, with two items marked for immediate fixing |
| **Method** | Every claim tested against the running code and the running containers before any change — the same method as [`16`](16-pipeline-reliability-notes.md), for the same reason |
| **Outcome** | One item confirmed and fixed, and larger than reported. One item **not a bug at all**, next to a real one the audit missed. Two correctly deferred, one wrong |
| **Verification** | 243 backend · 311 frontend · migration `0004` |

---

## 1. Where the audit landed

| # | Audit says | What the code says |
|---|---|---|
| 1 | `media.py` passes `body.etag` where `upload_id` belongs; multipart over 100 MB fails | ✅ **Confirmed**, and the reachable scope is different — §2 |
| 2 | `mc ready local` may not work; use `/minio/health/live` | ❌ **The stated failure does not occur.** `mc` is in the image, the check passes, the container is healthy. But the CI's MinIO never runs at all — §4 |
| 3 | Billing renewal not implemented | ✅ Accurate, and deliberate: `billing.py`'s own docstring says *"Real work arrives in M6."* Deferred, correctly |
| 4 | Storage cleanup not implemented | ✅ Accurate, and deliberate: retention policy is an open commercial decision, not a missing function. Deferred, correctly |
| 5 | Analysis features incomplete — a `not implemented yet` raise | ❌ **Wrong.** All three phase-1 tools are implemented. The raise is the exhaustive fallback for `export`, which M5 adds and which `PHASE_1_TOOLS` already rejects at the API boundary |
| — | Could not run the suite; `asyncpg` missing | Answered by running it: **243 passed, 2 skipped** ([`17`](17-first-real-test-run.md)) |

Points 3 and 4 are worth being precise about, because "not implemented" and
"missing" read the same in an audit and are not the same thing. Both functions
are stubs that say in their own docstrings which milestone they belong to. An
audit cannot tell a stub from an oversight; a plan can.

---

## 2. The multipart bug was real, and reachable by nobody

`storage.complete_multipart(key, upload_id, parts)` was being called as
`complete_multipart(asset.storage_key, str(body.etag or ""), parts)`. The
second argument is the upload id. It was being handed an ETag — or, since
`CompleteUploadRequest` has no upload id at all and clients sent `etag: null`,
an **empty string**.

Confirmed the way it should be, by putting the old line back and running the
new test against real MinIO:

```
botocore.errorfactory.NoSuchUpload: An error occurred (NoSuchUpload) when
calling the CompleteMultipartUpload operation: The specified multipart upload
does not exist.
```

**But the reported consequence — "files over 100 MB fail during upload" — was
not happening**, because nothing could reach the bug. `upload.ts` refused any
reservation that came back with a multipart plan:

```ts
throw new UploadError('MULTIPART_NOT_IMPLEMENTED',
  'Files over 100 MB are not supported by this build yet.')
```

So the true state was worse than the audit described and in a different place:
**large uploads were not broken at the completion step, they were not
implemented at all**, and the server half that was supposed to be finished
would have failed too on the day the browser half arrived. Fixing only what the
audit named would have produced a correct endpoint nobody could call.

This was already on the checklist, and had been for eleven days:

> ⚠️ *The 17 August plan limits invalidated the assumption this rested on… Pro
> is now 1 GB and Studio 5 GB, which means **a paying user cannot upload a file
> their own plan permits.***

Both halves ship here.

---

## 3. Where the upload id lives, and why not on the request

Two ways to give `POST /complete` the upload id: put it in the request body and
have the client echo it back, or keep it server-side. The audit's framing
implies the first — *"`CompleteUploadRequest` does not contain an `upload_id`
field"*.

**It is on the asset row instead** (migration `0004_multipart_upload_id`),
because the server started the upload and already knows which one it is.
Echoing it through the client means accepting a client-supplied identifier for
a server-side resource and then having to validate that it belongs to this
asset — a check whose only purpose is to undo the decision to ask for it. It
also changes the API contract, and so `openapi.json` and the frontend's
generated types, for information neither of them needs.

`openapi.json` is byte-identical after this pass. That is the argument.

Two things fell out of it that were not the point but are worth having:

**A replayed reservation no longer starts a second upload.** `_upload_response`
called `start_multipart` unconditionally, including on the idempotency-key
replay path — so every retry created a *new* S3 upload id and left the parts
already sent against the previous one orphaned in the bucket, billed, with
nothing able to complete or abort them. It now re-signs the parts of the upload
already on the row. Creating the upload and signing its parts are separate
functions now (`start_multipart`, `presign_parts`) for exactly this.

**A long upload can outlive its own URLs.** Part URLs are signed for fifteen
minutes; a 2 GB file on a domestic connection is not a fifteen-minute upload.
Because the upload id outlives the signatures, the client can ask for the
reservation again under the same idempotency key, get fresh URLs for the same
upload, and keep every part it has already sent. Without an id that survives
the replay this is not possible at all — the upload would have to start over,
which is the failure multipart exists to prevent.

**Still not done, deliberately:** `abort_multipart` has existed since M2 and has
never been callable, because nothing knew the id. It now could be, which means
`sweep_abandoned_uploads` could reclaim the parts of an upload nobody finished
instead of only marking the row `failed`. That is storage cleanup, which is
explicitly M5/M6 work, so it is named here rather than smuggled in.

---

## 4. The MinIO health check was fine. The CI's MinIO was not running.

The audit's reasoning: `mc` may not be in the MinIO server image, so the
container can stay `unhealthy` and `minio-init` never starts. Tested against
the container that was running at the time:

| | |
|---|---|
| `mc` in `minio/minio:latest` | present |
| `mc ready local` | `The cluster 'local' is ready` |
| Container state | `Up 13 hours (healthy)` |
| `minio-init` | started, on `condition: service_healthy` |

Every step of the described failure was false. The check was moved to
`/minio/health/live` anyway — `curl` is in the image and the endpoint answers —
but on a narrower argument than the audit's: `mc ready local` depends on the
client binary *and* its bundled `local` alias both surviving in an image pinned
to `latest`, while the HTTP endpoint depends only on the server answering,
which is the thing being checked. That is a preference, and it is described
here as one.

**The real problem was in the same file the audit pointed at, and is not a
health check.** GitHub Actions declared MinIO as a service container:

```yaml
minio:
  image: minio/minio:latest
  options: --health-cmd "mc ready local" …
```

A service container has no way to supply a command. The `services:` block takes
image, env, ports, volumes, options and credentials; `options` are passed to
`docker create` *before* the image name, which is not where a command goes. And
`minio/minio` needs one:

```console
$ docker run --rm minio/minio:latest
NAME:
  minio - High Performance Object Storage
USAGE:
  minio [FLAGS] COMMAND [ARGS...]
```

It prints its usage and exits. **The MinIO service in CI was never a running
server**, so every storage test in CI was running against nothing — and no
health command could have changed that, because there was nothing to be
healthy. Changing `mc ready local` to `/minio/health/live` there, as asked,
would have altered a line in a file and fixed nothing.

MinIO is now started as a step, with the reason written above it so nobody
moves it back into `services:`:

```bash
docker run -d --name minio -p 9000:9000 … minio/minio:latest server /data
```

Verified locally by running that exact command and polling that exact endpoint;
it is serving in about a second. **Not verified in GitHub Actions** — `gh` is
not installed on this machine and no run has been observed. What is established
is that the previous configuration cannot have worked, and that this one works
where it could be tested.

---

## 5. The tests that were missing

The backend suite had a multipart test. It reserved an upload, asserted the
plan came back with the right part count, and stopped — which is precisely the
shape of coverage that lets a broken completion sit in `main` behind 240 green
tests. The same gap as [`18`](18-media-asset-claim.md) and
[`17`](17-first-real-test-run.md): the test stopped one step before the step
that was wrong.

**Backend** (3 new, against real MinIO):

* `test_a_multipart_upload_completes_and_assembles_the_whole_object` — two real
  parts to two real presigned URLs, then `complete`, then the object is read
  back out of the bucket and compared byte for byte. It fails with
  `NoSuchUpload` against the old code; that was checked rather than assumed.
* `test_completing_with_parts_the_upload_never_started_is_refused`
* `test_a_replayed_reservation_reuses_the_same_multipart_upload` — asserts one
  open upload against the key, not two.

**Frontend** (7 new, in a file that did not exist): the parts are cut on the
server's boundaries and cover the whole file; a part carries no `Content-Type`;
progress never goes backwards and ends at the file's size; a 403 refreshes the
URLs **under the same idempotency key** and retries that part alone; a part
that keeps failing aborts the transfer rather than completing a hole; a missing
`ETag` — the CORS failure mode — is refused before completion.

`XMLHttpRequest` is stubbed rather than the transfer injected, because the
wiring between the module and XHR is what was wrong.

---

## 6. What is deliberately still open

**Parts go up one at a time.** Multipart is being used here for resilience, not
throughput: a dropped connection costs one part instead of the whole file.
Uploading several at once is faster and is a real follow-up, but it means
interleaving retries and URL refreshes across parts, and that is the half worth
getting right first.

**No end-to-end test drives a >100 MB upload through a browser.** The backend
half is proven against real MinIO and the frontend half against a fake
transport; the seam between them is held by the generated types. `make e2e`
remains unrun ([`17`](17-first-real-test-run.md) §5) and is still the gap.

**CORS on a real S3 bucket.** MinIO exposes `ETag` by default and AWS does not.
Written into the contract ([§3](05-api-contract.md)) because the symptom —
completion refused for a missing part identifier — says nothing about CORS.

---

## 7. What to tell the auditor

Their first point was real and their instinct to prioritise it was right. Two
corrections worth sending back:

* **The MinIO health check was not the problem, and the reasoning given for it
  was wrong** — `mc` is in the image and the check was passing. The CI's MinIO
  was not running at all, for an unrelated reason, and that is fixed.
* **Point 5 is not a defect.** The `not implemented yet` raise is an exhaustive
  fallback for a tool the API already rejects.

And the honest answer to the test-status note: the suite could not run because
`asyncpg` was not installed. It runs now — 243 passed, 2 skipped, on real
Postgres, real MinIO and real ffmpeg.

---

*Build note · 28 August 2026*

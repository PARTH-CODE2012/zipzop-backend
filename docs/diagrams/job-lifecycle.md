# Job Lifecycle

From invoking a tool to the result landing on the timeline, with every participant.
Referenced from [`../03-backend-architecture.md`](../03-backend-architecture.md) §5.

---

## 1. The happy path

Captions on a 10-minute clip. Note where money moves, and note that the user never waits.

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant API as FastAPI
    participant PG as PostgreSQL
    participant RD as Redis
    participant W as Analysis worker
    participant S3 as S3
    participant WS as WebSocket

    B->>API: POST /jobs/estimate
    API-->>B: 22 credits, ~45 s
    Note over B: Button reads<br/>"Add captions — 22 credits"

    B->>API: POST /jobs {tool: captions}<br/>Idempotency-Key: 3f9c…

    rect rgb(240, 246, 247)
        Note over API,PG: One transaction
        API->>PG: SELECT user FOR UPDATE
        API->>PG: INSERT credit_ledger (reserve, −22)
        API->>PG: UPDATE users.credit_balance
        API->>PG: INSERT jobs (status: queued)
    end

    API->>RD: enqueue on analysis queue
    API-->>B: 202 {id, status: queued, creditsReserved: 22}
    Note over B: Badge on the clip.<br/>User keeps editing.

    RD->>W: dispatch
    W->>PG: UPDATE status=running WHERE status=queued
    Note over W: Zero rows updated means<br/>another worker has it — stop

    W->>S3: GET proxy audio
    W->>W: transcribe, align words, detect emphasis

    loop at real checkpoints, not on a timer
        W->>RD: PUBLISH user:{id} job.progress
        RD->>WS: relay
        WS-->>B: {type: job.progress, progress: 62}
    end

    alt result under 256 KB
        W->>PG: UPDATE jobs SET result = {...}
    else larger — a 60-minute transcript
        W->>S3: PUT results/{job}/result.json
        W->>PG: UPDATE jobs SET result_key = …
    end

    W->>PG: UPDATE status=succeeded, credits_settled=22
    W->>RD: PUBLISH user:{id} job.succeeded
    RD->>WS: relay
    WS-->>B: {type: job.succeeded, jobId}

    B->>API: GET /jobs/{id}
    API-->>B: result, or resultUrl
    Note over B: One commit() →<br/>1 842 caption clips,<br/>ONE undo step
```

The reservation is not a separate step from creating the job — steps 3 to 6 are one transaction. A job cannot exist unpaid, and a reservation cannot exist without its job.

---

## 2. Failure and refund

The user pays nothing for our failures, and nobody has to ask.

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant PG as PostgreSQL
    participant W as Worker
    participant RD as Redis
    participant WS as WebSocket

    W->>W: work fails

    alt transient — S3 timeout, worker killed
        W->>PG: UPDATE status=queued, attempts+1
        Note over W: exponential backoff,<br/>up to 3 attempts.<br/>Reservation untouched.
    else permanent — no speech, unreadable media
        rect rgb(250, 236, 238)
            Note over W,PG: One transaction
            W->>PG: UPDATE status=failed, error_code=NO_SPEECH_DETECTED
            W->>PG: INSERT credit_ledger (refund, +22)
            W->>PG: UPDATE users.credit_balance
        end
        W->>RD: PUBLISH job.failed + credits.updated
        RD->>WS: relay
        WS-->>B: {errorCode: NO_SPEECH_DETECTED}
        WS-->>B: {type: credits.updated, balance: 340}
        Note over B: "We could not find any speech<br/>in this clip. Your 22 credits<br/>have been returned."
    end
```

The unique index on `credit_ledger (job_id, reason)` means a completion handler that runs twice cannot refund twice — the second insert fails on the constraint rather than on a code path someone remembered to write.

---

## 3. States

```mermaid
stateDiagram-v2
    [*] --> queued : POST /jobs — credits reserved

    queued --> running : worker claims it
    queued --> cancelled : user cancels — full refund

    running --> succeeded : result written — reservation settles
    running --> queued : transient failure — retry, max 3
    running --> failed : permanent failure — full refund
    running --> cancelled : render and inference only — full refund

    succeeded --> [*]
    failed --> [*]
    cancelled --> [*]

    note right of running
        Analysis jobs are not
        cancellable while running —
        they finish faster than the
        round trip to stop them
    end note
```

---

## 4. Upload and ingest

Before any tool can run, media has to arrive and be prepared. The file never passes through the API.

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant API as FastAPI
    participant S3 as S3
    participant PG as PostgreSQL
    participant W as Ingest worker

    B->>API: POST /media/uploads {filename, size, type}
    API->>PG: INSERT media_assets (pending_upload)
    API->>S3: presign PUT, 15 min
    API-->>B: {assetId, uploadUrl}

    B->>S3: PUT the file directly
    Note over B,S3: Progress from the browser's own<br/>upload events. No API involvement,<br/>no bandwidth cost to us.

    B->>API: POST /media/{id}/complete {etag}
    API->>S3: HEAD — verify it exists and the size matches
    API->>PG: UPDATE status=probing
    API-->>B: 202

    W->>S3: GET original
    W->>W: ffprobe — duration, dimensions, fps, codecs

    par four outputs
        W->>S3: PUT proxy.mp4 (480p H.264)
    and
        W->>S3: PUT thumb.jpg
    and
        W->>S3: PUT peaks.json (100 buckets/s)
    end

    W->>PG: UPDATE status=ready + probe data
    W-->>B: asset.ready via WebSocket
    Note over B: Clip is now placeable<br/>on the timeline
```

The asset becomes `ready` only when all four outputs exist. A clip cannot be placed on a timeline before then — `duration_ms` is unknown until probing, and without it the timeline cannot lay it out or price a job against it.

---

## 5. Export

The one job that reads the timeline document rather than an asset.

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant API as FastAPI
    participant PG as PostgreSQL
    participant W as Render worker
    participant S3 as S3

    B->>API: POST /jobs {tool: export, timelineVersion: 13}
    API->>PG: SELECT projects.version
    alt version is stale
        API-->>B: 409 VERSION_CONFLICT
        Note over B: Autosave first,<br/>then export again
    else current
        API->>PG: reserve credits, INSERT job
        API-->>B: 202
    end

    W->>PG: SELECT timeline JSONB
    W->>S3: GET originals — never the proxies

    W->>W: build one FFmpeg filter graph:<br/>trims → concat → transform + crop →<br/>LUT at strength → text overlays →<br/>transitions → audio mix with fades

    loop from FFmpeg -progress
        W->>B: job.progress against known total duration
    end

    W->>S3: PUT exports/{job}/final.mp4
    W->>PG: INSERT media_assets, set output_asset_id
    W->>PG: UPDATE status=succeeded
    W-->>B: job.succeeded
    B->>API: GET /jobs/{id}
    API-->>B: {downloadUrl, expiresAt: +30 days}
```

The renderer reads the **saved** timeline, not the client's request body. A client cannot ask for something the stored project does not contain — which is why a stale `timelineVersion` is rejected rather than rendered.

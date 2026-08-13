# System Overview

Component layout and what actually moves between the parts.
Referenced from [`../03-backend-architecture.md`](../03-backend-architecture.md) §3.

---

## 1. Components and traffic

Note the two edges that do **not** go through the API: the browser uploads to S3 directly, and plays media from the CDN directly. Only control traffic touches FastAPI. That is deliberate — media through a request handler is bandwidth we pay for twice and a failure mode we do not need.

```mermaid
flowchart TB
    subgraph client["Browser — the editor"]
        UI["Timeline, compositor,<br/>playback engine"]
    end

    subgraph edge["API tier — stateless, N replicas"]
        API["FastAPI<br/>validate · authorise · enqueue"]
        WS["WebSocket server"]
    end

    subgraph data["State"]
        PG[("PostgreSQL<br/>projects · timelines<br/>jobs · ledger · assets")]
        RD[("Redis<br/>broker · cache · pub/sub")]
    end

    subgraph store["Object storage"]
        S3[("S3<br/>originals · proxies<br/>derived · exports")]
        CDN["CloudFront<br/>signed URLs"]
    end

    subgraph workers["Celery workers — one service per queue"]
        ING["ingest<br/>probe · proxy · peaks"]
        ANA["analysis<br/>captions · trim · colour"]
        REN["render<br/>export via FFmpeg"]
        INF["inference — phase 2<br/>face map · lip sync · GPU"]
    end

    UI -->|"REST: projects, jobs, autosave"| API
    UI -.->|"PUT direct to storage<br/>presigned, 15 min"| S3
    UI -.->|"playback of proxies<br/>signed URL, 1 h"| CDN
    UI <-->|"job progress and results"| WS

    API -->|"read + write state"| PG
    API -->|"enqueue task"| RD
    API -->|"presign upload and playback URLs"| S3
    CDN --> S3

    RD -->|"dispatch"| ING
    RD -->|"dispatch"| ANA
    RD -->|"dispatch"| REN
    RD -->|"dispatch"| INF

    ING -->|"write proxy, thumb, peaks"| S3
    ANA -->|"read media"| S3
    REN -->|"read originals, write export"| S3
    INF -->|"read media, write derived"| S3

    ING -->|"status, probe data"| PG
    ANA -->|"result JSON"| PG
    REN -->|"output asset"| PG
    INF -->|"output asset"| PG

    ANA -.->|"publish user:{id}"| RD
    REN -.->|"publish user:{id}"| RD
    INF -.->|"publish user:{id}"| RD
    ING -.->|"publish user:{id}"| RD
    RD -.->|"subscribe and relay"| WS

    style INF stroke-dasharray: 5 5
    style UI fill:#0e5561,color:#fff
```

Dashed border on `inference`: phase 2 only. Nothing else in this picture changes when it arrives — that is the point of the design.

---

## 2. The two job families

The single most consequential difference in the backend. Same table, same lifecycle, same notification path — but what comes back, and what it costs, diverge completely.

```mermaid
flowchart LR
    START(["User invokes<br/>a tool"]) --> Q{"What does the<br/>tool produce?"}

    Q -->|"decisions"| A1["analysis queue<br/>CPU · seconds"]
    Q -->|"pixels or audio"| R1["inference or render queue<br/>GPU or heavy CPU · minutes"]

    A1 --> A2["result JSON<br/>cut points, transcript,<br/>LUT choice"]
    A2 --> A3["client applies it<br/>as one undoable edit"]
    A3 --> A4(["Timeline changed.<br/>Fully editable.<br/>Cost: cents"])

    R1 --> R2["new media asset<br/>derived_from = original"]
    R2 --> R3["clip's assetId swapped;<br/>original kept"]
    R3 --> R4(["Media replaced.<br/>Revertible, not editable.<br/>Cost: dollars"])

    style A4 fill:#0e5561,color:#fff
    style R4 fill:#92500a,color:#fff
```

**Phase 1 uses only the left path**, plus `export` on the render queue. This is why phase 1 needs no GPU hardware, and why a new tool should always be pushed down the left path if it possibly can be.

---

## 3. Where a frame comes from, editing versus exporting

Two different media paths for two different jobs. Confusing them is why editors feel slow.

```mermaid
flowchart TB
    ORIG[("Original upload<br/>4K HEVC, 890 MB")]

    ORIG -->|"ingest: transcode once"| PROXY[("Proxy<br/>480p H.264, 12 MB")]
    ORIG -->|"ingest"| PEAKS[("Waveform peaks<br/>JSON")]

    PROXY -->|"streamed to browser"| COMP["WebGL compositor<br/>crop · LUT · text · transition"]
    PEAKS --> TL["Timeline waveform"]
    COMP --> SCREEN(["What the user sees<br/>while editing — instant, free"])

    ORIG ==>|"export only"| FF["FFmpeg filter graph<br/>same LUT, same maths"]
    FF ==> OUT(["Final file<br/>1080p H.264"])

    style SCREEN fill:#0e5561,color:#fff
    style OUT fill:#0e5561,color:#fff
```

The original is touched exactly once during editing — at ingest — and once again at export. Everything in between runs on the proxy. **The LUT and its strength formula are shared between the compositor and FFmpeg**; if they drift, the user edits one picture and receives another.

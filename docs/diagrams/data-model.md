# Data Model

Entity relationships for the schema in [`../03-backend-architecture.md`](../03-backend-architecture.md) §4.

---

## 1. Phase 1

```mermaid
erDiagram
    users ||--o{ projects        : "owns"
    users ||--o{ media_assets    : "owns"
    users ||--o{ jobs            : "requests"
    users ||--o{ credit_ledger   : "is billed through"
    users ||--o{ refresh_tokens  : "authenticates with"
    users ||--|| subscriptions   : "always has one, free included"
    users ||--o{ payments        : "made"
    plans ||--o{ subscriptions   : "defines"
    subscriptions ||--o{ payments : "billed through"
    payments ||--o{ credit_ledger : "grants"

    projects ||--o{ project_assets : "references"
    media_assets ||--o{ project_assets : "is referenced by"

    projects ||--o{ jobs : "scopes"

    media_assets ||--o{ media_assets : "derived_from"
    jobs ||--o| media_assets : "produces"
    jobs ||--o{ credit_ledger : "reserves and refunds"

    users {
        uuid id PK
        citext email UK
        text hashed_password
        int plan_credits "monthly allowance — expires"
        int topup_credits "purchased — never expires"
        int facemap_seconds "GPU meter — expires"
        bigint storage_bytes_used
        timestamptz deleted_at
    }

    projects {
        uuid id PK
        uuid user_id FK
        text title
        text aspect_ratio
        int width
        int height
        jsonb timeline "the whole document"
        int version "optimistic concurrency"
        int duration_ms "derived on save"
    }

    project_assets {
        uuid project_id PK "also FK to projects"
        uuid asset_id PK "also FK to media_assets, ON DELETE RESTRICT"
    }

    media_assets {
        uuid id PK
        uuid user_id FK
        enum kind "video audio image"
        enum status "pending probing ready failed"
        text storage_key
        text proxy_key "480p for browser preview"
        text peaks_key
        int duration_ms
        int width
        int height
        uuid derived_from_asset_id FK "provenance chain"
        uuid derived_by_job_id FK
    }

    jobs {
        uuid id PK
        uuid user_id FK
        uuid project_id FK
        enum tool "captions smart_trim color_analysis export"
        enum family "analysis render inference"
        enum status "queued running succeeded failed cancelled"
        smallint progress
        smallint priority "queue band, copied from plan"
        jsonb input
        jsonb result "analysis output, small payloads"
        text result_key "large payloads go to S3"
        uuid output_asset_id FK "render output"
        int credits_reserved
        text idempotency_key
        text model_version
    }

    credit_ledger {
        bigserial id PK
        uuid user_id FK
        enum bucket "plan | topup | facemap"
        int delta "signed, never zero"
        enum reason "plan_grant plan_expiry topup_purchase reserve refund"
        uuid job_id FK
        uuid payment_id FK
        int balance_after "running total of THIS bucket"
    }

    refresh_tokens {
        uuid id PK
        uuid user_id FK
        text token_hash UK "SHA-256, never the token"
        uuid replaced_by FK "rotation chain"
        timestamptz revoked_at
    }

    plans {
        enum code PK "free pro business studio"
        int monthly_credits "granted each period, expires"
        int facemap_seconds "GPU allowance, expires"
        int fair_use_credits "ceiling for unlimited tiers"
        int max_export_height "720 1080 2160"
        enum watermark "forced none custom"
        smallint queue_priority
        int price_usd_cents
        int price_inr_paise
    }

    subscriptions {
        uuid id PK
        uuid user_id FK "one live per user, any provider"
        enum plan FK
        enum status "active past_due cancelled expired"
        enum provider "stripe | razorpay, NULL when free"
        text provider_subscription_id
        char currency "USD | INR"
        timestamptz current_period_end "drives the renewal sweep"
        bool cancel_at_period_end
    }

    payments {
        uuid id PK
        uuid user_id FK
        uuid subscription_id FK
        enum provider
        text provider_payment_id UK
        enum kind "subscription | topup"
        enum status "pending succeeded failed refunded"
        int amount_minor "cents or paise, never floats"
        char currency
        int credits_granted
    }

    provider_events {
        enum provider PK
        text event_id PK "duplicate delivery collides here"
        text event_type
        jsonb payload
        timestamptz processed_at
    }
```

### Three relationships that carry the design

**`media_assets.derived_from_asset_id` — the self-reference.**
When a job rewrites pixels, it writes a *new* asset pointing back at its source. The clip swaps which asset it points at; the original is never touched. This one column is the whole of "revert to original".

**`project_assets` — the side table.**
The timeline lives as JSONB, which Postgres cannot join against. This table is rebuilt from the document on every save so that "which projects use this file?" stays a plain query. `ON DELETE RESTRICT` on `asset_id` means a file in use cannot be deleted out from under a project.

**`credit_ledger.bucket` with a unique index on `(job_id, reason, bucket)`.**
Credits arrive by different routes and expire on different schedules, so every movement names which kind it moved. A job that costs more than the expiring allowance holds draws from two buckets and writes two reservation rows — which is why the bucket is part of the key. One reservation and at most one refund *per bucket* per job, enforced by the database: a worker whose completion handler runs twice cannot refund twice.

---

## 2. Phase 2 additions

No existing table changes shape. The facial data attaches to `users` and is consumed by `jobs`.

```mermaid
erDiagram
    users ||--o{ face_profiles   : "owns"
    users ||--o{ consent_records : "gave"
    consent_records ||--|| face_profiles : "authorises"
    face_profiles ||--o{ jobs : "used by face_map and lip_sync"

    face_profiles {
        uuid id PK
        uuid user_id FK
        uuid consent_record_id FK "NOT NULL — no profile without consent"
        text label
        vector embedding "512-d, pgvector"
        text mesh_key "the 3D model"
        jsonb reference_keys "the three source photographs"
        timestamptz deleted_at
    }

    consent_records {
        uuid id PK
        uuid user_id FK
        enum subject "self | third_party_asserted"
        text consent_text_version "exactly which wording was accepted"
        timestamptz accepted_at
        inet ip_address
        text user_agent
    }
```

`consent_record_id` is `NOT NULL` on purpose. A face profile cannot be created without recording what was agreed and when — retrofitting consent onto rows that already exist is the part of this work that goes wrong, and making it a constraint means it cannot be skipped under deadline pressure.

---

## 3. Deletion cascade

What happens when a user deletes an account, tracing the constraints above.

```mermaid
flowchart TB
    U["DELETE account"] --> SOFT["users.deleted_at set<br/>access revoked immediately"]
    SOFT --> GRACE{"30-day<br/>grace period"}
    GRACE -->|"restored"| BACK(["Account reactivated"])
    GRACE -->|"elapsed"| HARD["Hard delete"]

    SOFT --> CANCEL["subscription cancelled at the provider<br/>no further billing"]

    HARD --> P["projects — CASCADE"]
    HARD --> A["media_assets — CASCADE"]
    HARD --> F["face_profiles + consent_records — CASCADE"]
    HARD --> T["refresh_tokens — CASCADE"]

    A --> S3D["S3 objects deleted:<br/>originals, proxies, derived, exports"]
    F --> S3F["S3 objects deleted:<br/>meshes, reference photographs"]

    HARD --> L["credit_ledger + payments<br/>RETAINED, user_id nulled"]

    style L fill:#92500a,color:#fff
    style S3F fill:#0e5561,color:#fff
```

**Cancel the subscription at the provider on soft delete, not hard delete.** Waiting out the grace period means billing someone for a month after they asked to leave — the worst possible support conversation, and entirely avoidable.

The ledger and payment records are the one thing kept, anonymised, because financial records outlive accounts. Everything else goes, including every asset derived from a face profile — which is why `derived_from_asset_id` has to be walked, not just the directly-owned rows.

---

## 4. How credits are chosen when a job runs

Three balances, spent in a fixed order. The order is the whole point: it protects what the user paid for.

```mermaid
flowchart TB
    J(["Job needs 50 credits"]) --> FM{"Is it face<br/>mapping?"}

    FM -->|yes| FMB[("facemap seconds<br/>expires monthly")]
    FMB --> FMX{"meter<br/>empty?"}
    FMX -->|no| DONE2(["Spend the meter.<br/>General balance untouched."])
    FMX -->|yes| PLAN

    FM -->|no| PLAN[("plan credits<br/>EXPIRES at period end")]
    PLAN --> ENOUGH{"covers the<br/>whole cost?"}
    ENOUGH -->|yes| DONE1(["Reserve 50 from plan"])
    ENOUGH -->|no| TOPUP[("topup credits<br/>NEVER expire")]
    TOPUP --> SPLIT(["Reserve 20 from plan<br/>+ 30 from topup<br/>two ledger rows"])
    TOPUP --> SHORT{"still<br/>short?"}
    SHORT -->|yes| ERR(["402 INSUFFICIENT_CREDITS"])

    style DONE1 fill:#0e5561,color:#fff
    style DONE2 fill:#0e5561,color:#fff
    style SPLIT fill:#0e5561,color:#fff
    style ERR fill:#93142e,color:#fff
    style PLAN fill:#92500a,color:#fff
```

**Always the soonest to expire, first.** Drawing from `topup` before `plan` would let a user's monthly allowance quietly expire unused every month while the credits they paid extra for drained away. It looks like sharp practice, it generates refund requests, and it costs one line of code to avoid.

A refund returns credits to the buckets they came from, read back from the job's reservation rows. The one exception: if the billing period rolled over while the job was running, the refund goes to `topup` instead — refunding into a bucket that has already been swept and re-granted would credit a balance that no longer relates to the original charge.

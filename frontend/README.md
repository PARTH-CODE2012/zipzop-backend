# Frontend

Next.js + TypeScript. Architecture: [`../docs/04-frontend-architecture.md`](../docs/04-frontend-architecture.md).

```bash
corepack enable          # once, gives you pnpm
pnpm install
pnpm dev                 # http://localhost:3000
```

The backend must be running for anything beyond the landing page — `make up` from the repository root.

---

## The editor is client-only

Next.js earns its place on the marketing and pricing pages, which are server-rendered and want to be indexed. **The editor is not.** It mounts `<video>` elements, holds a WebGL context and keeps the whole timeline document in memory — none of which can be server-rendered.

`src/app/editor/[id]/page.tsx` is a thin server component that reads the route param and hands off to `editor-client.tsx`. Everything below that is `'use client'`. Do not try to make parts of the editor server components; you will spend a day discovering why not.

---

## Two kinds of state, kept apart

| | Server state | Editor state |
|---|---|---|
| What | Projects, assets, jobs, credits, plan | The open project's timeline, selection, playhead, zoom |
| Owner | **TanStack Query** (`src/app/providers.tsx`) | **Zustand + Immer** (`src/editor/state/`, from M3) |
| Changes | When the server says so | Constantly, at interaction rate |

**The timeline is never server state.** Putting it in a query cache means every clip drag triggers a refetch. It is fetched once when a project opens and pushed back by autosave.

---

## Types come from the contract

`src/lib/api/generated.ts` is generated from `../openapi.json` and is **not committed**:

```bash
pnpm generate:types      # or `make types` from the root, which regenerates openapi.json first
```

Never hand-write a request or response shape. A drift between the two sides should be a build error, not a bug found at integration. CI fails if `openapi.json` is stale.

---

## Conventions that will bite you otherwise

- **Times are integer milliseconds.** Never seconds, never floats — floating-point time in an editor produces drift the user sees as clips that will not butt together.
- **Spatial values are normalised 0–1** relative to the canvas, never pixels. This is what makes a 480p preview and a 1080p export agree. A caption at `y: 0.78` sits in the same place in both.
- **Money is integer minor units** with its currency beside it.
- **Drags use local component state and commit once on drop** — never one store write per pointer move.
- **The render loop never calls `setState`.** It reads the store imperatively and draws.
- Branch on `ApiError.code`, never on `message`.

---

## Layout

```
src/
  account/        sign in, register, the session
  app/            routes — App Router
  editor/
    state/        the timeline document and the editor store
    playback/     the compositor, lifted from the M1 spike
    timeline/     ruler, playhead, zoom, track, waveform canvas
  lib/api/        client, generated types, typed endpoints
  media/          upload with progress, media bin
  styles/         Tailwind v4 + theme tokens
e2e/              the end-to-end proof — see its README
```

`src/lib/api/` rather than `src/api/` as [`../docs/04-frontend-architecture.md`](../docs/04-frontend-architecture.md) §2 sketches: the client landed there in M0 and `package.json`'s `generate:types` and `.gitignore` both point at it. Not worth the churn; noted so the difference is deliberate rather than an oversight.

---

## The timeline has no visual identity yet

**Nothing in `editor/timeline/` expresses a design decision.** No palette,
typeface or visual states have been delivered by the project lead, so every
colour goes through a token declared in `src/styles/globals.css`, and every one
of those tokens is a neutral grey or a system font.

Applying the real charter means editing that one block. No component holds a
literal colour, so a reskin cannot break the behaviour — and the three states a
charter has to answer (**clip selected**, **clip dragging**, **track muted**)
are already named there, distinguishable today by lightness alone.

That is the minimum that makes the interface usable and the maximum that can be
justified without a designer. Do not add colour here; add it to the tokens.

---

## Playback

`editor/playback/` is the M1 spike's engine, moved rather than rewritten — its
45 tests came with it. `README.md` in that directory is the M1 write-up:
measurements, method, and **two bugs it must not reintroduce**. Read it before
touching the renderer or the clock.

`timeline-adapter.ts` is the join between the document and the engine. The
document names an `assetId`; the engine wants a URL and the asset's own
duration. The URL is signed and expires in an hour, so it is never stored
beside the clip — the adapter resolves it at playback time.

A clip whose asset is missing or still ingesting is **left out rather than
faked**. An empty `src` makes the element fail to load and the playhead hold
forever waiting for a frame that is never coming, which is M1's second fix
working correctly on bad input.

**`crossOrigin` must be set before `src`** on every playback element. Without
it the video loads and plays perfectly and then `texImage2D` throws
`SecurityError` — the picture is simply never drawn. Storage is on another
origin in every environment, so this is the normal case.

---

## Proving it

```bash
make e2e         # from the repository root
```

29 checks driving a real Chromium from an empty database to a clip playing
back. [`e2e/README.md`](e2e/README.md) covers what they check and the three
defects the browser found that a green unit suite, a strict type-check and a
clean lint all missed.

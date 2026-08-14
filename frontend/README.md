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
  app/            routes — App Router
  editor/         timeline state, playback engine, timeline UI, tools   (M2–M4)
  lib/api/        client, generated types, WebSocket
  styles/         Tailwind v4 + theme tokens
```

---

## Next up

The **compositor spike** (M1 in [`../PHASE1-TASKS.md`](../PHASE1-TASKS.md)) comes before any of this is built out. Two clips, a cut, a LUT, a text overlay — hardcoded, throwaway, no state management. It is the one part of the project with no library to fall back on, and if it resists, everything after it changes.

Do not build the editor layout out before that spike lands.

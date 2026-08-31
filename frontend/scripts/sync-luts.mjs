/**
 * Copy the `.cube` grades from the backend into `public/`, where the browser
 * can fetch them.
 *
 * **The backend owns them and the browser borrows them.** The export renderer
 * hands FFmpeg a path on disk and cannot fetch one over HTTP, and the container
 * is built from the `backend/` context, so anything living only under
 * `frontend/` is not in the image at all — `lut3d=file=…` would have nothing to
 * point at, and it would fail at the first graded export rather than at build
 * time (docs/15-m5-readiness.md §3). So `backend/app/assets/luts/` is the source
 * of truth and this copies from it.
 *
 * `public/luts/` is gitignored on purpose. Committing both copies means two
 * files that are supposed to be identical and a day when they are not; the copy
 * being generated makes that impossible rather than merely unlikely.
 *
 * Run from `dev`, `build` and `test` explicitly rather than through an npm
 * `pre` hook: pnpm does not run `pre`/`post` scripts unless
 * `enable-pre-post-scripts` is turned on, and a copy step that silently does not
 * happen is worse than no copy step — the browser would 404 on every grade and
 * the preview would simply not change colour.
 */

import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const SOURCE = join(here, '..', '..', 'backend', 'app', 'assets', 'luts')
const TARGET = join(here, '..', 'public', 'luts')

if (!existsSync(SOURCE)) {
  console.error(`sync-luts: ${SOURCE} does not exist — run: make luts`)
  process.exit(1)
}

const grades = readdirSync(SOURCE).filter((name) => name.endsWith('.cube'))
if (grades.length === 0) {
  console.error(`sync-luts: no .cube files in ${SOURCE} — run: make luts`)
  process.exit(1)
}

mkdirSync(TARGET, { recursive: true })

let copied = 0
for (const name of grades) {
  const from = join(SOURCE, name)
  const to = join(TARGET, name)
  // Skip a file that is already byte-for-byte current, so the dev server's
  // watcher does not reload the page for a change that is not one. Compared by
  // content and not by size: `make luts` rewrites all five every time, the
  // lines are fixed-width, and two different grades can easily come out the
  // same length — a size check would quietly stop copying an edited grade,
  // which is the failure this whole file exists to make impossible.
  if (existsSync(to) && readFileSync(to).equals(readFileSync(from))) continue
  copyFileSync(from, to)
  copied += 1
}

console.log(
  copied === 0
    ? `sync-luts: ${grades.length} grades already current`
    : `sync-luts: copied ${copied} of ${grades.length} grades into public/luts`,
)

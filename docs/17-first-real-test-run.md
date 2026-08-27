# The first real test run since M4

*27 August 2026 · what running the suite found that reading it could not*

Every note since M4 has carried the same caveat: the backend tests are written,
reviewed, believed correct, and **have not executed**, because this machine had
no Docker and the suite runs against real Postgres, real MinIO and real ffmpeg
by design ([`conftest.py`](../backend/tests/conftest.py) is explicit that
mocking them would produce "a green suite that proves the mock works").

Docker Desktop is now installed. This is what happened when the caveat was
finally removed.

---

## 1. Where it landed

| | |
|---|---|
| Backend | **231 passed, 2 skipped** — 227 on the first run, plus four written for §2.3 |
| Frontend | 304 passed, unchanged |
| `ruff check` · `ruff format --check` · `mypy app` | clean |
| `make e2e` | **still not run** — see §5 |

The two skips are `test_transcription.py`: they want a warmed `faster-whisper`
model cache and skip rather than fail without one, which is the behaviour the
fixture was written to have.

The thirteen tests from the pipeline-reliability pass
([`16-pipeline-reliability-notes.md`](16-pipeline-reliability-notes.md)) all
pass — after one of them was fixed.

---

## 2. Four defects, none of which a careful re-read would have found

This is the part worth keeping. Each of these survived hand-review by someone
who was specifically looking for this class of bug, and each fell over within
minutes of running the actual command.

### 2.1 A test that was wrong about code that was right

`test_one_failing_check_does_not_stop_the_others` proves that when one sweep
check raises, the other three still run. It failed.

The cause is in the seam between the test and the code, which is why reading
either one alone shows nothing:

* `_guarded` calls `session.rollback()` when a check raises. Correct — a check
  that failed part-way leaves the session dirty for the next one.
* The file's fixture helpers (`_user`, `_asset`, `_job`) end at `flush()`, not
  `commit()`. Correct for every other test in the file, none of which triggers
  a rollback.
* Put together: the rollback threw away the very row the test then asked the
  sweep to find. `failed_uploads` came back empty and the assertion blamed
  `_guarded`.

In production `sweep()` receives a session from `worker_session()` with no
pending writes of its own, and the rows it acts on were committed by other
processes minutes or hours earlier. The fix is one `await db.commit()` in the
test, with a comment saying why — it is what makes the test model production
instead of modelling the fixture.

**The lesson is not "write better tests".** It is that a test asserting a
rollback behaviour cannot share the fixture convention of tests that never
roll back, and nothing about reading the two files side by side makes that
visible.

### 2.2 The recovery job was dead in the main dev flow

The pipeline sweep got its own `reconciliation` Celery queue. Every worker
invocation was updated to consume it — `Makefile` in three places,
`docker-compose.yml` — except `scripts/dev-up.sh`, which is what `make watch`
runs and what the README tells you to use.

So on a developer's machine, beat enqueued `sweep_pipeline` every five minutes
into a queue with no consumer. The messages accumulated in Redis and the sweep
never ran.

**This is the same bug the pass was written to fix**, one layer out: a message
sent to something nobody is listening for. The pass caught it in
`complete_upload`, caught it again in its own `sweep_stuck_running_jobs` during
re-reading, and then shipped a third instance of it in the shell script nobody
re-read. Grepping for the queue name across the repository is what found it —
about four seconds of work, available at any point in the last day, and not
done because the code under review looked complete.

### 2.3 `movie=`'s path escaping was one level short

`color_analysis._escape` escaped colons and backslashes for the `movie=` lavfi
source, with a comment saying scratch paths contain neither and escaping anyway
"costs one line and removes the question".

It removed the wrong question. A filtergraph argument is unescaped **twice** —
the filtergraph parser strips one level before the filter's own option parser
sees the string — so `\:` arrived as a bare `:` and the path split at it. The
escaping was dead code on Linux (no colon in `/tmp/...`) that happened to be
wrong, and the first path with a colon in it proved it. Every Windows path
qualifies: `C:`.

Now escaped for both levels. A path needing no escaping comes out
byte-identical, so Linux behaviour is unchanged — and a POSIX path that *did*
contain a colon now works where it previously would not have.

**The first version of this fix was also wrong**, and is worth recording. It
rewrote backslashes to forward slashes before escaping, because that spares a
Windows drive path four backslashes per separator. But a backslash is a legal
character in a POSIX *filename*: `/tmp/od\d/a.mp4` would have been rewritten
into a path to somewhere else. Guarding the rewrite behind `os.name == "nt"`
fixed the corruption and introduced a worse problem — the function then had a
branch that could not be exercised on the machine running the tests, which is
how the original bug got in. Checking whether the plain two-level escape works
on Windows *as well* took one command; it does, and the branch is gone.

Worth noting the shape of all three versions: the code was written defensively,
the defence was never exercised, and being unexercised is exactly how it stayed
wrong. `_escape` now has tests — four of them, covering both levels and the
POSIX backslash — which is the actual fix.

### 2.4 CI was red and nobody knew

`ruff format --check` failed on two test files — one from this pass, one older.
CI runs `ruff check . && ruff format --check .`, so the branch had a failing
build. It went unnoticed because the virtualenv the linters live in did not
exist on this machine, so no local command could report it either.

---

## 3. The toolchain assumed POSIX, and said so badly

A virtualenv puts its executables in `bin` on POSIX and `Scripts` on Windows.
Every recipe in the `Makefile` hard-coded `bin`, so `make migrate` and
`make test-backend` failed with "no such file or directory" against a
virtualenv that was perfectly healthy — which reads as a broken install rather
than a path assumption.

Both now resolve the directory instead of assuming it (`VBIN` in the
`Makefile`, the same two lines in `scripts/dev-up.sh`). A POSIX checkout is
byte-for-byte unaffected; the fallback branch was checked explicitly.

`make doctor` had a related and more annoying failure. It tested for Python
with `command -v python3` — and on Windows the Microsoft Store stub sits on
PATH under that exact name, answers `command -v`, and then refuses to run. The
doctor printed a cheerful `ok python3 est`: the first word of the stub's French
"not found" message, parsed as a version number. **A diagnostic that reports
success when the thing is missing is worse than not having the check.** It now
asks Python to execute, and names the fallback when only `python` works.

---

## 4. What was installed

| | |
|---|---|
| Docker Desktop | 29.7.2 (WSL2 backend, already enabled) |
| Compose | v5.4.0 |
| ffmpeg / ffprobe | 9.0.1 (`winget install Gyan.FFmpeg`) |
| Postgres · Redis · MinIO | `make infra`, all healthy |

ffmpeg was the larger of the two blockers by test count: 26 of the 229 tests
error at collection without it, across ingest, media, analysis and
transcription. `make doctor` had been saying so all along.

---

## 5. What is still not verified

**`make e2e` has not run.** It drives a real browser against `make e2e-up`,
which needs three long-lived servers and `pnpm` resolved from `frontend/`. On
this machine `pnpm` is reachable only as `corepack pnpm` (the doctor still
reports it missing), and `make watch` wants `tmux`, which Git Bash does not
ship. Nothing here is hard; it is simply not done, and it remains the last
unverified thing before M5 — it is the only check that covers M2 end to end.

**`make` itself is not installed in this shell.** The `Makefile` changes were
verified by dry-running the targets through WSL's GNU Make (both the Windows
and the POSIX branch), and the commands themselves were run directly. That is
weaker than running `make test-backend` here, and is stated rather than glossed.

**The two transcription tests skip rather than run.** Warming the cache means
downloading a `faster-whisper` model on first use; worth doing deliberately
before anyone relies on captions, not as a side effect of a test run.

---

## 6. What this changes for M5

The infrastructure prerequisite in
[`15-m5-readiness.md`](15-m5-readiness.md) §6 is met apart from `make e2e`.
The claim gap in
[`16-pipeline-reliability-notes.md`](16-pipeline-reliability-notes.md) §5 —
`media_assets` having no atomic claim the way `jobs` does — is unchanged and
still the right thing to close before export adds a render queue.

The broader point for M5: **four defects were found in under an hour by running
commands against work that had already been carefully re-read twice.** The
sweep's own notes made a virtue of catching two bugs by re-reading, and that was
worth doing — but every defect in §2 was outside what re-reading can see, and
three of the four were in the seams between files rather than inside any of
them. M5's frame-comparison test is the same kind of thing: it cannot be
reasoned about, only run.

---

*Build note · 27 August 2026 · written from the terminal output, not from the plan*

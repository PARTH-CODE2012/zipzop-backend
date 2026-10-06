# Multi-stage. `dev` mounts the source and reloads; `prod` copies it in.
#
# FFmpeg is installed in both because the ingest and render workers shell out
# to it, and the same image serves the API and the workers — one image to
# build, one to keep patched.

# Pinned by digest, not only by tag (docs/07-security.md §5.4, M7). A tag is a
# pointer its publisher moves on every rebuild; the digest is the exact image
# that was built and scanned. It also pins FFmpeg, which comes from this
# image's Debian release (13, trixie) — the component that parses hostile input
# for us, so its version should change when we decide, not when a tag moves.
# To update: `docker pull python:3.12-slim`, take the new RepoDigest, rebuild,
# and let the `trivy (production image)` job judge it.
#
# Last moved 6 October 2026: the 29 September digest carried seven HIGH
# advisories with Debian fixes (OpenSSL CVE-2026-75804 / CVE-2026-84782 in
# libssl3t64, openssl and openssl-provider-legacy; pcre2 CVE-2026-103111), so
# the first `trivy (production image)` run on GitHub failed. The 6 October
# image ships 3.5.7-1~deb13u3 and 10.46-1~deb13u3.
FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# `fonts-noto-core` is not cosmetic and not optional. The export renderer draws
# captions with libass, and **Hindi is the product's stated edge**: without a
# font carrying the Devanagari block, every Hindi caption renders as empty boxes
# however correct the shaping is. What ffmpeg pulls in on its own is DejaVu,
# which covers Latin and no Indic script at all — so a Latin caption test passes
# and Hindi ships broken. `fc-cache` so fontconfig can resolve the family name
# the ASS script asks for.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        fontconfig \
        fonts-noto-core \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so a source change does not invalidate the layer.
#
# Runtime dependencies only. The `[dev]` extras — pytest, ruff, mypy — used to
# be installed here, in the stage `prod` inherits from, so the production image
# shipped a test runner and a type checker it never uses. Every package in the
# image is one more thing to patch and one more thing an intruder can run
# (docs/07-security.md §5.4, found in M7). They belong to the `dev` stage.
#
# **From the lockfile, with its hashes checked.** `pyproject.toml` only states
# ranges (`fastapi>=0.115`), so an image built from it got whatever was newest
# on the day of the build — two builds of the same commit could ship different
# code, and a compromised release would be picked up the moment it appeared.
# `requirements.lock` pins every package, transitive ones included, to a
# version *and* a hash; `--require-hashes` refuses anything that does not
# match. Regenerate with `make lock`.
COPY requirements.lock ./
RUN pip install --upgrade pip && pip install --require-hashes -r requirements.lock

# ------------------------------------------------------------------ dev ----
FROM base AS dev
COPY requirements-dev.lock ./
RUN pip install --require-hashes -r requirements-dev.lock
# Source arrives via a bind mount in docker-compose, so nothing is copied.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ----------------------------------------------------------------- prod ----
FROM base AS prod

COPY . .
# pip is removed once it has done its work. Nothing at runtime installs a
# package, and pip carries vendored copies of other libraries: the M7 image
# scan flagged two HIGH advisories (msgpack 1.1.2, setuptools 70.3.0) that
# existed in this image only as pip's own dependencies. A production container
# with no package manager is also one where an intruder cannot `pip install`
# their tooling.
RUN pip install --no-deps -e . \
    && pip uninstall -y pip

# Run as a non-root user. A container that does not need root should not have it.
RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health/live || exit 1

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

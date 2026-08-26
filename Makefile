.DEFAULT_GOAL := help
SHELL := /bin/bash

BACKEND  := backend
FRONTEND := frontend
COMPOSE  := docker compose

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------------------ setup ---

.PHONY: setup
setup: ## First run: copy .env, install both sides, generate the API types
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example")
	$(MAKE) install-backend
	$(MAKE) install-frontend
	# generated.ts is gitignored — it is derived from the committed openapi.json,
	# and committing it would let the two drift. A fresh clone therefore has no
	# API types at all, and `pnpm typecheck`, `pnpm build` and `pnpm dev` all
	# fail on a missing module until this runs. It belongs in setup, not in a
	# note somebody reads after losing twenty minutes.
	$(MAKE) types
	@echo ""
	@echo "Next: make infra && make migrate && make dev-all"

.PHONY: install-backend
install-backend: ## Install Python dependencies into backend/.venv
	cd $(BACKEND) && python3 -m venv .venv && \
		./.venv/bin/pip install --upgrade pip && \
		./.venv/bin/pip install -e ".[dev]"

.PHONY: install-frontend
install-frontend: ## Install Node dependencies
	cd $(FRONTEND) && corepack enable && pnpm install

# ------------------------------------------------------- local infrastructure

# Docker Hub can be slow to reach from some networks — one of its addresses
# regularly takes several seconds to answer. Pulling four images at once then
# blows past the default deadline, so give the client real headroom.
export COMPOSE_HTTP_TIMEOUT ?= 300
export DOCKER_CLIENT_TIMEOUT ?= 300

.PHONY: docker-ok
docker-ok: ## Fail early with a useful message if the daemon is unreachable
	@docker info >/dev/null 2>&1 || { \
		echo "cannot reach the Docker daemon."; \
		if getent group docker | grep -q "\b$$(id -un)\b"; then \
			echo "you ARE in the docker group, but this session predates it."; \
			echo "log out and back in, or run: newgrp docker"; \
		else \
			echo "run: sudo usermod -aG docker $$(id -un)   then log out and back in"; \
		fi; \
		exit 1; \
	}

.PHONY: pull
pull: docker-ok ## Fetch images one at a time, retrying — use this on a flaky connection
	@failed=""; \
	for img in postgres:16-alpine redis:7-alpine minio/minio:latest minio/mc:latest; do \
		echo "→ $$img"; \
		ok=0; \
		for attempt in 1 2 3; do \
			docker pull "$$img" && { ok=1; break; }; \
			echo "   attempt $$attempt failed, retrying..."; sleep 3; \
		done; \
		[ $$ok -eq 1 ] || failed="$$failed $$img"; \
	done; \
	if [ -n "$$failed" ]; then \
		echo ""; echo "FAILED to pull:$$failed"; exit 1; \
	fi; \
	echo "images ready"

.PHONY: up
up: docker-ok ## Start Postgres, Redis, MinIO, API, worker and beat
	$(COMPOSE) up -d
	@source scripts/ports.sh && zz_load_ports && \
		echo "api      http://localhost:$$API_PORT/docs"
	@echo "minio    http://localhost:9001  (zipzop / zipzop-dev-secret)"

# ---------------------------------------------- native services (no Docker) --
# Docker Hub is slow to reach from some networks. Postgres and Redis installed
# from apt work identically for everything up to M2; only MinIO (object
# storage, first needed for uploads) has no native equivalent here.

.PHONY: native-check
native-check: ## Check natively-installed Postgres and Redis are reachable
	@ok=1; \
	pg_isready -h localhost -p 5432 >/dev/null 2>&1 \
		&& echo "postgres  OK  localhost:5432" \
		|| { echo "postgres  DOWN — sudo systemctl start postgresql@18-main"; ok=0; }; \
	redis-cli -h localhost ping >/dev/null 2>&1 \
		&& echo "redis     OK  localhost:6379" \
		|| { echo "redis     DOWN — sudo systemctl start redis-server"; ok=0; }; \
	psql "postgresql://zipzop:zipzop@localhost:5432/zipzop" -c "SELECT 1" >/dev/null 2>&1 \
		&& echo "database  OK  zipzop/zipzop reachable" \
		|| { echo "database  MISSING — see README, 'Without Docker'"; ok=0; }; \
	[ $$ok -eq 1 ]

.PHONY: down
down: ## Stop everything, keep the data
	$(COMPOSE) down

.PHONY: nuke
nuke: ## Stop everything and DELETE all local data
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Tail all service logs
	$(COMPOSE) logs -f

.PHONY: ps
ps: ## Show service status
	$(COMPOSE) ps

# ---------------------------------------------------------------- database ---

.PHONY: migrate
migrate: ## Apply all migrations
	cd $(BACKEND) && ./.venv/bin/alembic upgrade head

.PHONY: migration
migration: ## Create a migration: make migration m="add users"
	@test -n "$(m)" || (echo "usage: make migration m=\"what changed\"" && exit 1)
	cd $(BACKEND) && ./.venv/bin/alembic revision --autogenerate -m "$(m)"

.PHONY: downgrade
downgrade: ## Roll back one migration
	cd $(BACKEND) && ./.venv/bin/alembic downgrade -1

.PHONY: psql
psql: ## Open a shell on the database
	$(COMPOSE) exec postgres psql -U zipzop -d zipzop

# --------------------------------------------------------------------- dev ---

# Every target that starts or names a server resolves its port through
# scripts/ports.sh first. Nothing below writes a port number down: 8000 and
# 3000 are the two most contested ports on a developer's machine, and the
# frontend assuming 8000 while the API had moved is exactly how this broke.
PORTS := source scripts/ports.sh && zz_resolve_ports >/dev/null

.PHONY: ports
ports: ## Show which ports the dev stack will use, and why
	@source scripts/ports.sh && zz_resolve_ports && \
		echo "  API      http://localhost:$$API_PORT" && \
		echo "  web      http://localhost:$$WEB_PORT" && \
		echo "  CORS     $$CORS_ORIGINS"

.PHONY: dev
dev: ## Run the API natively with reload (infrastructure must be up)
	@$(PORTS) && cd $(BACKEND) && \
		echo "API on http://localhost:$$API_PORT" && \
		./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $$API_PORT --reload

.PHONY: dev-worker
dev-worker: ## Run a Celery worker natively
	cd $(BACKEND) && ./.venv/bin/celery -A app.workers.celery_app worker --loglevel=INFO -Q ingest,analysis,render,billing,reconciliation

.PHONY: dev-frontend
dev-frontend: ## Run the Next.js dev server
	@$(PORTS) && cd $(FRONTEND) && \
		echo "web on http://localhost:$$WEB_PORT, API at $$NEXT_PUBLIC_API_BASE_URL" && \
		pnpm dev --port $$WEB_PORT

.PHONY: infra
infra: ## Start whatever infrastructure is not already up (Postgres, Redis, MinIO)
	@./scripts/infra.sh

.PHONY: dev-all
.PHONY: watch
watch: ## One tmux session, three visible panes: API + worker + web — Ctrl-C in a pane stops just that service
	@./scripts/dev-up.sh

.PHONY: watch-stop
watch-stop: ## Stop what `make watch` started, leaving the containers up
	@./scripts/dev-down.sh

dev-all: infra migrate ## Everything you need to click around: API + ingest worker + frontend
	@source scripts/ports.sh && zz_resolve_ports && \
	echo "starting the API, the ingest worker and the dev server…" && \
	(cd $(BACKEND) && ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $$API_PORT \
		--reload > /tmp/zipzop-api.log 2>&1 &) && \
	(cd $(BACKEND) && ./.venv/bin/celery -A app.workers.celery_app worker --loglevel=INFO \
		-Q ingest,analysis,render,billing,reconciliation --concurrency=2 > /tmp/zipzop-worker.log 2>&1 &) && \
	(cd $(FRONTEND) && pnpm dev --port $$WEB_PORT > /tmp/zipzop-web.log 2>&1 &) && \
	sleep 4 && \
	echo "" && \
	echo "  editor    http://localhost:$$WEB_PORT/editor/scratch" && \
	echo "  API docs  http://localhost:$$API_PORT/docs" && \
	echo "  logs      /tmp/zipzop-{api,worker,web}.log" && \
	echo "" && \
	echo "  stop with: make dev-stop"

# `pkill -f` matches whole command lines and knows nothing about which checkout
# a process belongs to, so the four `pkill`s that used to live here killed every
# Next dev server and every uvicorn on the machine — other projects included.
# scripts/dev-stop.sh filters on the process's working directory, and refuses to
# kill any ancestor of itself. Both filters are failures that happened.
.PHONY: dev-stop
dev-stop: ## Stop this project's dev processes — and only this project's
	@./scripts/dev-stop.sh

.PHONY: doctor
doctor: ## Check a fresh clone has everything it needs, and say what is missing
	@./scripts/doctor.sh

# ------------------------------------------------------------------- spike ---

.PHONY: spike-media
spike-media: ## Generate the M1 compositor spike's test clips and LUT (needs ffmpeg)
	./scripts/make-spike-media.sh

.PHONY: luts
luts: ## Generate the five shared .cube grades the browser and the renderer both read
	python3 scripts/make_luts.py $(FRONTEND)/public/luts

# --------------------------------------------------------------------- e2e ---
# M2's closing condition, checked in a real browser. See frontend/e2e/README.md
# for what the 29 checks cover and for the three bugs this found that the unit
# suites could not.

.PHONY: e2e-media
e2e-media: ## Generate the end-to-end fixture clip (needs ffmpeg)
	@ffmpeg -y -hide_banner -loglevel error \
		-f lavfi -i "testsrc2=size=1280x720:rate=30:duration=6" \
		-f lavfi -i "sine=frequency=440:duration=6,volume=0.8" \
		-c:v libx264 -preset veryfast -pix_fmt yuv420p \
		-c:a aac -b:a 128k -movflags +faststart \
		$(FRONTEND)/e2e/fixture.mp4
	@echo "wrote $(FRONTEND)/e2e/fixture.mp4"

# The resolved ports are written to a file the `e2e` target reads back, because
# each make recipe is its own shell: a port resolved in `e2e-up` is gone by the
# time `e2e` runs, and the browser would go looking on the default.
ZZ_PORTS_FILE := /tmp/zipzop-ports.env

.PHONY: e2e-up
e2e-up: ## Start everything the end-to-end run needs, in the background
	@$(MAKE) --no-print-directory native-check
	@source scripts/ports.sh && zz_resolve_ports && \
	{ echo "API_PORT=$$API_PORT"; echo "WEB_PORT=$$WEB_PORT"; } > $(ZZ_PORTS_FILE) && \
	echo "starting the API, a worker and the dev server on :$$API_PORT and :$$WEB_PORT…" && \
	(cd $(BACKEND) && ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $$API_PORT \
		--log-level warning > /tmp/zipzop-api.log 2>&1 &) && \
	(cd $(BACKEND) && ./.venv/bin/celery -A app.workers.celery_app worker --loglevel=INFO \
		-Q ingest,analysis,render,billing,reconciliation --concurrency=2 > /tmp/zipzop-worker.log 2>&1 &) && \
	(cd $(FRONTEND) && pnpm dev --port $$WEB_PORT > /tmp/zipzop-web.log 2>&1 &) && \
	echo "logs: /tmp/zipzop-{api,worker,web}.log"

.PHONY: e2e-down
e2e-down: dev-stop ## Stop what e2e-up started

.PHONY: e2e
e2e: e2e-media ## Prove M2 end to end in a real browser
	@[ -f $(ZZ_PORTS_FILE) ] && source $(ZZ_PORTS_FILE) || true; \
	source scripts/ports.sh && zz_load_ports && zz_export_urls && \
	cd $(FRONTEND) && node e2e/m2.mjs

.PHONY: e2e-headful
e2e-headful: e2e-media ## The same, with a window you can watch
	@[ -f $(ZZ_PORTS_FILE) ] && source $(ZZ_PORTS_FILE) || true; \
	source scripts/ports.sh && zz_load_ports && zz_export_urls && \
	cd $(FRONTEND) && node e2e/m2.mjs --headful

# ---------------------------------------------------------------- contract ---

.PHONY: razorpay-check
razorpay-check: ## Check the Razorpay keys authenticate, and which currencies the account takes
	bash scripts/razorpay-check.sh $(ARGS)

.PHONY: openapi
openapi: ## Regenerate openapi.json from FastAPI
	cd $(BACKEND) && ./.venv/bin/python -m app.scripts.dump_openapi ../openapi.json
	@echo "wrote openapi.json"

.PHONY: types
types: openapi ## Regenerate the frontend's API types from openapi.json
	cd $(FRONTEND) && pnpm run generate:types

.PHONY: contract-check
contract-check: ## Fail if openapi.json is stale — run this in CI
	cd $(BACKEND) && ./.venv/bin/python -m app.scripts.dump_openapi /tmp/openapi.check.json
	@diff -q openapi.json /tmp/openapi.check.json >/dev/null \
		|| (echo "openapi.json is stale — run 'make openapi' and commit the result" && exit 1)
	@echo "openapi.json is up to date"

# --------------------------------------------------------------- quality ----

.PHONY: test
test: test-backend test-frontend ## Run every test

.PHONY: test-backend
test-backend: ## Run backend tests
	cd $(BACKEND) && ./.venv/bin/pytest -q

.PHONY: test-frontend
test-frontend: ## Run frontend tests
	cd $(FRONTEND) && pnpm test

.PHONY: lint
lint: ## Lint and type-check both sides
	cd $(BACKEND) && ./.venv/bin/ruff check . && ./.venv/bin/mypy app
	cd $(FRONTEND) && pnpm lint && pnpm typecheck

.PHONY: format
format: ## Auto-format both sides
	cd $(BACKEND) && ./.venv/bin/ruff format . && ./.venv/bin/ruff check --fix .
	cd $(FRONTEND) && pnpm format

.PHONY: check
check: lint test contract-check ## Everything CI runs

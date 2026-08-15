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
setup: ## First run: copy .env, install both sides
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example")
	$(MAKE) install-backend
	$(MAKE) install-frontend
	@echo ""
	@echo "Next: make up && make migrate"

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
	@echo "api      http://localhost:8000/docs"
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

.PHONY: dev
dev: ## Run the API natively with reload (infrastructure must be up)
	cd $(BACKEND) && ./.venv/bin/uvicorn app.main:app --reload --port 8000

.PHONY: dev-worker
dev-worker: ## Run a Celery worker natively
	cd $(BACKEND) && ./.venv/bin/celery -A app.workers.celery_app worker --loglevel=INFO -Q ingest,analysis,render,billing

.PHONY: dev-frontend
dev-frontend: ## Run the Next.js dev server
	cd $(FRONTEND) && pnpm dev

# ------------------------------------------------------------------- spike ---

.PHONY: spike-media
spike-media: ## Generate the M1 compositor spike's test clips and LUT (needs ffmpeg)
	./scripts/make-spike-media.sh

# ---------------------------------------------------------------- contract ---

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

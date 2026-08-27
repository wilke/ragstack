.PHONY: help install-python test-python perf-python lint-python run-python \
       build-go test-go lint-go run-go \
       test-conformance-python test-conformance-go test-conformance \
       test-conformance-authz test-conformance-identity-google \
       infra-up infra-down up-python up-go down \
       infra-pull-apptainer infra-up-apptainer infra-down-apptainer \
       sidecars-pull-apptainer sidecars-up-apptainer sidecars-down-apptainer \
       new-tenant-apptainer \
       frontend-install frontend-dev frontend-build frontend-gen-api \
       test-all

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-28s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

install-python: ## Install Python package in dev mode
	cd python && pip install -e ".[all,dev]"

# PYTHONPATH: belt to the conftest guard's braces (#432). The guard fails the
# run when `ragstack` resolves outside this checkout; pinning the path here
# means it doesn't have to, whatever the caller's CWD and whatever editable
# install the active environment happens to carry.
test-python: ## Run Python unit + API tests
	cd python && PYTHONPATH=$(CURDIR)/python pytest tests/ -v

perf-python: ## Run Python performance budget tests
	cd python && PYTHONPATH=$(CURDIR)/python pytest tests/ -m perf -v -s

lint-python: ## Lint Python code
	cd python && ruff check . && mypy ragstack/

run-python: ## Start Python API server (dev)
	cd python && uvicorn ragstack.api.main:app --reload --port 8000

# ---------------------------------------------------------------------------
# Frontend (dashboard & explorer SPA — React + Vite + TS)
# ---------------------------------------------------------------------------

frontend-install: ## Install frontend deps
	cd frontend && npm install

frontend-dev: ## Start the Vite dev server (:5173, proxies /v1+/health to the API)
	cd frontend && npm run dev

frontend-build: ## Type-check + production build to frontend/dist
	cd frontend && npm run build

frontend-gen-api: ## Regenerate the typed API client from contracts/openapi.yaml
	cd frontend && npm run gen:api

# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------

build-go: ## Build Go API binary
	cd go && go build -o bin/api ./cmd/api

test-go: ## Run Go tests
	cd go && go test ./... -v

lint-go: ## Lint Go code
	cd go && golangci-lint run ./...

run-go: build-go ## Start Go API server (dev)
	cd go && ./bin/api

# ---------------------------------------------------------------------------
# Conformance
# ---------------------------------------------------------------------------

test-conformance-python: ## Run conformance tests against Python
	RAGSTACK_BASE_URL=http://localhost:8000 RAGSTACK_IMPL=python \
		pytest conformance/ -v

test-conformance-go: ## Run conformance tests against Go
	RAGSTACK_BASE_URL=http://localhost:8080 RAGSTACK_IMPL=go \
		pytest conformance/ -v

test-conformance: test-conformance-python test-conformance-go ## Run conformance against both

test-conformance-authz: ## Boot a keyed in-memory API and run the authz (401/403) conformance suite
	conformance/run_authz_keyed.sh

test-conformance-keyed: ## Boot a keyed in-memory API with FOUR distinct principals (incl. the P2 persona) and run the WHOLE conformance suite against it (#405)
	AUTHZ_CONF_SCOPE=. AUTHZ_CONF_CREATE_GATE=1 conformance/run_authz_keyed.sh

test-conformance-identity-google: ## Boot a Google-OIDC API and run the identity conformance suite (needs GOOGLE_OIDC_CLIENT_ID)
	conformance/run_identity_google.sh

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

infra-up: ## Start infrastructure services
	docker compose -f deploy/docker-compose.infra.yml up -d

infra-down: ## Stop infrastructure services
	docker compose -f deploy/docker-compose.infra.yml down

up-go: ## Start Go API + infra + sidecars
	docker compose -f deploy/docker-compose.infra.yml \
	               -f deploy/docker-compose.sidecars.yml \
	               -f deploy/docker-compose.yml up -d

up-python: ## Start Python API + infra + sidecars
	docker compose -f deploy/docker-compose.infra.yml \
	               -f deploy/docker-compose.sidecars.yml \
	               -f deploy/docker-compose.python.yml up -d

down: ## Stop all services
	docker compose -f deploy/docker-compose.infra.yml \
	               -f deploy/docker-compose.sidecars.yml \
	               -f deploy/docker-compose.yml \
	               -f deploy/docker-compose.python.yml down 2>/dev/null; true

# ---------------------------------------------------------------------------
# Apptainer (Docker-free infra stack)
# ---------------------------------------------------------------------------

infra-pull-apptainer: ## Pre-pull infra images as Apptainer SIFs
	./apptainer/pull.sh

infra-up-apptainer: ## Start infra stack via Apptainer
	./apptainer/up.sh

infra-down-apptainer: ## Stop the Apptainer infra stack
	./apptainer/down.sh

sidecars-pull-apptainer: ## Pre-pull base SIF used by sidecars
	./apptainer/sidecars-pull.sh

sidecars-up-apptainer: ## Start ML sidecars via Apptainer
	./apptainer/sidecars-up.sh

sidecars-down-apptainer: ## Stop the Apptainer sidecars
	./apptainer/sidecars-down.sh

new-tenant-apptainer: ## Provision a tenant (ADR-0005): NAME=acme [ARGS="--dry-run"]
	./apptainer/new-tenant.sh $(NAME) $(ARGS)

# ---------------------------------------------------------------------------
# All
# ---------------------------------------------------------------------------

test-all: test-python test-go ## Run all unit tests (Python + Go)

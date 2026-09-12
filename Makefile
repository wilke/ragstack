.PHONY: help install-python test-python perf-python lint-python run-python \
       build-go test-go lint-go run-go \
       test-conformance-python test-conformance-go test-conformance \
       test-conformance-authz test-conformance-identity-google \
       test-conformance-ctl validate-contracts \
       infra-up infra-down up-python up-go down \
       infra-pull-apptainer infra-up-apptainer infra-down-apptainer \
       sidecars-pull-apptainer sidecars-up-apptainer sidecars-down-apptainer \
       new-tenant-apptainer \
       frontend-install frontend-dev frontend-build frontend-gen-api \
       frontend-build-admin \
       build-ctl install-ctl test-ctl golang-sif go-mode \
       test-all

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-28s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

# Overridable the way GO is, so a host whose `python` is not the ragstack env
# can say `make validate-contracts PYTHON=/rag/envs/ragstack/bin/python`.
PYTHON ?= python

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

# The ADMIN bundle is a separate Vite build (frontend/vite.admin.config.ts), not
# a second entry of the one above: a shared multi-entry build would ship
# admin.html — the control plane's screens — inside every tenant's dist/. The
# base must match where nginx aliases it (the generated static snippet's
# `alias /rag/data/ctl/ui/dist/` under /ragstack/admin/ui/), because the app
# derives the ctl API's location from its own BASE_URL.
#
# That derivation is `gatewayApiBase()` (frontend/src/api/base.ts): it matches
# the base against `^(.*)/ui/?$` and returns null for anything else. A BASE that
# does not end in `/ui/` therefore does not fail loudly — it builds a bundle
# whose ctl API base is "", so the admin page calls the GATEWAY ROOT (`/v1/...`)
# instead of `/ragstack/admin/api/v1/...`, and every read 404s on a page that
# looks fine. Refuse it here, where the mistake is made.
BASE ?= /ragstack/admin/ui/

frontend-build-admin: ## Build the admin bundle to frontend/dist-admin (BASE=/ragstack/admin/ui/)
	@case "$(BASE)" in */ui/) ;; *) echo "BASE must end in /ui/ (got '$(BASE)'): the admin bundle derives its API base from it and would call the gateway root instead." >&2; exit 1;; esac
	cd frontend && npm run build:admin -- --base $(BASE)

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
# ragstack-ctl (tenant control plane, ADR-0007) — static binary, no CGO.
# install-ctl is versioned + symlinked so rollback is `ln -sfn`; it never
# overwrites a previous version.
#
# Toolchain: host `go` or the Go CONTAINER, selected by GO_MODE.
#   auto (default)  host `go` if one is on PATH, else the SIF if it exists
#   host            $(GO) on this machine (e.g. GO=~/sdk/go1.23.12/bin/go)
#   container       `apptainer exec $(GO_SIF) go` — the image pinned to the
#                   `toolchain` line of go/go.mod, so every build on coconut
#                   uses the same compiler as CI (actions/setup-go reads the
#                   same file). Caches live under $(GO_CACHE) on /rag, not in
#                   the NFS home. `make golang-sif` pulls the image once.
# Apptainer passes the caller's environment through, so the CGO_ENABLED=0 /
# -race prefixes below apply inside the container exactly as on the host.
# ---------------------------------------------------------------------------
GO ?= go
GO_MODE ?= auto
GO_SIF ?= /rag/apptainer/images/golang.sif
GO_IMAGE ?= docker://golang:1.23.12-bookworm
GO_CACHE ?= /rag/cache/go
ifeq ($(GO_MODE),auto)
  ifeq ($(shell command -v $(GO) 2>/dev/null),)
    ifneq ($(wildcard $(GO_SIF)),)
      GO_MODE := container
    else
      GO_MODE := host
    endif
  else
    GO_MODE := host
  endif
endif
ifeq ($(GO_MODE),container)
  GO := apptainer exec -B /rag --pwd $(CURDIR)/go \
        --env GOMODCACHE=$(GO_CACHE)/mod,GOCACHE=$(GO_CACHE)/build,GOPATH=$(GO_CACHE)/path,GOTOOLCHAIN=local \
        $(GO_SIF) go
endif

golang-sif: ## Pull the pinned Go image into $(GO_SIF) (once; cache under /rag/cache/apptainer, not the NFS home)
	mkdir -p /rag/cache/apptainer/tmp $(GO_CACHE)
	APPTAINER_CACHEDIR=/rag/cache/apptainer APPTAINER_TMPDIR=/rag/cache/apptainer/tmp \
	    apptainer pull --disable-cache $(GO_SIF) $(GO_IMAGE)
	apptainer exec $(GO_SIF) go version

go-mode: ## Print which Go toolchain the ctl targets will use (host | container)
	@echo "GO_MODE=$(GO_MODE)"
	@$(GO) version
CTL_VERSION ?= $(shell git describe --tags --always --dirty)
CTL_COMMIT ?= $(shell git rev-parse HEAD)
CTL_BUILT_AT ?= $(shell date -u +%Y-%m-%dT%H:%M:%SZ)
CTL_PKG := github.com/ragstack/ragstack/internal/ctl/version
CTL_LDFLAGS := -s -w -X $(CTL_PKG).Version=$(CTL_VERSION) -X $(CTL_PKG).Commit=$(CTL_COMMIT) -X $(CTL_PKG).BuiltAt=$(CTL_BUILT_AT)
CTL_PREFIX ?= /rag/bin

build-ctl: ## Build the ragstack-ctl static binary into go/bin/ragstack-ctl
	@echo "build-ctl: GO_MODE=$(GO_MODE)"
	cd go && CGO_ENABLED=0 $(GO) build -trimpath -buildvcs=false -ldflags '$(CTL_LDFLAGS)' -o bin/ragstack-ctl ./cmd/ragstack-ctl

install-ctl: build-ctl ## Install go/bin/ragstack-ctl as $(CTL_PREFIX)/ragstack-ctl-<version> and point the symlink at it
	install -m 0755 go/bin/ragstack-ctl $(CTL_PREFIX)/ragstack-ctl-$(CTL_VERSION)
	ln -sfn ragstack-ctl-$(CTL_VERSION) $(CTL_PREFIX)/ragstack-ctl

test-ctl: ## Run the ctl package tests (goldens, parity vs new-tenant.sh, canaries) with the race detector
	cd go && $(GO) test ./internal/ctl/... ./cmd/ragstack-ctl/... -race

# ---------------------------------------------------------------------------
# Conformance
# ---------------------------------------------------------------------------

# `conformance/` holds TWO suites now: the tenant API's, and the control
# plane's (`conformance/ctl`, ADR-0007) — a different daemon, different
# credentials, and a conftest that requires RAGSTACK_CTL_URL and refuses to
# default it (the conventional bind on the deployment host is the LIVE control
# plane). So a bare `pytest conformance/` collected the ctl suite and turned a
# healthy tenant API into ~96 collection errors. The root conftest skips that
# directory when the variable is unset; `--ignore` here is the explicit half,
# so each target says what it runs. The ctl suite has its own target below.
test-conformance-python: ## Run conformance tests against Python
	RAGSTACK_BASE_URL=http://localhost:8000 RAGSTACK_IMPL=python \
		pytest conformance/ --ignore=conformance/ctl -v

test-conformance-go: ## Run conformance tests against Go
	RAGSTACK_BASE_URL=http://localhost:8080 RAGSTACK_IMPL=go \
		pytest conformance/ --ignore=conformance/ctl -v

test-conformance: test-conformance-python test-conformance-go ## Run conformance against both

test-conformance-authz: ## Boot a keyed in-memory API and run the authz (401/403) conformance suite
	conformance/run_authz_keyed.sh

test-conformance-keyed: ## Boot a keyed in-memory API with FOUR distinct principals (incl. the P2 persona) and run the WHOLE conformance suite against it (#405)
	AUTHZ_CONF_SCOPE=. AUTHZ_CONF_CREATE_GATE=1 conformance/run_authz_keyed.sh

test-conformance-identity-google: ## Boot a Google-OIDC API and run the identity conformance suite (needs GOOGLE_OIDC_CLIENT_ID)
	conformance/run_identity_google.sh

test-conformance-ctl: build-ctl ## Boot a --fake-drivers ragstack-ctl on :23999 with its own principals and run the control-plane conformance suite
	conformance/run_ctl_local.sh

# No server, no infra, ~1 s — which is why it is part of `test-all` rather than
# something only the ctl conformance run reaches. `conformance/ctl/
# test_contract_static.py` wraps these same checks as individual assertions and
# is collected by a plain `pytest conformance/` (the root conftest keeps that
# one file even when it skips the rest of the ctl suite).
validate-contracts: ## Static checks on the control-plane contract (contracts/ctl/openapi.yaml + schemas)
	$(PYTHON) contracts/ctl/validate.py

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

# validate-contracts is in here because it was in nothing: it needs no server
# and no infra, so there was no reason for the control-plane contract's only
# automatic check to be reachable exclusively through `make
# test-conformance-ctl` (which builds a Go binary and boots a daemon).
test-all: test-python test-go validate-contracts ## Run all unit tests (Python + Go) + the control-plane contract checks

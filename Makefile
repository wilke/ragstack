.PHONY: help install-python test-python lint-python run-python \
       build-go test-go lint-go run-go \
       test-conformance-python test-conformance-go test-conformance \
       infra-up infra-down up-python up-go down \
       test-all

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-28s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

install-python: ## Install Python package in dev mode
	cd python && pip install -e ".[all,dev]"

test-python: ## Run Python unit + API tests
	cd python && pytest tests/ -v

lint-python: ## Lint Python code
	cd python && ruff check . && mypy ragstack/

run-python: ## Start Python API server (dev)
	cd python && uvicorn ragstack.api.main:app --reload --port 8000

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
# All
# ---------------------------------------------------------------------------

test-all: test-python test-go ## Run all unit tests (Python + Go)

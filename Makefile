.PHONY: install format lint typecheck test test-unit test-e2e conformance hosted-conformance hosted-e2e hosted-auth-adversarial hosted-auth-e2e browser-test check validate-compose-project docker-build docker-up docker-test docker-down docker-sync docker-sync-once

VCS_REF ?= $(shell git rev-parse HEAD)
export FOLIO_COMPOSE_PROJECT

install:
	uv sync --dev

format:
	uv run ruff format src tests

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

typecheck:
	uv run mypy

test:
	uv run pytest --cov=folio_lattice --cov-report=term-missing --cov-fail-under=80

test-unit:
	uv run pytest -q tests/test_mcp.py tests/test_sandbox.py tests/test_service.py tests/test_web.py

test-e2e:
	uv run pytest -q tests/test_e2e.py tests/test_browser_e2e.py

conformance:
	FOLIO_CONFORMANCE_EVIDENCE="$${FOLIO_CONFORMANCE_EVIDENCE:-/tmp/folio-lattice-mcp-conformance.json}" \
	uv run python tests/mcp_conformance.py

hosted-conformance:
	FOLIO_HOSTED_CONFORMANCE_EVIDENCE="$${FOLIO_HOSTED_CONFORMANCE_EVIDENCE:-/tmp/folio-lattice-fl-urj.30.json}" \
	uv run python tests/hosted_mcp_conformance.py

hosted-e2e:
	FOLIO_GATE_COMMAND="$${FOLIO_GATE_COMMAND:-make hosted-e2e}" \
	FOLIO_EVIDENCE_PATH="$${FOLIO_EVIDENCE_PATH:-/tmp/folio-lattice-fl-urj-5.2.json}" \
	uv run python tests/hyperset_hosted_consumer.py

hosted-auth-adversarial:
	uv run pytest -q tests/adversarial/test_hosted_auth.py
	FOLIO_HOSTED_AUTH_TEST_TARGET=tests.hosted_auth_negative_target:factory \
	uv run pytest -q tests/test_hosted_auth_negative.py

hosted-auth-e2e:
	FOLIO_EVIDENCE_PATH="$${FOLIO_EVIDENCE_PATH:-/tmp/folio-lattice-fl-urj-5.2.json}" \
	uv run python tests/hosted_auth_target.py e2e

browser-test:
	uv run pytest -q tests/test_browser_e2e.py

check: lint typecheck test

validate-compose-project:
	@project="$${FOLIO_COMPOSE_PROJECT:-$${COMPOSE_PROJECT_NAME:-}}"; \
	case "$$project" in \
		"" ) ;; \
		[!a-z0-9]*|*[!a-z0-9_-]*) \
			echo "FOLIO_COMPOSE_PROJECT must match Docker Compose's lowercase project-name grammar" >&2; \
			exit 2 ;; \
	esac

docker-build: validate-compose-project
	VCS_REF="$(VCS_REF)" COMPOSE_PROJECT_NAME="$${FOLIO_COMPOSE_PROJECT:-$${COMPOSE_PROJECT_NAME:-folio-lattice-local}}" docker compose build --build-arg VCS_REF="$(VCS_REF)"

docker-up: validate-compose-project
	VCS_REF="$(VCS_REF)" COMPOSE_PROJECT_NAME="$${FOLIO_COMPOSE_PROJECT:-$${COMPOSE_PROJECT_NAME:-folio-lattice-local}}" docker compose up -d --build

docker-test: validate-compose-project
	VCS_REF="$(VCS_REF)" COMPOSE_PROJECT_NAME="$${FOLIO_COMPOSE_PROJECT:-$${COMPOSE_PROJECT_NAME:-folio-lattice-local}}" docker compose build --build-arg VCS_REF="$(VCS_REF)"
	VCS_REF="$(VCS_REF)" COMPOSE_PROJECT_NAME="$${FOLIO_COMPOSE_PROJECT:-$${COMPOSE_PROJECT_NAME:-folio-lattice-local}}" docker compose up -d
	FOLIO_BASE_URL="$${FOLIO_BASE_URL:-http://127.0.0.1:$${FOLIO_HOST_PORT:-8000}}" \
	FOLIO_RENDER_URL="$${FOLIO_RENDER_URL:-http://127.0.0.1:$${FOLIO_RENDER_HOST_PORT:-8001}}" \
	FOLIO_COMPOSE_PROJECT="$${FOLIO_COMPOSE_PROJECT:-$${COMPOSE_PROJECT_NAME:-folio-lattice-local}}" \
	uv run python tests/test_docker_e2e.py

docker-down: validate-compose-project
	VCS_REF="$(VCS_REF)" COMPOSE_PROJECT_NAME="$${FOLIO_COMPOSE_PROJECT:-$${COMPOSE_PROJECT_NAME:-folio-lattice-local}}" docker compose down

docker-sync:
	./scripts/docker-sync-latest.sh

docker-sync-once:
	./scripts/docker-sync-latest.sh --once

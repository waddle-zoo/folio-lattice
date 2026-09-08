.PHONY: install format lint typecheck test test-unit test-e2e hosted-e2e hosted-auth-adversarial hosted-auth-e2e browser-test check docker-build docker-up docker-test docker-down docker-sync docker-sync-once

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

hosted-e2e:
	FOLIO_GATE_COMMAND="$${FOLIO_GATE_COMMAND:-make hosted-e2e}" \
	FOLIO_EVIDENCE_PATH="$${FOLIO_EVIDENCE_PATH:-/tmp/folio-lattice-fl-urj-5.2.json}" \
	uv run python tests/hyperset_hosted_consumer.py

hosted-auth-adversarial:
	uv run pytest -q tests/adversarial/test_hosted_auth.py

hosted-auth-e2e:
	FOLIO_EVIDENCE_PATH="$${FOLIO_EVIDENCE_PATH:-/tmp/folio-lattice-fl-urj-5.2.json}" \
	uv run python tests/hosted_auth_target.py e2e

browser-test:
	uv run pytest -q tests/test_browser_e2e.py

check: lint typecheck test

docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-test:
	uv run python tests/test_docker_e2e.py

docker-down:
	docker compose down

docker-sync:
	./scripts/docker-sync-latest.sh

docker-sync-once:
	./scripts/docker-sync-latest.sh --once

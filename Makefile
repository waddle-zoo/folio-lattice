.PHONY: install format lint typecheck test test-unit test-e2e browser-test check docker-build docker-up docker-test docker-down

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

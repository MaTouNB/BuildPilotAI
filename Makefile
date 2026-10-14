UV_CACHE_DIR ?= /tmp/buildpilot-uv-cache
export UV_CACHE_DIR
export npm_config_cache := /tmp/buildpilot-npm-cache
COMPOSE = docker compose --env-file .env -f infra/docker-compose.yml

.PHONY: setup db-up db-down db-migrate dev check check-db check-m02
setup:
	python3 scripts/setup_env.py
	cd backend && uv sync --locked
	cd frontend && npm ci

db-up:
	$(COMPOSE) up -d --wait

db-down:
	$(COMPOSE) down

dev:
	python3 scripts/dev.py

check:
	cd backend && uv run --locked ruff check --config pyproject.toml . ../scripts && uv run --locked ruff format --check --config pyproject.toml . ../scripts && uv run --locked pytest -q -m "not postgres"
	cd frontend && npm run lint && npm run typecheck && npm run test && npm run build

db-migrate:
	cd backend && uv run --locked alembic upgrade head

check-db:
	cd backend && BUILDPILOT_TEST_POSTGRES=1 uv run --locked pytest -q -m postgres

check-m02:
	cd backend && PYTHONPATH=. uv run --locked python ../scripts/check_m02.py

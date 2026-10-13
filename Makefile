UV_CACHE_DIR ?= /tmp/buildpilot-uv-cache
export UV_CACHE_DIR
export npm_config_cache := /tmp/buildpilot-npm-cache
COMPOSE = docker compose --env-file .env -f infra/docker-compose.yml

.PHONY: setup db-up db-down dev check
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
	cd backend && uv run --locked ruff check --config pyproject.toml . ../scripts && uv run --locked ruff format --check --config pyproject.toml . ../scripts && uv run --locked pytest -q
	cd frontend && npm run lint && npm run typecheck && npm run test && npm run build

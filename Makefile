SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
.PHONY: help bootstrap install lint format-check typecheck test test-api test-web e2e integration \
	quality dev down logs migrate seed-demo reset-demo compose-config compose-prod-config \
	compose-up compose-down compose-smoke smoke backup restore deploy shellcheck clean

COMPOSE := docker compose
PROD_COMPOSE := docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml

help: ## Show available targets
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / {printf "%-22s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

bootstrap: ## Validate tools and install locked local dependencies
	./scripts/bootstrap_local.sh

install: ## Install Python and web development dependencies
	cd apps/api && uv sync --all-extras --locked
	npm --prefix apps/web ci

lint: ## Run backend and frontend linters
	cd apps/api && uv run ruff check app tests
	npm --prefix apps/web run lint
	$(MAKE) shellcheck

format-check: ## Check formatting without changing files
	cd apps/api && uv run ruff format --check app tests
	npm --prefix apps/web run format:check

typecheck: ## Run strict backend and frontend type checking
	cd apps/api && uv run mypy app
	npm --prefix apps/web run typecheck

test: test-api test-web ## Run unit and integration tests

test-api: ## Run backend tests with branch coverage
	cd apps/api && uv run pytest --cov=app --cov-branch --cov-fail-under=85 --cov-report=term-missing --cov-report=xml

test-web: ## Run frontend unit tests with coverage
	npm --prefix apps/web run test:coverage

e2e: ## Run the current desktop/mobile Playwright browser checks
	npm --prefix apps/web run test:e2e

integration: ## Exercise Postgres-backed OTP, sync, chat, research, and report flows
	./scripts/local_e2e.sh

quality: format-check lint typecheck test compose-config ## Run the local merge gate

dev: ## Build and start the credential-free local stack
	$(COMPOSE) up -d --build postgres redis
	$(COMPOSE) build api
	$(COMPOSE) --profile tools run --rm migrate
	$(COMPOSE) up -d --build api worker scheduler web caddy

down: ## Stop the local stack without deleting durable volumes
	$(COMPOSE) --profile async --profile tools down --remove-orphans

compose-up: dev ## Build and start the credential-free local stack

compose-down: down ## Stop the local stack without deleting durable volumes

logs: ## Follow application logs
	$(COMPOSE) logs -f --tail=200 caddy web api worker scheduler

migrate: ## Apply migrations in a one-shot container
	$(COMPOSE) --profile tools run --rm migrate

seed-demo: ## Seed synthetic demo data when the database is empty
	./scripts/seed_demo.sh

reset-demo: ## Reset and reseed demo data (local/test only)
	RESET_DEMO_CONFIRM=reset-local-demo ./scripts/seed_demo.sh --reset

compose-config: ## Validate and render local Compose configuration
	$(COMPOSE) config --quiet

compose-prod-config: ## Validate production Compose using .env.production
	./scripts/validate_env.sh .env.production production
	$(PROD_COMPOSE) config --quiet

compose-smoke: ## Start the stack, migrate, seed and run cross-surface smoke checks
	./scripts/compose_smoke.sh

smoke: ## Smoke-test an already running stack
	./scripts/smoke_test.sh

backup: ## Create a database/export backup in the backups volume
	$(COMPOSE) --profile tools run --rm backup

restore: ## Restore BACKUP_PATH after explicit confirmation
	@test -n "$(BACKUP_PATH)" || (echo "Set BACKUP_PATH" >&2; exit 2)
	$(COMPOSE) --profile tools run --rm \
		-e RESTORE_CONFIRM=restore-bumpabestie \
		-e BACKUP_PATH="$(BACKUP_PATH)" \
		--entrypoint /usr/local/bin/restore.sh backup

deploy: ## Deploy the immutable production release selected in .env.production
	./scripts/deploy.sh

shellcheck: ## Validate shell syntax and run shellcheck when installed
	./scripts/validate_shell.sh

clean: ## Remove generated local test artifacts, not volumes
	rm -rf apps/web/.next apps/web/coverage apps/web/playwright-report apps/web/test-results apps/api/.coverage apps/api/coverage.xml

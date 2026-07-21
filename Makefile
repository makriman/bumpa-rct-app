SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
.PHONY: help bootstrap install lint format-check typecheck build bundle-isolation test test-api test-web test-ops react-doctor lighthouse e2e e2e-linux temporary-auth-e2e integration load-failure \
	quality dev down logs migrate seed-demo reset-demo compose-config compose-prod-config \
	compose-up compose-down compose-smoke smoke backup restore deploy shellcheck production-contract \
	api-contract api-contract-check clean

COMPOSE := docker compose
PROD_COMPOSE := docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml

help: ## Show available targets
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / {printf "%-22s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

bootstrap: ## Validate tools and install locked local dependencies
	./scripts/bootstrap_local.sh

install: ## Install Python and web development dependencies
	cd apps/api && uv sync --all-extras --locked
	npm --prefix apps/web ci
	npm --prefix apps/admin-web ci
	npm --prefix apps/research-web ci

lint: ## Run backend and frontend linters
	cd apps/api && uv run ruff check app tests
	npm --prefix apps/web run lint
	npm --prefix apps/admin-web run lint
	npm --prefix apps/research-web run lint
	$(MAKE) shellcheck

format-check: ## Check formatting without changing files
	cd apps/api && uv run ruff format --check app tests
	npm --prefix apps/web run format:check
	npm --prefix apps/admin-web run format:check
	npm --prefix apps/research-web run format:check

typecheck: ## Run strict backend and frontend type checking
	cd apps/api && uv run mypy app
	npm --prefix apps/web run typecheck
	npm --prefix apps/admin-web run typecheck
	npm --prefix apps/research-web run typecheck

build: ## Build all three production frontends
	npm --prefix apps/web run build
	npm --prefix apps/admin-web run build
	npm --prefix apps/research-web run build

bundle-isolation: build ## Prove emitted client bundles and route manifests stay surface-isolated
	node scripts/check_frontend_bundle_isolation.mjs

test: test-api test-web test-ops ## Run unit and integration tests

test-api: ## Run backend tests with branch coverage
	cd apps/api && uv run pytest --cov=app --cov-branch --cov-fail-under=85 --cov-report=term-missing --cov-report=xml

test-web: ## Run frontend unit tests with coverage
	npm --prefix apps/web run test:coverage
	npm --prefix apps/admin-web run test:coverage
	npm --prefix apps/research-web run test:coverage

test-ops: ## Run host operational-control unit tests
	python3 -m unittest discover -s scripts/tests -p 'test_*.py'

react-doctor: ## Require zero React Doctor errors or warnings across all frontends
	npx --yes react-doctor@0.8.3 . --project apps/web,apps/admin-web,apps/research-web --yes --blocking warning --no-telemetry

lighthouse: build ## Enforce representative consumer Lighthouse budgets
	npm --prefix apps/web run test:lighthouse

e2e: ## Run desktop/mobile Playwright checks for all three products
	npm --prefix apps/web run test:e2e
	npm --prefix apps/admin-web run test:e2e
	npm --prefix apps/research-web run test:e2e

e2e-linux: ## Run production-build browser checks in the pinned Linux Playwright image
	docker run --rm --init --platform linux/amd64 \
		-e CI=1 \
		-v "$(CURDIR):/work" \
		-v bumpabestie-playwright-web-node-modules:/work/apps/web/node_modules \
		-v bumpabestie-playwright-admin-node-modules:/work/apps/admin-web/node_modules \
		-v bumpabestie-playwright-research-node-modules:/work/apps/research-web/node_modules \
		-w /work mcr.microsoft.com/playwright:v1.61.1-noble@sha256:5b8f294aff9041b7191c34a4bab3ac270157a28774d4b0660e9743297b697e48 \
		bash -lc 'set -Eeuo pipefail; for app in web admin-web research-web; do cd "/work/apps/$$app"; npm ci; npm run test:e2e; done'

temporary-auth-e2e: ## Exercise the real provider-free browser auth path in disposable Compose
	@test -n "$(TEMPORARY_AUTH_E2E_PIN)" || (echo "Set TEMPORARY_AUTH_E2E_PIN" >&2; exit 2)
	@TEMPORARY_AUTH_E2E_PIN="$(TEMPORARY_AUTH_E2E_PIN)" ./scripts/temporary_web_auth_e2e.sh

integration: ## Exercise Postgres-backed OTP, sync, chat, research, and report flows
	./scripts/local_e2e.sh

load-failure: ## Run sealed chat/sync pressure, disk alert, webhook load, and restart drills
	python3 tests/load_failure/run.py

quality: format-check lint typecheck test api-contract-check compose-config react-doctor lighthouse bundle-isolation production-contract ## Run the local merge gate

api-contract: ## Regenerate the redacted OpenAPI and TypeScript API contracts
	cd apps/api && uv run python -m app.openapi_contract generate
	cd apps/web && npm run api-contract:generate

api-contract-check: ## Fail when FastAPI or generated TypeScript contracts have drifted
	cd apps/api && uv run python -m app.openapi_contract check
	./scripts/check_api_contract.sh

dev: ## Build and start the credential-free local stack
	$(COMPOSE) up -d --build postgres redis
	$(COMPOSE) build api
	$(COMPOSE) --profile tools run --rm migrate
	$(COMPOSE) up -d --build api worker scheduler web admin-web research-web caddy

down: ## Stop the local stack without deleting durable volumes
	$(COMPOSE) --profile async --profile tools down --remove-orphans

compose-up: dev ## Build and start the credential-free local stack

compose-down: down ## Stop the local stack without deleting durable volumes

logs: ## Follow application logs
	$(COMPOSE) logs -f --tail=200 caddy web admin-web research-web api worker scheduler

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

production-contract: ## Validate immutable production environment and Compose contracts
	./scripts/test_production_contract.sh

compose-smoke: ## Start the stack, migrate, seed and run cross-surface smoke checks
	./scripts/compose_smoke.sh

smoke: ## Smoke-test an already running stack
	./scripts/smoke_test.sh

backup: ## Create a database/export backup in the backups volume
	$(COMPOSE) --profile tools run --rm --no-deps backup

restore: ## Restore BACKUP_PATH after explicit confirmation
	@test -n "$(BACKUP_PATH)" || (echo "Set BACKUP_PATH" >&2; exit 2)
	$(COMPOSE) --profile restore run --rm --no-deps \
		-e RESTORE_CONFIRM=restore-bumpabestie \
		-e BACKUP_PATH="$(BACKUP_PATH)" \
		restore

deploy: ## Promote an immutable production release through the installed stable coordinator
	@test -n "$(REVISION)" -a -n "$(INFRA_IMAGE_TAG)" -a -n "$(API_IMAGE)" \
		-a -n "$(WEB_IMAGE)" -a -n "$(ADMIN_WEB_IMAGE)" -a -n "$(RESEARCH_WEB_IMAGE)" \
		-a -n "$(CADDY_IMAGE)" -a -n "$(POSTGRES_IMAGE)" \
		-a -n "$(BACKUP_IMAGE)" -a -n "$(HERMES_IMAGE)" || \
		(echo "Set REVISION, INFRA_IMAGE_TAG and all eight immutable image references" >&2; exit 2)
	/usr/local/sbin/bumpabestie-promote \
		"$(REVISION)" "$(INFRA_IMAGE_TAG)" "$(API_IMAGE)" "$(WEB_IMAGE)" "$(ADMIN_WEB_IMAGE)" "$(RESEARCH_WEB_IMAGE)" \
		"$(CADDY_IMAGE)" "$(POSTGRES_IMAGE)" "$(BACKUP_IMAGE)" "$(HERMES_IMAGE)"

shellcheck: ## Validate shell syntax and run shellcheck when installed
	./scripts/validate_shell.sh

clean: ## Remove generated local test artifacts, not volumes
	rm -rf apps/web/.next apps/web/coverage apps/web/playwright-report apps/web/test-results \
		apps/web/lighthouse-reports apps/admin-web/.next apps/admin-web/coverage \
		apps/admin-web/playwright-report apps/admin-web/test-results apps/research-web/.next \
		apps/research-web/coverage apps/research-web/playwright-report apps/research-web/test-results \
		apps/api/.coverage apps/api/coverage.xml

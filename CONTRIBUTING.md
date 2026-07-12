# Contributing

Use a short-lived branch and keep each change reviewable. Do not commit directly to
`main` once branch protection is enabled.

## Setup

1. Install Node 22, Python 3.12, `uv`, Docker and Compose.
2. Copy `.env.example` to `.env`.
3. Run `make bootstrap` and `make quality`.

## Change contract

- Add tests for behavior changes and regression tests for bug fixes.
- Add an Alembic migration for schema changes; never mutate an applied migration.
- Regenerate checked-in API contracts and clients when the OpenAPI schema changes.
- Update documentation and `docs/verification.md` for changed acceptance claims.
- Use synthetic data in tests, screenshots, traces and fixtures.
- Never weaken tenant checks, RLS, redaction or audit logging to make a test pass.

Commits should be focused and use an imperative subject. Pull requests must explain
the user outcome, risks, migration/rollback plan and verification evidence.

## Required checks

`make quality` is the local equivalent of the required CI checks: formatting and
lint, strict typing, unit/integration tests, Compose rendering and shell validation.
E2E, accessibility and visual checks are required for user-facing changes.

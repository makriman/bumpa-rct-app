# Operations Runbook

## Triage order

1. Establish impact: affected host, tenant, channel, start time and correlation ID.
2. Check `docker compose ps`, host disk/memory and recent deploy revision.
3. Inspect structured logs by correlation ID; never paste raw payloads into tickets.
4. Mitigate safely (pause queue/provider adapter, revoke session, suspend tenant).
5. Preserve redacted evidence, communicate status and document follow-up actions.

## Health and logs

```bash
docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml ps
docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml logs --since=30m api worker scheduler web caddy
curl -fsS https://api.bumpabestie.com/health
```

Never use `docker compose down -v` in production. Never expose Postgres, Redis or a
Hermes port to troubleshoot.

## Bumpa sync failure

Inspect the sync run status, HTTP/body error and rate-limit metadata. Retry only when
the idempotency/retry policy permits. Authentication errors require credential
rotation; unavailable metrics remain unavailable rather than zero. Escalate repeated
timeouts/rate limits without increasing concurrency blindly.

## WhatsApp failure

Check webhook health, signature rejection counts, dedupe status, token/phone ID,
template approval, service window, opt-out state and delivery callbacks. Replaying a
captured webhook requires a sanitized fixture in a non-production environment; do
not replay raw production payloads.

## Agent failure

Disable or circuit-break the affected profile, verify health/auth/profile mapping
and serve a clear unavailable response. Do not silently route a tenant to another
profile. Restore profile state only from a verified backup and rerun the isolation
canary.

## Queue or datastore failure

This procedure becomes active after the Redis queue adapter, bounded retries and
dead-letter handling are implemented. The current worker/scheduler are local idle
shells and production startup intentionally refuses them. For datastore failures,
restore Redis/Postgres first, confirm migrations and verify idempotency before any
manual replay. After a disk-full event, reserve space, stop writers, repair
underlying storage and verify database integrity before resuming.

## Backup and restore drill

Run `make backup`, copy the result off-host through the configured backup tool, and
alert if either step fails. At least monthly, restore into isolated new volumes:

1. Verify `SHA256SUMS` and manifest.
2. Restore Postgres, exports and the reserved Hermes state volume using explicit
   `RESTORE_CONFIRM`.
3. Apply migrations and start the candidate stack.
4. Compare expected row counts and artifact checksums.
5. Log in as synthetic admin/researcher and route a known synthetic tenant chat.
6. Record duration, revision, backup ID and results in `docs/verification.md` or the
   external evidence system.

## Suspected secret or PII exposure

Stop distribution first, revoke/rotate every affected credential, preserve access
evidence, remove public artifacts, determine tenants/data/time window, and follow the
incident notification policy. Do not rely on deleting logs or rewriting git history.

# Durable async runtime

## Contract

PostgreSQL is the system of record for accepted jobs, retry state and terminal
outcomes. Redis carries only job IDs as wake-up notifications. A Redis restart or a
duplicate notification therefore cannot lose or duplicate accepted business work.

Producers call `app.jobs.enqueue_job` inside the same SQLAlchemy transaction as the
business mutation or durable provider inbox row:

```python
job, created = enqueue_job(
    db,
    kind="provider.operation",
    payload={"record_id": record.id},
    idempotency_key=f"provider:{record.external_id}",
    tenant_id=record.tenant_id,
)
db.commit()
```

The payload must be a bounded JSON envelope of database identifiers and safe input.
Never enqueue credentials, authorization headers, raw webhook bodies or unnecessary
PII. Idempotency is enforced by `(queue_name, idempotency_key)`.

Worker handlers are registered in `app/jobs/handlers.py` and call service/provider
modules rather than FastAPI routes:

```python
@register_handler("provider.operation")
def provider_operation(db: Session, job: AsyncJob) -> dict:
    return provider_service.process(db, record_id=str(job.payload["record_id"]))
```

Raise `PermanentJobError` for a validated non-retriable condition. Other exceptions
receive bounded exponential backoff. Persisted error text contains only the exception
type and a fixed safe description, never the exception message.

## State and failure behavior

The durable job states are `pending`, `queued`, `running`, `retry`, `succeeded`,
`dead_letter` and `cancelled`. Job creation and its outbox row are transactional. The
scheduler locks due outbox/job rows, publishes the job ID, and marks the handoff
dispatched. The worker claims only a non-terminal due row under a database lock, so a
duplicate Redis delivery is ignored after the first successful claim.

If a worker lease remains `running` beyond `ASYNC_STALE_LOCK_SECONDS`, the scheduler
returns it to `retry` or moves it to `dead_letter` when its attempt budget is exhausted.
Dead-letter rows are durable and never automatically replayed. `replay_dead_letter`
is an explicit service operation; an operator must first identify the cause, confirm
the provider-side idempotency boundary, record a reason/change reference, and monitor
the replacement attempt. Never edit job status directly or replay a raw webhook.

## Health and operations

Worker and scheduler refresh separate Redis heartbeat keys with TTL. Docker health
checks call `python -m app.jobs.health worker|scheduler`; a stale heartbeat or Redis
failure makes the container unhealthy. `RedisWakeQueue.health_snapshot()` provides a
safe readiness payload containing Redis state, worker/scheduler heartbeat state and
the count of queued wake-up IDs. PostgreSQL job status counts remain the authoritative
backlog and dead-letter view.

Production validation requires the async runtime and validates heartbeat, poll,
batch, retry and stale-lock bounds. Deployment starts and waits for both containers,
records their exact API image reference, and includes them in rollback quiescing.
During an incident:

1. Disable or pause the producer boundary before stopping workers.
2. Preserve `async_jobs`, `job_outbox` and Redis/container logs; do not delete queue
   keys as a recovery shortcut.
3. Restart Redis/scheduler/worker and allow stale-lease recovery to run.
4. Compare pending/retry/running/dead-letter counts and the oldest due timestamp.
5. Replay a dead letter only after the cause and external idempotency behavior are
   understood and an operator owns the canary.

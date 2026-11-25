from collections import defaultdict
from queue import Empty, Queue
from threading import Event, Thread
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import AsyncJob
from app.jobs import worker
from app.jobs.runtime import (
    AsyncRuntimeConfig,
    RedisWakeQueue,
    dispatch_due_jobs,
    enqueue_job,
    register_handler,
)


class RedisLists:
    def __init__(self):
        self.lists = defaultdict(Queue)
        self.values = {}

    def rpush(self, key, value):
        self.lists[key].put(value)

    def blpop(self, keys, timeout):
        try:
            return [keys[0], self.lists[keys[0]].get(timeout=0.02)]
        except Empty:
            return None

    def set(self, key, value, *, ex):
        self.values[key] = value


def configuration():
    return AsyncRuntimeConfig(
        enabled=True,
        redis_url="redis://unused",
        queue_name="default",
        queue_key_prefix="test",
        heartbeat_ttl_seconds=45,
        pop_timeout_seconds=1,
        scheduler_interval_seconds=0.01,
        dispatch_batch_size=100,
        redispatch_seconds=60,
        retry_base_seconds=1,
        retry_max_seconds=10,
        stale_lock_seconds=300,
    )


def test_whatsapp_finishes_while_bulk_handler_is_still_blocked(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'jobs.db'}", connect_args={"check_same_thread": False}
    )
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(worker, "running", True)
    config = configuration()
    redis = RedisLists()
    bulk_queue = RedisWakeQueue(config, redis)
    fast_queue = RedisWakeQueue(config, redis, interactive=True)
    started, release, answered = Event(), Event(), Event()
    token = uuid4().hex

    @register_handler(f"test.slow_{token}")
    def slow(_session, _job):
        started.set()
        assert release.wait(10)
        return {"done": True}

    @register_handler(f"whatsapp.test_{token}")
    def reply(_session, _job):
        answered.set()
        return {"done": True}

    with factory() as db:
        bulk, _ = enqueue_job(db, kind=f"test.slow_{token}", payload={}, idempotency_key="bulk")
        fast, _ = enqueue_job(db, kind=f"whatsapp.test_{token}", payload={}, idempotency_key="fast")
        ids = bulk.id, fast.id
        db.commit()
        assert dispatch_due_jobs(db, bulk_queue, limit=10) == 2
    threads = [
        Thread(target=worker.work_loop, args=(config, "bulk", bulk_queue)),
        Thread(target=worker.work_loop, args=(config, "fast", fast_queue)),
    ]
    try:
        threads[0].start()
        assert started.wait(3)
        threads[1].start()
        assert answered.wait(3), "WhatsApp was starved by a bulk import"
        assert not release.is_set()
    finally:
        release.set()
        worker.running = False
        for thread in threads:
            if thread.ident is not None:
                thread.join(5)
    assert all(not thread.is_alive() for thread in threads)
    with factory() as db:
        assert list(db.scalars(select(AsyncJob.status).where(AsyncJob.id.in_(ids)))) == [
            "succeeded",
            "succeeded",
        ]
    engine.dispose()


def test_rolling_upgrade_restores_old_whatsapp_ids_without_recreating_jobs(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    config = configuration()
    redis = RedisLists()
    queue = RedisWakeQueue(config, redis)
    with factory() as db:
        job, _ = enqueue_job(
            db, kind="whatsapp.process_webhook", payload={}, idempotency_key="legacy"
        )
        job.status = "queued"
        job_id = job.id
        db.commit()
    redis.rpush(config.queue_key, job_id)
    assert worker.restore_interactive_wakeups(config, queue) == 1
    assert RedisWakeQueue(config, redis, interactive=True).pop() == job_id
    with factory() as db:
        assert db.scalar(select(AsyncJob.id)) == job_id
        assert db.get(AsyncJob, job_id).queue_name == "default"
    engine.dispose()

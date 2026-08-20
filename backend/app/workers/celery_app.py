"""Celery application.

Queues are split by family so a backlog of exports cannot starve captioning,
and each is banded by plan priority — see docs/03-backend-architecture.md §5.3.
Workers drain the highest band of their queue first.
"""

from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "zipzop",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # A worker that dies mid-task must not lose the job. Acknowledging late
    # means the broker redelivers it instead.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Long media tasks: never let one worker hoard queued work.
    worker_prefetch_multiplier=1,
    result_expires=86_400,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    # Priority bands within each queue — docs/03-backend-architecture.md §5.3.
    # Redis has no native priority, so Celery emulates it with one sub-queue per
    # step and drains them in order; the steps are exactly the four plans'
    # `queue_priority` values, so `apply_async(priority=job.priority)` needs no
    # translation. `sep` and the strategy are Celery's documented Redis
    # settings, not a guess.
    broker_transport_options={
        "priority_steps": [0, 10, 20, 30],
        "sep": ":",
        "queue_order_strategy": "priority",
    },
    task_routes={
        "app.workers.tasks.ingest.*": {"queue": "ingest"},
        "app.workers.tasks.analysis.*": {"queue": "analysis"},
        "app.workers.tasks.render.*": {"queue": "render"},
        "app.workers.tasks.inference.*": {"queue": "inference"},  # phase 2
        "app.workers.tasks.billing.*": {"queue": "billing"},
    },
)

# Scheduled work. Exactly one beat instance may run — a second one would grant
# the monthly allowance twice.
celery_app.conf.beat_schedule = {
    # Primary renewal path is the payment provider's webhook; this is the
    # safety net for webhooks that never arrive, and the only path free users
    # have (docs/03-backend-architecture.md §8.4).
    "renewal-sweep": {
        "task": "app.workers.tasks.billing.sweep_renewals",
        "schedule": crontab(minute=5),  # hourly, at :05
    },
    # If this ever reports drift, a transaction boundary is wrong.
    "ledger-reconciliation": {
        "task": "app.workers.tasks.billing.reconcile_ledger",
        "schedule": crontab(hour=3, minute=0),
    },
    "storage-lifecycle": {
        "task": "app.workers.tasks.ingest.sweep_expired_media",
        "schedule": crontab(hour=4, minute=0),
    },
}

from celery import Celery

from app.config import settings

celery_app = Celery(
    "crawlox",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,  # one task at a time per worker process
    task_acks_late=True,           # ack only after task completes — safe for retries
)

# autodiscover tasks in app/workers/tasks.py
celery_app.autodiscover_tasks(["app.workers"])

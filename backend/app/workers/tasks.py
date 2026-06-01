import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.workers.celery_app import celery_app


def _get_async_session() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(settings.database_url)
    return async_sessionmaker(engine, expire_on_commit=False)


@celery_app.task(bind=True, name="tasks.noop", max_retries=3)
def noop_task(self, task_id: str) -> dict:
    """
    No-op task used to verify the Celery pipeline end-to-end.
    Transitions a Task row from pending → completed.
    """
    async def _run():
        from app.db.models import Task

        AsyncSessionLocal = _get_async_session()
        async with AsyncSessionLocal() as db:
            from sqlalchemy.future import select
            result = await db.execute(select(Task).where(Task.id == uuid.UUID(task_id)))
            task = result.scalar_one_or_none()
            if not task:
                return {"error": f"Task {task_id} not found"}

            task.status = "completed"
            task.started_at = datetime.now(timezone.utc)
            task.completed_at = datetime.now(timezone.utc)
            task.scraped_data = {"message": "noop task completed successfully"}
            await db.commit()

        return {"task_id": task_id, "status": "completed"}

    return asyncio.run(_run())

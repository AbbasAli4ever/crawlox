import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.workers.celery_app import celery_app


def _get_async_session() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(settings.database_url)
    return async_sessionmaker(engine, expire_on_commit=False)


@celery_app.task(bind=True, name="tasks.scrape", max_retries=0)
def scrape_task(self, task_id: str) -> dict:
    """
    Full scrape task. Reads config from the Task row, runs Playwright pipeline,
    writes results back. Status transitions: analyzing → scraping → completed | failed.
    """
    async def _run():
        from sqlalchemy.future import select
        from app.db.models import Task

        AsyncSessionLocal = _get_async_session()
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Task).where(Task.id == uuid.UUID(task_id)))
            task = result.scalar_one_or_none()
            if not task:
                return {"error": f"Task {task_id} not found"}

            task.status = "scraping"
            task.started_at = datetime.now(timezone.utc)
            await db.commit()

        try:
            from app.scraper.runner import run_scrape
            from app.scraper.session_store import load_cookies, save_cookies

            task_row_data = {}
            user_id = None
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Task).where(Task.id == uuid.UUID(task_id)))
                task = result.scalar_one_or_none()
                task_row_data = {
                    "url": task.url,
                    "custom_fields": task.custom_fields or {},
                }
                user_id = task.user_id

            # Load any previously saved cookies for this domain
            cookies = []
            async with AsyncSessionLocal() as db:
                cookies = await load_cookies(db, user_id, task_row_data["url"])

            scrape_result = await run_scrape(
                url=task_row_data["url"],
                selector_config=task_row_data["custom_fields"].get("selector_config", {}),
                pagination_type=task_row_data["custom_fields"].get("pagination_type", "none"),
                pagination_config=task_row_data["custom_fields"].get("pagination_config"),
                max_items=task_row_data["custom_fields"].get("max_items", 500),
                timeout_seconds=task_row_data["custom_fields"].get("timeout_seconds", 300),
                cookies=cookies or None,
            )

            # Persist cookies from this scrape for future reuse
            final_cookies = scrape_result.pop("cookies", [])
            user_agent = scrape_result.pop("user_agent", None)
            if final_cookies:
                async with AsyncSessionLocal() as db:
                    await save_cookies(
                        db, user_id, task_row_data["url"],
                        final_cookies, user_agent=user_agent,
                    )

            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Task).where(Task.id == uuid.UUID(task_id)))
                task = result.scalar_one_or_none()
                task.status = "completed"
                task.completed_at = datetime.now(timezone.utc)
                task.scraped_data = scrape_result
                task.total_items_scraped = scrape_result.get("total_items", 0)
                await db.commit()

            return {"task_id": task_id, "status": "completed", "total_items": scrape_result.get("total_items", 0)}

        except asyncio.TimeoutError:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Task).where(Task.id == uuid.UUID(task_id)))
                task = result.scalar_one_or_none()
                if task:
                    task.status = "failed"
                    task.completed_at = datetime.now(timezone.utc)
                    task.scraped_data = {"error": "Scrape timed out after 5 minutes"}
                    await db.commit()
        except Exception as exc:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Task).where(Task.id == uuid.UUID(task_id)))
                task = result.scalar_one_or_none()
                if task:
                    task.status = "failed"
                    task.completed_at = datetime.now(timezone.utc)
                    task.scraped_data = {"error": str(exc)}
                    await db.commit()
            raise

    return asyncio.run(_run())


@celery_app.task(bind=True, name="tasks.playwright_smoke", max_retries=2)
def playwright_smoke_task(self, url: str) -> dict:
    """Run a Playwright smoke test: open URL, screenshot, return title."""
    from app.scraper.smoke import run_smoke_test
    return run_smoke_test(url)


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

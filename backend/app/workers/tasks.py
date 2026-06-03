import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.workers.celery_app import celery_app

logger = logging.getLogger("crawlox.worker")


def _get_async_session() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(settings.database_url)
    return async_sessionmaker(engine, expire_on_commit=False)


@celery_app.task(bind=True, name="tasks.analyze", max_retries=0)
def analyze_task(self, task_id: str) -> dict:
    """
    Render a page with Playwright, run LLM analysis, write result to task row.
    Status: pending → analyzing → completed | failed
    """
    async def _run():
        from sqlalchemy.future import select
        from app.db.models import Task
        from app.scraper.page_capture import capture_page
        from app.llm.router import get_llm_router
        from app.scraper.session_store import load_cookies

        AsyncSessionLocal = _get_async_session()

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Task).where(Task.id == uuid.UUID(task_id)))
            task = result.scalar_one_or_none()
            if not task:
                return {"error": f"Task {task_id} not found"}
            task.status = "analyzing"
            task.started_at = datetime.now(timezone.utc)
            url = task.url
            user_id = task.user_id
            await db.commit()

        try:
            # Load cookies for this domain
            async with AsyncSessionLocal() as db:
                cookies = await load_cookies(db, user_id, url)

            # Render page
            page_data = await capture_page(url, cookies=cookies or None)

            # Run LLM analysis
            router = get_llm_router()
            analysis = await router.analyze(page_data)

            # Persist result
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Task).where(Task.id == uuid.UUID(task_id)))
                task = result.scalar_one_or_none()
                task.status = "completed"
                task.completed_at = datetime.now(timezone.utc)
                task.analysis_result = {
                    "website_type": analysis.website_type,
                    "framework": analysis.framework,
                    "has_infinite_scroll": analysis.has_infinite_scroll,
                    "pagination_type": analysis.pagination_type,
                    "data_structure": {
                        "container_selector": analysis.data_structure.container_selector,
                        "fields": [
                            {"name": f.name, "selector": f.selector,
                             "type": f.type, "required": f.required}
                            for f in analysis.data_structure.fields
                        ],
                    },
                    "captcha_detected": analysis.captcha_detected,
                    "captcha_type": analysis.captcha_type,
                    "anti_bot_detected": analysis.anti_bot_detected,
                    "recommended_delay_seconds": analysis.recommended_delay_seconds,
                    "recommended_proxy": analysis.recommended_proxy,
                    "provider": analysis.provider,
                    "latency_ms": analysis.latency_ms,
                    "fallback_reason": analysis.fallback_reason,
                }
                await db.commit()

            return {"task_id": task_id, "status": "completed", "provider": analysis.provider}

        except Exception as exc:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Task).where(Task.id == uuid.UUID(task_id)))
                task = result.scalar_one_or_none()
                if task:
                    task.status = "failed"
                    task.completed_at = datetime.now(timezone.utc)
                    task.analysis_result = {"error": str(exc)}
                    await db.commit()
            raise

    return asyncio.run(_run())


@celery_app.task(bind=True, name="tasks.ai_scrape", max_retries=0)
def ai_scrape_task(self, task_id: str) -> dict:
    """
    Full AI-powered scrape: capture → analyze → generate script → execute.
    Status transitions: pending → analyzing → scraping → completed | failed.
    Falls back to runner.py pipeline if script execution fails.
    """
    async def _run():
        from sqlalchemy.future import select
        from app.db.models import Task
        from app.scraper.page_capture import capture_page
        from app.scraper.session_store import load_cookies, save_cookies
        from app.llm.router import get_llm_router
        from app.llm.script_gen import generate_and_validate_script
        from app.scraper.sandbox import execute_script

        AsyncSessionLocal = _get_async_session()

        # Load task
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Task).where(Task.id == uuid.UUID(task_id)))
            task = result.scalar_one_or_none()
            if not task:
                return {"error": f"Task {task_id} not found"}
            url = task.url
            user_id = task.user_id
            custom_fields = task.custom_fields or {}
            max_items = custom_fields.get("max_items", 500)
            task.status = "analyzing"
            task.started_at = datetime.now(timezone.utc)
            await db.commit()

        try:
            # Step 1: load cookies
            async with AsyncSessionLocal() as db:
                cookies = await load_cookies(db, user_id, url)

            # Step 2: capture page
            page_data = await capture_page(url, cookies=cookies or None)

            # Step 3: AI analysis
            router = get_llm_router()
            analysis = await router.analyze(page_data)

            # Persist analysis result
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Task).where(Task.id == uuid.UUID(task_id)))
                task = result.scalar_one_or_none()
                task.analysis_result = {
                    "website_type": analysis.website_type,
                    "pagination_type": analysis.pagination_type,
                    "container_selector": analysis.data_structure.container_selector,
                    "fields": [
                        {"name": f.name, "selector": f.selector,
                         "type": f.type, "required": f.required}
                        for f in analysis.data_structure.fields
                    ],
                    "captcha_detected": analysis.captcha_detected,
                    "captcha_type": analysis.captcha_type,
                    "provider": analysis.provider,
                    "latency_ms": analysis.latency_ms,
                    "fallback_reason": analysis.fallback_reason,
                }
                task.status = "scraping"
                await db.commit()

            # Step 4: generate script (stored for reference, not sandbox-executed)
            try:
                script = await generate_and_validate_script(router, analysis, url)
                async with AsyncSessionLocal() as db:
                    result = await db.execute(select(Task).where(Task.id == uuid.UUID(task_id)))
                    task = result.scalar_one_or_none()
                    task.generated_script = script
                    await db.commit()
            except Exception as script_exc:
                logger.warning("Script generation failed (non-fatal): %s", script_exc)

            # Step 5: execute using our runner pipeline driven by the analysis
            from app.scraper.runner import run_scrape
            from dataclasses import asdict

            selector_config = {
                "container_selector": analysis.data_structure.container_selector,
                "fields": [
                    {"name": f.name, "selector": f.selector,
                     "type": f.type, "required": f.required}
                    for f in analysis.data_structure.fields
                ],
            }

            scrape_result = await run_scrape(
                url=url,
                selector_config=selector_config,
                pagination_type=analysis.pagination_type,
                pagination_config={
                    "max_pages": 5,
                    "next_selector": "a[rel='next'], li.next a, .next a",
                },
                max_items=max_items,
                timeout_seconds=custom_fields.get("timeout_seconds", 300),
                cookies=cookies or None,
            )

            items = scrape_result.get("items", [])

            # Step 6: persist cookies
            final_cookies = scrape_result.pop("cookies", [])
            final_ua = scrape_result.pop("user_agent", None)
            if final_cookies:
                async with AsyncSessionLocal() as db:
                    await save_cookies(db, user_id, url, final_cookies, user_agent=final_ua)

            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Task).where(Task.id == uuid.UUID(task_id)))
                task = result.scalar_one_or_none()
                task.status = "completed"
                task.completed_at = datetime.now(timezone.utc)
                task.scraped_data = {
                    **scrape_result,
                    "provider": analysis.provider,
                    "fallback_reason": analysis.fallback_reason,
                }
                task.total_items_scraped = len(items)
                await db.commit()

            return {
                "task_id": task_id,
                "status": "completed",
                "total_items": len(items),
                "provider": analysis.provider,
            }

        except asyncio.TimeoutError:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Task).where(Task.id == uuid.UUID(task_id)))
                task = result.scalar_one_or_none()
                if task:
                    task.status = "failed"
                    task.completed_at = datetime.now(timezone.utc)
                    task.scraped_data = {"error": "AI scrape timed out"}
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

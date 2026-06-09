import logging
import uuid

from playwright.async_api import Page

from app.captcha.detector import CaptchaContext
from app.captcha.injector import inject_captcha_solution, submit_form_after_captcha
from app.captcha.solver import (
    CaptchaSolver,
    FallbackToManual,
    ManualOnlySolver,
    SolveResult,
)
from app.captcha.store import (
    clear_captcha,
    get_captcha_context,
    save_captcha_context,
)

logger = logging.getLogger("crawlox.captcha")


async def handle_captcha(
    page: Page,
    ctx: CaptchaContext,
    solver: CaptchaSolver,
    task_id: str,
    user_id: uuid.UUID,
) -> bool:
    """
    Full CAPTCHA handling flow:
    1. Save context to Redis (so WS server can push it to client)
    2. Emit captcha:required via WS (caller does this after this function saves ctx)
    3. Wait for solution via solver (manual: waits for pubsub message)
    4. Inject solution into page
    5. Submit form
    6. Persist cookies to sessions table
    7. Clean up Redis keys

    Returns True if CAPTCHA was solved and scraping can continue, False otherwise.
    """
    # Save context so WS handler can retrieve it when client subscribes/reconnects
    await save_captcha_context(task_id, ctx)
    logger.info(
        "CAPTCHA handler started: type=%s task=%s",
        ctx.captcha_type, task_id,
    )

    # Solve — try the configured solver; on FallbackToManual, switch to manual flow
    try:
        result: SolveResult = await solver.solve(
            captcha_type=ctx.captcha_type,
            sitekey=ctx.sitekey,
            page_url=ctx.page_url,
            task_id=task_id,
        )
    except FallbackToManual as fb:
        logger.info(
            "Solver requested fallback to manual for task %s: %s", task_id, fb
        )
        manual = ManualOnlySolver()
        result = await manual.solve(
            captcha_type=ctx.captcha_type,
            sitekey=ctx.sitekey,
            page_url=ctx.page_url,
            task_id=task_id,
        )

    if not result.success or not result.token:
        logger.warning("CAPTCHA solve failed for task %s: %s", task_id, result.error)
        return False

    logger.info("CAPTCHA solved for task %s (cost=%.4f)", task_id, result.cost)

    # Inject token into page
    injected = await inject_captcha_solution(page, ctx.captcha_type, result.token)
    if not injected:
        logger.warning("Token injection failed for task %s", task_id)
        return False

    # Try to submit the form
    await submit_form_after_captcha(page)

    # Wait for page to settle after submission
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=10_000)
    except Exception:
        pass

    # Clean up Redis CAPTCHA keys
    await clear_captcha(task_id)

    return True


async def log_captcha_metric(
    task_id: str,
    user_id: uuid.UUID,
    captcha_type: str,
    solved_by: str,
    success: bool,
    cost: float = 0.0,
) -> None:
    """Write CAPTCHA solve attempt to usage_metrics."""
    try:
        from sqlalchemy.future import select
        from app.db.base import AsyncSessionLocal
        from app.db.models import Task, UsageMetric

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Task).where(Task.id == uuid.UUID(task_id))
            )
            task = result.scalar_one_or_none()
            if task:
                task.captcha_solved_by = solved_by

            metric = UsageMetric(
                id=uuid.uuid4(),
                user_id=user_id,
                task_id=uuid.UUID(task_id),
                captcha_solve_attempts=1,
                captcha_solve_successes=1 if success else 0,
                cost_2captcha=cost,
            )
            db.add(metric)
            await db.commit()
    except Exception as e:
        logger.warning("Failed to log captcha metric: %s", e)

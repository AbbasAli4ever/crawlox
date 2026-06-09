import asyncio
import logging
from datetime import datetime, timezone
from typing import Literal

from app.scraper.browser import get_browser, get_page, random_delay, safe_goto
from app.scraper.extractor import ContainerSchema, FieldSchema, extract_page
from app.scraper.pagination import (
    no_pagination,
    paginate_infinite_scroll,
    paginate_load_more,
    paginate_next_button,
    paginate_url_params,
)
from app.scraper.retry import with_retry

logger = logging.getLogger("crawlox.scraper")

PaginationType = Literal["url_params", "next_button", "load_more", "infinite_scroll", "none"]


def _build_schema(config: dict) -> ContainerSchema:
    """Convert a raw dict config into a ContainerSchema."""
    fields = [
        FieldSchema(
            name=f["name"],
            selector=f["selector"],
            field_type=f.get("type", "text"),
            required=f.get("required", True),
            attribute=f.get("attribute"),
        )
        for f in config.get("fields", [])
    ]
    return ContainerSchema(
        container_selector=config["container_selector"],
        fields=fields,
    )


class CaptchaDetectedError(Exception):
    """Raised when a CAPTCHA is detected mid-scrape. Caller should pause and handle."""
    def __init__(self, task_id: str, captcha_type: str, sitekey: str | None, page_url: str, screenshot_b64: str | None):
        self.task_id = task_id
        self.captcha_type = captcha_type
        self.sitekey = sitekey
        self.page_url = page_url
        self.screenshot_b64 = screenshot_b64
        super().__init__(f"CAPTCHA detected: {captcha_type} at {page_url}")


async def run_scrape(
    url: str,
    selector_config: dict,
    pagination_type: PaginationType = "none",
    pagination_config: dict | None = None,
    max_items: int = 500,
    timeout_seconds: int = 300,
    cookies: list | None = None,
    task_id: str | None = None,
    check_captcha: bool = True,
    captcha_handler=None,
) -> dict:
    """
    Full scrape pipeline:
      1. Launch stealth browser
      2. Navigate to URL
      3. Extract items page by page (pagination-aware)
      4. Return results + metadata

    CAPTCHA handling:
      - If captcha_handler is provided, it is awaited inline when a CAPTCHA is
        detected (keeping the browser alive). Signature:
          async def captcha_handler(page, captcha_ctx) -> bool  # True = solved, continue
        The CAPTCHA wait time is excluded from timeout_seconds (the human/2captcha
        may take minutes). If the handler returns False or is absent, raises
        CaptchaDetectedError.

    Raises asyncio.TimeoutError if timeout_seconds exceeded (excluding CAPTCHA waits).
    """
    schema = _build_schema(selector_config)
    pg_cfg = pagination_config or {}
    all_items: list[dict] = []
    final_cookies: list[dict] = []
    final_user_agent: str | None = None
    started_at = datetime.now(timezone.utc)

    async def _handle_or_raise(page, captcha_ctx):
        """Call the inline handler if present, else raise CaptchaDetectedError."""
        if captcha_handler is not None:
            solved = await captcha_handler(page, captcha_ctx)
            if solved:
                return  # CAPTCHA solved, continue scraping with same browser
        raise CaptchaDetectedError(
            task_id=task_id or "unknown",
            captcha_type=captcha_ctx.captcha_type,
            sitekey=captcha_ctx.sitekey,
            page_url=captcha_ctx.page_url,
            screenshot_b64=captcha_ctx.screenshot_b64,
        )

    async def _scrape():
        nonlocal final_cookies, final_user_agent
        async with get_browser() as browser:
            async with get_page(browser, cookies=cookies) as page:
                # Navigate with retry
                async def _goto():
                    ok = await safe_goto(page, url)
                    if not ok:
                        raise RuntimeError(f"Failed to load {url}")

                await with_retry(_goto, max_attempts=3, base_delay=2.0)
                await random_delay(1.0, 2.0)

                # Check for CAPTCHA after initial page load
                if check_captcha:
                    from app.captcha.detector import detect_and_capture
                    captcha_ctx = await detect_and_capture(page)
                    if captcha_ctx.detected:
                        await _handle_or_raise(page, captcha_ctx)

                # Choose paginator
                if pagination_type == "url_params":
                    paginator = paginate_url_params(
                        page,
                        base_url=url,
                        param=pg_cfg.get("param", "page"),
                        start=pg_cfg.get("start", 1),
                        max_pages=pg_cfg.get("max_pages", 20),
                        items_selector=schema.container_selector,
                    )
                elif pagination_type == "next_button":
                    paginator = paginate_next_button(
                        page,
                        next_selector=pg_cfg.get(
                            "next_selector",
                            "a[rel='next'], .next a, [aria-label='Next page']",
                        ),
                        max_pages=pg_cfg.get("max_pages", 20),
                    )
                elif pagination_type == "load_more":
                    paginator = paginate_load_more(
                        page,
                        button_selector=pg_cfg.get(
                            "button_selector",
                            "button.load-more, [data-action='load-more']",
                        ),
                        max_clicks=pg_cfg.get("max_clicks", 20),
                        items_selector=schema.container_selector,
                    )
                elif pagination_type == "infinite_scroll":
                    paginator = paginate_infinite_scroll(
                        page,
                        max_scrolls=pg_cfg.get("max_scrolls", 20),
                        items_selector=schema.container_selector,
                    )
                else:
                    paginator = no_pagination(page)

                async for _ in paginator:
                    # Check for CAPTCHA on each paginated page
                    if check_captcha:
                        from app.captcha.detector import detect_and_capture
                        captcha_ctx = await detect_and_capture(page)
                        if captcha_ctx.detected:
                            await _handle_or_raise(page, captcha_ctx)

                    items = await extract_page(page, schema, base_url=page.url)
                    all_items.extend(items)
                    # deduplicate by converting to frozenset of items
                    seen = set()
                    unique = []
                    for item in all_items:
                        key = str(sorted(item.items()))
                        if key not in seen:
                            seen.add(key)
                            unique.append(item)
                    all_items[:] = unique

                    if len(all_items) >= max_items:
                        logger.info("Reached max_items=%d, stopping pagination", max_items)
                        break

                    await random_delay(1.0, 3.0)

                # Capture cookies + UA for persistence after scrape completes
                final_cookies = await page.context.cookies()
                final_user_agent = await page.evaluate("navigator.userAgent")

    # When a CAPTCHA handler is active, the human/2captcha may take up to ~5 min
    # to solve. Extend the wall-clock timeout so the solve window isn't counted
    # against the scrape budget. The handler enforces its own 300s solve timeout.
    effective_timeout = timeout_seconds + (300 if captcha_handler is not None else 0)
    await asyncio.wait_for(_scrape(), timeout=effective_timeout)

    completed_at = datetime.now(timezone.utc)
    duration = (completed_at - started_at).total_seconds()

    return {
        "items": all_items[:max_items],
        "total_items": len(all_items[:max_items]),
        "pagination_type": pagination_type,
        "duration_seconds": round(duration, 2),
        "url": url,
        "cookies": final_cookies,
        "user_agent": final_user_agent,
    }


def run_scrape_sync(
    url: str,
    selector_config: dict,
    pagination_type: PaginationType = "none",
    pagination_config: dict | None = None,
    max_items: int = 500,
    timeout_seconds: int = 300,
    cookies: list | None = None,
) -> dict:
    """Sync wrapper for use in Celery tasks."""
    return asyncio.run(
        run_scrape(
            url=url,
            selector_config=selector_config,
            pagination_type=pagination_type,
            pagination_config=pagination_config,
            max_items=max_items,
            timeout_seconds=timeout_seconds,
            cookies=cookies,
        )
    )

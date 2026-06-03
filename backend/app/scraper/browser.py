import asyncio
import random
from contextlib import asynccontextmanager

from playwright.async_api import Browser, Page, async_playwright

from app.scraper.stealth import apply_stealth, new_stealth_context


@asynccontextmanager
async def get_browser():
    """Launch a Chromium browser, yield it, then close it."""
    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",   # avoids /dev/shm OOM in Docker
                "--disable-gpu",
                "--no-first-run",
                "--no-zygote",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        try:
            yield browser
        finally:
            await browser.close()


@asynccontextmanager
async def get_page(browser: Browser, cookies: list | None = None) -> Page:
    """Create a stealth context + page, yield the page, then close the context."""
    context = await new_stealth_context(browser, cookies=cookies)
    page = await context.new_page()
    await apply_stealth(page)
    try:
        yield page
    finally:
        await context.close()


async def safe_goto(page: Page, url: str, timeout_ms: int = 30_000) -> bool:
    """Navigate to URL. Returns True on success, False on timeout/error."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        return True
    except Exception:
        return False


async def random_delay(min_s: float = 1.0, max_s: float = 3.0) -> None:
    await asyncio.sleep(min_s + (max_s - min_s) * random.random())

import asyncio
import base64

from app.scraper.browser import get_browser, get_page, safe_goto


async def smoke_test(url: str) -> dict:
    """
    Open a URL with a stealth browser, take a screenshot, return title + screenshot.
    Used on Day 6 to verify Playwright works end-to-end inside the worker.
    """
    async with get_browser() as browser:
        async with get_page(browser) as page:
            success = await safe_goto(page, url)
            if not success:
                return {"success": False, "error": f"Failed to load {url}"}

            title = await page.title()
            screenshot_bytes = await page.screenshot(type="png", full_page=False)
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode()

            return {
                "success": True,
                "url": url,
                "title": title,
                "screenshot_b64": screenshot_b64,
                "viewport": page.viewport_size,
            }


def run_smoke_test(url: str) -> dict:
    return asyncio.run(smoke_test(url))

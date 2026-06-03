import asyncio
import base64
import logging

from playwright.async_api import Request

from app.llm.types import PageData
from app.scraper.browser import get_browser, get_page, random_delay, safe_goto

logger = logging.getLogger("crawlox.scraper")


async def capture_page(url: str, cookies: list | None = None) -> PageData:
    """
    Render a URL with a stealth browser and return everything the LLM needs:
    - Full page HTML (after JS execution)
    - Base64 screenshot (PNG)
    - Network log summary (request URLs + status codes)
    """
    network_entries: list[str] = []

    async with get_browser() as browser:
        async with get_page(browser, cookies=cookies) as page:

            # Capture network requests
            def on_request(request: Request):
                network_entries.append(f"REQ {request.method} {request.url}")

            def on_response(response):
                network_entries.append(f"RES {response.status} {response.url}")

            page.on("request", on_request)
            page.on("response", on_response)

            ok = await safe_goto(page, url, timeout_ms=30_000)
            if not ok:
                raise RuntimeError(f"Failed to load {url}")

            # Wait for dynamic content to settle
            await random_delay(1.5, 2.5)
            try:
                await page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:
                pass  # networkidle timeout is acceptable — page may still have useful content

            html = await page.content()
            screenshot_bytes = await page.screenshot(type="png", full_page=False)
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode()

            # Summarise network log — keep first 50 entries, truncate URLs
            network_log = "\n".join(
                entry[:120] for entry in network_entries[:50]
            )

    return PageData(
        url=url,
        html=html,
        network_log=network_log,
        screenshot_b64=screenshot_b64,
    )


def capture_page_sync(url: str, cookies: list | None = None) -> PageData:
    """Sync wrapper for use in Celery tasks."""
    return asyncio.run(capture_page(url, cookies=cookies))

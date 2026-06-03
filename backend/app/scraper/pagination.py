import logging
from typing import AsyncGenerator
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from playwright.async_api import Page

from app.scraper.browser import random_delay

logger = logging.getLogger("crawlox.scraper")


def _set_url_param(url: str, param: str, value: str | int) -> str:
    """Return url with the given query param set to value."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params[param] = [str(value)]
    new_query = urlencode(params, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


async def paginate_url_params(
    page: Page,
    base_url: str,
    param: str = "page",
    start: int = 1,
    max_pages: int = 20,
    items_selector: str | None = None,
) -> AsyncGenerator[Page, None]:
    """
    Yield the page after navigating to each page number via URL param.
    Stops early if items_selector matches 0 elements (empty page detected).
    """
    for page_num in range(start, start + max_pages):
        url = _set_url_param(base_url, param, page_num)
        logger.debug("url_params: navigating to %s", url)
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await random_delay(1.0, 2.0)

        if items_selector:
            items = await page.query_selector_all(items_selector)
            if not items:
                logger.debug("url_params: empty page at %s, stopping", url)
                break

        yield page


async def paginate_next_button(
    page: Page,
    next_selector: str = "a[rel='next'], .next a, [aria-label='Next page']",
    max_pages: int = 20,
) -> AsyncGenerator[Page, None]:
    """
    Yield current page, then click the next button repeatedly until it disappears.
    """
    for _ in range(max_pages):
        yield page
        await random_delay(1.0, 2.5)

        next_btn = await page.query_selector(next_selector)
        if not next_btn:
            logger.debug("next_button: no next button found, stopping")
            break

        is_disabled = await next_btn.get_attribute("disabled")
        if is_disabled is not None:
            logger.debug("next_button: next button is disabled, stopping")
            break

        await next_btn.click()
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)


async def paginate_load_more(
    page: Page,
    button_selector: str = "button.load-more, [data-action='load-more'], .btn-load-more",
    max_clicks: int = 20,
    items_selector: str | None = None,
) -> AsyncGenerator[Page, None]:
    """
    Click a "Load More" button repeatedly, yielding page after each click.
    Stops when button disappears or item count stops growing.
    """
    prev_count = 0

    for _ in range(max_clicks):
        yield page
        await random_delay(1.0, 2.0)

        btn = await page.query_selector(button_selector)
        if not btn:
            logger.debug("load_more: button gone, stopping")
            break

        is_visible = await btn.is_visible()
        if not is_visible:
            break

        if items_selector:
            current_count = len(await page.query_selector_all(items_selector))
            if current_count == prev_count and prev_count > 0:
                logger.debug("load_more: item count unchanged, stopping")
                break
            prev_count = current_count

        await btn.click()
        await page.wait_for_timeout(2000)  # wait for new items to render


async def paginate_infinite_scroll(
    page: Page,
    max_scrolls: int = 20,
    items_selector: str | None = None,
    scroll_pause_ms: int = 2000,
) -> AsyncGenerator[Page, None]:
    """
    Scroll to the bottom of the page repeatedly.
    Stops when scroll position stops changing or item count stabilises.
    """
    prev_height = 0
    prev_count = 0

    for _ in range(max_scrolls):
        yield page

        current_height = await page.evaluate("document.body.scrollHeight")
        if current_height == prev_height:
            logger.debug("infinite_scroll: page height unchanged, stopping")
            break
        prev_height = current_height

        if items_selector:
            current_count = len(await page.query_selector_all(items_selector))
            if current_count == prev_count and prev_count > 0:
                logger.debug("infinite_scroll: item count unchanged, stopping")
                break
            prev_count = current_count

        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(scroll_pause_ms)


async def no_pagination(page: Page) -> AsyncGenerator[Page, None]:
    """Single-page — just yield the current page once."""
    yield page

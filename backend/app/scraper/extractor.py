import logging
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urljoin

from playwright.async_api import Page

logger = logging.getLogger("crawlox.scraper")


@dataclass
class FieldSchema:
    name: str
    selector: str
    field_type: Literal["text", "href", "image", "price", "number"]
    required: bool = True
    attribute: str | None = None  # for custom attribute extraction


@dataclass
class ContainerSchema:
    container_selector: str
    fields: list[FieldSchema]


async def extract_field(element, field: FieldSchema) -> str | None:
    """Extract a single field value from a Playwright element."""
    try:
        if field.field_type == "href":
            return await element.get_attribute("href")
        elif field.field_type == "image":
            return await element.get_attribute("src") or await element.get_attribute("data-src")
        elif field.field_type == "price":
            raw = await element.inner_text()
            return raw.strip() if raw else None
        elif field.field_type == "number":
            raw = await element.inner_text()
            # strip non-numeric except dot and comma
            cleaned = "".join(c for c in (raw or "") if c.isdigit() or c in ".," )
            return cleaned.strip() or None
        elif field.attribute:
            return await element.get_attribute(field.attribute)
        else:  # text (default)
            raw = await element.inner_text()
            return raw.strip() if raw else None
    except Exception:
        return None


async def extract_page(page: Page, schema: ContainerSchema, base_url: str | None = None) -> list[dict]:
    """
    Extract all items matching schema.container_selector from the current page.
    Returns a list of dicts, one per container element.
    If base_url is provided, relative href/image values are resolved to absolute URLs.
    """
    if base_url is None:
        base_url = page.url

    containers = await page.query_selector_all(schema.container_selector)
    if not containers:
        logger.warning(
            "No elements matched container selector '%s' on %s",
            schema.container_selector, page.url,
        )
    results = []

    for container in containers:
        item: dict[str, str | None] = {}
        skip = False

        for field in schema.fields:
            try:
                el = await container.query_selector(field.selector)
                if el is None:
                    if field.required:
                        skip = True
                        break
                    item[field.name] = None
                    continue
                value = await extract_field(el, field)
                # Resolve relative URLs to absolute
                if value and field.field_type in ("href", "image") and base_url:
                    if value.startswith(("http://", "https://", "//", "data:")):
                        pass  # already absolute
                    else:
                        value = urljoin(base_url, value)
                if value is None and field.required:
                    skip = True
                    break
                item[field.name] = value
            except Exception:
                if field.required:
                    skip = True
                    break
                item[field.name] = None

        if not skip and item:
            results.append(item)

    return results


async def extract_all_pages(
    page: Page,
    schema: ContainerSchema,
    max_items: int = 500,
) -> list[dict]:
    """Extract from current page only (pagination handled separately)."""
    items = await extract_page(page, schema)
    return items[:max_items]

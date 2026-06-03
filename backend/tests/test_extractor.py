import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.scraper.extractor import ContainerSchema, FieldSchema, extract_field, extract_page
from app.scraper.retry import with_retry

pytestmark = pytest.mark.asyncio


# ---------- helpers ----------

def make_element(text=None, href=None, src=None):
    el = AsyncMock()
    el.inner_text = AsyncMock(return_value=text or "")
    el.get_attribute = AsyncMock(side_effect=lambda attr: {
        "href": href, "src": src, "data-src": None
    }.get(attr))
    return el


def make_page(containers):
    """Mock page that returns containers from query_selector_all."""
    page = AsyncMock()
    page.query_selector_all = AsyncMock(return_value=containers)
    return page


# ---------- extract_field ----------

class TestExtractField:
    async def test_text_field(self):
        el = make_element(text="  Hello World  ")
        field = FieldSchema("title", "h1", "text")
        result = await extract_field(el, field)
        assert result == "Hello World"

    async def test_href_field(self):
        el = make_element(href="https://example.com/product/1")
        field = FieldSchema("url", "a", "href")
        result = await extract_field(el, field)
        assert result == "https://example.com/product/1"

    async def test_image_field(self):
        el = make_element(src="https://example.com/img.jpg")
        field = FieldSchema("image", "img", "image")
        result = await extract_field(el, field)
        assert result == "https://example.com/img.jpg"

    async def test_price_field(self):
        el = make_element(text="  $29.99  ")
        field = FieldSchema("price", ".price", "price")
        result = await extract_field(el, field)
        assert result == "$29.99"

    async def test_number_field(self):
        el = make_element(text="1,234 reviews")
        field = FieldSchema("reviews", ".count", "number")
        result = await extract_field(el, field)
        assert result == "1,234"

    async def test_returns_none_on_exception(self):
        el = AsyncMock()
        el.inner_text = AsyncMock(side_effect=Exception("page crashed"))
        field = FieldSchema("title", "h1", "text", required=False)
        result = await extract_field(el, field)
        assert result is None


# ---------- extract_page ----------

class TestExtractPage:
    async def test_extracts_multiple_items(self):
        def make_container(title, price):
            container = AsyncMock()
            title_el = make_element(text=title)
            price_el = make_element(text=price)
            container.query_selector = AsyncMock(side_effect=lambda sel: {
                "h2": title_el, ".price": price_el
            }.get(sel))
            return container

        containers = [
            make_container("Widget A", "$10.00"),
            make_container("Widget B", "$20.00"),
        ]
        page = make_page(containers)
        schema = ContainerSchema(
            container_selector=".product",
            fields=[
                FieldSchema("title", "h2", "text"),
                FieldSchema("price", ".price", "price"),
            ],
        )

        results = await extract_page(page, schema)
        assert len(results) == 2
        assert results[0] == {"title": "Widget A", "price": "$10.00"}
        assert results[1] == {"title": "Widget B", "price": "$20.00"}

    async def test_skips_item_missing_required_field(self):
        container = AsyncMock()
        container.query_selector = AsyncMock(return_value=None)  # no elements found
        page = make_page([container])
        schema = ContainerSchema(
            container_selector=".product",
            fields=[FieldSchema("title", "h2", "text", required=True)],
        )

        results = await extract_page(page, schema)
        assert results == []

    async def test_keeps_item_with_missing_optional_field(self):
        container = AsyncMock()
        title_el = make_element(text="Widget A")
        container.query_selector = AsyncMock(side_effect=lambda sel: {
            "h2": title_el,
            ".optional": None,
        }.get(sel))
        page = make_page([container])
        schema = ContainerSchema(
            container_selector=".product",
            fields=[
                FieldSchema("title", "h2", "text", required=True),
                FieldSchema("desc", ".optional", "text", required=False),
            ],
        )

        results = await extract_page(page, schema)
        assert len(results) == 1
        assert results[0]["title"] == "Widget A"
        assert results[0]["desc"] is None

    async def test_empty_page_returns_empty_list(self):
        page = make_page([])
        schema = ContainerSchema(
            container_selector=".product",
            fields=[FieldSchema("title", "h2", "text")],
        )
        results = await extract_page(page, schema)
        assert results == []


# ---------- retry ----------

class TestRetry:
    async def test_succeeds_on_first_try(self):
        fn = AsyncMock(return_value="ok")
        result = await with_retry(fn, max_attempts=3)
        assert result == "ok"
        assert fn.call_count == 1

    async def test_retries_on_failure_then_succeeds(self):
        fn = AsyncMock(side_effect=[ValueError("fail"), ValueError("fail"), "ok"])
        result = await with_retry(fn, max_attempts=3, base_delay=0.01)
        assert result == "ok"
        assert fn.call_count == 3

    async def test_raises_after_max_attempts(self):
        fn = AsyncMock(side_effect=ValueError("always fails"))
        with pytest.raises(ValueError, match="always fails"):
            await with_retry(fn, max_attempts=3, base_delay=0.01)
        assert fn.call_count == 3

    async def test_only_catches_specified_exceptions(self):
        fn = AsyncMock(side_effect=RuntimeError("unexpected"))
        with pytest.raises(RuntimeError):
            await with_retry(fn, max_attempts=3, base_delay=0.01, exceptions=(ValueError,))
        assert fn.call_count == 1


# ---------- url helper (sync, no asyncio mark needed) ----------

class TestUrlHelper:
    def test_set_url_param(self):
        from app.scraper.pagination import _set_url_param
        url = "https://example.com/products?category=shoes"
        result = _set_url_param(url, "page", 2)
        assert "page=2" in result
        assert "category=shoes" in result

    def test_set_url_param_overwrites_existing(self):
        from app.scraper.pagination import _set_url_param
        url = "https://example.com/products?page=1"
        result = _set_url_param(url, "page", 3)
        assert "page=3" in result
        assert "page=1" not in result

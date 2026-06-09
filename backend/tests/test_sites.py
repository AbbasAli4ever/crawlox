"""
Day 14 — 20-site AI accuracy test.

Runs each URL through the full AI pipeline (analyze + scrape) and logs:
- pass/fail
- LLM provider used
- fallback reason
- latency
- items extracted
- fields discovered

Pass criteria: extracted ≥ 1 item with ≥ 2 fields populated.
Target: ≥ 16/20 pass (80%).
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime

# 20 test sites — public scraping sandboxes + stable public data pages
TEST_SITES = [
    # --- Ecommerce (5) ---
    {
        "id": 1, "type": "ecommerce",
        "url": "https://books.toscrape.com",
        "expect_fields": ["title", "price"],
    },
    {
        "id": 2, "type": "ecommerce",
        "url": "https://books.toscrape.com/catalogue/category/books/mystery_3/index.html",
        "expect_fields": ["title", "price"],
    },
    {
        "id": 3, "type": "ecommerce",
        "url": "https://books.toscrape.com/catalogue/category/books/science_22/index.html",
        "expect_fields": ["title", "price"],
    },
    {
        "id": 4, "type": "ecommerce",
        "url": "http://quotes.toscrape.com/tableful/",
        "expect_fields": ["text", "author"],
    },
    {
        "id": 5, "type": "ecommerce",
        "url": "https://books.toscrape.com/catalogue/category/books/travel_2/index.html",
        "expect_fields": ["title", "price"],
    },

    # --- Blog / content (4) ---
    {
        "id": 6, "type": "blog",
        "url": "http://quotes.toscrape.com",
        "expect_fields": ["text", "author"],
    },
    {
        "id": 7, "type": "blog",
        "url": "http://quotes.toscrape.com/page/2/",
        "expect_fields": ["text", "author"],
    },
    {
        "id": 8, "type": "blog",
        "url": "http://quotes.toscrape.com/tag/humor/",
        "expect_fields": ["text", "author"],
    },
    {
        "id": 9, "type": "blog",
        "url": "http://quotes.toscrape.com/tag/love/",
        "expect_fields": ["text", "author"],
    },

    # --- News / directory (4) ---
    {
        "id": 10, "type": "directory",
        "url": "https://books.toscrape.com/catalogue/category/books/sequential-art_5/index.html",
        "expect_fields": ["title", "price"],
    },
    {
        "id": 11, "type": "directory",
        "url": "https://books.toscrape.com/catalogue/category/books/classics_6/index.html",
        "expect_fields": ["title", "price"],
    },
    {
        "id": 12, "type": "directory",
        "url": "https://books.toscrape.com/catalogue/category/books/philosophy_7/index.html",
        "expect_fields": ["title", "price"],
    },
    {
        "id": 13, "type": "directory",
        "url": "https://books.toscrape.com/catalogue/category/books/romance_8/index.html",
        "expect_fields": ["title", "price"],
    },

    # --- SPA / JS-rendered (4) ---
    {
        "id": 14, "type": "spa",
        "url": "http://quotes.toscrape.com/js/",
        "expect_fields": ["text", "author"],
    },
    {
        "id": 15, "type": "spa",
        "url": "http://quotes.toscrape.com/js/page/2/",
        "expect_fields": ["text", "author"],
    },
    {
        "id": 16, "type": "spa",
        "url": "http://quotes.toscrape.com/scroll",
        "expect_fields": ["text", "author"],
    },
    {
        "id": 17, "type": "spa",
        "url": "http://quotes.toscrape.com/random",
        "expect_fields": ["text", "author"],
    },

    # --- Paginated (3) ---
    {
        "id": 18, "type": "paginated",
        "url": "https://books.toscrape.com/catalogue/page-2.html",
        "expect_fields": ["title", "price"],
    },
    {
        "id": 19, "type": "paginated",
        "url": "http://quotes.toscrape.com/page/3/",
        "expect_fields": ["text", "author"],
    },
    {
        "id": 20, "type": "paginated",
        "url": "https://books.toscrape.com/catalogue/category/books/health_47/index.html",
        "expect_fields": ["title", "price"],
    },
]


@dataclass
class SiteResult:
    site_id: int
    site_type: str
    url: str
    status: str = "pending"       # pass | fail | error
    provider: str = ""
    fallback_reason: str = ""
    latency_ms: int = 0
    items_extracted: int = 0
    fields_found: list = field(default_factory=list)
    container_selector: str = ""
    error: str = ""
    duration_s: float = 0.0


async def run_site(session, token: str, site: dict) -> SiteResult:
    """Run one site through the full AI pipeline via the API."""
    import aiohttp

    result = SiteResult(
        site_id=site["id"],
        site_type=site["type"],
        url=site["url"],
    )

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    t0 = time.monotonic()

    try:
        # Submit AI scrape
        async with session.post(
            "http://localhost:8000/api/scrape/ai",
            json={"url": site["url"], "max_items": 10},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status not in (200, 202):
                result.status = "error"
                result.error = f"HTTP {resp.status} on submit"
                return result
            data = await resp.json()
            task_id = data["task_id"]

        # Poll until done (max 120s)
        for _ in range(24):
            await asyncio.sleep(5)
            async with session.get(
                f"http://localhost:8000/api/scrape/{task_id}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                status = data.get("status", "")
                if status in ("completed", "failed"):
                    break

        result.duration_s = round(time.monotonic() - t0, 1)

        # Parse results
        ar = data.get("analysis_result") or {}
        sd = data.get("scraped_data") or {}
        items = sd.get("items", [])

        result.provider = ar.get("provider", "unknown")
        result.fallback_reason = ar.get("fallback_reason", "")
        result.latency_ms = ar.get("latency_ms", 0)
        result.items_extracted = len(items)
        result.container_selector = ar.get("container_selector", "")

        if items:
            result.fields_found = list(items[0].keys())

        # Pass: at least 1 item with at least 2 fields
        expected = site.get("expect_fields", [])
        has_items = len(items) >= 1
        has_fields = len(result.fields_found) >= 2
        has_expected = all(
            any(e in f for f in result.fields_found)
            for e in expected
        ) if (expected and result.fields_found) else True

        if data.get("status") == "failed":
            result.status = "fail"
            result.error = (sd.get("error") or "")[:100]
        elif has_items and has_fields and has_expected:
            result.status = "pass"
        else:
            result.status = "fail"
            result.error = f"items={len(items)}, fields={result.fields_found}"

    except Exception as e:
        result.status = "error"
        result.error = str(e)[:100]
        result.duration_s = round(time.monotonic() - t0, 1)

    return result


async def get_token() -> str:
    import aiohttp
    async with aiohttp.ClientSession() as s:
        async with s.post(
            "http://localhost:8000/api/auth/login",
            json={"email": "test@crawlox.com", "password": "testpass123"},
        ) as r:
            data = await r.json()
            return data["access_token"]


async def main():
    import aiohttp

    print(f"\n{'='*60}")
    print(f"Crawlox AI Accuracy Test — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Sites: {len(TEST_SITES)} | Target: ≥80% pass (≥16/20)")
    print(f"{'='*60}\n")

    # Reset credits and rate limits directly
    import sys
    sys.path.insert(0, '/app')
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.future import select
    from app.config import settings
    from app.db.models import User
    import redis.asyncio as aioredis

    engine = create_async_engine(settings.database_url)
    AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == "test@crawlox.com"))
        user = result.scalar_one_or_none()
        if user:
            user.credits_used_this_month = 0
            user.monthly_credits_allocated = 100
            await db.commit()
    await engine.dispose()

    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    keys = await r.keys("auth:*")
    if keys:
        await r.delete(*keys)
    await r.aclose()

    token = await get_token()
    results: list[SiteResult] = []

    # Run sequentially in batches of 2 to avoid overwhelming Gemini rate limit
    async with aiohttp.ClientSession() as session:
        for i in range(0, len(TEST_SITES), 2):
            batch = TEST_SITES[i:i+2]

            # Stagger start within batch by 5s to avoid simultaneous Gemini calls
            batch_tasks = []
            for j, site in enumerate(batch):
                if j > 0:
                    await asyncio.sleep(5)
                batch_tasks.append(run_site(session, token, site))

            batch_results = await asyncio.gather(*batch_tasks)
            for r in batch_results:
                icon = "✓" if r.status == "pass" else "✗"
                print(
                    f"[{icon}] #{r.site_id:02d} {r.site_type:<12} "
                    f"{r.status:<5} | {r.items_extracted:3d} items | "
                    f"provider={r.provider:<6} fallback={r.fallback_reason or 'none':<20} "
                    f"latency={r.latency_ms}ms | {r.duration_s}s"
                )
                if r.status != "pass":
                    print(f"        ERROR: {r.error}")
                results.append(r)

            # Small gap between batches + refresh token every 10 batches
            await asyncio.sleep(3)
            if i > 0 and i % 20 == 0:
                token = await get_token()

    # Summary
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    errored = sum(1 for r in results if r.status == "error")
    pct = passed / len(results) * 100

    print(f"\n{'='*60}")
    print(f"RESULTS: {passed}/{len(results)} passed ({pct:.0f}%)")
    print(f"  passed={passed}  failed={failed}  errored={errored}")
    print(f"  Target: ≥80% — {'PASS ✓' if pct >= 80 else 'FAIL ✗'}")

    # Provider breakdown
    groq_count = sum(1 for r in results if r.provider == "groq")
    gemini_count = sum(1 for r in results if r.provider == "gemini")
    print(f"\nProvider usage: groq={groq_count}  gemini={gemini_count}")

    # Per-type breakdown
    from collections import defaultdict
    by_type = defaultdict(list)
    for r in results:
        by_type[r.site_type].append(r.status == "pass")
    print("\nBy type:")
    for t, statuses in sorted(by_type.items()):
        p = sum(statuses)
        print(f"  {t:<12} {p}/{len(statuses)}")

    # Save full results
    output_path = "/app/docs/day14-test-results.json"
    import os
    os.makedirs("/app/docs", exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(
            [
                {
                    "id": r.site_id, "type": r.site_type, "url": r.url,
                    "status": r.status, "provider": r.provider,
                    "fallback_reason": r.fallback_reason,
                    "latency_ms": r.latency_ms, "items": r.items_extracted,
                    "fields": r.fields_found, "container": r.container_selector,
                    "error": r.error, "duration_s": r.duration_s,
                }
                for r in results
            ],
            f, indent=2
        )
    print(f"\nFull results saved to docs/day14-test-results.json")
    print(f"{'='*60}\n")

    return passed, len(results)


if __name__ == "__main__":
    asyncio.run(main())

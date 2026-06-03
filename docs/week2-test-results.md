# Week 2 — Day 9 Test Results

## Reference Sites

### Site 1 — Static HTML + next_button pagination
- **URL:** `https://books.toscrape.com/catalogue/page-1.html`
- **Type:** Static HTML (server-rendered)
- **Pagination:** `next_button` (`li.next a`), 3 pages
- **Result:** ✅ 60 items, 19.4s
- **Fields extracted:** title, price, link (absolute URL)

### Site 2 — JavaScript-rendered SPA
- **URL:** `http://quotes.toscrape.com/js/`
- **Type:** SPA (content rendered via JavaScript, not in raw HTML)
- **Pagination:** none (single page)
- **Result:** ✅ 10 items, 10.5s
- **Fields extracted:** text, author, tags
- **Note:** Playwright waits for JS execution — works correctly where plain HTTP scraping would fail

### Site 3 — Static + next_button pagination (different domain)
- **URL:** `http://quotes.toscrape.com/`
- **Type:** Static HTML
- **Pagination:** `next_button` (`li.next a`), 3 pages
- **Result:** ✅ 30 items, 18.0s
- **Fields extracted:** text, author

## Fixes Applied During Testing

| Issue | Fix |
|---|---|
| Relative `href` values (e.g. `catalogue/book.html`) | `urljoin(page.url, value)` in extractor — all href/image fields now return absolute URLs |
| Rate limit blocking test logins | Dev-only: flush `auth:*` Redis keys between test runs |

## Status
All 3 acceptance criteria met. Pipeline handles:
- Static HTML ✅
- JavaScript-rendered SPA ✅  
- Multi-page pagination (next_button) ✅

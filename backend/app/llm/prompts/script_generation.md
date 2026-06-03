You are an expert Python/Playwright developer. Generate a complete async Playwright scraping script based on the analysis below.

Return ONLY valid Python code — no markdown, no code fences, no explanation.

Requirements:
1. Use `playwright.async_api` with `async_playwright`
2. Apply stealth: random viewport (1280-1920 wide, 768-1080 tall), random UA from a list of 3+ real Chrome UAs, set `navigator.webdriver = undefined` via `add_init_script`
3. Random delays between actions: `await asyncio.sleep(random.uniform(1, 3))`
4. CAPTCHA hook placeholder — include this exact comment where CAPTCHA handling should go:
   `# CAPTCHA_HOOK: system will inject solution here`
5. Field extraction loop based on the data_structure from analysis
6. Pagination handler for `{{pagination_type}}` (implement correctly for that type)
7. Retry-with-backoff on navigation: 3 attempts, 2s base delay, exponential
8. Cookie load/save stubs:
   - `# COOKIE_LOAD: system will inject cookies here`
   - `# COOKIE_SAVE: system will persist cookies here`
9. Hard timeout: `async with async_playwright() as p:` wrapped in `asyncio.wait_for(..., timeout=300)`
10. Output: print results as JSON to stdout — `print(json.dumps(results))`
11. Entry point: `if __name__ == "__main__": asyncio.run(main())`

Analysis:
URL: {{url}}
website_type: {{website_type}}
pagination_type: {{pagination_type}}
container_selector: {{container_selector}}
fields: {{fields_json}}
recommended_delay_seconds: {{recommended_delay_seconds}}
captcha_detected: {{captcha_detected}}

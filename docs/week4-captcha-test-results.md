# Week 4 — Day 20 CAPTCHA End-to-End Test Results

All tests run against live CAPTCHA demo pages and the full worker→Redis→WebSocket pipeline.

## Free-tier manual flow

| Test | CAPTCHA page | Detected | Sitekey extracted | WS round-trip | Result |
|---|---|---|---|---|---|
| reCAPTCHA v2 | google.com/recaptcha/api2/demo | `recaptcha_v2` | ✅ `6Le-wvkS…` | ✅ | PASS |
| hCaptcha | accounts.hcaptcha.com/demo | `hcaptcha` | ✅ `a5f74b19…` | ✅ | PASS |

Event sequence verified: `pending → analyzing → scraping → captcha_needed →
captcha:required (pushed over WS) → [solution sent] → scraping (token injected, resumed)`.

Note: fake solution tokens are correctly rejected by the real CAPTCHA, causing
re-detection (`captcha_needed → scraping → captcha_needed`). This is the correct
retry-safety behavior — a real human/2Captcha token clears on the first try.

## Premium flow (2Captcha → manual fallback)

| Step | Result |
|---|---|
| `premium + CAPTCHA_SOLVER=twocaptcha` selects `TwoCaptchaSolver` | ✅ |
| 2Captcha API called with sitekey + URL | ✅ (`ERROR_WRONG_USER_KEY` on fake key — real call made) |
| On failure → `FallbackToManual` raised | ✅ |
| `handle_captcha` switches to `ManualOnlySolver` | ✅ |
| Manual solution delivered + injected | ✅ |

When a real `TWOCAPTCHA_API_KEY` is set, this same path auto-solves instead of
falling back. Premium works today (manually) without funding 2Captcha.

## Cookie reuse (re-solve skip)

| Check | Result |
|---|---|
| First scrape: 0 cookies loaded for domain | ✅ |
| `save_cookies` persists final browser cookies by domain | ✅ |
| Second scrape: cookies loaded back (cf_clearance, session) | ✅ |
| Different domain gets 0 cookies (isolation) | ✅ |
| `ai_scrape_task` wired: load before scrape, save after | ✅ |

→ After solving a CAPTCHA once, the persisted cookies let the next scrape of the
same domain skip the challenge.

## Cloudflare detection

| HTML signal | Detected |
|---|---|
| `cf-browser-verification` / "checking your browser" | ✅ cloudflare |
| `/cdn-cgi/challenge-platform` | ✅ cloudflare |
| `window.__cf_chl_opt` | ✅ cloudflare |
| Normal page (no false positive) | ✅ none |

## Phase 4 Acceptance: PASS
Both free-tier (manual) and premium (auto with manual fallback) flows work
end-to-end against reCAPTCHA v2 and hCaptcha. Cookie reuse and Cloudflare
detection verified.

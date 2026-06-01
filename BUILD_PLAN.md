# AI Web Scraper Platform — Day-by-Day Build Plan

## Context

You're building a production AI-powered scraping platform per `PLAN.md`. The product's core loop is: user submits URL → LLM analyzes the page (HTML + screenshot + network logs) → LLM generates a Playwright script → worker executes it → CAPTCHAs are solved either manually (free tier, via WebSocket) or automatically (premium, via 2Captcha) → results returned. CAPTCHA handling is the single biggest risk; the plan front-loads de-risking around it.

**Your situation:**
- Solo developer, full-time (~8 hrs/day, 5 days/week)
- Comfortable with FastAPI, Playwright, Celery, Next.js — no learning buffer needed
- Based in Pakistan → Stripe access deferred (will be added later once account is available); all billing code is written against Stripe SDK now behind an interface
- **No paid accounts needed yet** — Stripe, 2Captcha, and VM are all built behind interfaces with offline-safe stubs; drop in keys when ready
- Local docker-compose for all dev; deployment VM decision deferred to Day 36

**Key architectural principle — provider-agnostic interfaces.** Every paid external dependency is built behind an interface with a stub implementation that works offline. Production switches by setting env vars.
- **Payments** → `BillingProvider` interface with `StripeProvider` + `NoopBillingProvider`. Quota logic, tier flags, and webhook handlers are testable today; Stripe keys go in later.
- **CAPTCHA auto-solve** → `CaptchaSolver` interface with `TwoCaptchaSolver` + `ManualOnlySolver`. Premium tier falls back to manual flow until 2Captcha is funded — same path as the existing fallback logic.
- **LLM** → `LLMClient` interface with a **router**: Groq primary (fast, free, text-only) → Gemini 2.5 Flash fallback (vision-capable). Routing rules below.
- **Deployment** → identical docker-compose between laptop and VM; only `.env` differs.

**LLM router design (text-first with vision fallback):**
1. **Primary:** Groq (Llama 3.3 70B or Qwen 2.5 72B) — input is HTML + network-log summary only
2. **Fallback to Gemini 2.5 Flash** (multimodal: HTML + screenshot + network log) when:
   - Groq returns error / rate limit / timeout
   - Groq response fails JSON schema validation after 2 retries
   - Site is flagged "visually complex" (sparse semantic HTML, heavy CSS-driven layout) → skip Groq, go straight to Gemini
3. Two prompt variants live side-by-side: `analysis_text_only.md` (Groq) and `analysis_multimodal.md` (Gemini)
4. Log provider + latency + success per task in `usage_metrics` to tune routing over time

**Timeline:** 40 working days (8 weeks × 5 days). Each week ends with a working, demoable slice. Do not advance until the acceptance check passes.

---

## Pre-Work — Day 0 (30 minutes, before Day 1)

- Get a **Groq** API key (console.groq.com — free tier, very fast)
- Get a **Google AI Studio** API key for **Gemini 2.5 Flash** (free tier, vision-capable)
- Install Docker Desktop, confirm `docker compose version` works
- That's it. **No Stripe, no 2Captcha, no VM, no domain yet.** All deferred:
  - Stripe → whenever account access becomes available (interface lets you drop in keys with zero rework)
  - 2Captcha funding → before Week 7 load test (Day 33) or whenever affordable
  - VM + domain → Day 36 (start of deployment week)

---

## Week 1 — Foundation (Days 1–5)

**Goal:** Auth works, services run via docker-compose, Celery executes a no-op job.

### Day 1 — Repo & infra skeleton
- Init repo with `backend/`, `frontend/`, `infra/`, `docs/`
- `docker-compose.dev.yml` with Postgres, Redis, backend, worker, frontend services
- `.env.example` for backend and frontend
- FastAPI app boots with `/health`; Next.js 14 app boots
- Pre-commit hooks (ruff/black + prettier/eslint)

### Day 2 — Database & migrations
- SQLAlchemy async setup with `asyncpg` + connection pool
- Alembic configured
- All 5 tables modeled (`users`, `tasks`, `sessions`, `usage_metrics`, `payment_history`) with indexes from §4
- First migration runs clean against a fresh DB

### Day 3 — Auth core
- `bcrypt` password hashing
- JWT issuance (15-min access + 7-day refresh)
- Refresh stored in HTTP-only + Secure + SameSite cookie
- Endpoints: `register`, `login`, `refresh`, `logout`
- Request-ID middleware wired into log format

### Day 4 — Auth hardening + Celery
- Email-verification token flow (`verify-email/{token}`) — stub mail send to logs for now
- Rate limiter on auth endpoints (5/15min/IP) using Redis
- Celery worker container with Redis broker
- One no-op task end-to-end (`POST → enqueue → worker → DB row update`)

### Day 5 — Phase 1 acceptance + buffer
- Write integration tests: register → login → protected endpoint → refresh → logout
- Verify Celery picks up jobs and persists results
- Fix anything flaky; tag `phase-1-done`
- **Acceptance:** clean `docker-compose up`, all auth flows pass, dummy Celery job runs.

---

## Week 2 — Core Scraping Engine (Days 6–10)

**Goal:** Hardcoded Playwright pipeline scrapes 3 reference sites cleanly. No AI yet.

### Day 6 — Playwright in worker
- Install Playwright + browsers in worker Dockerfile (chromium only to keep image lean)
- Stealth setup module: random viewport, UA rotation, `navigator.webdriver` patch
- Smoke test: render a page, screenshot, return title

### Day 7 — Extraction pipeline
- Generic extractor module: takes a selector schema, returns structured rows
- Pagination handler stubs for `url_params`, `next_button`, `load_more`, `infinite_scroll`
- Random-delay utility (configurable, default 1–3s)
- Retry-with-backoff wrapper (3 attempts)

### Day 8 — `POST /api/scrape` (hardcoded path)
- Endpoint accepts URL + selector config, enqueues Celery task
- Task status transitions written to `tasks` table
- `GET /api/scrape/{task_id}` returns current state
- Hard timeout (5 min default) enforced

### Day 9 — Test against 3 sites
- One static HTML site (e.g. a docs page)
- One SPA (e.g. a React-based listing)
- One paginated site (e.g. a multi-page directory)
- Hand-write selector configs for each; verify clean JSON output

### Day 10 — Buffer + Phase 2 acceptance
- Cookie save/load against `sessions` table
- Fix extraction edge cases (missing fields, empty pages)
- **Acceptance:** all 3 reference sites scrape cleanly via the API.

---

## Week 3 — AI Integration (Days 11–15)

**Goal:** LLM analyzes a page and generates an executable Playwright script that runs first-try on ≥80% of test sites.

### Day 11 — LLM interface + Groq client
- Write `docs/decisions/001-llm-provider.md` documenting the Groq-primary + Gemini-fallback router and why (Groq speed + free tier, Gemini for vision)
- Define `LLMClient` interface (`analyze(page) → AnalysisResult`, `generate_script(analysis) → str`)
- Implement `GroqClient` with retry/backoff
- Prompts under `backend/app/llm/prompts/`: `analysis_text_only.md`, `analysis_multimodal.md`, `script_generation.md`

### Day 12 — Gemini client + router + analysis flow
- Implement `GeminiClient` (multimodal: HTML + screenshot + network log)
- Build `LLMRouter` implementing `LLMClient`:
  - Primary: Groq with text-only prompt
  - Fallback to Gemini on: error / rate limit / timeout / JSON schema validation fail after 2 retries / "visually complex" page flag
  - Log `provider`, `latency_ms`, `fallback_reason` per call into `usage_metrics`
- Strict JSON schema validator matching §7.1
- `POST /api/scrape/analyze-only` endpoint returns the JSON + which provider answered

### Day 13 — Script generation prompt
- Prompt that emits a Playwright async Python script meeting §7.2 requirements
- Run script generation through the router too (Groq primary works well here since input is structured JSON, no vision needed)
- System fills CAPTCHA-handling hook placeholders per tier (stub for now)
- Sandbox executor: write script to temp file, run inside worker with resource limits
- Capture stdout/stderr + structured results

### Day 14 — Test set: 20 sites
- Curate 20 URLs across ecommerce / blog / news / directory / social
- Build a runner that loops all 20 through analyze + generate + execute
- Log per site: pass/fail, which provider answered, fallback reason, latency

### Day 15 — Iterate to ≥80% + Phase 3 acceptance
- Tune prompts based on failures; tune router thresholds (when to skip Groq and go straight to Gemini)
- Confirm Groq handles ≥60% of analyses without fallback (otherwise the router isn't pulling its weight — revisit prompt)
- **Acceptance:** ≥16/20 sites produce a valid, executable script on first try.

---

## Week 4 — CAPTCHA System (Days 16–21, 6 days — buffer absorbed here)

**Highest-risk phase.** If something slips, it slips here.

### Day 16 — CAPTCHA detection
- Detection module checks DOM (reCAPTCHA/hCaptcha iframes, Cloudflare challenge markers) + network signals
- On detection, worker pauses task, transitions status to `captcha_needed`, persists context (sitekey, page URL, screenshot)

### Day 17 — WebSocket infrastructure
- FastAPI WebSocket endpoint with JWT auth on connect
- Channels keyed by `task_{task_id}`; subscribe/unsubscribe protocol
- Server emits `task:status_update` for every status change

### Day 18 — Free-tier manual flow
- Worker writes CAPTCHA payload (screenshot or sitekey + URL) to Redis
- Server emits `captcha:required` to subscribed client
- Client emits `captcha:solution`; server forwards to worker via Redis pubsub
- Worker injects solution into page, resumes script
- Cookies persisted to `sessions` to avoid re-solving same domain

### Day 19 — Premium tier 2Captcha (behind interface)
- Define `CaptchaSolver` interface (`solve(sitekey, url, type) → token`)
- Implement `TwoCaptchaSolver`: SDK integration, submit sitekey + URL, poll every 5s, max 2 min, log `cost_2captcha`
- Implement `ManualOnlySolver`: always raises `FallbackToManual` — used until 2Captcha is funded
- Selector: `CAPTCHA_SOLVER=twocaptcha|manual_only` env var, defaults to `manual_only` in dev
- Tier-aware orchestrator: premium tries solver first, falls back to manual flow + WS notify on `FallbackToManual` or any solver failure
- This means premium tier *works today* (it just behaves like free tier) — drop in 2Captcha key later to enable auto-solve with zero code change

### Day 20 — End-to-end CAPTCHA tests
- Test free tier (manual flow) against Google's reCAPTCHA v2 demo page
- Test free tier against hCaptcha demo page
- Test premium tier with `CAPTCHA_SOLVER=manual_only` — confirm clean fallback to manual flow
- If 2Captcha is funded: test premium with `CAPTCHA_SOLVER=twocaptcha` end-to-end
- Verify cookie reuse skips re-solve on second scrape of same domain
- Verify Cloudflare challenge detection (resolution best-effort)

### Day 21 — Buffer + Phase 4 acceptance
- Fix race conditions (worker resume after WS reconnect, Redis pubsub edge cases)
- **Acceptance:** both flows pass end-to-end against reCAPTCHA v2 and hCaptcha test pages.

---

## Week 5 — Subscriptions & Payments (Days 22–25, 4 days)

**No Stripe account needed this week.** All code targets the Stripe SDK behind a `BillingProvider` interface. A `NoopBillingProvider` is used in dev: it simulates checkout success and emits fake webhook events so the full flow can be tested. When the Stripe account is ready, set `BILLING_PROVIDER=stripe` + keys in `.env` and the same code runs against real Stripe.

### Day 22 — Billing interface + checkout
- Define `BillingProvider` interface (`create_checkout`, `verify_webhook`, `cancel_subscription`, `get_invoices`)
- Implement `StripeProvider` using Stripe SDK (works the moment keys are added)
- Implement `NoopBillingProvider` for dev: returns a fake checkout URL that POSTs back to a local "fake success" endpoint
- Plans defined in code: monthly $29, annual $290
- `GET /api/subscription/plans` + `POST /api/subscription/create-checkout`
- Customer ID stored on `users.stripe_customer_id` (name kept for forward-compat)

### Day 23 — Webhook handlers
- `POST /api/subscription/webhook` dispatches to `BillingProvider.verify_webhook`
- Handle: `checkout.completed`, `invoice.paid`, `invoice.payment_failed`, `customer.subscription.updated`, `customer.subscription.deleted`
- Sync `subscription_tier`, write `payment_history` rows
- Dev: a `/api/dev/simulate-webhook` endpoint (only mounted when `BILLING_PROVIDER=noop`) lets you fire each event type

### Day 24 — Quota enforcement
- Middleware increments `credits_used_this_month` per scrape
- Monthly reset job (Celery beat) on `last_reset_date` rollover
- Hard limit + 3-day grace period before 429
- Tier-gated features: export formats, webhook configure, queue priority

### Day 25 — Phase 5 acceptance
- Run the simulated-webhook flow end-to-end: fake checkout → user flips to premium → quota raises → fake cancel → user reverts to free
- Smoke-test against real Stripe test mode if account is available (otherwise schedule for whenever access lands — no code change needed)
- **Acceptance:** quota enforces; webhook events (real or simulated) sync state correctly.

---

## Week 6 — Frontend Complete (Days 26–30)

### Day 26 — Foundation
- Tailwind + shadcn/ui set up; theme + base layout
- Zustand stores: `auth`, `tasks`, `subscription`
- API client with refresh-on-401 interceptor
- Auth pages: register / login / verify-email

### Day 27 — Scrape submission + history
- URL input + custom-fields builder
- `POST /api/scrape` flow with optimistic task creation
- History page (paginated) with status badges
- Task detail page skeleton

### Day 28 — WebSocket client + CAPTCHA modal
- Socket.io client, subscribe on task open
- Live status pill, progress events
- CAPTCHA modal: renders screenshot or reCAPTCHA/hCaptcha widget, submits solution
- Reconnect handling

### Day 29 — Results, exports, dashboard
- Results viewer with field-level inspection + JSON preview
- Export buttons (JSON for all, CSV/Excel gated on premium)
- Dashboard: usage chart (Recharts), success-rate stat, recent tasks
- Subscription page: plan compare, upgrade button → Stripe checkout, manage portal link

### Day 30 — Phase 6 acceptance
- Walk every API endpoint; confirm UI surface exists
- Manual browser test of CAPTCHA modal against a live recaptcha demo
- **Acceptance:** every endpoint has a working UI; CAPTCHA modal works in browser.

---

## Week 7 — Testing & Hardening (Days 31–35)

### Day 31 — Unit tests
- Auth, JWT, rate limiter, CAPTCHA detection, 2Captcha client, Stripe webhook signature
- Target ~70% coverage on critical modules (don't chase 100%)

### Day 32 — Integration tests
- Full task lifecycle (pending → analyzing → scraping → completed)
- WebSocket subscribe + receive event
- Quota enforcement at boundary
- Stripe webhook replay via fixtures

### Day 33 — Load test
- Locust or k6 script simulating 100 concurrent scrapes
- Tune: Celery concurrency, worker pool size, Postgres pool, browser context reuse
- Identify and fix the first bottleneck (usually browser memory)

### Day 34 — Security audit
- OWASP top-10 walkthrough against the API
- `pip-audit` + `npm audit` — patch what's not noisy
- Verify: CORS allowlist, URL/domain blocklist, generic external errors, no leaked secrets, parameterized queries everywhere
- Worker container CPU/memory/time limits enforced

### Day 35 — AI regression + Phase 7 acceptance
- Re-run the 20 Phase-3 sites + 10 new ones
- **Acceptance:** critical-path tests green; 100-concurrent load stable; AI accuracy still ≥80%.

---

## Week 8 — Deployment & Docs (Days 36–40)

### Day 36 — Production compose
- `docker-compose.prod.yml` with Nginx + Certbot
- Health checks on every service
- Graceful Celery shutdown (`SIGTERM` → finish in-flight → exit)
- Log aggregation to stdout (host-level shipper out of scope)

### Day 37 — Deploy to VM
- Pick provider (Hetzner CX22 recommended for cost), provision VM
- Configure firewall, SSH hardening, swap
- DNS → VM; Certbot issues SSL
- First production deploy; smoke-test the full flow

### Day 38 — Observability + admin
- Admin endpoints: list users, tasks, metrics, adjust credits
- Simple admin page gated by role flag
- Sentry (or equivalent) wired to backend + frontend
- Backup script for Postgres (daily cron, retain 7)

### Day 39 — Docs
- `README.md` — quickstart
- `API.md` — links to `/docs` OpenAPI + auth flow narrative
- `DEPLOYMENT.md` — VM bring-up steps
- `CONTRIBUTING.md`, `TROUBLESHOOTING.md`
- LLM provider decision doc finalized

### Day 40 — Final acceptance
- Fresh VM dry-run: clone → fill `.env` → `docker-compose up` → working stack
- Real card on Stripe test mode; cancel before charge
- Tag `v1.0.0`
- **Acceptance:** fresh-VM deploy works end-to-end with only `.env` config.

---

## Critical Files / Modules to Plan For

- `backend/app/main.py` — FastAPI app, middleware stack (request-ID, CORS, rate limit)
- `backend/app/auth/` — JWT, password hashing, dependencies
- `backend/app/db/models.py` + `backend/alembic/versions/` — schema + migrations
- `backend/app/scraper/` — Playwright runner, stealth, extraction, pagination, retries
- `backend/app/llm/prompts/analysis.md` + `generation.md` — version-controlled prompt templates
- `backend/app/llm/client.py` — LLM SDK wrapper with retry + schema validation
- `backend/app/captcha/` — detector, manual flow, 2Captcha client, session/cookie persistence
- `backend/app/ws/` — WebSocket manager, channel auth, event protocol
- `backend/app/billing/` — Stripe checkout, webhook handlers, quota middleware
- `backend/app/workers/tasks.py` — Celery task definitions
- `frontend/src/lib/api.ts` + `frontend/src/lib/ws.ts` — clients
- `frontend/src/stores/` — Zustand stores
- `frontend/src/components/captcha-modal.tsx` — the critical UX surface
- `infra/docker-compose.dev.yml` + `infra/docker-compose.prod.yml` + `infra/nginx/`

---

## Risk & Buffer Notes

- **Week 4 is the danger zone.** It's allocated 6 days instead of 5. If it slips further, cut admin polish in Week 8, not testing in Week 7.
- **LLM costs:** Groq + Gemini free tiers should cover all of Phase 3 dev and testing. Watch for Groq rate limits during the 20-site test (Day 14) — may need to throttle the test runner.
- **2Captcha not blocking.** `ManualOnlySolver` keeps premium tier functional. Schedule funding ($5 covers ~500 solves) anytime before public launch.
- **Stripe deferral:** `NoopBillingProvider` handles all dev/testing. Real Stripe integration tested whenever account access lands — interface guarantees zero-rework drop-in.
- **If hosting decision shifts to managed cloud (RDS/ElastiCache),** add 1–2 days to Week 8 for IaC and managed-service wiring.
- **Don't generate all 8 weeks of code in one pass.** Ship each phase, verify acceptance, then continue.

---

## Verification (End-to-End)

After Day 40, on a brand-new VM:

1. `git clone` the repo
2. Copy `.env.example` → `.env`, fill in: DB creds, Redis URL, JWT secret, `GROQ_API_KEY`, `GEMINI_API_KEY`, `BILLING_PROVIDER` (`stripe` once keys available, else `noop`), `CAPTCHA_SOLVER` (`twocaptcha` once funded, else `manual_only`), domain
3. `docker-compose -f infra/docker-compose.prod.yml up -d`
4. Wait for Certbot, hit the domain
5. Register → verify email → submit a scrape on a known reCAPTCHA-protected page → solve manually → confirm results
6. Upgrade to premium via Stripe test card → resubmit same URL → confirm 2Captcha auto-solves
7. Hit `/docs` — OpenAPI spec loads
8. Check `/health` on every service

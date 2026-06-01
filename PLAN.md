# AI-Powered Web Scraper Platform — Build Plan

> **For Claude Code:** This document is the complete specification. Read it end-to-end before planning. Do not start coding until you have produced your own phase-by-phase implementation plan and confirmed it with the user. Ask clarifying questions on anything ambiguous.

---

## 1. What We're Building

A production-ready web scraping platform that uses an LLM to analyze any website and dynamically generate scraping scripts. The platform's key differentiator is a **dual-tier CAPTCHA strategy**:

- **Free tier** — User solves CAPTCHAs manually in real-time via WebSocket
- **Premium tier** — 2Captcha API solves them automatically

If CAPTCHA handling breaks, the product breaks. Treat it as the core, not a feature.

### Success Criteria

- `docker-compose up` brings the full stack online with only `.env` configuration
- AI analysis correctly identifies structure for ≥80% of common sites (ecommerce, blogs, news, directories)
- CAPTCHA flow works end-to-end for both tiers
- Stripe subscriptions sync correctly with usage limits
- 100 concurrent scraping tasks supported without degradation

---

## 2. Tech Stack (Fixed Decisions)

| Layer | Choice | Notes |
|---|---|---|
| Backend | FastAPI (Python, async) | |
| Task queue | Celery + Redis | Redis also for cache |
| Browser automation | Playwright + stealth plugin | Python async API |
| LLM provider | **Claude Code decides** | Pick Anthropic Claude or OpenAI GPT-4 in Phase 3, justify the choice in your plan |
| Database | PostgreSQL (JSONB) | |
| Auth | JWT (15min access + 7d refresh, refresh in HTTP-only cookie) | |
| Payments | Stripe | |
| CAPTCHA API | 2Captcha | Premium only |
| Frontend | Next.js 14 + TypeScript + Tailwind | |
| State | Zustand (preferred) or Redux Toolkit | |
| Realtime | Socket.io client ↔ FastAPI WebSockets | |
| UI kit | shadcn/ui | |
| Charts | Recharts | |
| Infra | Docker + Docker Compose, Nginx + SSL | |

---

## 3. Subscription Tiers (Source of Truth)

| | Free ($0) | Premium ($29/mo or $290/yr) |
|---|---|---|
| Requests/month | 100 | 5,000 |
| Max items/scrape | 500 | 10,000 |
| CAPTCHA | Manual via WebSocket | Auto via 2Captcha |
| Queue priority | Low | High |
| Exports | JSON only | JSON, CSV, Excel |
| Webhooks | ❌ | ✅ |
| Result retention | 30 days | 90 days |
| API rate limit | 100/min | 1,000/min |

Annual saves 17% (2 months free).

---

## 4. Database Schema

Five tables. UUIDs everywhere. All timestamps `timestamptz`.

**users** — `id`, `email` (unique), `password_hash` (bcrypt), `subscription_tier`, `stripe_customer_id`, `monthly_credits_allocated`, `credits_used_this_month`, `last_reset_date`, `created_at`, `updated_at`

**tasks** — `id`, `user_id` (FK), `url`, `custom_fields` (JSONB), `status` (enum: `pending|analyzing|scraping|captcha_needed|completed|failed`), `analysis_result` (JSONB), `generated_script` (text), `scraped_data` (JSONB), `captcha_type`, `captcha_solved_by` (enum: `manual|2captcha`), `total_items_scraped`, `created_at`, `started_at`, `completed_at`

**sessions** — `id`, `user_id` (FK), `domain`, `cookies` (JSONB), `user_agent`, `created_at`, `expires_at` *(for CAPTCHA cookie persistence — avoids re-solving)*

**usage_metrics** — `id`, `user_id` (FK), `task_id` (FK), `api_calls_made`, `captcha_solve_attempts`, `captcha_solve_successes`, `total_time_seconds`, `cost_2captcha`

**payment_history** — `id`, `user_id` (FK), `stripe_invoice_id`, `amount`, `currency`, `status`, `subscription_period_start`, `subscription_period_end`, `created_at`

Use Alembic for migrations. Index `tasks.user_id`, `tasks.status`, `sessions.domain`, `users.email`.

---

## 5. API Surface

### Public
- `POST /api/auth/register`
- `POST /api/auth/login`
- `POST /api/auth/refresh`
- `POST /api/auth/logout`
- `GET  /api/auth/verify-email/{token}`

### Protected (JWT or API key)
- `GET    /api/user/profile`
- `PUT    /api/user/profile`
- `POST   /api/user/api-key/generate`
- `POST   /api/scrape` — start a task
- `GET    /api/scrape/{task_id}` — fetch result
- `DELETE /api/scrape/{task_id}` — cancel
- `GET    /api/scrape/history` — paginated
- `POST   /api/scrape/analyze-only` — AI analysis, no scraping
- `POST   /api/export/{task_id}` — CSV/Excel (premium for non-JSON)
- `POST   /api/webhook/configure` — premium only

### Subscription
- `GET  /api/subscription/plans`
- `POST /api/subscription/create-checkout`
- `POST /api/subscription/webhook` — Stripe events
- `GET  /api/subscription/invoices`

### Admin
- `GET  /api/admin/users`
- `GET  /api/admin/tasks`
- `GET  /api/admin/metrics`
- `POST /api/admin/users/{user_id}/adjust-credits`

### WebSocket Protocol

**Client → Server**
- `subscribe:task_{task_id}`
- `captcha:solution` *(free users only)*

**Server → Client**
- `task:status_update`
- `captcha:required` *(payload includes screenshot or sitekey + page URL)*
- `task:completed`
- `task:failed`

---

## 6. Core Workflows

### 6.1 Free User Scrape

1. `POST /api/scrape` with URL + optional custom fields → returns `task_id`
2. Client opens WebSocket, subscribes to `task_{task_id}`
3. Status: `analyzing` → LLM analyzes page (HTML + screenshot + network logs)
4. LLM generates Playwright script
5. Status: `scraping` → Celery worker executes script
6. **If CAPTCHA detected mid-scrape:**
   - Status: `captcha_needed`
   - Worker pauses, screenshots CAPTCHA or extracts sitekey
   - Server emits `captcha:required` over WS
   - Frontend renders CAPTCHA modal
   - User solves; client emits `captcha:solution`
   - Worker injects solution, resumes
   - Cookies persisted to `sessions` table
7. Status: `completed` → results emitted via WS and queryable via API

### 6.2 Premium User Scrape

Steps 1–5 identical. On CAPTCHA detection:
- Call 2Captcha with sitekey + page URL
- Poll every 5s, max 2min
- On success: inject token, resume, log cost in `usage_metrics`
- On failure: fall back to manual (notify user)

### 6.3 Analyze-Only

Returns the LLM analysis JSON without running the scrape. Useful for users to estimate complexity and configure fields before committing credits.

---

## 7. LLM Prompts (Build These as Templates)

### 7.1 Website Analysis Prompt

Input: rendered HTML, screenshot, network log summary.
Output: strict JSON matching this schema —

```json
{
  "website_type": "ecommerce|blog|news|social|directory|other",
  "framework": "react|vue|angular|wordpress|shopify|custom",
  "has_infinite_scroll": false,
  "pagination_type": "url_params|next_button|load_more|infinite_scroll|none",
  "data_structure": {
    "container_selector": "string",
    "fields": [
      { "name": "title", "selector": "string", "type": "text|href|image|price|number", "required": true }
    ]
  },
  "captcha_detected": false,
  "captcha_type": "recaptcha_v2|recaptcha_v3|hcaptcha|cloudflare|text|none",
  "anti_bot_detected": false,
  "recommended_delay_seconds": 2,
  "recommended_proxy": false
}
```

Validate against this schema before accepting. Retry on parse failure (max 2 retries) with a tightened prompt.

### 7.2 Script Generation Prompt

Output: Python file using Playwright async API. Must include:
- Stealth setup (random viewport, UA rotation, navigator.webdriver patching)
- Random delays between actions (configurable, default 1–3s)
- CAPTCHA-handling placeholder hooks (system fills these per tier)
- Field extraction loop based on the analysis schema
- Pagination handler per detected `pagination_type`
- Retry-with-backoff on transient failures (3 attempts)
- Cookie load/save against the `sessions` table
- Hard timeout (default 5 minutes)

---

## 8. Stripe Integration

- Create checkout sessions for monthly/annual
- Webhook handlers: `checkout.completed`, `invoice.paid`, `invoice.payment_failed`, `customer.subscription.updated`, `customer.subscription.deleted`
- Sync `subscription_tier` and reset `credits_used_this_month` on period rollover
- Grace period: 3 days after limit exceeded before hard-blocking

---

## 9. Security Requirements (Non-Negotiable)

- bcrypt for passwords, never store plain text
- All secrets in env vars, never hardcoded — provide `.env.example`
- Parameterized queries everywhere (SQLAlchemy ORM is fine)
- Rate limit auth endpoints: 5 attempts / 15 min per IP
- CORS allowlist, not `*`
- URL validation + domain blocklist (banking, gov, known abuse targets) before scraping
- Scraping workers run in isolated Docker containers with CPU/memory/time limits
- HTTP-only + Secure + SameSite cookies for refresh tokens
- Generic error messages externally; full detail in logs with request ID

---

## 10. Error Handling Matrix

| Failure | Response |
|---|---|
| Scrape timeout | Retry ×3 with exponential backoff |
| CAPTCHA | Tier-based fallback (manual ↔ 2Captcha) |
| IP blocked | Surface error + proxy integration docs |
| Page structure changed mid-scrape | Re-run analysis, regenerate script, retry once |
| DB connection lost | Circuit breaker + retry |
| Redis down | In-memory queue fallback + admin alert |
| 2Captcha down | Fall back to manual + notify user |
| LLM rate-limited | Queue with backoff |
| User exceeded quota | 429 + upgrade link |
| Invalid URL | 400 + format hint |

---

## 11. Phased Build Plan (8 Weeks)

> Treat these as **milestones, not rigid sprints.** Each phase ends with a working, testable slice. Do not start the next phase until the previous one passes its acceptance check.

### Phase 1 — Foundation (Week 1)
- Repo layout: `backend/`, `frontend/`, `infra/`, `docs/`
- FastAPI skeleton + Next.js skeleton
- PostgreSQL + Redis containers in `docker-compose.dev.yml`
- Alembic migrations for all 5 tables
- JWT auth: register, login, refresh, logout
- Celery worker boots and processes a no-op task
- **Acceptance:** user can register, log in, hit a protected endpoint; Celery executes a dummy job.

### Phase 2 — Core Scraping Engine (Week 2)
- Playwright installed in worker container
- Basic fetch + render + extract pipeline (no AI yet — hardcoded selectors)
- Stealth plugin wired up
- Results written to `tasks.scraped_data`
- **Acceptance:** scrape 3 hardcoded sites (one static, one SPA, one with pagination) and return clean JSON.

### Phase 3 — AI Integration (Week 3)
- **Decide LLM provider here.** Document the choice in `docs/decisions/001-llm-provider.md` covering cost per analysis, latency, structured output reliability, and vision support.
- Implement analysis prompt + JSON schema validator
- Implement script generation prompt
- `POST /api/scrape/analyze-only` endpoint
- Test against 20 sites spanning ecommerce, blog, news, directory, social
- **Acceptance:** ≥80% of test sites produce a valid, executable script on first try.

### Phase 4 — CAPTCHA System (Week 4) — *Highest risk, allocate buffer*
- CAPTCHA detection module (DOM + network signals)
- WebSocket infrastructure (FastAPI + Socket.io)
- Free flow: screenshot/embed CAPTCHA → push to client → receive solution → inject
- Premium flow: 2Captcha SDK integration with polling + cost logging
- Cookie persistence in `sessions` table
- **Acceptance:** both flows work against a reCAPTCHA v2 test page and an hCaptcha test page end-to-end.

### Phase 5 — Subscriptions & Payments (Week 5)
- Stripe checkout + webhook handlers
- Usage tracking middleware (increment `credits_used_this_month` per scrape)
- Quota enforcement + grace period
- Subscription UI (plans, upgrade, manage)
- **Acceptance:** test-mode purchase flips a user to premium, webhook syncs state, quota enforces correctly.

### Phase 6 — Frontend Complete (Week 6)
- Scraping interface (URL input, custom fields builder, submit)
- Results viewer with field-level inspection
- Export buttons (CSV/Excel gated on premium)
- User dashboard: usage chart, history, success rate
- WebSocket live progress + CAPTCHA modal
- **Acceptance:** every API endpoint has a working UI; CAPTCHA modal tested in browser.

### Phase 7 — Testing & Hardening (Week 7)
- Unit tests for: auth, CAPTCHA detection, WebSocket, 2Captcha integration, Stripe webhooks, rate limiter
- Load test: 100 concurrent scrapes (use Locust or k6)
- Security audit: OWASP top 10 checklist, dependency scan
- AI accuracy regression suite (the 20 sites from Phase 3 + 10 new)
- **Acceptance:** all critical-path tests green; load test stable at 100 concurrent users.

### Phase 8 — Deployment & Docs (Week 8)
- Production `docker-compose.prod.yml` with Nginx + Certbot
- OpenAPI/Swagger published at `/docs`
- README, API.md, DEPLOYMENT.md, CONTRIBUTING.md, TROUBLESHOOTING.md
- Health check endpoints on every service
- Graceful shutdown for Celery workers
- **Acceptance:** fresh VM → clone → configure `.env` → `docker-compose up` → working production stack.

---

## 12. Deliverables Checklist

- [ ] Backend code (FastAPI app, Celery tasks, WebSocket handlers)
- [ ] Frontend code (Next.js app, all pages, WS client)
- [ ] Alembic migrations
- [ ] `docker-compose.dev.yml` + `docker-compose.prod.yml`
- [ ] `.env.example` for backend and frontend
- [ ] OpenAPI spec (auto-generated by FastAPI)
- [ ] Deployment guide
- [ ] Test suite (unit + integration)
- [ ] Prompt templates as version-controlled files in `backend/app/llm/prompts/`
- [ ] Stripe webhook handlers
- [ ] LLM provider decision doc

---

## 13. Hard Constraints

- No plain-text passwords. Ever.
- No hardcoded API keys.
- Async DB driver (`asyncpg`), connection pooling configured.
- Request ID propagated through all logs (FastAPI middleware → Celery context → worker logs).
- Health checks on every service.
- Default scrape timeout: 5 minutes, configurable per task.
- Generic external error messages; internal detail in logs only.

---

## 14. What Claude Code Should Do First

1. Read this document completely.
2. Produce a written implementation plan covering Phase 1 in detail (file structure, dependencies, exact endpoints to build first).
3. **Ask the user** about: hosting target (single VM vs cloud-managed services), Stripe account availability for testing, 2Captcha account for testing, and any sites that should be in the test set for Phase 3.
4. Confirm the LLM provider decision will be deferred to Phase 3.
5. Only then start scaffolding.

Do not generate all 8 phases of code in one pass. Ship Phase 1, verify, then continue.

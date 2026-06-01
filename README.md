# Crawlox

AI-powered web scraping platform. LLM analyzes any URL, generates a Playwright script, executes it, and solves CAPTCHAs either manually (free tier, via WebSocket) or automatically (premium, via 2Captcha).

See [BUILD_PLAN.md](BUILD_PLAN.md) for the day-by-day build plan and [PLAN.md](PLAN.md) for the full spec.

## Quickstart (dev)

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env

# Add your keys to backend/.env:
#   GROQ_API_KEY=...
#   GEMINI_API_KEY=...

docker compose -f infra/docker-compose.dev.yml up --build
```

- Backend health: http://localhost:8000/health
- Frontend: http://localhost:3000
- API docs (FastAPI auto): http://localhost:8000/docs

## Layout

```
backend/   FastAPI app, Celery workers, Playwright scraping engine
frontend/  Next.js 14 app (TS + Tailwind)
infra/     docker-compose files
docs/      Architecture decision records
```

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.scrape_router import router as scrape_router
from app.api.scraper_router import router as scraper_router
from app.api.tasks_router import router as tasks_router
from app.auth.router import router as auth_router
from app.config import settings
from app.core.middleware import RequestIDMiddleware
from app.ws.redis_relay import start_relay
from app.ws.router import router as ws_router

logging.basicConfig(level=settings.log_level.upper())

app = FastAPI(title="Crawlox API", version="0.1.0")

app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(tasks_router)
app.include_router(scraper_router)
app.include_router(scrape_router)
app.include_router(ws_router)

# Start Redis → WebSocket relay on startup
start_relay(app)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

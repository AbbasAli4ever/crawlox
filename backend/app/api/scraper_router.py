from fastapi import APIRouter, Depends
from pydantic import BaseModel, HttpUrl

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.workers.tasks import playwright_smoke_task

router = APIRouter(prefix="/api/scraper", tags=["scraper"])


class SmokeRequest(BaseModel):
    url: HttpUrl


class SmokeResponse(BaseModel):
    task_id: str
    message: str


@router.post("/smoke", response_model=SmokeResponse)
async def smoke(body: SmokeRequest, user: User = Depends(get_current_user)):
    """Enqueue a Playwright smoke test. Check result via Celery task ID."""
    result = playwright_smoke_task.delay(str(body.url))
    return SmokeResponse(
        task_id=result.id,
        message="Smoke test enqueued. Fetch result with the task_id.",
    )


@router.get("/smoke/{task_id}")
async def smoke_result(task_id: str, user: User = Depends(get_current_user)):
    """Poll the result of an enqueued smoke test."""
    from app.workers.celery_app import celery_app
    from celery.result import AsyncResult

    result = AsyncResult(task_id, app=celery_app)
    if result.ready():
        return {"status": "done", "result": result.get()}
    return {"status": result.state.lower()}

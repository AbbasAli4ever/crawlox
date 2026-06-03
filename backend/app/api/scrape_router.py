import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.auth.dependencies import get_current_user
from app.db.base import get_db
from app.db.models import Task, User
from app.workers.tasks import ai_scrape_task, analyze_task, scrape_task

router = APIRouter(prefix="/api/scrape", tags=["scrape"])

DEFAULT_TIMEOUT = 300
FREE_MAX_ITEMS = 500
PREMIUM_MAX_ITEMS = 10_000


# ---------- schemas ----------

class FieldConfig(BaseModel):
    name: str
    selector: str
    type: str = "text"
    required: bool = True
    attribute: str | None = None


class SelectorConfig(BaseModel):
    container_selector: str
    fields: list[FieldConfig]


VALID_PAGINATION_TYPES = {"none", "url_params", "next_button", "load_more", "infinite_scroll"}


class ScrapeRequest(BaseModel):
    url: HttpUrl
    selector_config: SelectorConfig
    pagination_type: str = "none"
    pagination_config: dict | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT

    def validate_request(self):
        if not self.selector_config.fields:
            raise ValueError("selector_config.fields must contain at least one field")
        if self.pagination_type not in VALID_PAGINATION_TYPES:
            raise ValueError(f"pagination_type must be one of {VALID_PAGINATION_TYPES}")
        if self.timeout_seconds < 10 or self.timeout_seconds > DEFAULT_TIMEOUT:
            raise ValueError(f"timeout_seconds must be between 10 and {DEFAULT_TIMEOUT}")


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    url: str
    total_items_scraped: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    scraped_data: dict | None = None
    analysis_result: dict | None = None


class TaskListResponse(BaseModel):
    tasks: list[TaskStatusResponse]
    total: int
    page: int
    page_size: int


# ---------- helpers ----------

def _max_items(user: User) -> int:
    return PREMIUM_MAX_ITEMS if user.subscription_tier == "premium" else FREE_MAX_ITEMS


def _task_to_response(task: Task, include_data: bool = False) -> TaskStatusResponse:
    return TaskStatusResponse(
        task_id=str(task.id),
        status=task.status,
        url=task.url,
        total_items_scraped=task.total_items_scraped,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        scraped_data=task.scraped_data if include_data else None,
        analysis_result=task.analysis_result if include_data else None,
    )


# ---------- endpoints ----------

class AIScrapeRequest(BaseModel):
    url: HttpUrl
    max_items: int = 500
    timeout_seconds: int = DEFAULT_TIMEOUT


@router.post("/ai", status_code=status.HTTP_202_ACCEPTED, response_model=TaskStatusResponse)
async def start_ai_scrape(
    body: AIScrapeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    AI-powered scrape — no selector config needed. The AI analyzes the page
    structure, generates a Playwright script, and executes it automatically.
    """
    if user.credits_used_this_month >= user.monthly_credits_allocated:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Monthly scrape quota exceeded.",
        )

    max_items = min(body.max_items, _max_items(user))
    timeout = min(body.timeout_seconds, DEFAULT_TIMEOUT)

    task = Task(
        id=uuid.uuid4(),
        user_id=user.id,
        url=str(body.url),
        status="pending",
        custom_fields={
            "mode": "ai_scrape",
            "max_items": max_items,
            "timeout_seconds": timeout,
        },
    )
    db.add(task)
    user.credits_used_this_month += 1
    await db.commit()
    await db.refresh(task)

    queue = "high" if user.subscription_tier == "premium" else "celery"
    ai_scrape_task.apply_async(args=[str(task.id)], queue=queue)

    return _task_to_response(task)


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=TaskStatusResponse)
async def start_scrape(
    body: ScrapeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        body.validate_request()
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Quota check
    if user.credits_used_this_month >= user.monthly_credits_allocated:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Monthly scrape quota exceeded. Upgrade to premium for more credits.",
        )

    max_items = _max_items(user)
    timeout = min(body.timeout_seconds, DEFAULT_TIMEOUT)

    task = Task(
        id=uuid.uuid4(),
        user_id=user.id,
        url=str(body.url),
        status="pending",
        custom_fields={
            "selector_config": body.selector_config.model_dump(),
            "pagination_type": body.pagination_type,
            "pagination_config": body.pagination_config,
            "max_items": max_items,
            "timeout_seconds": timeout,
        },
    )
    db.add(task)

    # Increment credit usage
    user.credits_used_this_month += 1
    await db.commit()
    await db.refresh(task)

    # Enqueue — premium gets high priority queue
    queue = "high" if user.subscription_tier == "premium" else "celery"
    scrape_task.apply_async(args=[str(task.id)], queue=queue)

    return _task_to_response(task)


@router.get("/history", response_model=TaskListResponse)
async def scrape_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    offset = (page - 1) * page_size

    count_result = await db.execute(
        select(Task).where(Task.user_id == user.id)
    )
    all_tasks = count_result.scalars().all()
    total = len(all_tasks)

    result = await db.execute(
        select(Task)
        .where(Task.user_id == user.id)
        .order_by(Task.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    tasks = result.scalars().all()

    return TaskListResponse(
        tasks=[_task_to_response(t) for t in tasks],
        total=total,
        page=page,
        page_size=page_size,
    )


class AnalyzeRequest(BaseModel):
    url: HttpUrl


class AnalyzeResponse(BaseModel):
    task_id: str
    status: str
    message: str


@router.post("/analyze-only", response_model=AnalyzeResponse, status_code=status.HTTP_202_ACCEPTED)
async def analyze_only(
    body: AnalyzeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    AI analysis only — renders the page, runs LLM analysis, returns structured JSON.
    Does not scrape. Useful to preview what the AI will extract before committing credits.
    """
    task = Task(
        id=uuid.uuid4(),
        user_id=user.id,
        url=str(body.url),
        status="pending",
        custom_fields={"mode": "analyze_only"},
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    analyze_task.delay(str(task.id))

    return AnalyzeResponse(
        task_id=str(task.id),
        status="pending",
        message="Analysis enqueued. Poll GET /api/scrape/{task_id} for results.",
    )


@router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_scrape(
    task_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Task).where(Task.id == uuid.UUID(task_id), Task.user_id == user.id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    return _task_to_response(task, include_data=True)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_scrape(
    task_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Task).where(Task.id == uuid.UUID(task_id), Task.user_id == user.id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    if task.status in ("completed", "failed"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel a task with status '{task.status}'",
        )

    task.status = "failed"
    task.scraped_data = {"error": "Cancelled by user"}
    task.completed_at = datetime.utcnow()
    await db.commit()

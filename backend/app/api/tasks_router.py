import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.auth.dependencies import get_current_user
from app.db.base import get_db
from app.db.models import Task, User
from app.workers.tasks import noop_task

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskResponse(BaseModel):
    task_id: str
    status: str


@router.post("/noop", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def trigger_noop(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Enqueue a no-op Celery task and return the task_id. Used to verify the worker pipeline."""
    task_row = Task(
        id=uuid.uuid4(),
        user_id=user.id,
        url="noop://test",
        status="pending",
    )
    db.add(task_row)
    await db.commit()
    await db.refresh(task_row)

    noop_task.delay(str(task_row.id))

    return TaskResponse(task_id=str(task_row.id), status=task_row.status)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
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

    return TaskResponse(task_id=str(task.id), status=task.status)

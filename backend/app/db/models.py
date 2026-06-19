import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    subscription_tier: Mapped[str] = mapped_column(
        String(20), nullable=False, default="free"
    )  # free | premium
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    monthly_credits_allocated: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    credits_used_this_month: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_reset_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    tasks: Mapped[list["Task"]] = relationship("Task", back_populates="user", lazy="noload")
    usage_metrics: Mapped[list["UsageMetric"]] = relationship(
        "UsageMetric", back_populates="user", lazy="noload"
    )
    payment_history: Mapped[list["PaymentHistory"]] = relationship(
        "PaymentHistory", back_populates="user", lazy="noload"
    )


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_user_id", "user_id"),
        Index("ix_tasks_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    custom_fields: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        Enum(
            "pending",
            "analyzing",
            "scraping",
            "captcha_needed",
            "completed",
            "failed",
            name="task_status",
        ),
        nullable=False,
        default="pending",
    )
    analysis_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    generated_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    scraped_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    captcha_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    captcha_solved_by: Mapped[str | None] = mapped_column(
        Enum("manual", "2captcha", name="captcha_solver_type"), nullable=True
    )
    total_items_scraped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="tasks", lazy="noload")
    usage_metrics: Mapped[list["UsageMetric"]] = relationship(
        "UsageMetric", back_populates="task", lazy="noload"
    )


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_domain", "domain"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    cookies: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UsageMetric(Base):
    __tablename__ = "usage_metrics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    api_calls_made: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    captcha_solve_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    captcha_solve_successes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_time_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cost_2captcha: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False, default=0.0)
    llm_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    llm_latency_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    llm_fallback_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped["User"] = relationship("User", back_populates="usage_metrics", lazy="noload")
    task: Mapped["Task | None"] = relationship(
        "Task", back_populates="usage_metrics", lazy="noload"
    )


class PaymentHistory(Base):
    __tablename__ = "payment_history"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    stripe_invoice_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="usd")
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    subscription_period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    subscription_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped["User"] = relationship("User", back_populates="payment_history", lazy="noload")

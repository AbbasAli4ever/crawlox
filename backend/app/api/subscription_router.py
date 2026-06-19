import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.auth.dependencies import get_current_user
from app.billing.factory import get_billing_provider
from app.billing.plans import ALL_PLANS, get_plan
from app.billing.webhook_processor import process_webhook_event
from app.config import settings
from app.db.base import get_db
from app.db.models import PaymentHistory, User

logger = logging.getLogger("crawlox.billing")

router = APIRouter(prefix="/api/subscription", tags=["subscription"])


# ---------- schemas ----------

class PlanResponse(BaseModel):
    id: str
    name: str
    tier: str
    interval: str | None
    price_usd: float
    monthly_credits: int
    max_items_per_scrape: int
    captcha_mode: str
    exports: list[str]
    webhooks: bool
    retention_days: int
    api_rate_limit_per_min: int


class CheckoutRequest(BaseModel):
    plan_id: str  # "premium_monthly" | "premium_annual"


class CheckoutResponse(BaseModel):
    checkout_url: str
    session_id: str
    provider: str


# ---------- endpoints ----------

@router.get("/plans", response_model=list[PlanResponse])
async def list_plans():
    """Public list of all subscription plans."""
    return [
        PlanResponse(
            id=p.id, name=p.name, tier=p.tier, interval=p.interval,
            price_usd=p.price_usd, monthly_credits=p.monthly_credits,
            max_items_per_scrape=p.max_items_per_scrape, captcha_mode=p.captcha_mode,
            exports=p.exports, webhooks=p.webhooks, retention_days=p.retention_days,
            api_rate_limit_per_min=p.api_rate_limit_per_min,
        )
        for p in ALL_PLANS
    ]


@router.post("/create-checkout", response_model=CheckoutResponse)
async def create_checkout(
    body: CheckoutRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a checkout session for a premium plan."""
    plan = get_plan(body.plan_id)
    if not plan or plan.tier != "premium":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or non-purchasable plan",
        )

    if user.subscription_tier == "premium":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Already subscribed to premium",
        )

    provider = get_billing_provider()
    try:
        session = await provider.create_checkout(
            user_id=str(user.id),
            user_email=user.email,
            plan_id=body.plan_id,
            existing_customer_id=user.stripe_customer_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return CheckoutResponse(
        checkout_url=session.checkout_url,
        session_id=session.session_id,
        provider=settings.billing_provider,
    )


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Receive billing webhooks (Stripe in prod, simulated in dev).
    Verifies signature via the provider, then applies the event.
    """
    payload = await request.body()
    signature = request.headers.get("stripe-signature")

    provider = get_billing_provider()
    try:
        event = await provider.verify_and_parse_webhook(payload, signature)
    except Exception as e:
        logger.warning("Webhook verification failed: %s", e)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook")

    result = await process_webhook_event(db, event)
    return result


class InvoiceResponse(BaseModel):
    id: str
    amount: float
    currency: str
    status: str
    created_at: str


@router.get("/invoices", response_model=list[InvoiceResponse])
async def list_invoices(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the current user's payment history."""
    result = await db.execute(
        select(PaymentHistory)
        .where(PaymentHistory.user_id == user.id)
        .order_by(PaymentHistory.created_at.desc())
    )
    rows = result.scalars().all()
    return [
        InvoiceResponse(
            id=str(p.id), amount=float(p.amount), currency=p.currency,
            status=p.status, created_at=p.created_at.isoformat(),
        )
        for p in rows
    ]


@router.post("/cancel", status_code=status.HTTP_200_OK)
async def cancel_subscription(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel the user's premium subscription at period end."""
    if user.subscription_tier != "premium":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active premium subscription",
        )
    provider = get_billing_provider()
    if user.stripe_subscription_id:
        await provider.cancel_subscription(user.stripe_subscription_id)
    return {"message": "Subscription will cancel at period end", "tier": user.subscription_tier}

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.billing.interface import WebhookEvent
from app.billing.plans import get_plan, plan_for_tier
from app.db.models import PaymentHistory, User

logger = logging.getLogger("crawlox.billing")


def _ts_to_dt(ts: int | None) -> datetime | None:
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None


async def _find_user(db: AsyncSession, event: WebhookEvent) -> User | None:
    """Resolve the user from the event — by user_id metadata, then customer id."""
    if event.user_id:
        try:
            r = await db.execute(select(User).where(User.id == uuid.UUID(event.user_id)))
            u = r.scalar_one_or_none()
            if u:
                return u
        except (ValueError, TypeError):
            pass
    if event.customer_id:
        r = await db.execute(
            select(User).where(User.stripe_customer_id == event.customer_id)
        )
        return r.scalar_one_or_none()
    return None


async def _upgrade_to_premium(db: AsyncSession, user: User, event: WebhookEvent) -> None:
    plan = get_plan(event.plan_id) if event.plan_id else plan_for_tier("premium")
    user.subscription_tier = "premium"
    user.monthly_credits_allocated = plan.monthly_credits
    user.credits_used_this_month = 0  # reset on upgrade
    user.last_reset_date = datetime.now(timezone.utc)
    if event.customer_id:
        user.stripe_customer_id = event.customer_id
    if event.subscription_id:
        user.stripe_subscription_id = event.subscription_id


async def _downgrade_to_free(db: AsyncSession, user: User) -> None:
    free = plan_for_tier("free")
    user.subscription_tier = "free"
    user.monthly_credits_allocated = free.monthly_credits
    user.stripe_subscription_id = None


async def process_webhook_event(db: AsyncSession, event: WebhookEvent) -> dict:
    """
    Apply a normalized webhook event to our DB.
    Returns a small summary dict. Idempotent where practical.
    """
    user = await _find_user(db, event)

    if event.event_type == "checkout.completed":
        if not user:
            logger.warning("checkout.completed: user not found for event %s", event.customer_id)
            return {"handled": False, "reason": "user_not_found"}
        await _upgrade_to_premium(db, user, event)
        await db.commit()
        logger.info("User %s upgraded to premium", user.email)
        return {"handled": True, "action": "upgraded", "user": str(user.id)}

    elif event.event_type == "invoice.paid":
        if not user:
            return {"handled": False, "reason": "user_not_found"}
        # Record payment + ensure premium + reset monthly credits (period rollover)
        payment = PaymentHistory(
            id=uuid.uuid4(),
            user_id=user.id,
            stripe_invoice_id=event.invoice_id,
            amount=event.amount or 0.0,
            currency=event.currency or "usd",
            status="paid",
            subscription_period_start=_ts_to_dt(event.period_start),
            subscription_period_end=_ts_to_dt(event.period_end),
        )
        db.add(payment)
        if user.subscription_tier == "premium":
            user.credits_used_this_month = 0  # new billing period → reset usage
            user.last_reset_date = datetime.now(timezone.utc)
        await db.commit()
        logger.info("invoice.paid recorded for %s ($%.2f)", user.email, event.amount or 0)
        return {"handled": True, "action": "payment_recorded", "user": str(user.id)}

    elif event.event_type == "invoice.payment_failed":
        if not user:
            return {"handled": False, "reason": "user_not_found"}
        payment = PaymentHistory(
            id=uuid.uuid4(),
            user_id=user.id,
            stripe_invoice_id=event.invoice_id,
            amount=event.amount or 0.0,
            currency=event.currency or "usd",
            status="failed",
            subscription_period_start=_ts_to_dt(event.period_start),
            subscription_period_end=_ts_to_dt(event.period_end),
        )
        db.add(payment)
        await db.commit()
        # Note: grace period before downgrade is enforced by quota logic (Day 24)
        logger.info("invoice.payment_failed recorded for %s", user.email)
        return {"handled": True, "action": "payment_failed", "user": str(user.id)}

    elif event.event_type == "customer.subscription.updated":
        if not user:
            return {"handled": False, "reason": "user_not_found"}
        # Reflect status — if canceled/unpaid, downgrade; else keep premium
        if event.status in ("canceled", "unpaid", "incomplete_expired"):
            await _downgrade_to_free(db, user)
        await db.commit()
        return {"handled": True, "action": "subscription_updated", "status": event.status}

    elif event.event_type == "customer.subscription.deleted":
        if not user:
            return {"handled": False, "reason": "user_not_found"}
        await _downgrade_to_free(db, user)
        await db.commit()
        logger.info("User %s downgraded to free (subscription deleted)", user.email)
        return {"handled": True, "action": "downgraded", "user": str(user.id)}

    # Unhandled event type — acknowledge without action
    logger.debug("Unhandled webhook event type: %s", event.event_type)
    return {"handled": False, "reason": "unhandled_event_type", "type": event.event_type}

import logging

import stripe

from app.billing.interface import BillingProvider, CheckoutSession, WebhookEvent
from app.billing.plans import get_plan
from app.config import settings

logger = logging.getLogger("crawlox.billing")

# Map Stripe event types → our normalized event types
_EVENT_MAP = {
    "checkout.session.completed": "checkout.completed",
    "invoice.paid": "invoice.paid",
    "invoice.payment_failed": "invoice.payment_failed",
    "customer.subscription.updated": "customer.subscription.updated",
    "customer.subscription.deleted": "customer.subscription.deleted",
}


class StripeProvider(BillingProvider):
    """
    Real Stripe integration. Dormant until STRIPE_SECRET_KEY is configured.
    Selected when BILLING_PROVIDER=stripe.
    """

    def __init__(self):
        if not settings.stripe_secret_key:
            raise RuntimeError("STRIPE_SECRET_KEY not configured")
        stripe.api_key = settings.stripe_secret_key

    async def create_checkout(
        self,
        user_id: str,
        user_email: str,
        plan_id: str,
        existing_customer_id: str | None,
    ) -> CheckoutSession:
        plan = get_plan(plan_id)
        if not plan or plan.tier != "premium":
            raise ValueError(f"Not a purchasable plan: {plan_id}")
        if not plan.stripe_price_id:
            raise ValueError(f"No Stripe price ID configured for plan {plan_id}")

        params = {
            "mode": "subscription",
            "line_items": [{"price": plan.stripe_price_id, "quantity": 1}],
            "success_url": f"{settings.frontend_url}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{settings.frontend_url}/billing/cancel",
            "client_reference_id": user_id,
            "metadata": {"user_id": user_id, "plan_id": plan_id},
            "subscription_data": {"metadata": {"user_id": user_id, "plan_id": plan_id}},
        }
        if existing_customer_id:
            params["customer"] = existing_customer_id
        else:
            params["customer_email"] = user_email

        # stripe SDK is sync; small call, fine to run inline
        session = stripe.checkout.Session.create(**params)
        return CheckoutSession(checkout_url=session.url, session_id=session.id)

    async def verify_and_parse_webhook(
        self,
        payload: bytes,
        signature: str | None,
    ) -> WebhookEvent:
        if not settings.stripe_webhook_secret:
            raise RuntimeError("STRIPE_WEBHOOK_SECRET not configured")

        event = stripe.Webhook.construct_event(
            payload, signature, settings.stripe_webhook_secret
        )

        normalized_type = _EVENT_MAP.get(event["type"])
        if not normalized_type:
            # Unhandled event — return a benign event the handler will ignore
            return WebhookEvent(
                event_type=event["type"], customer_id=None, subscription_id=None,
                invoice_id=None, amount=None, currency=None,
                period_start=None, period_end=None, status=None, raw=event,
            )

        obj = event["data"]["object"]
        metadata = obj.get("metadata", {}) or {}

        return WebhookEvent(
            event_type=normalized_type,
            customer_id=obj.get("customer"),
            subscription_id=obj.get("subscription") or obj.get("id"),
            invoice_id=obj.get("id") if normalized_type.startswith("invoice") else None,
            amount=(obj.get("amount_paid", 0) / 100) if obj.get("amount_paid") else None,
            currency=obj.get("currency", "usd"),
            period_start=obj.get("current_period_start") or obj.get("period_start"),
            period_end=obj.get("current_period_end") or obj.get("period_end"),
            status=obj.get("status"),
            user_id=metadata.get("user_id") or obj.get("client_reference_id"),
            plan_id=metadata.get("plan_id"),
            raw=event,
        )

    async def cancel_subscription(self, subscription_id: str) -> bool:
        try:
            stripe.Subscription.modify(subscription_id, cancel_at_period_end=True)
            return True
        except Exception as e:
            logger.warning("Stripe cancel failed for %s: %s", subscription_id, e)
            return False

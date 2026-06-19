import json
import time
import uuid

from app.billing.interface import BillingProvider, CheckoutSession, WebhookEvent
from app.billing.plans import get_plan
from app.config import settings


class NoopBillingProvider(BillingProvider):
    """
    Dev/test billing provider — no real payment processor.

    create_checkout returns a URL pointing at a local dev endpoint that, when
    visited, simulates a successful payment by firing a checkout.completed
    webhook back into our own webhook handler.

    Used when BILLING_PROVIDER=noop (the default). Lets the entire subscription
    flow be exercised without a Stripe account.
    """

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

        session_id = f"noop_cs_{uuid.uuid4().hex[:16]}"
        customer_id = existing_customer_id or f"noop_cus_{uuid.uuid4().hex[:12]}"

        # Dev "checkout page" — visiting it simulates a completed payment.
        # The frontend (or a curl) hits this to flip the user to premium.
        checkout_url = (
            f"{settings.frontend_url}/dev/checkout"
            f"?session_id={session_id}"
            f"&plan_id={plan_id}"
            f"&user_id={user_id}"
            f"&customer_id={customer_id}"
        )
        return CheckoutSession(checkout_url=checkout_url, session_id=session_id)

    async def verify_and_parse_webhook(
        self,
        payload: bytes,
        signature: str | None,
    ) -> WebhookEvent:
        # In noop mode there's no signature to verify — the payload is trusted
        # local JSON produced by the dev simulate endpoint.
        data = json.loads(payload.decode("utf-8"))
        plan = get_plan(data.get("plan_id", "")) if data.get("plan_id") else None
        now = int(time.time())
        period = 30 * 24 * 3600 if (plan and plan.interval == "monthly") else 365 * 24 * 3600

        return WebhookEvent(
            event_type=data["event_type"],
            customer_id=data.get("customer_id"),
            subscription_id=data.get("subscription_id", f"noop_sub_{uuid.uuid4().hex[:12]}"),
            invoice_id=data.get("invoice_id", f"noop_inv_{uuid.uuid4().hex[:12]}"),
            amount=plan.price_usd if plan else data.get("amount"),
            currency="usd",
            period_start=data.get("period_start", now),
            period_end=data.get("period_end", now + period),
            status=data.get("status", "active"),
            user_id=data.get("user_id"),
            plan_id=data.get("plan_id"),
            raw=data,
        )

    async def cancel_subscription(self, subscription_id: str) -> bool:
        # Nothing to call — cancellation is reflected by our own webhook/DB update.
        return True

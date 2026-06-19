"""
Dev-only endpoints for simulating the billing flow without a real payment
processor. Mounted ONLY when BILLING_PROVIDER=noop (see main.py).
"""
import json
import logging

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.billing.factory import get_billing_provider
from app.billing.plans import get_plan
from app.billing.webhook_processor import process_webhook_event
from app.db.base import AsyncSessionLocal

logger = logging.getLogger("crawlox.billing")

router = APIRouter(tags=["dev"])


@router.get("/dev/checkout", response_class=HTMLResponse)
async def dev_checkout(session_id: str, plan_id: str, user_id: str, customer_id: str):
    """
    Simulated checkout page. In a real flow this is Stripe's hosted page.
    Visiting it (or POSTing the confirm) fires a checkout.completed webhook.
    Here we auto-complete and return a tiny confirmation page.
    """
    # Build a noop checkout.completed event and run it through the webhook pipeline
    event_payload = json.dumps({
        "event_type": "checkout.completed",
        "user_id": user_id,
        "plan_id": plan_id,
        "customer_id": customer_id,
        "subscription_id": f"noop_sub_{session_id}",
    }).encode("utf-8")

    provider = get_billing_provider()
    event = await provider.verify_and_parse_webhook(event_payload, None)
    async with AsyncSessionLocal() as db:
        result = await process_webhook_event(db, event)

    plan = get_plan(plan_id)
    return f"""
    <html><body style="font-family:sans-serif;max-width:420px;margin:60px auto;text-align:center">
      <h2>✅ Payment simulated</h2>
      <p>Plan: <b>{plan.name if plan else plan_id}</b></p>
      <p>Result: <code>{result}</code></p>
      <p style="color:#888">(dev noop mode — no real charge)</p>
      <a href="/">Return to app</a>
    </body></html>
    """


class SimulateWebhookRequest(BaseModel):
    event_type: str          # checkout.completed | invoice.paid | invoice.payment_failed |
                             # customer.subscription.updated | customer.subscription.deleted
    user_id: str
    plan_id: str | None = None
    customer_id: str | None = None
    subscription_id: str | None = None
    status: str | None = None


@router.post("/api/dev/simulate-webhook")
async def simulate_webhook(body: SimulateWebhookRequest):
    """Fire an arbitrary billing webhook event (noop mode testing)."""
    payload = json.dumps(body.model_dump()).encode("utf-8")
    provider = get_billing_provider()
    try:
        event = await provider.verify_and_parse_webhook(payload, None)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    async with AsyncSessionLocal() as db:
        result = await process_webhook_event(db, event)
    return result

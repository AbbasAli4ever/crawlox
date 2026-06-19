from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class CheckoutSession:
    checkout_url: str
    session_id: str


@dataclass
class WebhookEvent:
    """Normalized webhook event — provider-agnostic."""
    event_type: str          # checkout.completed | invoice.paid | invoice.payment_failed |
                             # customer.subscription.updated | customer.subscription.deleted
    customer_id: str | None
    subscription_id: str | None
    invoice_id: str | None
    amount: float | None
    currency: str | None
    period_start: int | None  # unix ts
    period_end: int | None
    status: str | None        # active | canceled | past_due | etc.
    user_id: str | None = None  # our internal user id, when carried in metadata
    plan_id: str | None = None
    raw: dict | None = None


class BillingProvider(ABC):
    """
    Provider-agnostic billing interface. Implementations: StripeProvider, NoopBillingProvider.
    Swapping providers is an env-var change (BILLING_PROVIDER) — no code changes.
    """

    @abstractmethod
    async def create_checkout(
        self,
        user_id: str,
        user_email: str,
        plan_id: str,
        existing_customer_id: str | None,
    ) -> CheckoutSession:
        """Create a checkout session for a plan. Returns a redirect URL."""
        ...

    @abstractmethod
    async def verify_and_parse_webhook(
        self,
        payload: bytes,
        signature: str | None,
    ) -> WebhookEvent:
        """Verify webhook authenticity and return a normalized event. Raises on invalid."""
        ...

    @abstractmethod
    async def cancel_subscription(self, subscription_id: str) -> bool:
        """Cancel a subscription at period end. Returns True on success."""
        ...

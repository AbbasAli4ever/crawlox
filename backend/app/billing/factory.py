import logging

from app.billing.interface import BillingProvider
from app.billing.noop_provider import NoopBillingProvider
from app.config import settings

logger = logging.getLogger("crawlox.billing")

_provider: BillingProvider | None = None


def get_billing_provider() -> BillingProvider:
    """
    Return the configured billing provider (singleton).
    - BILLING_PROVIDER=stripe + key present → StripeProvider
    - otherwise → NoopBillingProvider (dev/test, no account needed)
    """
    global _provider
    if _provider is not None:
        return _provider

    if settings.billing_provider == "stripe":
        try:
            from app.billing.stripe_provider import StripeProvider
            _provider = StripeProvider()
            logger.info("Billing provider: Stripe")
            return _provider
        except Exception as e:
            logger.warning(
                "Stripe provider unavailable (%s) — using NoopBillingProvider", e
            )

    _provider = NoopBillingProvider()
    logger.info("Billing provider: Noop (dev)")
    return _provider

"""
Subscription plans — source of truth in code (mirrors PLAN.md §3).
Stripe price IDs are injected from env so plans work without hardcoding.
"""
from dataclasses import dataclass
from typing import Literal

from app.config import settings

BillingInterval = Literal["monthly", "annual"]


@dataclass(frozen=True)
class Plan:
    id: str                       # internal id: "free" | "premium_monthly" | "premium_annual"
    name: str
    tier: str                     # "free" | "premium"
    interval: BillingInterval | None
    price_usd: float              # display price
    monthly_credits: int
    max_items_per_scrape: int
    captcha_mode: str             # "manual" | "auto"
    exports: list[str]
    webhooks: bool
    retention_days: int
    api_rate_limit_per_min: int
    stripe_price_id: str | None = None


# Free tier — no Stripe involvement
FREE = Plan(
    id="free",
    name="Free",
    tier="free",
    interval=None,
    price_usd=0.0,
    monthly_credits=100,
    max_items_per_scrape=500,
    captcha_mode="manual",
    exports=["json"],
    webhooks=False,
    retention_days=30,
    api_rate_limit_per_min=100,
)

PREMIUM_MONTHLY = Plan(
    id="premium_monthly",
    name="Premium (Monthly)",
    tier="premium",
    interval="monthly",
    price_usd=29.0,
    monthly_credits=5000,
    max_items_per_scrape=10_000,
    captcha_mode="auto",
    exports=["json", "csv", "excel"],
    webhooks=True,
    retention_days=90,
    api_rate_limit_per_min=1000,
    stripe_price_id=settings.stripe_price_monthly or None,
)

PREMIUM_ANNUAL = Plan(
    id="premium_annual",
    name="Premium (Annual)",
    tier="premium",
    interval="annual",
    price_usd=290.0,  # ~17% off (2 months free)
    monthly_credits=5000,
    max_items_per_scrape=10_000,
    captcha_mode="auto",
    exports=["json", "csv", "excel"],
    webhooks=True,
    retention_days=90,
    api_rate_limit_per_min=1000,
    stripe_price_id=settings.stripe_price_annual or None,
)

ALL_PLANS = [FREE, PREMIUM_MONTHLY, PREMIUM_ANNUAL]
_BY_ID = {p.id: p for p in ALL_PLANS}


def get_plan(plan_id: str) -> Plan | None:
    return _BY_ID.get(plan_id)


def plan_for_tier(tier: str) -> Plan:
    """Return the canonical plan for a tier (used to apply credits/limits)."""
    return PREMIUM_MONTHLY if tier == "premium" else FREE

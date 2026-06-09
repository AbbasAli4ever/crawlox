import logging
import re
from dataclasses import dataclass
from typing import Literal

from playwright.async_api import Page

logger = logging.getLogger("crawlox.captcha")

CaptchaType = Literal[
    "recaptcha_v2", "recaptcha_v3", "hcaptcha", "cloudflare", "text", "none"
]


@dataclass
class CaptchaContext:
    detected: bool
    captcha_type: CaptchaType
    sitekey: str | None
    page_url: str
    screenshot_b64: str | None = None


# --- DOM signal patterns ---

_RECAPTCHA_V2_SIGNALS = [
    r"google\.com/recaptcha",
    r"recaptcha/api\.js",
    r"g-recaptcha",
    r"grecaptcha\.render",
]

_RECAPTCHA_V3_SIGNALS = [
    r"grecaptcha\.execute",
    r"recaptcha/api\.js\?render=",
]

_HCAPTCHA_SIGNALS = [
    r"hcaptcha\.com",
    r"h-captcha",
    r"hcaptcha\.render",
]

_CLOUDFLARE_SIGNALS = [
    r"cf-browser-verification",
    r"cloudflare ray id",
    r"checking your browser",
    r"cdn-cgi/challenge-platform",
    r"__cf_chl",
]

_TEXT_CAPTCHA_SIGNALS = [
    r"captcha",
    r"are you human",
    r"verify you are not a robot",
    r"bot verification",
]


def _extract_sitekey(html: str, captcha_type: CaptchaType) -> str | None:
    """Extract sitekey from page HTML."""
    if captcha_type in ("recaptcha_v2", "recaptcha_v3"):
        # data-sitekey attribute
        m = re.search(r'data-sitekey=["\']([^"\']+)["\']', html, re.IGNORECASE)
        if m:
            return m.group(1)
        # render= query param (v3)
        m = re.search(r'render=([A-Za-z0-9_\-]{20,})', html)
        if m:
            return m.group(1)

    elif captcha_type == "hcaptcha":
        m = re.search(r'data-sitekey=["\']([^"\']+)["\']', html, re.IGNORECASE)
        if m:
            return m.group(1)

    return None


def _check_signals(html: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if re.search(pattern, html, re.IGNORECASE):
            return True
    return False


async def detect_captcha(page: Page) -> CaptchaContext:
    """
    Analyze current page for CAPTCHA presence.
    Checks both rendered HTML content and iframes.
    Returns a CaptchaContext with detection details.
    """
    html = await page.content()
    url = page.url

    # Check for Cloudflare first (whole-page block)
    if _check_signals(html, _CLOUDFLARE_SIGNALS):
        logger.info("Cloudflare challenge detected at %s", url)
        return CaptchaContext(
            detected=True,
            captcha_type="cloudflare",
            sitekey=None,
            page_url=url,
        )

    # Check hCaptcha BEFORE reCAPTCHA — both use data-sitekey, hCaptcha must win
    if _check_signals(html, _HCAPTCHA_SIGNALS):
        sitekey = _extract_sitekey(html, "hcaptcha")
        logger.info("hCaptcha detected at %s (sitekey=%s)", url, sitekey)
        return CaptchaContext(
            detected=True,
            captcha_type="hcaptcha",
            sitekey=sitekey,
            page_url=url,
        )

    # Check for reCAPTCHA v3 (invisible, score-based)
    if _check_signals(html, _RECAPTCHA_V3_SIGNALS):
        if re.search(r'render=([A-Za-z0-9_\-]{20,})', html):
            sitekey = _extract_sitekey(html, "recaptcha_v3")
            logger.info("reCAPTCHA v3 detected at %s (sitekey=%s)", url, sitekey)
            return CaptchaContext(
                detected=True,
                captcha_type="recaptcha_v3",
                sitekey=sitekey,
                page_url=url,
            )

    # Check for reCAPTCHA v2 (visible checkbox / invisible badge)
    if _check_signals(html, _RECAPTCHA_V2_SIGNALS):
        sitekey = _extract_sitekey(html, "recaptcha_v2")
        logger.info("reCAPTCHA v2 detected at %s (sitekey=%s)", url, sitekey)
        return CaptchaContext(
            detected=True,
            captcha_type="recaptcha_v2",
            sitekey=sitekey,
            page_url=url,
        )

    # Check for generic text CAPTCHA — only if page has very little content
    # (avoids false positives on pages that mention "captcha" in their copy)
    body_text = await page.evaluate("document.body ? document.body.innerText : ''")
    if len(body_text.strip()) < 500 and _check_signals(html, _TEXT_CAPTCHA_SIGNALS):
        logger.info("Text CAPTCHA detected at %s", url)
        return CaptchaContext(
            detected=True,
            captcha_type="text",
            sitekey=None,
            page_url=url,
        )

    return CaptchaContext(
        detected=False,
        captcha_type="none",
        sitekey=None,
        page_url=url,
    )


async def detect_and_capture(page: Page) -> CaptchaContext:
    """Detect CAPTCHA and capture a screenshot if found."""
    import base64
    ctx = await detect_captcha(page)
    if ctx.detected:
        screenshot_bytes = await page.screenshot(type="png", full_page=False)
        ctx.screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
    return ctx

import logging

from playwright.async_api import Page

logger = logging.getLogger("crawlox.captcha")


async def inject_recaptcha_v2(page: Page, token: str) -> bool:
    """Inject a reCAPTCHA v2 token into the page and submit."""
    try:
        # Set the g-recaptcha-response textarea (hidden by Google)
        await page.evaluate(
            """(token) => {
                // Set response in the textarea
                const textarea = document.querySelector('#g-recaptcha-response');
                if (textarea) {
                    Object.defineProperty(textarea, 'value', { writable: true });
                    textarea.value = token;
                }
                // Also set via grecaptcha if available
                if (window.grecaptcha && window.grecaptcha.getResponse) {
                    // Patch getResponse to return our token
                    const originalGetResponse = window.grecaptcha.getResponse;
                    window.grecaptcha.getResponse = () => token;
                }
                // Trigger change event to notify the page
                if (textarea) {
                    textarea.dispatchEvent(new Event('change', { bubbles: true }));
                }
            }""",
            token,
        )
        logger.info("reCAPTCHA v2 token injected successfully")
        return True
    except Exception as e:
        logger.warning("reCAPTCHA v2 injection failed: %s", e)
        return False


async def inject_hcaptcha(page: Page, token: str) -> bool:
    """Inject an hCaptcha token into the page."""
    try:
        await page.evaluate(
            """(token) => {
                // Set h-captcha-response
                const textarea = document.querySelector('[name="h-captcha-response"]');
                if (textarea) {
                    Object.defineProperty(textarea, 'value', { writable: true });
                    textarea.value = token;
                    textarea.dispatchEvent(new Event('change', { bubbles: true }));
                }
                // Also try the g-recaptcha-response field hCaptcha sometimes uses
                const rc = document.querySelector('[name="g-recaptcha-response"]');
                if (rc) {
                    Object.defineProperty(rc, 'value', { writable: true });
                    rc.value = token;
                }
            }""",
            token,
        )
        logger.info("hCaptcha token injected successfully")
        return True
    except Exception as e:
        logger.warning("hCaptcha injection failed: %s", e)
        return False


async def inject_captcha_solution(
    page: Page,
    captcha_type: str,
    token: str,
) -> bool:
    """Dispatch to the correct injector based on captcha_type."""
    if captcha_type in ("recaptcha_v2", "recaptcha_v3"):
        return await inject_recaptcha_v2(page, token)
    elif captcha_type == "hcaptcha":
        return await inject_hcaptcha(page, token)
    else:
        # Generic fallback — try reCAPTCHA method
        logger.warning("Unknown captcha_type '%s', trying generic injection", captcha_type)
        return await inject_recaptcha_v2(page, token)


async def submit_form_after_captcha(page: Page) -> bool:
    """
    Try to submit the form after CAPTCHA is solved.
    Looks for common submit button patterns.
    """
    try:
        selectors = [
            "input[type='submit']",
            "button[type='submit']",
            "button.submit",
            "#submit",
            ".g-recaptcha + input",
        ]
        for sel in selectors:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                await page.wait_for_load_state("domcontentloaded", timeout=10_000)
                logger.info("Form submitted after CAPTCHA using selector: %s", sel)
                return True
    except Exception as e:
        logger.warning("Form submit after CAPTCHA failed: %s", e)
    return False

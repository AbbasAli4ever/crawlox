import asyncio
import logging

from twocaptcha import TwoCaptcha

from app.captcha.solver import CaptchaSolver, FallbackToManual, SolveResult
from app.config import settings

logger = logging.getLogger("crawlox.captcha")

# 2Captcha pricing (approximate, USD per solve) — used to log cost.
# Real cost is billed by 2Captcha; these are conservative estimates.
_COST_PER_SOLVE = {
    "recaptcha_v2": 0.0029,
    "recaptcha_v3": 0.0029,
    "hcaptcha": 0.0029,
    "text": 0.001,
}

# Hard cap on how long we wait for 2Captcha (spec: poll every 5s, max 2 min)
_MAX_WAIT_S = 120
_POLL_INTERVAL_S = 5


class TwoCaptchaSolver(CaptchaSolver):
    """
    Premium auto-solver using the 2Captcha API.
    The 2Captcha SDK is synchronous, so each call is run in a thread executor.
    On any failure, raises FallbackToManual so the orchestrator can switch to
    the manual WebSocket flow.
    """

    def __init__(self):
        if not settings.twocaptcha_api_key:
            raise FallbackToManual("2Captcha API key not configured")
        self._client = TwoCaptcha(
            settings.twocaptcha_api_key,
            defaultTimeout=_MAX_WAIT_S,
            pollingInterval=_POLL_INTERVAL_S,
        )

    def _solve_sync(self, captcha_type: str, sitekey: str, page_url: str) -> dict:
        """Blocking call into the 2Captcha SDK. Runs in a thread."""
        if captcha_type in ("recaptcha_v2", "recaptcha_v3"):
            version = "v3" if captcha_type == "recaptcha_v3" else "v2"
            return self._client.recaptcha(
                sitekey=sitekey,
                url=page_url,
                version=version,
            )
        elif captcha_type == "hcaptcha":
            return self._client.hcaptcha(sitekey=sitekey, url=page_url)
        else:
            raise FallbackToManual(
                f"2Captcha auto-solve not supported for type '{captcha_type}'"
            )

    async def solve(
        self,
        captcha_type: str,
        sitekey: str | None,
        page_url: str,
        task_id: str,
    ) -> SolveResult:
        if not sitekey:
            logger.warning(
                "TwoCaptchaSolver: no sitekey for task %s — falling back to manual",
                task_id,
            )
            raise FallbackToManual("No sitekey available for 2Captcha")

        logger.info(
            "TwoCaptchaSolver: submitting %s (sitekey=%s) for task %s",
            captcha_type, sitekey, task_id,
        )

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self._solve_sync, captcha_type, sitekey, page_url
                ),
                timeout=_MAX_WAIT_S + 10,  # SDK timeout + small buffer
            )
        except asyncio.TimeoutError:
            logger.warning("TwoCaptchaSolver: timed out for task %s", task_id)
            raise FallbackToManual("2Captcha timed out")
        except FallbackToManual:
            raise
        except Exception as e:
            logger.warning("TwoCaptchaSolver: error for task %s — %s", task_id, e)
            raise FallbackToManual(f"2Captcha error: {e}")

        token = result.get("code") if isinstance(result, dict) else None
        if not token:
            logger.warning("TwoCaptchaSolver: no token returned for task %s", task_id)
            raise FallbackToManual("2Captcha returned no token")

        cost = _COST_PER_SOLVE.get(captcha_type, 0.003)
        logger.info(
            "TwoCaptchaSolver: solved task %s (cost≈$%.4f)", task_id, cost
        )
        return SolveResult(success=True, token=token, cost=cost)

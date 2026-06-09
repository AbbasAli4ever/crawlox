from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SolveResult:
    success: bool
    token: str | None
    error: str | None = None
    cost: float = 0.0


class CaptchaSolver(ABC):
    """Base interface for all CAPTCHA solving strategies."""

    @abstractmethod
    async def solve(
        self,
        captcha_type: str,
        sitekey: str | None,
        page_url: str,
        task_id: str,
    ) -> SolveResult:
        """
        Attempt to solve a CAPTCHA.
        Returns SolveResult with token on success.
        """
        ...


class ManualOnlySolver(CaptchaSolver):
    """
    Waits for a human solution delivered via WebSocket → Redis pubsub.
    Used for free-tier users and as fallback when 2Captcha is unavailable.
    """

    def __init__(self, timeout_s: int = 300):
        self.timeout_s = timeout_s

    async def solve(
        self,
        captcha_type: str,
        sitekey: str | None,
        page_url: str,
        task_id: str,
    ) -> SolveResult:
        from app.captcha.store import wait_for_solution
        import logging
        logger = logging.getLogger("crawlox.captcha")

        logger.info(
            "ManualOnlySolver: waiting up to %ds for human solution on task %s",
            self.timeout_s, task_id,
        )
        solution = await wait_for_solution(task_id, timeout_s=self.timeout_s)

        if solution:
            logger.info("ManualOnlySolver: solution received for task %s", task_id)
            return SolveResult(success=True, token=solution)

        logger.warning("ManualOnlySolver: timed out waiting for task %s", task_id)
        return SolveResult(success=False, token=None, error="Timed out waiting for manual solution")


class FallbackToManual(Exception):
    """Raised by auto-solvers to signal they want to fall back to manual flow."""
    pass


def get_solver(user_tier: str, captcha_solver_env: str) -> CaptchaSolver:
    """
    Select solver based on user tier and env config.
    - premium + CAPTCHA_SOLVER=twocaptcha → TwoCaptchaSolver (falls back to
      manual at solve time if 2Captcha fails)
    - everything else → ManualOnlySolver

    If TwoCaptchaSolver can't be constructed (missing SDK or no API key),
    we silently fall back to ManualOnlySolver — premium still works, just manually.
    """
    import logging
    logger = logging.getLogger("crawlox.captcha")

    if user_tier == "premium" and captcha_solver_env == "twocaptcha":
        try:
            from app.captcha.twocaptcha_solver import TwoCaptchaSolver
            return TwoCaptchaSolver()
        except (ImportError, FallbackToManual) as e:
            logger.info("TwoCaptchaSolver unavailable (%s) — using ManualOnlySolver", e)
    return ManualOnlySolver()

import asyncio
import logging
from typing import Callable, TypeVar

logger = logging.getLogger("crawlox.scraper")

T = TypeVar("T")


async def with_retry(
    fn: Callable,
    *args,
    max_attempts: int = 3,
    base_delay: float = 2.0,
    exceptions: tuple = (Exception,),
    **kwargs,
) -> T:
    """
    Call fn(*args, **kwargs) up to max_attempts times.
    Delay doubles after each failure (exponential backoff).
    Raises the last exception if all attempts fail.
    """
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await fn(*args, **kwargs)
        except exceptions as e:
            last_exc = e
            if attempt == max_attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "Attempt %d/%d failed (%s). Retrying in %.1fs...",
                attempt, max_attempts, type(e).__name__, delay,
            )
            await asyncio.sleep(delay)

    raise last_exc

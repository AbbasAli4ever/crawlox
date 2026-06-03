import logging
import re

from app.llm.gemini_client import GeminiClient
from app.llm.groq_client import GroqClient
from app.llm.interface import LLMClient
from app.llm.types import AnalysisResult, PageData

logger = logging.getLogger("crawlox.llm")

# HTML patterns that suggest CSS-driven or visually-complex layout
# where screenshot context helps Gemini more than raw HTML helps Groq
_VISUAL_COMPLEXITY_SIGNALS = [
    r"data-react",
    r"__NEXT_DATA__",
    r"ng-app",
    r"v-app",
    r"data-component",
    r"window\.__",
    r"<canvas",
]


def _is_visually_complex(html: str) -> bool:
    """
    Return True only when the page is a known JS SPA framework that renders
    its content client-side — where screenshot input gives Gemini a real edge.
    The text/tag ratio check was removed: real rendered pages always have low
    ratios due to nav/footer/scripts, causing Groq to be bypassed incorrectly.
    """
    for pattern in _VISUAL_COMPLEXITY_SIGNALS:
        if re.search(pattern, html, re.IGNORECASE):
            return True
    return False


class LLMRouter(LLMClient):
    """
    Routes analysis requests between Groq (fast, text-only) and Gemini (vision).

    Routing rules:
    1. Visually complex page → skip Groq, go straight to Gemini
    2. Try Groq (up to 3 retries with prompt tightening)
    3. On Groq error / rate limit / all retries exhausted → fallback to Gemini
    4. Log provider + latency + fallback_reason per call
    """

    def __init__(self):
        self._groq = GroqClient()
        self._gemini = GeminiClient()

    async def analyze(self, page: PageData) -> AnalysisResult:
        # Rule 1: visually complex → skip to Gemini
        if _is_visually_complex(page.html):
            logger.info("Page flagged visually complex — routing directly to Gemini")
            result = await self._gemini.analyze(page)
            result.fallback_reason = "visually_complex"
            return result

        # Rule 2: try Groq first
        try:
            result = await self._groq.analyze(page)
            result.fallback_reason = ""
            return result

        except Exception as groq_exc:
            fallback_reason = type(groq_exc).__name__
            logger.warning(
                "Groq failed (%s) — falling back to Gemini: %s",
                fallback_reason, groq_exc,
            )

        # Rule 3: fallback to Gemini
        result = await self._gemini.analyze(page)
        result.fallback_reason = fallback_reason
        return result

    async def generate_script(self, analysis: AnalysisResult, url: str) -> str:
        """Script generation: Groq primary, Gemini fallback."""
        try:
            return await self._groq.generate_script(analysis, url)
        except Exception as e:
            logger.warning("Groq script gen failed (%s) — falling back to Gemini", e)
            return await self._gemini.generate_script(analysis, url)


# Module-level singleton — one router shared across the app
_router: LLMRouter | None = None


def get_llm_router() -> LLMRouter:
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router

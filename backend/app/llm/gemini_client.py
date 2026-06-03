import asyncio
import base64
import logging
import re
import time

from google import genai
from google.genai import types as genai_types

from app.config import settings
from app.llm.interface import LLMClient
from app.llm.prompts import load_prompt
from app.llm.types import AnalysisResult, PageData
from app.llm.validator import parse_and_validate_analysis

logger = logging.getLogger("crawlox.llm")

MODEL = "gemini-2.5-flash"
# Gemini free tier: 10 RPM — enforce minimum gap between calls
_MIN_CALL_GAP_S = 7.0
_last_call_time: float = 0.0


def _parse_retry_after(error_str: str) -> float:
    """Extract retry delay seconds from Gemini 429 error message."""
    m = re.search(r"retry_delay\s*\{\s*seconds:\s*(\d+)", error_str)
    if m:
        return float(m.group(1)) + 2.0  # add 2s buffer
    return 15.0  # conservative default


class GeminiClient(LLMClient):
    def __init__(self):
        self._client = genai.Client(api_key=settings.gemini_api_key)

    async def _call_with_ratelimit(self, contents, config) -> str:
        """Make a Gemini API call, respecting the per-minute rate limit."""
        global _last_call_time

        # Enforce minimum gap between calls
        elapsed = time.monotonic() - _last_call_time
        if elapsed < _MIN_CALL_GAP_S:
            wait = _MIN_CALL_GAP_S - elapsed
            logger.debug("Gemini rate limit gap: waiting %.1fs", wait)
            await asyncio.sleep(wait)

        _last_call_time = time.monotonic()
        response = self._client.models.generate_content(
            model=MODEL,
            contents=contents,
            config=config,
        )
        return response.text or ""

    async def analyze(self, page: PageData) -> AnalysisResult:
        """
        Analyze page using Gemini (multimodal if screenshot provided, text-only otherwise).
        Retries up to 3 times. Respects 429 retry-after headers.
        """
        prompt_name = "analysis_multimodal" if page.screenshot_b64 else "analysis_text_only"
        prompt_text = load_prompt(
            prompt_name,
            url=page.url,
            html=page.html[:15000],
            network_log=page.network_log,
        )

        config = genai_types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=4096,  # increased from 2048 to avoid truncation
        )

        last_error: Exception | None = None

        for attempt in range(1, 4):
            t0 = time.monotonic()
            try:
                contents: list = [prompt_text]
                if page.screenshot_b64:
                    image_bytes = base64.b64decode(page.screenshot_b64)
                    contents.append(
                        genai_types.Part.from_bytes(data=image_bytes, mime_type="image/png")
                    )

                raw = await self._call_with_ratelimit(contents, config)
                latency_ms = int((time.monotonic() - t0) * 1000)

                result = parse_and_validate_analysis(raw)
                result.provider = "gemini"
                result.latency_ms = latency_ms
                logger.info("Gemini analysis OK (attempt %d, %dms)", attempt, latency_ms)
                return result

            except ValueError as e:
                last_error = e
                logger.warning("Gemini attempt %d: JSON validation failed — %s", attempt, e)
                if attempt < 3:
                    prompt_text += f"\n\nPrevious attempt failed: {e}\nReturn ONLY valid JSON, nothing else."

            except Exception as e:
                last_error = e
                error_str = str(e)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    wait = _parse_retry_after(error_str)
                    logger.warning(
                        "Gemini attempt %d: rate limited — waiting %.0fs before retry",
                        attempt, wait,
                    )
                    await asyncio.sleep(wait)
                elif attempt < 3:
                    await asyncio.sleep(3.0 * attempt)
                else:
                    logger.warning("Gemini attempt %d: API error — %s", attempt, e)

        raise ValueError(f"Gemini analysis failed after 3 attempts: {last_error}")

    async def generate_script(self, analysis: AnalysisResult, url: str) -> str:
        """Generate Playwright script using Gemini."""
        import json as _json
        fields_json = _json.dumps([
            {"name": f.name, "selector": f.selector, "type": f.type, "required": f.required}
            for f in analysis.data_structure.fields
        ])

        prompt = load_prompt(
            "script_generation",
            url=url,
            website_type=analysis.website_type,
            pagination_type=analysis.pagination_type,
            container_selector=analysis.data_structure.container_selector,
            fields_json=fields_json,
            recommended_delay_seconds=analysis.recommended_delay_seconds,
            captcha_detected=str(analysis.captcha_detected).lower(),
        )

        config = genai_types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=4096,
        )

        t0 = time.monotonic()
        raw = await self._call_with_ratelimit([prompt], config)
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.info("Gemini script generation OK (%dms)", latency_ms)
        return raw.strip()

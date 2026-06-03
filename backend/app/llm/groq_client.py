import asyncio
import logging
import time

import httpx
from groq import AsyncGroq

from app.config import settings
from app.llm.interface import LLMClient
from app.llm.prompts import load_prompt
from app.llm.types import AnalysisResult, PageData
from app.llm.validator import parse_and_validate_analysis

logger = logging.getLogger("crawlox.llm")

MODEL = "llama-3.3-70b-versatile"
MAX_TOKENS = 2048
TEMPERATURE = 0.1  # low temp for deterministic JSON output


class GroqClient(LLMClient):
    def __init__(self):
        # Explicit httpx client avoids connection issues in some async environments
        self._http = httpx.AsyncClient(timeout=60.0)
        self._client = AsyncGroq(api_key=settings.groq_api_key, http_client=self._http)

    async def analyze(self, page: PageData) -> AnalysisResult:
        """
        Analyze page structure using Groq text-only model.
        Retries up to 2 times with a tightened prompt on JSON validation failure.
        """
        prompt = load_prompt(
            "analysis_text_only",
            url=page.url,
            html=page.html[:15000],
            network_log=page.network_log,
        )

        last_error: Exception | None = None
        for attempt in range(1, 4):  # 3 attempts total
            t0 = time.monotonic()
            try:
                response = await self._client.chat.completions.create(
                    model=MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=MAX_TOKENS,
                    temperature=TEMPERATURE,
                )
                raw = response.choices[0].message.content or ""
                latency_ms = int((time.monotonic() - t0) * 1000)

                result = parse_and_validate_analysis(raw)
                result.provider = "groq"
                result.latency_ms = latency_ms
                logger.info("Groq analysis OK (attempt %d, %dms)", attempt, latency_ms)
                return result

            except ValueError as e:
                last_error = e
                logger.warning("Groq attempt %d: JSON validation failed — %s", attempt, e)
                if attempt < 3:
                    # Tighten prompt by adding explicit failure feedback
                    prompt += f"\n\nPrevious attempt failed validation: {e}\nReturn ONLY valid JSON, nothing else."
                    await asyncio.sleep(1.0)

            except Exception as e:
                last_error = e
                logger.warning("Groq attempt %d: API error — %s", attempt, e)
                if attempt < 3:
                    await asyncio.sleep(2.0 * attempt)

        raise ValueError(f"Groq analysis failed after 3 attempts: {last_error}")

    async def generate_script(self, analysis: AnalysisResult, url: str) -> str:
        """Generate a Playwright scraping script from analysis JSON."""
        import json as _json
        fields_json = _json.dumps(
            [
                {"name": f.name, "selector": f.selector, "type": f.type, "required": f.required}
                for f in analysis.data_structure.fields
            ]
        )

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

        t0 = time.monotonic()
        response = await self._client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
            temperature=0.1,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        script = response.choices[0].message.content or ""
        logger.info("Groq script generation OK (%dms)", latency_ms)
        return script.strip()

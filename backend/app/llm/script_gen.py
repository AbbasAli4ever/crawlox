import logging
import re

from app.llm.types import AnalysisResult

logger = logging.getLogger("crawlox.llm")

# Strings that must appear in a valid generated script
_REQUIRED_PATTERNS = [
    r"async_playwright",
    r"async def main",
    r"asyncio\.run",
    r"json\.dumps",
]

# Third-party imports that aren't installed — strip them from generated scripts
_BANNED_IMPORTS = ["numpy", "pandas", "scipy", "sklearn", "torch", "tensorflow", "requests"]


def _clean_script(raw: str) -> str:
    """Strip markdown code fences from LLM output."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:python)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _strip_bad_imports(script: str) -> str:
    """Remove import lines for packages not available in the sandbox."""
    lines = script.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        skip = False
        for pkg in _BANNED_IMPORTS:
            if re.match(rf"^import {pkg}|^from {pkg}", stripped):
                logger.warning("Stripped banned import: %s", stripped)
                skip = True
                break
        if not skip:
            cleaned.append(line)
    return "\n".join(cleaned)


def validate_script(script: str) -> str:
    """
    Clean, sanitize and validate a generated script.
    Returns the cleaned script or raises ValueError.
    """
    cleaned = _clean_script(script)
    cleaned = _strip_bad_imports(cleaned)

    for pattern in _REQUIRED_PATTERNS:
        if not re.search(pattern, cleaned):
            raise ValueError(
                f"Generated script missing required pattern: {pattern}"
            )

    # Must not contain obvious injection risks
    if "os.system" in cleaned or "subprocess.call" in cleaned or "__import__('os').system" in cleaned:
        raise ValueError("Generated script contains disallowed system calls")

    return cleaned


async def generate_and_validate_script(
    router,
    analysis: AnalysisResult,
    url: str,
    max_attempts: int = 2,
) -> str:
    """
    Generate a Playwright script via the LLM router, validate it.
    Retries once with an error message if validation fails.
    """
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        raw = await router.generate_script(analysis, url)
        try:
            script = validate_script(raw)
            logger.info(
                "Script generated OK (attempt %d, %d chars, provider=%s)",
                attempt, len(script), analysis.provider,
            )
            return script
        except ValueError as e:
            last_error = e
            logger.warning("Script validation failed attempt %d: %s", attempt, e)
            # Patch analysis with failure context for next attempt
            if attempt < max_attempts:
                analysis.fallback_reason = f"script_validation_failed: {e}"

    raise ValueError(f"Script generation failed after {max_attempts} attempts: {last_error}")

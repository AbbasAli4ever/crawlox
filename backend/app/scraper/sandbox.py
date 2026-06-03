import asyncio
import json
import logging
import os
import sys
import tempfile

logger = logging.getLogger("crawlox.scraper")

# Hard limits for sandbox execution
SANDBOX_TIMEOUT = 300  # 5 minutes max
MAX_OUTPUT_BYTES = 10 * 1024 * 1024  # 10 MB


async def execute_script(script: str, timeout: int = SANDBOX_TIMEOUT) -> dict:
    """
    Write script to a temp file and run it as a subprocess.
    The script must print its results as JSON to stdout.
    Returns {"success": bool, "items": [...], "error": str | None}
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        # Inject COOKIE_LOAD / COOKIE_SAVE stubs so the script runs standalone
        script_with_stubs = _inject_stubs(script)
        f.write(script_with_stubs)
        script_path = f.name

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONPATH": "/app"},
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "success": False,
                "items": [],
                "error": f"Script execution timed out after {timeout}s",
            }

        stdout_text = stdout[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        stderr_text = stderr[:4096].decode("utf-8", errors="replace")

        if proc.returncode != 0:
            logger.warning("Script exited %d. stderr: %s", proc.returncode, stderr_text[:500])
            return {
                "success": False,
                "items": [],
                "error": f"Script exited with code {proc.returncode}: {stderr_text[:300]}",
            }

        # Script must print JSON as last non-empty line
        lines = [l.strip() for l in stdout_text.splitlines() if l.strip()]
        if not lines:
            return {"success": False, "items": [], "error": "Script produced no output"}

        # Try to find a JSON array or object in stdout
        for line in reversed(lines):
            try:
                data = json.loads(line)
                if isinstance(data, list):
                    return {"success": True, "items": data, "error": None}
                if isinstance(data, dict):
                    items = data.get("items", data.get("results", [data]))
                    return {"success": True, "items": items, "error": None}
            except json.JSONDecodeError:
                continue

        return {
            "success": False,
            "items": [],
            "error": f"Script output was not valid JSON. stdout: {stdout_text[:300]}",
        }

    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


def _inject_stubs(script: str) -> str:
    """Replace COOKIE_LOAD / COOKIE_SAVE placeholders and enforce headless mode."""
    script = script.replace(
        "# COOKIE_LOAD: system will inject cookies here",
        "cookies = []  # stub: no cookies in sandbox mode",
    )
    script = script.replace(
        "# COOKIE_SAVE: system will persist cookies here",
        "pass  # stub: cookie persistence skipped in sandbox mode",
    )
    # Always force headless=True in sandbox — LLM sometimes generates headless=False
    import re
    script = re.sub(r"headless\s*=\s*False", "headless=True", script)
    return script

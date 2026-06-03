import json
from pathlib import Path

_PROMPT_DIR = Path(__file__).parent


def load_prompt(name: str, **variables) -> str:
    """Load a prompt template and substitute {{variable}} placeholders."""
    path = _PROMPT_DIR / f"{name}.md"
    template = path.read_text(encoding="utf-8")
    for key, value in variables.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value, indent=2)
        template = template.replace("{{" + key + "}}", str(value))
    return template

import json
import re

import jsonschema

from app.llm.types import (
    ANALYSIS_JSON_SCHEMA,
    AnalysisResult,
    DataStructure,
    FieldDefinition,
)


def _extract_json(text: str) -> str:
    """Strip markdown code fences and extract raw JSON from LLM output."""
    text = text.strip()
    # Remove ```json ... ``` or ``` ... ```
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_and_validate_analysis(raw: str) -> AnalysisResult:
    """
    Parse raw LLM output into an AnalysisResult.
    Raises ValueError on parse failure or schema validation failure.
    """
    cleaned = _extract_json(raw)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned invalid JSON: {e}\nRaw output: {raw[:300]}")

    try:
        jsonschema.validate(data, ANALYSIS_JSON_SCHEMA)
    except jsonschema.ValidationError as e:
        raise ValueError(f"LLM JSON failed schema validation: {e.message}")

    ds = data["data_structure"]
    fields = [
        FieldDefinition(
            name=f["name"],
            selector=f["selector"],
            type=f["type"],
            required=f.get("required", True),
        )
        for f in ds["fields"]
    ]

    return AnalysisResult(
        website_type=data["website_type"],
        framework=data["framework"],
        has_infinite_scroll=data["has_infinite_scroll"],
        pagination_type=data["pagination_type"],
        data_structure=DataStructure(
            container_selector=ds["container_selector"],
            fields=fields,
        ),
        captcha_detected=data["captcha_detected"],
        captcha_type=data["captcha_type"],
        anti_bot_detected=data["anti_bot_detected"],
        recommended_delay_seconds=data["recommended_delay_seconds"],
        recommended_proxy=data["recommended_proxy"],
    )

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class FieldDefinition:
    name: str
    selector: str
    type: Literal["text", "href", "image", "price", "number"]
    required: bool = True


@dataclass
class DataStructure:
    container_selector: str
    fields: list[FieldDefinition]


@dataclass
class AnalysisResult:
    website_type: Literal["ecommerce", "blog", "news", "social", "directory", "other"]
    framework: Literal["react", "vue", "angular", "wordpress", "shopify", "custom"]
    has_infinite_scroll: bool
    pagination_type: Literal["url_params", "next_button", "load_more", "infinite_scroll", "none"]
    data_structure: DataStructure
    captcha_detected: bool
    captcha_type: Literal["recaptcha_v2", "recaptcha_v3", "hcaptcha", "cloudflare", "text", "none"]
    anti_bot_detected: bool
    recommended_delay_seconds: int
    recommended_proxy: bool
    # routing metadata
    provider: str = ""
    latency_ms: int = 0
    fallback_reason: str = ""


@dataclass
class PageData:
    url: str
    html: str
    network_log: str
    screenshot_b64: str | None = None  # only provided to Gemini path


# JSON schema for validating LLM output
ANALYSIS_JSON_SCHEMA = {
    "type": "object",
    "required": [
        "website_type", "framework", "has_infinite_scroll",
        "pagination_type", "data_structure", "captcha_detected",
        "captcha_type", "anti_bot_detected",
        "recommended_delay_seconds", "recommended_proxy",
    ],
    "properties": {
        "website_type": {"type": "string", "enum": ["ecommerce", "blog", "news", "social", "directory", "other"]},
        "framework": {"type": "string", "enum": ["react", "vue", "angular", "wordpress", "shopify", "custom"]},
        "has_infinite_scroll": {"type": "boolean"},
        "pagination_type": {"type": "string", "enum": ["url_params", "next_button", "load_more", "infinite_scroll", "none"]},
        "data_structure": {
            "type": "object",
            "required": ["container_selector", "fields"],
            "properties": {
                "container_selector": {"type": "string"},
                "fields": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["name", "selector", "type", "required"],
                        "properties": {
                            "name": {"type": "string"},
                            "selector": {"type": "string"},
                            "type": {"type": "string", "enum": ["text", "href", "image", "price", "number"]},
                            "required": {"type": "boolean"},
                        },
                    },
                },
            },
        },
        "captcha_detected": {"type": "boolean"},
        "captcha_type": {"type": "string", "enum": ["recaptcha_v2", "recaptcha_v3", "hcaptcha", "cloudflare", "text", "none"]},
        "anti_bot_detected": {"type": "boolean"},
        "recommended_delay_seconds": {"type": "integer"},
        "recommended_proxy": {"type": "boolean"},
    },
    "additionalProperties": False,
}

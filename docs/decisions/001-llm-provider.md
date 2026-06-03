# ADR 001 — LLM Provider: Groq Primary + Gemini Fallback

## Decision

Use **Groq (Llama 3.3 70B)** as the primary LLM and **Google Gemini 2.0 Flash** as the fallback.

## Context

The analysis prompt requires:
1. Parsing HTML structure to identify containers, fields, and selectors
2. Detecting pagination type, CAPTCHA presence, anti-bot signals
3. Returning strict JSON matching a defined schema
4. (Fallback path) Understanding visually-complex pages via screenshot

The script generation prompt requires:
1. Generating valid Python/Playwright code from a JSON schema
2. No vision needed — structured input, structured output

## Options Considered

| Provider | Speed | Cost | Vision | JSON reliability | Free tier |
|---|---|---|---|---|---|
| Groq (Llama 3.3 70B) | Very fast (~1-2s) | Free tier generous | ❌ | Good with prompting | ✅ |
| Gemini 2.0 Flash | Fast (~2-4s) | Free tier generous | ✅ | Excellent | ✅ |
| OpenAI GPT-4o | Medium (~3-6s) | Paid only | ✅ | Excellent | ❌ |
| Anthropic Claude | Medium (~3-6s) | Paid only | ✅ | Excellent | ❌ |

## Decision Details

### Primary: Groq (Llama 3.3 70B)
- Fastest inference available (token generation ~10× faster than GPT-4o)
- Free tier covers all dev/testing and early production
- Handles HTML analysis well when page structure is semantic
- Used for both analysis (text-only) and script generation

### Fallback: Gemini 2.5 Flash
- Triggered when Groq: errors, rate limits, fails JSON schema validation after 2 retries,
  or page is flagged "visually complex" (sparse semantic HTML)
- Multimodal: receives HTML + screenshot + network log
- More robust for CSS-heavy, image-driven, or SPA-heavy layouts
- Free tier (Google AI Studio) covers dev and early production

### Prompt variants
- `analysis_text_only.md` — Groq path (HTML + network log, no screenshot)
- `analysis_multimodal.md` — Gemini path (HTML + screenshot + network log)
- `script_generation.md` — shared (Groq primary, no vision needed)

### Router logic
1. Try Groq with text-only prompt
2. On error / rate limit / timeout → fallback to Gemini
3. On JSON validation failure after 2 Groq retries → fallback to Gemini
4. On "visually complex" page flag → skip Groq, go straight to Gemini
5. Log provider + latency_ms + fallback_reason per call in usage_metrics

## Migration path
Both clients implement `LLMClient` interface. Swapping providers = one env var change.

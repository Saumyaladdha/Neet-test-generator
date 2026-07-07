import json
from pathlib import Path
from openai import OpenAI

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "detector" / "system_prompt.txt"


def load_system_prompt():
    """Load the detector system prompt from prompts/detector/system_prompt.txt."""
    if not _PROMPT_PATH.exists():
        raise FileNotFoundError(
            f"Detector prompt not found: {_PROMPT_PATH}\n"
            "Create prompts/detector/system_prompt.txt to continue."
        )
    return _PROMPT_PATH.read_text(encoding="utf-8")


def generate_question_distribution(
    image_urls=None,
    content_blocks=None,
    system_prompt=None,
    tools=None,
    model="gpt-5.4-mini",
    max_output_tokens=2048,
    api_key=None
):
    """
    Generate question difficulty distribution using OpenAI API.

    Args:
        image_urls:      List of HTTPS image URLs (for image uploads)
        content_blocks:  Pre-built list of OpenAI content blocks — use this for PDFs
                         e.g. [{"type": "input_file", "file_id": "file-abc123"}]
                         Takes precedence over image_urls when provided.
        system_prompt:   Override prompt (loads from file if None)
        tools:           Override tools list (uses TOOLS if None)
        model:           Model to use (default: gpt-5.4-mini)
        max_output_tokens: Maximum tokens in response (default: 2048)
        api_key:         Optional API key (uses OPENAI_API_KEY env var if not provided)

    Returns:
        The API response object
    """
    if system_prompt is None:
        system_prompt = load_system_prompt()
    if tools is None:
        tools = TOOLS

    client = OpenAI(api_key=api_key) if api_key else OpenAI()

    if content_blocks is not None:
        blocks = content_blocks
    else:
        blocks = [{"type": "input_image", "image_url": url} for url in (image_urls or [])]

    response = client.responses.create(
        model=model,
        instructions=system_prompt,
        input=[{"role": "user", "content": blocks}],
        tools=tools,
        text={"format": {"type": "text"}},
        reasoning={},
        max_output_tokens=max_output_tokens,
        store=True,
        include=["web_search_call.action.sources"]
    )

    return response


# ============================================================
# TOOLS — v1
# 9 counts + 9 reasoning strings = 18 fields
# ============================================================

TOOLS = [
    {
        "type": "function",
        "name": "set_question_distribution",
        "description": (
            "Report the maximum number of questions that can be generated from the content, "
            "split into 9 buckets: easy/medium/hard × MCQ/AR/MTC. "
            "Provide a reasoning string for each count."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "easy_mcq_count": {
                    "type": "integer",
                    "description": "Easy MCQ count — direct recall, <10 sec each."
                },
                "easy_mcq_reasoning": {
                    "type": "string",
                    "description": "Step-by-step reasoning for easy MCQ count."
                },
                "easy_ar_count": {
                    "type": "integer",
                    "description": "Easy A-R count — trivially obvious cause-effect, <10 sec each."
                },
                "easy_ar_reasoning": {
                    "type": "string",
                    "description": "Step-by-step reasoning for easy A-R count."
                },
                "easy_mtc_count": {
                    "type": "integer",
                    "description": "Easy MTC count — 2-3 pair obvious matching."
                },
                "easy_mtc_reasoning": {
                    "type": "string",
                    "description": "Step-by-step reasoning for easy MTC count."
                },
                "medium_mcq_count": {
                    "type": "integer",
                    "description": "Medium MCQ count — conceptual or long/confusing, 30-60 sec."
                },
                "medium_mcq_reasoning": {
                    "type": "string",
                    "description": "Step-by-step reasoning for medium MCQ count."
                },
                "medium_ar_count": {
                    "type": "integer",
                    "description": "Medium A-R count — requires deliberate analysis, 30-60 sec."
                },
                "medium_ar_reasoning": {
                    "type": "string",
                    "description": "Step-by-step reasoning for medium A-R count."
                },
                "medium_mtc_count": {
                    "type": "integer",
                    "description": "Medium MTC count — exactly 4 pairs per question."
                },
                "medium_mtc_reasoning": {
                    "type": "string",
                    "description": "Step-by-step reasoning for medium MTC count (show pair counting)."
                },
                "hard_mcq_count": {
                    "type": "integer",
                    "description": "Hard MCQ count — multi-step / verify-all / calculation, >60 sec."
                },
                "hard_mcq_reasoning": {
                    "type": "string",
                    "description": "Step-by-step reasoning for hard MCQ count."
                },
                "hard_ar_count": {
                    "type": "integer",
                    "description": "Hard A-R count — complex cause-effect, >60 sec."
                },
                "hard_ar_reasoning": {
                    "type": "string",
                    "description": "Step-by-step reasoning for hard A-R count."
                },
                "hard_mtc_count": {
                    "type": "integer",
                    "description": "Hard MTC count — 5+ pairs per question, maximized."
                },
                "hard_mtc_reasoning": {
                    "type": "string",
                    "description": "Step-by-step reasoning for hard MTC count (show maximization working)."
                },
            },
            "required": [
                "easy_mcq_count",   "easy_mcq_reasoning",
                "easy_ar_count",    "easy_ar_reasoning",
                "easy_mtc_count",   "easy_mtc_reasoning",
                "medium_mcq_count", "medium_mcq_reasoning",
                "medium_ar_count",  "medium_ar_reasoning",
                "medium_mtc_count", "medium_mtc_reasoning",
                "hard_mcq_count",   "hard_mcq_reasoning",
                "hard_ar_count",    "hard_ar_reasoning",
                "hard_mtc_count",   "hard_mtc_reasoning",
            ],
            "additionalProperties": False
        }
    }
]

_ALL_FIELDS = [
    "easy_mcq_count",   "easy_mcq_reasoning",
    "easy_ar_count",    "easy_ar_reasoning",
    "easy_mtc_count",   "easy_mtc_reasoning",
    "medium_mcq_count", "medium_mcq_reasoning",
    "medium_ar_count",  "medium_ar_reasoning",
    "medium_mtc_count", "medium_mtc_reasoning",
    "hard_mcq_count",   "hard_mcq_reasoning",
    "hard_ar_count",    "hard_ar_reasoning",
    "hard_mtc_count",   "hard_mtc_reasoning",
]


def parse_detection_result(result):
    """
    Parse the raw OpenAI response from generate_question_distribution.

    Returns a dict with all 9 counts + 9 reasoning strings,
    or an 'error' key if parsing fails.
    """
    import json

    args = None

    if hasattr(result, "output"):
        for item in result.output:
            item_type = getattr(item, "type", None)
            if item_type == "function_call":
                args_raw = getattr(item, "arguments", None)
                if args_raw:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                break
            elif item_type in ("tool_use", "tool_call"):
                args_raw = getattr(item, "input", None) or getattr(item, "arguments", None)
                if args_raw:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                break

    if not args and hasattr(result, "choices"):
        for choice in result.choices:
            msg = getattr(choice, "message", None)
            if msg and hasattr(msg, "tool_calls") and msg.tool_calls:
                tc = msg.tool_calls[0]
                func = getattr(tc, "function", None)
                if func:
                    args_raw = getattr(func, "arguments", None)
                    if args_raw:
                        args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            break

    if args:
        defaults = {f: (0 if f.endswith("_count") else "") for f in _ALL_FIELDS}
        return {f: args.get(f, defaults[f]) for f in _ALL_FIELDS}

    return {
        "error": "Could not parse response",
        "raw_types": [
            type(item).__name__ + ":" + str(getattr(item, "type", "no-type"))
            for item in getattr(result, "output", [])
        ],
        "raw_preview": str(result)[:500],
    }


# ── Gemini detector ───────────────────────────────────────────────────────────

# JSON format instruction appended to system prompt for Gemini (no tool calling needed)
#
# Reasoning strings were dropped from this schema (2026-07-06) — api/detect.py's
# _build_results() only ever read the *_count fields; the 9 reasoning strings
# were generated and discarded on every call. See tests/benchmarks/results/
# reasoning_ab_*.json for the measured latency impact of this change.
#
# INCLUDE_REASONING_FOR_AB_TEST: set True only to temporarily restore the old
# 18-field schema for repeated-trial validation (tests/benchmarks/
# reasoning_removal_ab_test.py). Leave False otherwise.
INCLUDE_REASONING_FOR_AB_TEST = False

_GEMINI_JSON_INSTRUCTION_NO_REASONING = """

Return ONLY a valid JSON object with exactly these 9 fields and no other text.

{
  "easy_mcq_count": <integer>,
  "easy_ar_count": <integer>,
  "easy_mtc_count": <integer>,
  "medium_mcq_count": <integer>,
  "medium_ar_count": <integer>,
  "medium_mtc_count": <integer>,
  "hard_mcq_count": <integer>,
  "hard_ar_count": <integer>,
  "hard_mtc_count": <integer>
}
"""

_GEMINI_JSON_INSTRUCTION_WITH_REASONING = """

Return ONLY a valid JSON object with exactly these 18 fields and no other text.
Keep every reasoning string to ONE short sentence (max 15 words).

{
  "easy_mcq_count": <integer>,   "easy_mcq_reasoning": "<max 15 words>",
  "easy_ar_count": <integer>,    "easy_ar_reasoning": "<max 15 words>",
  "easy_mtc_count": <integer>,   "easy_mtc_reasoning": "<max 15 words>",
  "medium_mcq_count": <integer>, "medium_mcq_reasoning": "<max 15 words>",
  "medium_ar_count": <integer>,  "medium_ar_reasoning": "<max 15 words>",
  "medium_mtc_count": <integer>, "medium_mtc_reasoning": "<max 15 words>",
  "hard_mcq_count": <integer>,   "hard_mcq_reasoning": "<max 15 words>",
  "hard_ar_count": <integer>,    "hard_ar_reasoning": "<max 15 words>",
  "hard_mtc_count": <integer>,   "hard_mtc_reasoning": "<max 15 words>"
}
"""

_GEMINI_RESPONSE_SCHEMA_NO_REASONING = {
    "type": "object",
    "properties": {
        "easy_mcq_count":    {"type": "integer"},
        "easy_ar_count":     {"type": "integer"},
        "easy_mtc_count":    {"type": "integer"},
        "medium_mcq_count":  {"type": "integer"},
        "medium_ar_count":   {"type": "integer"},
        "medium_mtc_count":  {"type": "integer"},
        "hard_mcq_count":    {"type": "integer"},
        "hard_ar_count":     {"type": "integer"},
        "hard_mtc_count":    {"type": "integer"},
    },
    "required": [
        "easy_mcq_count",
        "easy_ar_count",
        "easy_mtc_count",
        "medium_mcq_count",
        "medium_ar_count",
        "medium_mtc_count",
        "hard_mcq_count",
        "hard_ar_count",
        "hard_mtc_count",
    ],
}

_GEMINI_RESPONSE_SCHEMA_WITH_REASONING = {
    "type": "object",
    "properties": {
        "easy_mcq_count":    {"type": "integer"},
        "easy_mcq_reasoning": {"type": "string"},
        "easy_ar_count":     {"type": "integer"},
        "easy_ar_reasoning":  {"type": "string"},
        "easy_mtc_count":    {"type": "integer"},
        "easy_mtc_reasoning": {"type": "string"},
        "medium_mcq_count":  {"type": "integer"},
        "medium_mcq_reasoning": {"type": "string"},
        "medium_ar_count":   {"type": "integer"},
        "medium_ar_reasoning": {"type": "string"},
        "medium_mtc_count":  {"type": "integer"},
        "medium_mtc_reasoning": {"type": "string"},
        "hard_mcq_count":    {"type": "integer"},
        "hard_mcq_reasoning": {"type": "string"},
        "hard_ar_count":     {"type": "integer"},
        "hard_ar_reasoning":  {"type": "string"},
        "hard_mtc_count":    {"type": "integer"},
        "hard_mtc_reasoning": {"type": "string"},
    },
    "required": [
        "easy_mcq_count",   "easy_mcq_reasoning",
        "easy_ar_count",    "easy_ar_reasoning",
        "easy_mtc_count",   "easy_mtc_reasoning",
        "medium_mcq_count", "medium_mcq_reasoning",
        "medium_ar_count",  "medium_ar_reasoning",
        "medium_mtc_count", "medium_mtc_reasoning",
        "hard_mcq_count",   "hard_mcq_reasoning",
        "hard_ar_count",    "hard_ar_reasoning",
        "hard_mtc_count",   "hard_mtc_reasoning",
    ],
}

_GEMINI_JSON_INSTRUCTION = (
    _GEMINI_JSON_INSTRUCTION_WITH_REASONING if INCLUDE_REASONING_FOR_AB_TEST
    else _GEMINI_JSON_INSTRUCTION_NO_REASONING
)
_GEMINI_RESPONSE_SCHEMA = (
    _GEMINI_RESPONSE_SCHEMA_WITH_REASONING if INCLUDE_REASONING_FOR_AB_TEST
    else _GEMINI_RESPONSE_SCHEMA_NO_REASONING
)


def generate_question_distribution_gemini(
    media_items,
    system_prompt=None,
    model="gemini-2.5-flash",
    api_key=None,
):
    """
    Gemini path — accepts images and PDFs via presigned S3 URLs directly.

    media_items: list of {"url": str, "mime_type": str}
      e.g. [{"url": "https://...", "mime_type": "application/pdf"}]
           [{"url": "https://...", "mime_type": "image/jpeg"}, ...]

    Returns raw Gemini response object.
    """
    from google import genai
    from google.genai import types

    if system_prompt is None:
        system_prompt = load_system_prompt()

    key = api_key or __import__("os").environ.get("GEMINI_API_KEY")
    client = genai.Client(api_key=key)

    # Build content parts: file parts first, then the instruction
    parts = []
    for item in media_items:
        parts.append(types.Part.from_uri(
            file_uri=item["url"],
            mime_type=item["mime_type"],
        ))
    parts.append(types.Part.from_text(
        text="Analyse this content and call set_question_distribution with your counts."
    ))

    response = client.models.generate_content(
        model=model,
        contents=[types.Content(role="user", parts=parts)],
        config=types.GenerateContentConfig(
            system_instruction=system_prompt + _GEMINI_JSON_INSTRUCTION,
            response_mime_type="application/json",
            max_output_tokens=4096,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return response


def _gemini_response_text(response) -> str:
    """Extract full text from Gemini response, checking finish_reason."""
    # Log finish reason so we can spot truncation
    try:
        for cand in response.candidates:
            reason = getattr(cand, "finish_reason", None)
            if reason and str(reason) not in ("FinishReason.STOP", "STOP", "1"):
                _log = __import__("logging").getLogger(__name__)
                _log.warning("gemini_finish_reason=%s", reason)
    except Exception:
        pass
    return response.text


def parse_detection_result_gemini(response) -> dict:
    """
    Parse Gemini JSON response into the same 18-field dict as parse_detection_result().
    """
    try:
        text = _gemini_response_text(response).strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        data = json.loads(text)
    except Exception as exc:
        raw = getattr(response, "text", str(response))
        _log = __import__("logging").getLogger(__name__)
        _log.error("gemini_parse_failed | raw_length=%d | raw_tail=%s",
                   len(raw), raw[-300:] if len(raw) > 300 else raw)
        return {
            "error": f"Gemini JSON parse failed: {exc}",
            "raw_preview": raw[:500],
        }

    defaults = {f: (0 if f.endswith("_count") else "") for f in _ALL_FIELDS}
    return {f: data.get(f, defaults[f]) for f in _ALL_FIELDS}


# ── Unified detect() ──────────────────────────────────────────────────────────

def detect(
    media_items,
    system_prompt=None,
    model=None,
    provider=None,
):
    """
    Unified detector — routes to Gemini or OpenAI based on DETECTOR_PROVIDER config.

    media_items: list of {"url": str, "mime_type": str}
      Used by both providers. For OpenAI PDF path, also pass "file_id" key.

    Returns: (raw_response, parsed_dict)
    """
    from core.config import DETECTOR_PROVIDER, GEMINI_MODEL, OPENAI_MODEL

    active_provider = provider or DETECTOR_PROVIDER

    if active_provider == "gemini":
        active_model = model or GEMINI_MODEL
        raw = generate_question_distribution_gemini(
            media_items=media_items,
            system_prompt=system_prompt,
            model=active_model,
        )
        parsed = parse_detection_result_gemini(raw)
    else:
        # OpenAI path — expects content_blocks format (built by api/detect.py)
        active_model = model or OPENAI_MODEL
        content_blocks = []
        for item in media_items:
            if "file_id" in item:
                content_blocks.append({"type": "input_file", "file_id": item["file_id"]})
            else:
                content_blocks.append({"type": "input_image", "image_url": item["url"]})
        raw = generate_question_distribution(
            content_blocks=content_blocks,
            system_prompt=system_prompt,
            model=active_model,
        )
        parsed = parse_detection_result(raw)

    return raw, parsed


if __name__ == "__main__":
    images = [
        "/path/to/image1.png",
        "/path/to/image2.png",
    ]

    response = generate_question_distribution(image_urls=images)
    print(parse_detection_result(response))

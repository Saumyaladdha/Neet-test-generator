"""
JSON parse utilities for LLM responses.

Public API:
  strip_markdown_fences(text)          — strip ```json ... ``` wrappers
  parse_json_response(raw)             — parse dict from LLM text (4-step pipeline)
  parse_json_array_response(raw)       — parse list from LLM text (array variant)
"""

import json
import logging
import re

logger = logging.getLogger(__name__)


def _fix_latex_escapes(text: str) -> str:
    """
    Escape unescaped LaTeX backslash commands so they are valid JSON strings.
    Leaves already-escaped sequences and single-char JSON escapes untouched.
    """
    return re.sub(r'(?<!\\)\\([a-zA-Z]{2,})', r'\\\\\\1', text)


def strip_markdown_fences(text: str) -> str:
    """
    Strip leading/trailing markdown code fences (```json or ```).
    Returns the cleaned string; unchanged if no fences present.
    """
    text = text.strip()
    for prefix in ("```json", "```"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _strip_markdown_fence_search(text: str) -> str | None:
    """Extract JSON content from inside ```json ... ``` fences. Returns None if no fence."""
    match = re.search(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)
    return match.group(1) if match else None


def parse_json_response(raw: str) -> dict | None:
    """
    Parse a raw LLM response string into a dict.

    4-step pipeline:
      1. Direct json.loads
      2. Fix LaTeX escapes, retry
      3. Strip markdown fences, retry both above
      4. json_repair for truncated/malformed JSON

    Returns the parsed dict on success, or None if all steps fail.
    """
    if not raw or not raw.strip():
        logger.warning("parse_json_response: empty input")
        return None

    latex_fixed = _fix_latex_escapes(raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    try:
        return json.loads(latex_fixed)
    except json.JSONDecodeError:
        pass

    fenced = _strip_markdown_fence_search(raw)
    if fenced:
        try:
            return json.loads(fenced)
        except json.JSONDecodeError:
            pass
        try:
            return json.loads(_fix_latex_escapes(fenced))
        except json.JSONDecodeError:
            pass

    try:
        from json_repair import repair_json
        for candidate in (raw, latex_fixed):
            result = repair_json(candidate, return_objects=True)
            if isinstance(result, dict) and result.get("questions"):
                logger.info("parse_json_response: recovered via json_repair")
                return result
    except Exception as e:
        logger.warning(f"parse_json_response: json_repair failed — {e}")

    logger.error(
        "parse_json_response: all 4 steps failed. "
        f"Raw preview (first 300): {raw[:300]!r}"
    )
    return None


def parse_json_array_response(raw: str) -> list | None:
    """
    Parse a raw LLM response string that should contain a JSON array.

    Strips markdown fences, finds the outermost [...] bracket pair,
    and parses. Returns list on success, None on failure.
    """
    if not raw or not raw.strip():
        logger.warning("parse_json_array_response: empty input")
        return None

    cleaned = strip_markdown_fences(raw)

    # Try direct parse first (handles clean responses)
    try:
        result = json.loads(cleaned)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # Find outermost array brackets
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1:
        logger.error(f"parse_json_array_response: no JSON array found. Preview: {raw[:200]!r}")
        return None

    try:
        result = json.loads(cleaned[start:end + 1])
        if not isinstance(result, list):
            logger.error("parse_json_array_response: parsed value is not a list")
            return None
        return result
    except json.JSONDecodeError as exc:
        logger.error(f"parse_json_array_response: parse failed — {exc}. Preview: {raw[:200]!r}")
        return None

"""
Topic-based PDF chunking for the Generator pipeline.

Fallback chain:
  1. LLM topic detection + validate page math
     → If page counts wrong: mechanical split (8-page chunks, 1-page overlap)
  2. LLM density estimation (suggested_questions per chunk)
     → If density LLM fails: equal distribution weighted by page count
  3. distribute_questions() — density-weighted with min-1 guarantee
"""

import math
from pathlib import Path

from openai import OpenAI

from core.logger import get_logger
from core.parser import parse_json_array_response

log = get_logger(__name__)

_PROMPTS = Path(__file__).parent.parent / "prompts" / "topic_detector"
_SYSTEM_PROMPT = (_PROMPTS / "system_prompt.txt").read_text(encoding="utf-8")
_DENSITY_PROMPT = (_PROMPTS / "density_prompt.txt").read_text(encoding="utf-8")

_MIN_PAGES = 3
_MAX_PAGES = 15
_FALLBACK_CHUNK_SIZE = 8
_FALLBACK_OVERLAP = 1


# ── Public API ────────────────────────────────────────────────────────────────

def detect_topics(
    file_id: str, total_pages: int, model: str = "gpt-5.4-mini",
    user_id: str = None, test_id: str = None,
) -> dict:
    """
    Returns:
    {
      "topics": [
        {
          "topic": str,
          "start_page": int,
          "end_page": int,
          "overlap_next": int,
          "suggested_questions": int,
          "page_count": int,
        }, ...
      ],
      "page_fallback_used": bool,       # mechanical split was used
      "density_fallback_used": bool,    # page-proportional density was used
      "fallback_reason": str | None,
    }
    """
    client = OpenAI()
    page_fallback_used = False
    density_fallback_used = False
    fallback_reason = None

    # ── Step 1: Get chunks (LLM or mechanical) ───────────────────────────────
    log.info("topic_detector.start", user_id=user_id, test_id=test_id,
             file_id=file_id, total_pages=total_pages)

    try:
        raw = _call_topic_llm(client, file_id, total_pages, model)
        valid, errors = _validate_page_math(raw, total_pages)
        if valid:
            chunks = raw
            log.info("topic_detector.llm_ok", user_id=user_id, test_id=test_id, topics_identified=len(chunks))
        else:
            reason = "; ".join(errors)
            log.warning("topic_detector.page_math_failed", user_id=user_id, test_id=test_id, errors=reason)
            chunks = _mechanical_split(total_pages, _FALLBACK_CHUNK_SIZE, _FALLBACK_OVERLAP, user_id, test_id)
            page_fallback_used = True
            fallback_reason = reason
    except Exception as exc:
        reason = f"LLM call failed: {exc}"
        log.warning("topic_detector.llm_error", user_id=user_id, test_id=test_id, error=reason)
        chunks = _mechanical_split(total_pages, _FALLBACK_CHUNK_SIZE, _FALLBACK_OVERLAP, user_id, test_id)
        page_fallback_used = True
        fallback_reason = reason

    # ── Step 2: Get question density ─────────────────────────────────────────
    # Skip density LLM when topic LLM succeeded — it already returned suggested_questions.
    # Only call density LLM for mechanical chunks (all have suggested_questions=1).
    if page_fallback_used:
        try:
            chunks = _estimate_density(client, file_id, chunks, model)
            log.info("topic_detector.density_ok", user_id=user_id, test_id=test_id, topics_estimated=len(chunks))
        except Exception as exc:
            log.warning("topic_detector.density_failed", user_id=user_id, test_id=test_id, error=str(exc))
            chunks = _density_by_pages(chunks)
            density_fallback_used = True
            fallback_reason = (fallback_reason or "") + f"; density LLM failed: {exc}"
    else:
        log.info("topic_detector.density_skipped", user_id=user_id, test_id=test_id,
                 reason="topic LLM already provided suggested_questions")

    return {
        "topics": _add_page_count(_normalize_pages(chunks)),
        "page_fallback_used": page_fallback_used,
        "density_fallback_used": density_fallback_used,
        "fallback_reason": fallback_reason,
    }


def distribute_questions(
    topics: list, total_requested: int, user_id: str = None, test_id: str = None,
) -> tuple:
    """
    Density-weighted distribution with min-1 per topic guarantee.
    Uses suggested_questions as weights.

    Returns: (counts: list[int], skipped_topics: list[str])
    """
    total_requested = int(total_requested)  # DynamoDB returns Decimal; slice indices must be int
    n = len(topics)
    if n == 0:
        return [], []

    weights = [max(1, t.get("suggested_questions", 1)) for t in topics]
    total_weight = sum(weights)

    if total_requested < n:
        # Not enough questions for every topic — give to the densest ones
        sorted_idx = sorted(range(n), key=lambda i: weights[i], reverse=True)
        counts = [0] * n
        for i in sorted_idx[:total_requested]:
            counts[i] = 1
        skipped = [topics[i]["topic"] for i in sorted_idx[total_requested:]]
        log.warning("distribute.topics_skipped", user_id=user_id, test_id=test_id,
                    requested=total_requested, topics=n, skipped=len(skipped))
        return counts, skipped

    # Floor allocation, min-1 forced
    raw = [total_requested * w / total_weight for w in weights]
    counts = [max(1, math.floor(r)) for r in raw]

    diff = sum(counts) - total_requested
    if diff > 0:
        # Over by diff — trim from largest counts first (keep ≥ 1)
        order = sorted(range(n), key=lambda i: counts[i], reverse=True)
        for i in order:
            if diff <= 0:
                break
            if counts[i] > 1:
                counts[i] -= 1
                diff -= 1
    elif diff < 0:
        # Under by abs(diff) — add to highest-remainder topics
        remainders = sorted(range(n), key=lambda i: raw[i] - math.floor(raw[i]), reverse=True)
        for i in remainders:
            if diff >= 0:
                break
            counts[i] += 1
            diff += 1

    if sum(counts) != total_requested:
        log.error("distribute.math_error", user_id=user_id, test_id=test_id,
                  computed=sum(counts), requested=total_requested, counts=counts)
        raise RuntimeError(
            f"distribute_questions math error: {sum(counts)} != {total_requested}"
        )

    return counts, []


# ── LLM calls ─────────────────────────────────────────────────────────────────

def _call_topic_llm(client: OpenAI, file_id: str, total_pages: int, model: str) -> list:
    user_msg = (
        f"This PDF has {total_pages} pages total. "
        f"Identify topic chunks following the rules. Return only the JSON array."
    )
    response = client.responses.create(
        model=model,
        instructions=_SYSTEM_PROMPT,
        input=[{
            "role": "user",
            "content": [
                {"type": "input_file", "file_id": file_id},
                {"type": "input_text", "text": user_msg},
            ],
        }],
        max_output_tokens=2048,
        store=True,
    )
    result = parse_json_array_response(_extract_text(response))
    if result is None:
        raise ValueError("topic_llm: no JSON array in LLM response")
    return result


def _estimate_density(client: OpenAI, file_id: str, chunks: list, model: str) -> list:
    chunk_list = "\n".join(
        f'  {{"chunk_index": {i}, "pages": "{c["start_page"]}-{c["end_page"]}"}}'
        for i, c in enumerate(chunks)
    )
    user_msg = (
        f"Estimate NEET question density for each chunk below.\n"
        f"Chunks:\n{chunk_list}\n\nReturn only the JSON array."
    )
    response = client.responses.create(
        model=model,
        instructions=_DENSITY_PROMPT,
        input=[{
            "role": "user",
            "content": [
                {"type": "input_file", "file_id": file_id},
                {"type": "input_text", "text": user_msg},
            ],
        }],
        max_output_tokens=1024,
        store=True,
    )
    items = parse_json_array_response(_extract_text(response))
    if items is None:
        raise ValueError("density_llm: no JSON array in LLM response")
    density_map = {int(d["chunk_index"]): max(1, int(d.get("suggested_questions", 1)))
                   for d in items}
    for i, chunk in enumerate(chunks):
        chunk["suggested_questions"] = density_map.get(i, 1)
    return chunks


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_page_math(topics: list, total_pages: int) -> tuple:
    """
    Validates page count arithmetic only — bounds, ordering, coverage.
    Question-count validation happens separately in distribute_questions().
    Returns (valid: bool, errors: list[str]).
    """
    errors = []

    if not isinstance(topics, list) or len(topics) == 0:
        return False, ["LLM returned empty or non-array"]

    for i, t in enumerate(topics):
        name = t.get("topic", f"chunk_{i}")
        sp, ep = t.get("start_page"), t.get("end_page")
        ov = t.get("overlap_next", 0)

        if sp is None or ep is None:
            errors.append(f"[{name}] missing start_page or end_page")
            continue

        try:
            sp, ep, ov = int(sp), int(ep), int(ov)
        except (TypeError, ValueError):
            errors.append(f"[{name}] non-integer page values")
            continue

        page_count = ep - sp + 1

        if sp < 1:
            errors.append(f"[{name}] start_page {sp} < 1")
        if ep > total_pages:
            errors.append(f"[{name}] end_page {ep} > total pages {total_pages}")
        if sp > ep:
            errors.append(f"[{name}] start_page {sp} > end_page {ep}")
        if page_count < _MIN_PAGES:
            errors.append(f"[{name}] {page_count} pages < min {_MIN_PAGES}")
        if page_count > _MAX_PAGES:
            errors.append(f"[{name}] {page_count} pages > max {_MAX_PAGES}")
        if ov >= page_count:
            errors.append(f"[{name}] overlap_next {ov} >= page_count {page_count}")

    if errors:
        return False, errors

    sorted_t = sorted(topics, key=lambda t: int(t["start_page"]))

    # First chunk must start at page 1 (allow up to page 3 for intro pages)
    if int(sorted_t[0]["start_page"]) > 3:
        errors.append(f"First chunk starts at page {sorted_t[0]['start_page']}, expected 1-3")

    # Last chunk must reach end of PDF (allow up to 2 pages gap)
    if int(sorted_t[-1]["end_page"]) < total_pages - 2:
        errors.append(
            f"Last chunk ends at page {sorted_t[-1]['end_page']}, "
            f"PDF has {total_pages} pages"
        )

    # No large gaps between consecutive chunks
    for i in range(len(sorted_t) - 1):
        cur_end = int(sorted_t[i]["end_page"])
        nxt_start = int(sorted_t[i + 1]["start_page"])
        gap = nxt_start - cur_end - 1
        if gap > 2:
            errors.append(
                f"Gap of {gap} uncovered pages between chunk {i} "
                f"(ends p{cur_end}) and chunk {i + 1} (starts p{nxt_start})"
            )

    return (not errors), errors


# ── Fallbacks ─────────────────────────────────────────────────────────────────

def _mechanical_split(
    total_pages: int, chunk_size: int, overlap: int,
    user_id: str = None, test_id: str = None,
) -> list:
    """Fixed-size chunks with `overlap` pages shared with next chunk.

    Small-PDF fast-path: if the entire PDF fits within one chunk (≤ MAX_PAGES),
    return a single chunk rather than creating a tiny unusable tail chunk.
    """
    # Small PDF — single chunk covers everything
    if total_pages <= _MAX_PAGES:
        log.info("topic_detector.single_chunk", user_id=user_id, test_id=test_id,
                 total_pages=total_pages,
                 reason="PDF fits within MAX_PAGES, no split needed")
        return [{
            "topic": f"Pages 1–{total_pages}",
            "start_page": 1,
            "end_page": total_pages,
            "overlap_next": 0,
            "suggested_questions": 1,
        }]

    chunks = []
    step = chunk_size - overlap
    page = 1
    while page <= total_pages:
        end = min(page + chunk_size - 1, total_pages)
        is_last = (end == total_pages)
        chunks.append({
            "topic": f"Pages {page}–{end}",
            "start_page": page,
            "end_page": end,
            "overlap_next": 0 if is_last else overlap,
            "suggested_questions": 1,
        })
        if is_last:
            break
        page += step

    # Merge tail chunk if it's too thin (< MIN_PAGES) to generate good questions
    if len(chunks) >= 2:
        tail = chunks[-1]
        tail_pages = tail["end_page"] - tail["start_page"] + 1
        if tail_pages < _MIN_PAGES:
            prev = chunks[-2]
            prev["end_page"] = tail["end_page"]
            prev["overlap_next"] = 0
            chunks.pop()
            log.info("topic_detector.tail_merged", user_id=user_id, test_id=test_id,
                     tail_pages=tail_pages, merged_into=chunks[-1]["topic"])

    return chunks


def _density_by_pages(chunks: list) -> list:
    """
    Fallback density: proportional to page count.
    Uses a base of 2 questions per page as a rough NEET heuristic,
    then scales so the total feels sensible.
    """
    total_pages = sum(c["end_page"] - c["start_page"] + 1 for c in chunks)
    for chunk in chunks:
        pages = chunk["end_page"] - chunk["start_page"] + 1
        # Proportional share: min 1, scaled so a 10-page chunk gets ~5 questions
        chunk["suggested_questions"] = max(1, round(pages / total_pages * 5 * len(chunks)))
    return chunks


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalize_pages(chunks: list) -> list:
    """Coerce start_page, end_page, overlap_next to int — LLMs sometimes return floats."""
    for c in chunks:
        c["start_page"]   = int(c["start_page"])
        c["end_page"]     = int(c["end_page"])
        c["overlap_next"] = int(c.get("overlap_next", 0))
    return chunks


def _add_page_count(topics: list) -> list:
    for t in topics:
        t["page_count"] = int(t["end_page"]) - int(t["start_page"]) + 1
    return topics


def _extract_text(response) -> str:
    for item in getattr(response, "output", []):
        if getattr(item, "type", None) == "message":
            for block in getattr(item, "content", []):
                if getattr(block, "type", None) == "output_text":
                    return block.text
    return str(response)



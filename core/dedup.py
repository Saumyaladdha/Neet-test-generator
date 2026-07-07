"""
Question deduplication — removes near-duplicate questions from overlapping PDF chunks.
"""

import re

from core.logger import get_logger

log = get_logger(__name__)


def dedup_questions(test_id: str, questions: list, threshold: float = 0.72) -> list:
    """
    Remove near-duplicate questions using Jaccard similarity on normalised word sets.

    threshold=0.72 means 72%+ word overlap → considered a duplicate.
    The first occurrence is kept; subsequent duplicates are dropped with a warning log.
    """
    def _words(text: str) -> frozenset:
        # Keep ASCII Latin/digits (English, formulas, units) AND Devanagari
        # (ऀ-ॿ) so Hindi-medium question text isn't stripped down
        # to just its "(A)"/"(R)" labels, which previously made every Hindi
        # AR question collapse to the same tiny token set and register as a
        # 100% duplicate of every other one.
        text = re.sub(r"[^a-z0-9ऀ-ॿ\s]", " ", text.lower())
        return frozenset(text.split())

    seen: list[frozenset] = []
    unique: list[dict] = []

    for q in questions:
        words = _words(q.get("question_text", ""))
        if not words:
            unique.append(q)
            continue

        is_dup = False
        for seen_words in seen:
            union = seen_words | words
            if not union:
                continue
            jaccard = len(seen_words & words) / len(union)
            if jaccard >= threshold:
                log.warning(
                    "dedup.removed",
                    test_id=test_id,
                    question=q.get("question_text", "")[:80],
                    similarity=round(jaccard, 2),
                )
                is_dup = True
                break

        if not is_dup:
            seen.append(words)
            unique.append(q)

    removed = len(questions) - len(unique)
    if removed:
        log.info("dedup.summary", test_id=test_id, removed=removed, kept=len(unique))

    return unique

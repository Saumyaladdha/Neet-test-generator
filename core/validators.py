"""
Post-generation quality validators.

These catch failure classes that prompt instructions alone don't reliably
prevent (confirmed via real E2E testing):

1. AR round-robin skew — the model is told to cycle through 4 answer types
   roughly equally but sometimes converges on one type across a whole job.
2. MCQ-hard multiple_correct / identify_incorrect — the model's own
   statement evaluation sometimes produces a correct-statement combination
   that doesn't match ANY of the 4 given options, making the question
   unanswerable.
3. Match-the-column options that aren't valid one-to-one mappings — an
   option like "1-v, 2-ii, 3-v, 4-iii" reuses "v" for two different
   Column-I items, which is structurally impossible to answer correctly.
4. Match-the-column content-level duplication that option-key checks can't
   see: two Column-II items with identical text, or two Column-I items
   that are near-duplicate concepts — both make the "correct" match
   ambiguous even when the option letters themselves look fine.
5. Match-the-column options that are each individually well-formed but
   where NONE of the 4 actually represents the correct matching sequence
   — the model is told to derive the correct sequence first and build
   the other 3 options FROM it, but sometimes writes all 4 as independent
   permutations instead, so the true correct mapping never appears among
   them (confirmed more frequent in Hindi-medium generation than English).

Validators 2 and 5 consume the internal "correct_statement_numbers" /
"correct_sequence" fields (see prompts/schemas/mcq_hard.txt and
prompts/schemas/mtc.txt) and strip them before the question is stored or
returned — students never see them, exactly like correct_answer is never
shown at generation time.
"""

import re

from core.logger import get_logger

log = get_logger(__name__)

_NUMBER_RE = re.compile(r"\d+")
_MTC_OPTION_RE = re.compile(r"\d+\s*-\s*([ivx]+)", re.IGNORECASE)
_MTC_PAIR_RE = re.compile(r"(\d+)\s*-\s*([ivx]+)", re.IGNORECASE)
_MTC_ROW_RE = re.compile(r"(?:(\d+)\.\s*([^&\n]+?))?\s*&\s*([ivx]+)\.\s*([^\\]+?)\s*\\\\", re.IGNORECASE)


def strip_internal_fields(questions: list) -> list:
    """Remove internal-only validation fields from every question in place."""
    for q in questions:
        q.pop("answer_type", None)
        q.pop("correct_statement_numbers", None)
        q.pop("correct_sequence", None)
    return questions


def check_ar_type_distribution(test_id: str, questions: list, max_single_type_ratio: float = 0.5) -> None:
    """
    Log a warning (does not drop questions) if one AR answer type dominates
    a job's output, violating the prompt's ~25%-per-type round-robin rule.

    This is observability-only, not a filter: unlike the MCQ-hard option
    -consistency check below, there's no "wrong" AR question here — every
    type is individually valid, so the fix is prompt/generation tuning, not
    dropping otherwise-correct questions.
    """
    types = [q.get("answer_type") for q in questions if q.get("answer_type")]
    if len(types) < 4:
        return

    counts = {t: types.count(t) for t in set(types)}
    total = len(types)
    dominant_type, dominant_count = max(counts.items(), key=lambda kv: kv[1])
    ratio = dominant_count / total

    if ratio > max_single_type_ratio:
        log.warning(
            "ar.type_distribution_skewed",
            test_id=test_id,
            counts=counts,
            total=total,
            dominant_type=dominant_type,
            dominant_ratio=round(ratio, 2),
        )


def _option_numbers(option_text: str) -> frozenset:
    return frozenset(int(n) for n in _NUMBER_RE.findall(option_text))


def filter_inconsistent_multi_statement_mcq(test_id: str, questions: list) -> list:
    """
    Drop multiple_correct / identify_incorrect MCQ-hard questions where the
    model's self-reported correct-statement-numbers don't match the
    number-set in ANY of the 4 options — i.e. the question has no valid
    answer. Every other question_category passes through unchanged.
    """
    valid = []
    for q in questions:
        category = q.get("question_category")
        if category not in ("multiple_correct", "identify_incorrect"):
            valid.append(q)
            continue

        stated = q.get("correct_statement_numbers")
        if not stated:
            # Model didn't populate the field — can't validate, let it through
            # rather than discarding a possibly-fine question over a missing field.
            valid.append(q)
            continue

        stated_set = frozenset(int(n) for n in stated)
        options = q.get("options", {})
        if any(_option_numbers(opt_text) == stated_set for opt_text in options.values()):
            valid.append(q)
        else:
            log.warning(
                "mcq_hard.no_valid_option",
                test_id=test_id,
                question=q.get("question_text", "")[:100],
                stated_correct=sorted(stated_set),
                options=options,
            )

    removed = len(questions) - len(valid)
    if removed:
        log.info("mcq_hard.consistency_filter", test_id=test_id, removed=removed, kept=len(valid))
    return valid


def _mtc_pairs(text: str) -> dict:
    """Parse '1-iii, 2-i, 3-iv, 4-ii' into {1: 'iii', 2: 'i', 3: 'iv', 4: 'ii'}.

    Used to compare a matching sequence structurally (which Column-II item
    goes with which Column-I number) rather than as raw text, so pair-order
    differences ("1-iii, 2-i" vs "2-i, 1-iii") never register as a mismatch.
    """
    return {int(num): roman.lower() for num, roman in _MTC_PAIR_RE.findall(text)}


def filter_inconsistent_mtc(test_id: str, questions: list) -> list:
    """
    Drop match_the_column questions where the model's self-derived
    correct_sequence (internal field, populated during generation — see
    prompts/schemas/mtc.txt) doesn't match ANY of the 4 given options, i.e.
    the question has no valid answer among its own options. Mirrors
    filter_inconsistent_multi_statement_mcq's approach for MTC's analogous
    failure mode: generation sometimes writes 4 options as independent
    permutations rather than deriving 3 of them FROM the correct sequence,
    so the true correct mapping never appears among them.
    """
    valid = []
    for q in questions:
        if q.get("question_type", "").upper() != "MATCH_THE_COLUMN":
            valid.append(q)
            continue

        stated = q.get("correct_sequence")
        if not stated:
            # Model didn't populate the field — can't validate, let it through
            # rather than discarding a possibly-fine question over a missing field.
            valid.append(q)
            continue

        stated_map = _mtc_pairs(stated)
        options = q.get("options", {})
        if any(_mtc_pairs(opt_text) == stated_map for opt_text in options.values()):
            valid.append(q)
        else:
            log.warning(
                "mtc.no_valid_option",
                test_id=test_id,
                question=q.get("question_text", "")[:100],
                stated_correct=stated,
                options=options,
            )

    removed = len(questions) - len(valid)
    if removed:
        log.info("mtc.answer_consistency_filter", test_id=test_id, removed=removed, kept=len(valid))
    return valid


def _mtc_option_letters(option_text: str) -> list:
    """Extract the Column-II roman numerals from an option like '1-v, 2-ii, 3-v, 4-iii'."""
    return [m.lower() for m in _MTC_OPTION_RE.findall(option_text)]


def filter_invalid_mtc_options(test_id: str, questions: list) -> list:
    """
    Drop match_the_column questions where ANY option reuses the same
    Column-II roman numeral for two different Column-I items — e.g.
    "1-v, 2-ii, 3-v, 4-iii" maps both item 1 and item 3 to "v", which is
    not a valid one-to-one match and has no correct interpretation.

    Applies to all MTC difficulties: easy/medium (4x4, no distractor) and
    hard (4x5 with one unused distractor) both require every option's
    letters to be pairwise distinct — only the pool size differs.
    """
    valid = []
    for q in questions:
        if q.get("question_type", "").upper() != "MATCH_THE_COLUMN":
            valid.append(q)
            continue

        options = q.get("options", {})
        broken_option = None
        for opt_key, opt_text in options.items():
            letters = _mtc_option_letters(opt_text)
            if len(letters) >= 2 and len(letters) != len(set(letters)):
                broken_option = (opt_key, opt_text)
                break

        if broken_option is None:
            valid.append(q)
        else:
            log.warning(
                "mtc.duplicate_option_letter",
                test_id=test_id,
                question=q.get("question_text", "")[:100],
                broken_option=broken_option,
                options=options,
            )

    removed = len(questions) - len(valid)
    if removed:
        log.info("mtc.consistency_filter", test_id=test_id, removed=removed, kept=len(valid))
    return valid


def _parse_mtc_table(question_text: str) -> tuple:
    """Parse a \\begin{tabular} MTC table into (Column I dict, Column II dict) keyed by number/roman numeral."""
    col1, col2 = {}, {}
    for num, text1, roman, text2 in _MTC_ROW_RE.findall(question_text):
        if num:
            col1[int(num)] = text1.strip()
        col2[roman.lower()] = text2.strip()
    return col1, col2


def filter_mtc_duplicate_content(test_id: str, questions: list) -> list:
    """
    Drop match_the_column questions where two Column-II items have
    IDENTICAL text (e.g. both say "विषमयुग्मक") — even if option letters
    are all distinct, there's no way to tell which of the two identical
    items is the "real" match.

    NOTE: this previously also flagged Column-I items with high word
    overlap as "near-duplicates," but that check had a ~88% false-positive
    rate in practice — it flagged legitimate NEET contrast-pairs (e.g.
    अलैंगिक जनन vs लैंगिक जनन, नर युग्मक vs मादा युग्मक, leptotene vs
    pachytene) as duplicates purely because they share common Hindi
    sentence-template words, not because they're conceptually the same.
    Simple word-overlap can't distinguish "same template, different
    entity" from "same entity, reworded" reliably enough to justify
    dropping content over it. Removed rather than mistuned.
    """
    valid = []
    for q in questions:
        if q.get("question_type", "").upper() != "MATCH_THE_COLUMN":
            valid.append(q)
            continue

        _, col2 = _parse_mtc_table(q.get("question_text", ""))
        col2_texts = list(col2.values())

        if len(col2_texts) == len(set(col2_texts)):
            valid.append(q)
        else:
            log.warning(
                "mtc.duplicate_column2_text",
                test_id=test_id,
                question=q.get("question_text", "")[:100],
                column2=col2,
            )

    removed = len(questions) - len(valid)
    if removed:
        log.info("mtc.content_filter", test_id=test_id, removed=removed, kept=len(valid))
    return valid

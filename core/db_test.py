"""
Finished-test operations — NeetTestGenerator_Test table.

This table holds test/question content AND the generated correct answer for
each question — merged directly onto the question object as
question.correct_answer, not a separate parallel list. Everything about the
pipeline itself (status, retries, batch tracing) stays in
NeetTestGenerator_Jobs, keyed by the same test_id.

Written once, when generation finishes successfully or partially —
never created empty at job-start time. Each question's correct_answer starts
as None and gets filled in later by the answer worker (answers_status tracks
that fill-in step).
"""

from core.config import DYNAMO_TEST_TABLE
from core.db_base import dynamo, now_iso
from core.enums import Difficulty, AnswerStatus, validate, validate_list


def create_test(test_id: str, test_series_name: str, questions: list) -> None:
    """
    Write the finished test's question content. One put_item per test_id.

    difficulty_level is derived from the questions themselves (each question
    carries its own "difficulty") — a sorted, deduplicated list, e.g. ["hard"]
    for a single-difficulty test or ["easy", "hard"] for a mixed one. Never a
    "mixed" sentinel string.
    """
    difficulty_level = sorted({q["difficulty"] for q in questions})
    validate_list(Difficulty, difficulty_level, "difficulty_level")

    table = dynamo().Table(DYNAMO_TEST_TABLE)
    now = now_iso()
    for q in questions:
        q.setdefault("correct_answer", None)
    item = {
        "test_id":          test_id,
        "test_series_name": test_series_name,
        "difficulty_level": difficulty_level,
        "total_questions":  len(questions),
        "questions":        questions,
        "answers_status":   AnswerStatus.PENDING.value,
        "created_at":       now,
        "updated_at":       now,
    }
    table.put_item(Item=item)


def get_test(test_id: str) -> dict | None:
    table = dynamo().Table(DYNAMO_TEST_TABLE)
    resp = table.get_item(Key={"test_id": test_id})
    return resp.get("Item")


def save_answers(test_id: str, answers: list, answers_status: str = AnswerStatus.DONE.value) -> None:
    """
    Merge each {question_number, correct_answer} into its matching question
    object (questions[question_number - 1].correct_answer) and update
    answers_status. answers may be empty — still updates answers_status.
    """
    validate(AnswerStatus, answers_status, "answers_status")
    set_parts = ["answers_status = :as", "updated_at = :ts"]
    values = {":as": answers_status, ":ts": now_iso()}

    for a in answers:
        idx = int(a["question_number"]) - 1
        placeholder = f":ans{idx}"
        set_parts.append(f"questions[{idx}].correct_answer = {placeholder}")
        values[placeholder] = a["correct_answer"]

    dynamo().Table(DYNAMO_TEST_TABLE).update_item(
        Key={"test_id": test_id},
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeValues=values,
    )


def update_answers_status(test_id: str, answers_status: str) -> None:
    """Update answers_status field only (e.g. 'Processing', 'Failed')."""
    validate(AnswerStatus, answers_status, "answers_status")
    dynamo().Table(DYNAMO_TEST_TABLE).update_item(
        Key={"test_id": test_id},
        UpdateExpression="SET answers_status = :as, updated_at = :ts",
        ExpressionAttributeValues={":as": answers_status, ":ts": now_iso()},
    )

"""
Answers API — serves correct answers for completed generation jobs.

Each question in NeetTestGenerator_Test carries its own correct_answer field
(filled in by the answer worker) — this endpoint just extracts those per
question rather than reading a separate list.

GET /answers/{test_id}
  - answers_status = "Done"                     → 200 with answers list
  - answers_status = "Processing" or "Pending"   → 200 with empty list and status
  - answers_status = "Failed"                    → 200 with status (client may retry)
  - test not found                               → 404
"""

from fastapi import APIRouter, HTTPException

from core.db_test import get_test

router = APIRouter()


@router.get(
    "/answers/{test_id}",
    summary="Get correct answers for a completed test",
    response_description=(
        "answers_status: Pending | Processing | Done | Failed. "
        "answers list is populated only when answers_status = Done."
    ),
)
def get_answers(test_id: str):
    test = get_test(test_id)
    if not test:
        raise HTTPException(status_code=404, detail="Test not found")

    answers_status = test.get("answers_status", "Pending")

    clean = []
    for q in test.get("questions", []):
        if q.get("correct_answer") is not None:
            clean.append({
                "question_number": int(q["question_number"]),
                "correct_answer":  str(q["correct_answer"]),
            })

    return {
        "test_id":        test_id,
        "answers_status": answers_status,
        "answers":        clean,
    }

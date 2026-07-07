"""
Answer Worker — SQS long-poll loop for NEET answer generation.

Flow per message:
  1. Parse test_id from SQS body
  2. Load the test row from NeetTestGenerator_Test; skip if answers_status already "Done"
  3. Mark answers_status = "Processing"
  4. Call OpenAI on the test's questions (single text call, no file upload)
  5. Parse response → [{question_number, correct_answer}, ...]
  6. Save to NeetTestGenerator_Test: answers, answers_status = "Done"
  7. Delete SQS message

On failure: do NOT delete SQS → SQS retries → DLQ after maxReceiveCount.

Run locally:
  python worker/answer_worker.py

ECS CMD:
  ["python", "worker/answer_worker.py"]
"""

import json
import os
import sys
import time
from pathlib import Path

import boto3
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from core.config import (
    AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
    SQS_ANSWERS_QUEUE_URL, SQS_WAIT_TIME,
    OPENAI_MODEL,
)
from core.enums import AnswerStatus
from core.db_jobs import get_job
from core.db_test import get_test, save_answers, update_answers_status
from core.parser import parse_json_array_response
from core.logger import get_logger

log = get_logger(__name__)

_ANSWER_PROMPT = (Path(__file__).parent.parent / "prompts" / "answer_prompt.txt").read_text(
    encoding="utf-8"
)

# How many questions to evaluate per LLM call (keeps prompt size manageable)
_BATCH_SIZE = 20


def _db_write(_operation: str, _ctx_test_id: str, _ctx_user_id: str, _fn, *args, **kwargs):
    """
    Run a DynamoDB write, logging with full [user_id][test_id] context on
    failure before re-raising. Own parameters are underscore-prefixed so
    they never collide with same-named kwargs (test_id=..., user_id=...)
    passed through to _fn.
    """
    try:
        return _fn(*args, **kwargs)
    except Exception as exc:
        log.error("answer_worker.dynamo_write_failed",
                  user_id=_ctx_user_id, test_id=_ctx_test_id, operation=_operation, error=str(exc))
        raise


# ── AWS client ────────────────────────────────────────────────────────────────

def _sqs():
    return boto3.client(
        "sqs",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )


# ── LLM call ──────────────────────────────────────────────────────────────────

def _generate_answers_for_batch(client: OpenAI, questions: list, user_id: str = None, test_id: str = None) -> list:
    """
    Send a batch of questions to OpenAI; return [{question_number, correct_answer}, ...].
    Uses chat completions (text only — no file upload needed).
    """
    user_msg = json.dumps(questions, ensure_ascii=False)

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": _ANSWER_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        max_completion_tokens=1024,
        temperature=0,
    )

    raw = response.choices[0].message.content.strip()

    parsed = parse_json_array_response(raw)
    if parsed is None:
        raise ValueError(f"No JSON array in LLM response: {raw[:300]}")

    # Validate and normalise each item
    result = []
    for item in parsed:
        qnum = item.get("question_id") or item.get("question_number")
        ans = str(item.get("correct_answer", "")).lower().strip()
        if qnum is None or ans not in ("a", "b", "c", "d"):
            log.warning("answer_worker.bad_item", user_id=user_id, test_id=test_id, item=item)
            continue
        result.append({"question_number": int(qnum), "correct_answer": ans})

    return result


def _generate_answers(test_id: str, questions: list, user_id: str = None) -> list:
    """
    Generate correct answers for all questions in batches.
    Returns [{question_number, correct_answer}, ...].
    """
    client = OpenAI()
    all_answers = []

    # Strip down to evaluator-needed fields; coerce Decimal question_number → int
    slim = []
    for q in questions:
        item = {
            "question_id":   int(q["question_number"]),
            "question_type": q.get("question_type", ""),
            "question_text": q.get("question_content_latex", ""),
            "options":       {k: str(v) for k, v in q.get("answer_options", {}).items()},
        }
        if "question_category" in q:
            item["question_category"] = q["question_category"]
        slim.append(item)

    total_answer_batches = (len(slim) + _BATCH_SIZE - 1) // _BATCH_SIZE
    for i in range(0, len(slim), _BATCH_SIZE):
        batch = slim[i:i + _BATCH_SIZE]
        answer_batch_number = i // _BATCH_SIZE + 1
        log.info("answer_worker.batch_start", user_id=user_id, test_id=test_id,
                 answer_batch=f"{answer_batch_number}/{total_answer_batches}",
                 questions_in_batch=len(batch))
        answers = _generate_answers_for_batch(client, batch, user_id=user_id, test_id=test_id)
        all_answers.extend(answers)
        log.info("answer_worker.batch_done", user_id=user_id, test_id=test_id,
                 answer_batch=f"{answer_batch_number}/{total_answer_batches}",
                 answers_generated=len(answers))

    return all_answers


# ── Message handler ───────────────────────────────────────────────────────────

def process_message(message: dict) -> None:
    job_start = time.time()
    try:
        body = json.loads(message["Body"])
    except Exception as exc:
        log.error("answer_worker.bad_json", error=str(exc))
        _sqs().delete_message(
            QueueUrl=SQS_ANSWERS_QUEUE_URL,
            ReceiptHandle=message["ReceiptHandle"],
        )
        return

    test_id = body.get("test_id")
    receipt = message["ReceiptHandle"]

    if not test_id:
        log.error("answer_worker.missing_test_id", body=body)
        _sqs().delete_message(QueueUrl=SQS_ANSWERS_QUEUE_URL, ReceiptHandle=receipt)
        return

    retry_count = int(
        message.get("Attributes", {}).get("ApproximateReceiveCount", 1)
    )
    log.info("answer_worker.received", test_id=test_id, sqs_delivery_attempt=retry_count)

    job = get_job(test_id)
    if not job:
        log.error("answer_worker.job_not_found", test_id=test_id)
        _sqs().delete_message(QueueUrl=SQS_ANSWERS_QUEUE_URL, ReceiptHandle=receipt)
        return

    user_id = job.get("user_id")

    test = get_test(test_id)
    if not test:
        log.error("answer_worker.test_not_found", user_id=user_id, test_id=test_id)
        _sqs().delete_message(QueueUrl=SQS_ANSWERS_QUEUE_URL, ReceiptHandle=receipt)
        return

    # Skip if already answered
    if test.get("answers_status") == AnswerStatus.DONE.value:
        log.info("answer_worker.skip_already_done", user_id=user_id, test_id=test_id)
        _sqs().delete_message(QueueUrl=SQS_ANSWERS_QUEUE_URL, ReceiptHandle=receipt)
        return

    questions = test.get("questions", [])
    if not questions:
        log.warning("answer_worker.no_questions", user_id=user_id, test_id=test_id,
                    reason="Test row has zero questions — marking answers Done with an empty list to avoid retrying forever")
        # Mark as done with empty answers so we don't retry forever
        _db_write("save_answers", test_id, user_id, save_answers,
                  test_id, [], answers_status=AnswerStatus.DONE.value)
        _sqs().delete_message(QueueUrl=SQS_ANSWERS_QUEUE_URL, ReceiptHandle=receipt)
        return

    _db_write("update_answers_status", test_id, user_id, update_answers_status,
              test_id, AnswerStatus.PROCESSING.value)

    try:
        answers = _generate_answers(test_id, questions, user_id=user_id)
        _db_write("save_answers", test_id, user_id, save_answers,
                  test_id, answers, answers_status=AnswerStatus.DONE.value)
        log.info("answer_worker.complete",
                 user_id=user_id, test_id=test_id,
                 questions_total=len(questions), answers_generated=len(answers),
                 elapsed_seconds=round(time.time() - job_start, 2))

    except Exception as exc:
        log.error("answer_worker.failed", user_id=user_id, test_id=test_id, error=str(exc))
        _db_write("update_answers_status", test_id, user_id, update_answers_status,
                  test_id, AnswerStatus.FAILED.value)
        # Do NOT delete — let SQS retry → DLQ
        return

    _sqs().delete_message(QueueUrl=SQS_ANSWERS_QUEUE_URL, ReceiptHandle=receipt)
    log.info("answer_worker.sqs_deleted", user_id=user_id, test_id=test_id)


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    if not SQS_ANSWERS_QUEUE_URL:
        log.error("answer_worker.no_queue_url",
                  msg="SQS_ANSWERS_QUEUE_URL is not set — answer worker cannot start")
        sys.exit(1)

    log.info("answer_worker.start", queue=SQS_ANSWERS_QUEUE_URL, model=OPENAI_MODEL)
    sqs = _sqs()

    while True:
        try:
            resp = sqs.receive_message(
                QueueUrl=SQS_ANSWERS_QUEUE_URL,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=SQS_WAIT_TIME,
                VisibilityTimeout=120,
                AttributeNames=["ApproximateReceiveCount"],
            )
            for msg in resp.get("Messages", []):
                process_message(msg)

        except KeyboardInterrupt:
            log.info("answer_worker.stop")
            break

        except Exception as exc:
            log.error("answer_worker.poll_error", error=str(exc))
            time.sleep(5)


if __name__ == "__main__":
    main()

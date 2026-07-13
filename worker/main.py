"""
ECS Worker — SQS long-poll loop for NEET Test Generator.

Flow per message:
  1. Parse test_id from SQS body; read ApproximateReceiveCount for retry tracking
  2. Load job from DynamoDB; skip if already Done/Partially Failed/Failed
  3. Mark status = "In progress", store retry_count
  4. job["components"] = [{question_type, difficulty, question_count}, ...] — one
     entry for a normal single-type test, several for a mixed test (e.g. easy MCQ
     + hard AR + easy MTC all in one test_id).
     Image: generate_chunk once per component (single "chunk" each).
     PDF:   download → upload full to OpenAI → detect_topics ONCE, shared across
            all components → per component: distribute_questions → parallel chunks
            → each chunk: extract pages → upload → generate → delete upload file.
     All components' questions merge into one test.
  5. Determine outcome:
     - ALL components/chunks failed → status="Failed",           do NOT delete SQS
     - SOME failed                  → status="Partially Failed",  delete SQS
     - All succeeded                → status="Done",              delete SQS
  6. Done/Partially Failed: write test content (NeetTestGenerator_Test), record
     usage, delete SQS message
  7. Failed:                do NOT delete SQS message (SQS handles retry + DLQ)

Run locally:
  python worker/main.py

ECS CMD:
  ["python", "worker/main.py"]
"""

import json
import os
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import boto3
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from core.config import (
    AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
    SQS_GENERATOR_QUEUE_URL, SQS_ANSWERS_QUEUE_URL, SQS_WAIT_TIME,
    OPENAI_MODEL,
)
from core.enums import JobStatus, BatchStatus
from core.db_jobs import (
    get_job, update_job_status, complete_job, init_batches, update_batch_in_job,
)
from core.db_test import create_test
from core.db_users import record_test_generated
from core.dedup import dedup_questions
from core.validators import (
    check_ar_type_distribution,
    filter_inconsistent_multi_statement_mcq,
    filter_inconsistent_mtc,
    filter_invalid_mtc_options,
    filter_mtc_duplicate_content,
    strip_internal_fields,
)
from core.messages import build_answer_message
from core.generator import generate_chunk
from core.logger import get_logger
from core.pdf import get_page_count_from_bytes, extract_pages
from core.redis_client import (
    init_job_progress, update_chunk_progress,
    get_job_progress, delete_job_progress,
)
from core.storage import get_presigned_url
from core.topic_detector import detect_topics, distribute_questions

log = get_logger(__name__)

_MAX_PARALLEL_CHUNKS = 4

_COMPONENT_ABBR = {
    "mcq": "mcq",
    "assertion_reason": "ar",
    "match_the_column": "mtc",
}
_QUESTION_TYPE_MAP = {
    "mcq": "MCQ",
    "assertion_reason": "ASSERTION_REASON",
    "match_the_column": "MATCH_THE_COLUMN",
}


# ── AWS clients ───────────────────────────────────────────────────────────────

def _sqs():
    return boto3.client(
        "sqs",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )


def _s3():
    return boto3.client(
        "s3",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _download_s3(s3_uri: str) -> bytes:
    without_prefix = s3_uri[5:]
    bucket, _, key = without_prefix.partition("/")
    resp = _s3().get_object(Bucket=bucket, Key=key)
    return resp["Body"].read()


def _openai_upload(pdf_bytes: bytes) -> str:
    """Upload PDF bytes to OpenAI Files API; return file_id."""
    client = OpenAI()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name
    try:
        with open(tmp_path, "rb") as f:
            uploaded = client.files.create(file=f, purpose="user_data")
        return uploaded.id
    finally:
        os.unlink(tmp_path)


def _openai_delete(file_id: str) -> None:
    try:
        OpenAI().files.delete(file_id)
    except Exception as exc:
        log.warning("worker.openai_delete_failed", file_id=file_id, error=str(exc))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_write(_operation: str, _ctx_test_id: str, _ctx_user_id: str, _fn, *args, **kwargs):
    """
    Run a DynamoDB write, logging with full [user_id][test_id] context on
    failure before re-raising — every write on the critical path used to fail
    silently or crash with a bare, unattributed traceback.

    Own parameters are underscore-prefixed and distinct from any real field
    name (test_id, user_id, etc.) specifically so they never collide with
    the same-named kwargs passed through to _fn — e.g. create_test(test_id=...)
    would otherwise raise "got multiple values for argument" before _fn runs.
    """
    try:
        return _fn(*args, **kwargs)
    except Exception as exc:
        log.error("worker.dynamo_write_failed",
                  user_id=_ctx_user_id, test_id=_ctx_test_id, operation=_operation, error=str(exc))
        raise


def _enqueue_answers(test_id: str, user_id: str = None) -> None:
    """Push test_id to the answers SQS queue for async answer generation."""
    if not SQS_ANSWERS_QUEUE_URL:
        log.warning("worker.answers_queue_not_configured", user_id=user_id, test_id=test_id)
        return
    try:
        _sqs().send_message(
            QueueUrl=SQS_ANSWERS_QUEUE_URL,
            MessageBody=build_answer_message(test_id),
        )
        log.info("worker.answers_enqueued", user_id=user_id, test_id=test_id)
    except Exception as exc:
        log.error("worker.answers_enqueue_failed", user_id=user_id, test_id=test_id, error=str(exc))


def _to_test_questions(questions: list) -> list:
    """
    Shape merged, multi-component generator output into NeetTestGenerator_Test's
    question_object: question_number, question_type, difficulty, question,
    answer_options, and question_category (MCQ-hard only, passthrough).
    Trusts each question's own "question_type" (set by the model per
    prompts/schemas/*.txt) and "difficulty" (set by the worker per component).
    """
    out = []
    for i, q in enumerate(questions, 1):
        item = {
            "question_number": i,
            "question_type":   q.get("question_type", ""),
            "difficulty":      q.get("difficulty", ""),
            "question":        q.get("question_text", ""),
            "answer_options":  q.get("options", {}),
        }
        if "question_category" in q:
            item["question_category"] = q["question_category"]
        out.append(item)
    return out


def _apply_type_validators(test_id: str, questions: list) -> tuple:
    """
    Route each question to the validator(s) for its own type/difficulty, then
    recombine. Questions are matched by object identity (id()) since dicts
    aren't hashable and we need to know exactly which ones got dropped.

    Returns (kept_questions, dropped_counts) where dropped_counts is a
    dict[(question_type, difficulty), int] — used by _backfill_dropped_questions
    to know how many replacement questions to request per combo.
    """
    ar_list = [q for q in questions if q.get("question_type") == "ASSERTION_REASON"]
    if ar_list:
        check_ar_type_distribution(test_id, ar_list)

    mcq_hard_list = [q for q in questions if q.get("question_type") == "MCQ" and q.get("difficulty") == "hard"]
    mcq_hard_kept = filter_inconsistent_multi_statement_mcq(test_id, mcq_hard_list) if mcq_hard_list else []
    mcq_hard_dropped = {id(q) for q in mcq_hard_list} - {id(q) for q in mcq_hard_kept}

    mtc_list = [q for q in questions if q.get("question_type") == "MATCH_THE_COLUMN"]
    mtc_step0 = filter_inconsistent_mtc(test_id, mtc_list) if mtc_list else []
    mtc_step1 = filter_invalid_mtc_options(test_id, mtc_step0) if mtc_step0 else []
    mtc_step2 = filter_mtc_duplicate_content(test_id, mtc_step1) if mtc_step1 else []
    mtc_dropped = {id(q) for q in mtc_list} - {id(q) for q in mtc_step2}

    dropped = mcq_hard_dropped | mtc_dropped
    kept = [q for q in questions if id(q) not in dropped]

    dropped_counts = {}
    for q in questions:
        if id(q) in dropped:
            key = (q.get("question_type", ""), q.get("difficulty", ""))
            dropped_counts[key] = dropped_counts.get(key, 0) + 1

    return kept, dropped_counts


_REVERSE_QUESTION_TYPE_MAP = {v: k for k, v in _QUESTION_TYPE_MAP.items()}


def _backfill_dropped_questions(job: dict, questions: list, dropped_counts: dict, source: dict, max_rounds: int = 2) -> list:
    """
    After _apply_type_validators drops questions (no valid answer among their
    own options), request replacement questions from the whole original
    source content — not tied to a specific topic-chunk, since drops are
    discovered only after all chunks are already combined — and re-run the
    same validators on the merged result. Repeats up to max_rounds; if a
    shortfall remains after that, accepts a shorter-than-requested list
    (today's existing fallback behavior, unchanged).

    source: {"file_type": "pdf", "openai_file_id": ...} or
            {"file_type": <image type>, "presigned_url": ...}
    """
    test_id = job["test_id"]
    user_id = job.get("user_id")
    subject = job["subject"]
    medium = job["medium"]

    requested = {
        (_QUESTION_TYPE_MAP[c["question_type"]], c["difficulty"]): c["question_count"]
        for c in job["components"]
    }

    def _shortfall(qs: list) -> dict:
        counts = {}
        for q in qs:
            key = (q.get("question_type", ""), q.get("difficulty", ""))
            counts[key] = counts.get(key, 0) + 1
        return {
            key: want - counts.get(key, 0)
            for key, want in requested.items()
            if want - counts.get(key, 0) > 0
        }

    kept = questions
    short = {k: v for k, v in dropped_counts.items() if v > 0}

    for round_num in range(1, max_rounds + 1):
        if not short:
            break

        new_questions = []
        for (question_type_upper, difficulty), count in short.items():
            question_type = _REVERSE_QUESTION_TYPE_MAP.get(question_type_upper)
            if not question_type:
                continue
            same_type_texts = [
                q.get("question_text", "") for q in kept
                if q.get("question_type") == question_type_upper
            ]
            try:
                backfilled = generate_chunk(
                    file_type=source["file_type"],
                    presigned_url=source.get("presigned_url"),
                    openai_file_id=source.get("openai_file_id"),
                    subject=subject,
                    medium=medium,
                    question_type=question_type,
                    difficulty=difficulty,
                    question_count=count,
                    previous_questions=same_type_texts,
                    user_id=user_id,
                    test_id=test_id,
                    batch_id=f"backfill_r{round_num}_{question_type}_{difficulty}",
                )
            except Exception as exc:
                log.warning("worker.backfill_generation_failed",
                            test_id=test_id, question_type=question_type,
                            difficulty=difficulty, round=round_num, error=str(exc))
                continue

            for q in backfilled:
                q["difficulty"] = difficulty
                q["topic"] = "backfill"
                q["subject"] = subject
            new_questions.extend(backfilled)

        if not new_questions:
            log.info("worker.backfill_no_new_questions", test_id=test_id, round=round_num)
            break

        merged = dedup_questions(test_id, kept + new_questions)
        kept, _ = _apply_type_validators(test_id, merged)
        short = _shortfall(kept)
        if short:
            log.info("worker.backfill_round_done", test_id=test_id, round=round_num, still_short=short)

    if short:
        log.info("worker.backfill_shortfall_accepted", test_id=test_id, final_shortfall=short)

    # Never ship more than what was actually requested per (type, difficulty)
    # — a backfill round can overshoot its ask by a question or two.
    counts_so_far = {}
    final = []
    for q in kept:
        key = (q.get("question_type", ""), q.get("difficulty", ""))
        limit = requested.get(key)
        if limit is None:
            final.append(q)
            continue
        counts_so_far[key] = counts_so_far.get(key, 0) + 1
        if counts_so_far[key] <= limit:
            final.append(q)

    return final


# ── Image job ─────────────────────────────────────────────────────────────────

def _process_image_job(job: dict) -> tuple:
    """
    Returns (questions: list, components_failed: int, components_total: int).
    Each component is one presigned-URL call to generate_chunk — no PDF
    chunking involved, so one component = one "batch" entry.
    """
    test_id = job["test_id"]
    user_id = job.get("user_id")
    components = job["components"]
    components_total = len(components)
    components_failed = 0
    all_questions = []

    _db_write("init_batches", test_id, user_id, init_batches, test_id, components_total)
    init_job_progress(test_id, [
        {"topic": f"{c['question_type']}_{c['difficulty']}"} for c in components
    ], user_id=user_id)

    presigned_url = get_presigned_url(job["file_reference"], expires=3600)

    for comp in components:
        abbr = _COMPONENT_ABBR[comp["question_type"]]
        batch_key = f"{abbr}_{comp['difficulty']}_chunk_0"
        batch_chunk_id = str(uuid.uuid4())  # per-attempt random id, distinct from batch_key (the human-readable chunk identifier)
        started = _now_iso()

        update_chunk_progress(test_id, batch_key, {
            "batch_chunk_id": batch_chunk_id, "status": "processing", "started_at": started,
        }, user_id=user_id)
        log.info("worker.image_component_start",
                 user_id=user_id, test_id=test_id, batch_id=batch_key,
                 question_type=comp["question_type"],
                 difficulty=comp["difficulty"], question_count=comp["question_count"])

        try:
            questions = generate_chunk(
                file_type=job["file_type"],
                presigned_url=presigned_url,
                subject=job["subject"],
                medium=job["medium"],
                question_type=comp["question_type"],
                difficulty=comp["difficulty"],
                question_count=comp["question_count"],
                user_id=user_id,
                test_id=test_id,
                batch_id=batch_key,
            )
            for q in questions:
                q["difficulty"] = comp["difficulty"]
                q["topic"] = "image"
                q["subject"] = job["subject"]
            all_questions.extend(questions)

            completed = _now_iso()
            _db_write("update_batch_in_job", test_id, user_id, update_batch_in_job, test_id, batch_key, {
                "batch_chunk_id": batch_chunk_id,
                "component_question_type": comp["question_type"],
                "component_difficulty": comp["difficulty"],
                "question_count": comp["question_count"],
                "generated": len(questions),
                "status": BatchStatus.DONE.value,
                "retry_count": 0,
                "error_message": None,
                "started_at": started,
                "completed_at": completed,
            }, delta_active=-1, delta_successful=1)
            update_chunk_progress(test_id, batch_key, {
                "status": "done", "generated": len(questions), "completed_at": completed,
            }, user_id=user_id)

        except Exception as exc:
            completed = _now_iso()
            log.error("worker.image_component_failed",
                      user_id=user_id, test_id=test_id, batch_id=batch_key,
                      question_type=comp["question_type"],
                      difficulty=comp["difficulty"], error=str(exc))
            _db_write("update_batch_in_job", test_id, user_id, update_batch_in_job, test_id, batch_key, {
                "batch_chunk_id": batch_chunk_id,
                "component_question_type": comp["question_type"],
                "component_difficulty": comp["difficulty"],
                "question_count": comp["question_count"],
                "generated": 0,
                "status": BatchStatus.FAILED.value,
                "retry_count": 0,
                "error_message": str(exc),
                "started_at": started,
                "completed_at": completed,
            }, delta_active=-1, delta_failed=1)
            update_chunk_progress(test_id, batch_key, {
                "status": "failed", "error": str(exc)[:300], "completed_at": completed,
            }, user_id=user_id)
            components_failed += 1

    all_questions, dropped_counts = _apply_type_validators(test_id, all_questions)
    if any(dropped_counts.values()):
        all_questions = _backfill_dropped_questions(
            job, all_questions, dropped_counts,
            source={"file_type": job["file_type"], "presigned_url": presigned_url},
        )
    strip_internal_fields(all_questions)
    return all_questions, components_failed, components_total


# ── PDF job ───────────────────────────────────────────────────────────────────

def _process_pdf_chunk(
    test_id: str,
    user_id: str,
    batch_key: str,
    chunk: dict,
    chunk_count: int,
    pdf_bytes: bytes,
    subject: str,
    medium: str,
    question_type: str,
    difficulty: str,
) -> tuple:
    """
    Process one (component, PDF-chunk) pair in a worker thread.
    Returns (questions: list, succeeded: bool).
    succeeded=False means an exception occurred; succeeded=True even if questions=[].
    """
    file_id = None
    chunk_t0 = time.time()
    started = _now_iso()
    batch_chunk_id = str(uuid.uuid4())  # per-attempt random id, distinct from batch_key (the human-readable chunk identifier)
    page_range = f"{chunk['start_page']}-{chunk['end_page']}"

    try:
        update_chunk_progress(test_id, batch_key, {
            "batch_chunk_id": batch_chunk_id, "status": "processing", "started_at": started,
        }, user_id=user_id)

        chunk_bytes = extract_pages(pdf_bytes, chunk["start_page"], chunk["end_page"])
        file_id = _openai_upload(chunk_bytes)
        log.info("worker.chunk_uploaded_to_openai",
                 user_id=user_id, test_id=test_id, batch_id=batch_key,
                 openai_file_id=file_id, page_range=page_range)

        questions = generate_chunk(
            file_type="pdf",
            openai_file_id=file_id,
            subject=subject,
            medium=medium,
            question_type=question_type,
            difficulty=difficulty,
            question_count=chunk_count,
            user_id=user_id,
            test_id=test_id,
            batch_id=batch_key,
        )

        for q in questions:
            q["difficulty"] = difficulty
            q["topic"] = chunk.get("topic", batch_key)
            q["subject"] = subject

        completed = _now_iso()
        _db_write("update_batch_in_job", test_id, user_id, update_batch_in_job, test_id, batch_key, {
            "batch_chunk_id": batch_chunk_id,
            "component_question_type": question_type,
            "component_difficulty": difficulty,
            "pdf_split_page_range": page_range,
            "question_count": chunk_count,
            "generated": len(questions),
            "status": BatchStatus.DONE.value,
            "retry_count": 0,
            "error_message": None,
            "started_at": started,
            "completed_at": completed,
        }, delta_active=-1, delta_successful=1)
        update_chunk_progress(test_id, batch_key, {
            "status": "done", "generated": len(questions), "completed_at": completed,
        }, user_id=user_id)

        log.info("worker.chunk_done",
                 user_id=user_id, test_id=test_id, batch_id=batch_key,
                 page_range=page_range, questions_generated=len(questions),
                 elapsed_seconds=round(time.time() - chunk_t0, 2))
        return questions, True

    except Exception as exc:
        completed = _now_iso()
        log.error("worker.chunk_failed",
                  user_id=user_id, test_id=test_id, batch_id=batch_key,
                  page_range=page_range, error=str(exc))
        _db_write("update_batch_in_job", test_id, user_id, update_batch_in_job, test_id, batch_key, {
            "batch_chunk_id": batch_chunk_id,
            "component_question_type": question_type,
            "component_difficulty": difficulty,
            "pdf_split_page_range": page_range,
            "question_count": chunk_count,
            "generated": 0,
            "status": BatchStatus.FAILED.value,
            "retry_count": 0,
            "error_message": str(exc),
            "started_at": started,
            "completed_at": completed,
        }, delta_active=-1, delta_failed=1)
        update_chunk_progress(test_id, batch_key, {
            "status": "failed", "error": str(exc)[:300], "completed_at": completed,
        }, user_id=user_id)
        return [], False

    finally:
        if file_id:
            _openai_delete(file_id)


def _process_pdf_job(job: dict) -> tuple:
    """
    Returns (questions: list, chunks_failed: int, chunks_total: int).
    Topic detection runs ONCE for the whole PDF; each component then
    distributes its own question_count across those same topics.
    """
    test_id = job["test_id"]
    user_id = job.get("user_id")
    components = job["components"]

    pdf_bytes = _download_s3(job["file_reference"])
    total_pages = get_page_count_from_bytes(pdf_bytes)
    log.info("worker.pdf_downloaded", user_id=user_id, test_id=test_id,
             page_count=total_pages, file_size_bytes=len(pdf_bytes))

    full_file_id = _openai_upload(pdf_bytes)
    try:
        detection = detect_topics(full_file_id, total_pages, user_id=user_id, test_id=test_id)
    finally:
        _openai_delete(full_file_id)

    topics = detection["topics"]
    log.info("worker.topics", user_id=user_id, test_id=test_id, topic_count=len(topics),
             used_mechanical_page_split_fallback=detection["page_fallback_used"],
             used_page_proportional_density_fallback=detection["density_fallback_used"])

    # Build one flat work list across all components' chunks
    work_items = []
    for comp in components:
        counts, skipped = distribute_questions(topics, comp["question_count"], user_id=user_id, test_id=test_id)
        if skipped:
            log.warning("worker.topics_skipped",
                        user_id=user_id, test_id=test_id,
                        reason="More topics than requested questions — lowest-density topics got zero",
                        question_type=comp["question_type"], difficulty=comp["difficulty"],
                        skipped_topics=skipped)
        active = [(idx, topics[idx], counts[idx]) for idx in range(len(topics)) if counts[idx] > 0]
        abbr = _COMPONENT_ABBR[comp["question_type"]]
        for local_idx, (_, chunk, count) in enumerate(active):
            work_items.append({
                "batch_key": f"{abbr}_{comp['difficulty']}_chunk_{local_idx}",
                "chunk": chunk,
                "count": count,
                "question_type": comp["question_type"],
                "difficulty": comp["difficulty"],
            })

    chunks_total = len(work_items)
    chunks_failed = 0
    chunk_results = []

    _db_write("init_batches", test_id, user_id, init_batches, test_id, chunks_total)
    init_job_progress(test_id, [{"topic": w["batch_key"]} for w in work_items], user_id=user_id)

    with ThreadPoolExecutor(max_workers=min(max(chunks_total, 1), _MAX_PARALLEL_CHUNKS)) as pool:
        futures = {
            pool.submit(
                _process_pdf_chunk,
                test_id, user_id, w["batch_key"], w["chunk"], w["count"],
                pdf_bytes, job["subject"], job["medium"],
                w["question_type"], w["difficulty"],
            ): w["batch_key"]
            for w in work_items
        }
        for future in as_completed(futures):
            batch_key = futures[future]
            try:
                questions, succeeded = future.result()
            except Exception as exc:
                log.error("worker.chunk_future_error",
                          user_id=user_id, test_id=test_id, batch_id=batch_key, error=str(exc))
                questions, succeeded = [], False
            chunk_results.append(questions)
            if not succeeded:
                chunks_failed += 1

    all_questions = [q for questions in chunk_results for q in questions]

    before = len(all_questions)
    all_questions = dedup_questions(test_id, all_questions)
    if len(all_questions) < before:
        log.info("worker.dedup_done", user_id=user_id, test_id=test_id,
                 reason="Near-duplicate questions removed (Jaccard similarity, likely from page overlap between chunks)",
                 questions_removed=before - len(all_questions),
                 questions_remaining=len(all_questions))

    all_questions, dropped_counts = _apply_type_validators(test_id, all_questions)
    if any(dropped_counts.values()):
        backfill_file_id = None
        try:
            backfill_file_id = _openai_upload(pdf_bytes)
            all_questions = _backfill_dropped_questions(
                job, all_questions, dropped_counts,
                source={"file_type": "pdf", "openai_file_id": backfill_file_id},
            )
        except Exception as exc:
            log.warning("worker.backfill_upload_failed", test_id=test_id, error=str(exc))
        finally:
            if backfill_file_id:
                _openai_delete(backfill_file_id)
    strip_internal_fields(all_questions)

    return all_questions, chunks_failed, chunks_total


# ── Message handler ───────────────────────────────────────────────────────────

def process_message(message: dict) -> None:
    job_start = time.time()
    try:
        body = json.loads(message["Body"])
    except Exception as exc:
        log.error("worker.bad_message_json", error=str(exc))
        _sqs().delete_message(QueueUrl=SQS_GENERATOR_QUEUE_URL, ReceiptHandle=message["ReceiptHandle"])
        return

    test_id = body.get("test_id")
    receipt = message["ReceiptHandle"]

    retry_count = int(message.get("Attributes", {}).get("ApproximateReceiveCount", 1))

    if not test_id:
        log.error("worker.missing_test_id", body=body)
        _sqs().delete_message(QueueUrl=SQS_GENERATOR_QUEUE_URL, ReceiptHandle=receipt)
        return

    log.info("worker.received", test_id=test_id,
             sqs_delivery_attempt=retry_count)

    job = get_job(test_id)
    if not job:
        log.error("worker.job_not_found", test_id=test_id)
        _sqs().delete_message(QueueUrl=SQS_GENERATOR_QUEUE_URL, ReceiptHandle=receipt)
        return

    user_id = job.get("user_id")

    current_status = job.get("status")
    if current_status in (JobStatus.DONE.value, JobStatus.PARTIALLY_FAILED.value):
        log.info("worker.skip_already_done",
                 user_id=user_id, test_id=test_id,
                 reason="SQS redelivered a message for a job that already finished — deleting, nothing to do",
                 current_status=current_status)
        _sqs().delete_message(QueueUrl=SQS_GENERATOR_QUEUE_URL, ReceiptHandle=receipt)
        return
    if current_status == JobStatus.FAILED.value:
        log.info("worker.skip_already_failed",
                 user_id=user_id, test_id=test_id,
                 reason="Job already marked Failed — leaving SQS message alone for its own retry/DLQ policy",
                 sqs_delivery_attempt=retry_count)
        return

    _db_write("update_job_status", test_id, user_id, update_job_status,
              test_id, JobStatus.IN_PROGRESS.value, retry_count=retry_count)

    questions = []
    all_failed = False

    try:
        file_type = job.get("file_type", "")
        is_pdf = file_type in ("pdf", "application/pdf")

        if is_pdf:
            questions, chunks_failed, chunks_total = _process_pdf_job(job)
        else:
            questions, chunks_failed, chunks_total = _process_image_job(job)
        all_failed = (chunks_total > 0 and chunks_failed == chunks_total)

    except Exception as exc:
        log.error("worker.job_exception", user_id=user_id, test_id=test_id,
                  reason="Uncaught exception before any batch outcome could be determined — job marked Failed",
                  error=str(exc))
        _db_write("complete_job", test_id, user_id, complete_job,
                  test_id, JobStatus.FAILED.value, error_message=str(exc))
        return

    if all_failed:
        _db_write("complete_job", test_id, user_id, complete_job,
                  test_id, JobStatus.FAILED.value,
                  error_message="All generation batches failed after retries.")
        delete_job_progress(test_id, user_id=user_id)
        log.warning("worker.all_failed", user_id=user_id, test_id=test_id,
                    reason="Every batch/chunk in this job failed after exhausting its own retries")
        return

    requested = int(job.get("question_count", 0))
    generated = len(questions)
    is_pdf = job.get("file_type", "") in ("pdf", "application/pdf")

    if generated == 0:
        final_status = JobStatus.PARTIALLY_FAILED.value
        _db_write("complete_job", test_id, user_id, complete_job,
                  test_id, final_status,
                  final_question_count=0,
                  partial_message=(
                      "Content se questions generate nahi ho sake. "
                      "Please try with a different section."
                  ))
    else:
        test_questions = _to_test_questions(questions)
        _db_write("create_test", test_id, user_id, create_test,
                  test_id=test_id,
                  test_series_name=job.get("test_series_name", f"{job['subject']} Test"),
                  questions=test_questions)

        if generated < requested:
            final_status = JobStatus.PARTIALLY_FAILED.value
            _db_write("complete_job", test_id, user_id, complete_job,
                      test_id, final_status,
                      final_question_count=generated,
                      partial_message=(
                          f"{generated}/{requested} questions generated. Some chunks could not be processed."
                      ))
        else:
            final_status = JobStatus.DONE.value
            _db_write("complete_job", test_id, user_id, complete_job,
                      test_id, final_status, final_question_count=generated)

        _enqueue_answers(test_id, user_id=user_id)

        try:
            record_test_generated(user_id or "", generated, "pdf" if is_pdf else "image")
            log.info("worker.usage_recorded", user_id=user_id, test_id=test_id,
                      questions_counted_toward_quota=generated)
        except Exception as exc:
            log.error("worker.usage_record_failed", user_id=user_id, test_id=test_id, error=str(exc))

    delete_job_progress(test_id)

    log.info("worker.complete", user_id=user_id, test_id=test_id,
             subject=job.get("subject"), medium=job.get("medium"),
             file_type=job.get("file_type"),
             final_status=final_status,
             questions_generated=generated, questions_requested=requested,
             elapsed_seconds=round(time.time() - job_start, 2))

    _sqs().delete_message(QueueUrl=SQS_GENERATOR_QUEUE_URL, ReceiptHandle=receipt)
    log.info("worker.sqs_deleted", user_id=user_id, test_id=test_id)


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    log.info("worker.start", queue=SQS_GENERATOR_QUEUE_URL, model=OPENAI_MODEL)
    sqs = _sqs()

    while True:
        try:
            resp = sqs.receive_message(
                QueueUrl=SQS_GENERATOR_QUEUE_URL,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=SQS_WAIT_TIME,
                VisibilityTimeout=300,
                AttributeNames=["ApproximateReceiveCount"],
            )
            for msg in resp.get("Messages", []):
                process_message(msg)

        except KeyboardInterrupt:
            log.info("worker.stop")
            break

        except Exception as exc:
            log.error("worker.poll_error", error=str(exc))
            time.sleep(5)


if __name__ == "__main__":
    main()

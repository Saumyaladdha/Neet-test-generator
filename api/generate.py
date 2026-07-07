"""
Generator API — POST /generate  +  GET /status/{test_id}

POST /generate
  1. Validate request fields, including each component against /detect's
     stored ranges if source_test_id is given
  2. check_and_reserve_quota (read-only — worker records usage after completion)
  3. create_job in DynamoDB (status = "Pending")
  4. Push message to SQS NeetTestGenerator queue
  5. Return 202 {test_id, status, message, created_at}

`components` lets one test mix question types/difficulties — e.g. easy MCQ +
hard AR + easy MTC all under one test_id. A normal single-type test is just a
components list with one entry.

GET /status/{test_id}
  Returns normalized job status per spec:
  - Done:              {test_id, status, questions, final_question_count, message, created_at, completed_at}
  - Partially Failed:  {test_id, status, questions, final_question_count, message, created_at, completed_at}
  - Failed:            {test_id, status, message, created_at}
  - In progress:       {test_id, status, message, created_at}
  - Pending:           {test_id, status, message, created_at}
"""

import sys
import os
import uuid
from datetime import datetime, timezone

import boto3
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.config import (
    AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
    SQS_GENERATOR_QUEUE_URL, PLAN_DAILY_LIMITS,
)
from core.enums import JobStatus
from core.db_users import check_and_reserve_quota
from core.db_jobs import create_job, get_job, update_job_status
from core.db_test import get_test
from core.db_detection import get_detection_result
from core.messages import build_generator_message
from core.logger import get_logger
from core.redis_client import get_job_progress

log = get_logger(__name__)

# ── Validation constants ──────────────────────────────────────────────────────

_VALID_TYPES    = {"mcq", "assertion_reason", "match_the_column"}
_VALID_DIFFS    = {"easy", "medium", "hard"}
_VALID_MEDIUMS  = {"english", "hindi"}
_VALID_SUBJECTS = {"biology", "chemistry"}
_MAX_QUESTIONS  = 100

# component question_type -> /detect's results[difficulty] key ("mcq" | "ar" | "mtc")
_DETECT_RESULT_KEY = {"mcq": "mcq", "assertion_reason": "ar", "match_the_column": "mtc"}

# Normalize MIME types to canonical "image" / "pdf"
_FILE_TYPE_MAP = {
    "image/png": "image", "image/jpeg": "image", "image/jpg": "image",
    "image/webp": "image", "image/gif": "image",
    "application/pdf": "pdf",
    "image": "image", "pdf": "pdf",
}


# ── Request model ─────────────────────────────────────────────────────────────

class Component(BaseModel):
    question_type: str
    difficulty: str
    question_count: int


class GenerateRequest(BaseModel):
    user_id: str
    subject: str
    medium: str   # required — no fallback; routes Hindi vs English prompt flow
    components: List[Component]   # one entry for a normal test, several for a mixed one
    file_reference: str   # s3:// URI pointing to uploaded file
    file_type: str        # "image" | "pdf"  (MIME types also accepted)
    source_test_id: Optional[str] = None  # from /detect's response, if this job follows a detection.
                                           # Purely for traceability — this job always gets its OWN
                                           # fresh test_id, since one detection commonly feeds several
                                           # generate calls (easy/medium/hard x mcq/ar/mtc) that must
                                           # not share a primary key.
    test_series_name: Optional[str] = None
    plan: str = "free"    # plan tier from the UI — determines the daily limit, never stored


# ── SQS client (lazy singleton) ───────────────────────────────────────────────

_sqs_client = None


def _sqs():
    global _sqs_client
    if _sqs_client is None:
        _sqs_client = boto3.client(
            "sqs",
            region_name=AWS_REGION,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        )
    return _sqs_client


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _traced(_operation: str, _ctx_test_id: str, _ctx_user_id: str, _fn, *args, **kwargs):
    """
    Run a write, logging with full [user_id][test_id] context on failure
    before re-raising. Own parameters are underscore-prefixed and distinct
    from any real field name (test_id, user_id, etc.) specifically so they
    never collide with the same-named kwargs being passed through to _fn —
    e.g. create_job(test_id=..., user_id=...) would otherwise raise
    "got multiple values for argument" before _fn ever runs.
    """
    try:
        return _fn(*args, **kwargs)
    except Exception as exc:
        log.error("generate.write_failed",
                  user_id=_ctx_user_id, test_id=_ctx_test_id, operation=_operation, error=str(exc))
        raise


# ── Router (also used by api/main.py for single-service deployment) ───────────

router = APIRouter()


@router.post(
    "/generate",
    status_code=202,
    summary="Submit a question-generation job",
    response_description=(
        "{test_id, status: 'Pending', message, created_at}"
    ),
)
async def generate(req: GenerateRequest):
    """
    **Submit a generation job.**

    Validates the daily quota for the given plan tier, creates a DynamoDB
    job record (status=Pending), and enqueues it for the ECS worker.
    Returns immediately with a test_id.

    Poll `GET /status/{test_id}` every 2 seconds to track progress.

    **Status lifecycle:** `Pending` → `In progress` → `Done` | `Partially Failed` | `Failed`
    """
    # ── Validate ──────────────────────────────────────────────────────────────
    if req.subject.lower() not in _VALID_SUBJECTS:
        raise HTTPException(400, f"Invalid subject '{req.subject}'. Allowed: {sorted(_VALID_SUBJECTS)}")
    if req.medium not in _VALID_MEDIUMS:
        raise HTTPException(400, f"Invalid medium '{req.medium}'. Allowed: {sorted(_VALID_MEDIUMS)}")
    if not req.file_reference.startswith("s3://"):
        raise HTTPException(400, "file_reference must be an s3:// URI")
    if req.plan not in PLAN_DAILY_LIMITS:
        raise HTTPException(400, f"Invalid plan '{req.plan}'. Allowed: {sorted(PLAN_DAILY_LIMITS)}")
    if not req.components:
        raise HTTPException(400, "components must have at least one entry")

    for c in req.components:
        if c.question_type not in _VALID_TYPES:
            raise HTTPException(400, f"Invalid question_type '{c.question_type}'. Allowed: {sorted(_VALID_TYPES)}")
        if c.difficulty not in _VALID_DIFFS:
            raise HTTPException(400, f"Invalid difficulty '{c.difficulty}'. Allowed: {sorted(_VALID_DIFFS)}")
        if c.question_count < 1:
            raise HTTPException(400, f"question_count must be >= 1 (got {c.question_count} for {c.question_type}/{c.difficulty})")

    question_count = sum(c.question_count for c in req.components)
    if not (1 <= question_count <= _MAX_QUESTIONS):
        raise HTTPException(400, f"total question_count across components must be 1–{_MAX_QUESTIONS}")

    # Normalize file_type to "image" or "pdf"
    canonical_type = _FILE_TYPE_MAP.get(req.file_type.lower())
    if not canonical_type:
        raise HTTPException(400, f"Invalid file_type '{req.file_type}'. Use 'image' or 'pdf'.")

    # ── Validate each component against /detect's stored ranges, if available ──
    if req.source_test_id:
        detection = get_detection_result(req.source_test_id)
        if detection and detection.get("results"):
            results = detection["results"]
            for c in req.components:
                result_key = _DETECT_RESULT_KEY[c.question_type]
                cell = results.get(c.difficulty, {}).get(result_key)
                if cell and c.question_count > cell.get("max", c.question_count):
                    raise HTTPException(
                        400,
                        f"Requested {c.question_count} {c.difficulty}/{c.question_type} questions, "
                        f"but detection for '{req.source_test_id}' only supports up to {cell['max']}."
                    )

    # ── Quota check ───────────────────────────────────────────────────────────
    try:
        check_and_reserve_quota(req.user_id, req.plan)
    except LookupError as exc:
        raise HTTPException(400, str(exc))
    except ValueError as exc:
        raise HTTPException(429, str(exc))

    # ── Create job ────────────────────────────────────────────────────────────
    # test_id is always freshly minted here — never reuse req.source_test_id as
    # the job's own key, or two generate calls from the same detection would
    # overwrite each other's row.
    test_id = str(uuid.uuid4())
    components = [c.model_dump() for c in req.components]
    test_series_name = req.test_series_name or f"{req.subject.title()} Test"
    created_at = _now_iso()
    try:
        _traced("create_job", test_id, req.user_id, create_job,
                test_id=test_id,
                user_id=req.user_id,
                subject=req.subject.lower(),
                medium=req.medium,
                components=components,
                question_count=question_count,
                file_reference=req.file_reference,
                file_type=canonical_type,
                test_series_name=test_series_name,
                source_test_id=req.source_test_id,
            )
    except Exception:
        raise HTTPException(500, "Could not create the job right now — please retry.")

    # ── Enqueue ───────────────────────────────────────────────────────────────
    # If this fails, the job row exists but nothing will ever pick it up — a
    # "ghost job" the client thinks is queued. Mark it Failed immediately so
    # /status reflects reality instead of hanging at Pending forever.
    try:
        _traced("sqs_send_message", test_id, req.user_id, _sqs().send_message,
                QueueUrl=SQS_GENERATOR_QUEUE_URL,
                MessageBody=build_generator_message(
                    test_id=test_id,
                    user_id=req.user_id,
                    subject=req.subject.lower(),
                    medium=req.medium,
                    components=components,
                    file_reference=req.file_reference,
                    file_type=canonical_type,
                    test_series_name=test_series_name,
                ),
            )
    except Exception as exc:
        try:
            update_job_status(test_id, JobStatus.FAILED.value, error_message=f"Enqueue failed: {exc}")
        except Exception:
            pass
        raise HTTPException(500, "Could not queue the job right now — please retry.")

    log.info("generate.queued",
             test_id=test_id,
             user_id=req.user_id,
             subject=req.subject,
             components=components,
             question_count=question_count,
             file_type=canonical_type,
             medium=req.medium)

    return {
        "test_id":    test_id,
        "status":     JobStatus.PENDING.value,
        "message":    "Your test is being generated.",
        "created_at": created_at,
    }


@router.get(
    "/status/{test_id}",
    summary="Get job status",
)
async def status(test_id: str):
    """
    **Poll job status.** Call every 2 seconds.

    Response shape by status:
    - `Pending` / `In progress` → `{test_id, status, message, created_at}`
    - `Done`             → adds `questions` list and `final_question_count`
    - `Partially Failed` → adds `questions` list, `final_question_count`, and hint `message`
    - `Failed`           → adds `message` with error description

    `questions` never includes `correct_answer` — that's stripped here on
    every call. The answer key is only ever served from GET /answers/{test_id},
    kept deliberately separate so a client rendering the test for the student
    to attempt can't accidentally receive the answer key in the same response.

    `batch_processing_detail` (batch_chunk_id, error_message, retry_count, page
    ranges, timestamps) is internal engineering telemetry and is NOT included
    here — only the summary counts (`total_batches`, `batches_completed`,
    `batches_failed`) are exposed, enough for a progress bar without leaking
    implementation detail to the client.
    """
    try:
        job = _traced("get_job", test_id, None, get_job, test_id)
    except Exception:
        raise HTTPException(500, "Could not fetch job status right now — please retry.")
    if not job:
        raise HTTPException(404, f"Test '{test_id}' not found")

    user_id = job.get("user_id")
    s = job.get("status", JobStatus.PENDING.value)
    resp = {
        "test_id":    test_id,
        "status":     s,
        "created_at": job.get("created_at"),
    }

    if s in (JobStatus.DONE.value, JobStatus.PARTIALLY_FAILED.value):
        try:
            test = _traced("get_test", test_id, user_id, get_test, test_id)
        except Exception:
            raise HTTPException(500, "Could not fetch test content right now — please retry.")
        # Never leak correct_answer here — /status renders the test itself;
        # the answer key is only ever served from GET /answers/{test_id}.
        questions = [
            {k: v for k, v in q.items() if k != "correct_answer"}
            for q in (test.get("questions", []) if test else [])
        ]
        batch_detail = job.get("batch_processing_detail", {})
        done_count   = job.get("successful_batches", len(batch_detail))
        failed_count = job.get("failed_batches", 0)
        total_batches = job.get("total_batches", len(batch_detail))

        resp["questions"]             = questions
        resp["final_question_count"]  = int(job.get("final_question_count", 0))
        resp["batches_completed"]     = done_count
        resp["total_batches"]         = total_batches
        resp["completed_at"]          = job.get("completed_at")

        if s == JobStatus.DONE.value:
            resp["message"] = None
        else:
            resp["message"] = (
                job.get("partial_message")
                or f"Partial result: {done_count}/{total_batches} chunks succeeded, "
                   f"{failed_count} failed. {int(job.get('final_question_count', 0))} questions generated."
            )

    elif s == JobStatus.FAILED.value:
        resp["message"] = (
            job.get("error_message")
            or "Abhi hum generate nahi kar paye. Please try again."
        )
        resp["completed_at"] = job.get("completed_at")

    elif s == JobStatus.IN_PROGRESS.value:
        progress = get_job_progress(test_id, user_id=user_id)
        if progress:
            total   = len(progress)
            done    = sum(1 for v in progress.values() if v.get("status") == "done")
            failed  = sum(1 for v in progress.values() if v.get("status") == "failed")
            pending = total - done - failed
            parts = []
            if done:    parts.append(f"{done} done")
            if failed:  parts.append(f"{failed} failed")
            if pending: parts.append(f"{pending} in progress")
            resp["total_batches"]     = total
            resp["batches_completed"] = done
            resp["batches_failed"]    = failed
            resp["message"] = f"Generating... ({', '.join(parts)} of {total} chunks)"
        else:
            resp["message"] = "Generating questions... please wait."

    else:  # Pending
        resp["message"] = "Your test is queued and will start soon."

    log.info("status.fetched", user_id=user_id, test_id=test_id, status=s)
    return resp


# ── Standalone app (for direct uvicorn api.generate:app) ─────────────────────

app = FastAPI(
    title="NEET Test Generator — Generator API",
    description="Submit generation jobs and poll status.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "Generator", "description": "Job submission and status"},
        {"name": "System",    "description": "Health check"},
    ],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/health", tags=["System"], summary="Health check")
def health_generate():
    return {"status": "ok", "service": "generator", "version": "1.0.0"}

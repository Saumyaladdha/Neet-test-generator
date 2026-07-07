"""
Detector API — POST /detect

Flow:
  1. Validate files (type, count, size)
  2. Generate test_id — the single ID that will later identify the job and
     the finished test too
  3. Stream SSE heartbeats while:
     a. Upload files to S3
     b. Run OpenAI detector (120s hard timeout)
     c. Compute question count ranges
     d. Save result to DynamoDB
  4. Stream complete event with structured results

Daily generation limits are enforced at /generate time (against
tests_generated_today), not here — detection itself is unmetered.

Error cases (per spec):
  File too large      → 400, not stored in DB
  Unsupported type    → 400, not stored in DB
  S3 upload failed    → error SSE event, stored (success=false)
  OpenAI failed       → error SSE event, stored (success=false)
  JSON parse failed   → error SSE event, stored (success=false)
  Timeout > 30s       → error SSE event, stored (success=false)
"""

import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.config import (
    DETECTOR_PROVIDER,
    DETECTOR_TARGET_SECONDS,
    DETECTOR_TIMEOUT_SECONDS,
    SSE_HEARTBEAT_INTERVAL,
)
from core.db_detection import create_detection_result
from core.db_users import get_user
from core.detector import detect as run_detector
from core.pdf import upload_chunk, build_file_content
from core.logger import get_logger
from core.storage import get_presigned_url, upload_bytes

log = get_logger(__name__)

# ── File validation constants ─────────────────────────────────────────────────

_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
_ALLOWED_PDF_TYPE = "application/pdf"
_MAX_IMAGE_FILES = 5
_MAX_IMAGE_MB = 5
_MAX_PDF_MB = 50
_VALID_MEDIUMS = {"english", "hindi"}


# ── Router (also used by api/main.py for single-service deployment) ───────────

router = APIRouter()


# ── Range computation ─────────────────────────────────────────────────────────

def _compute_range(count: int) -> dict:
    """
    count >= 10  →  min = count - 5,  max = count
    count < 10   →  min = max(0, count - 2),  max = count
    count == 0   →  {count: 0, min: 0, max: 0}
    """
    if count == 0:
        return {"count": 0, "min": 0, "max": 0}
    if count >= 10:
        return {"count": count, "min": count - 5, "max": count}
    return {"count": count, "min": max(0, count - 2), "max": count}


def _build_results(parsed: dict) -> dict:
    """
    Convert flat 18-field dict to nested spec shape:
    {easy: {mcq: {count, min, max}, ar: ..., mtc: ...}, medium: {...}, hard: {...}}
    """
    out = {}
    for diff in ("easy", "medium", "hard"):
        out[diff] = {}
        for qt in ("mcq", "ar", "mtc"):
            count = int(parsed.get(f"{diff}_{qt}_count", 0))
            out[diff][qt] = _compute_range(count)
    return out


# ── File validation ───────────────────────────────────────────────────────────

def _validate_files(files: List[UploadFile], file_type: str) -> None:
    """Raises HTTPException 400 on any validation failure. Does not store to DB."""
    if file_type == "image":
        if len(files) > _MAX_IMAGE_FILES:
            raise HTTPException(400, f"Max {_MAX_IMAGE_FILES} images allowed, got {len(files)}")
        for f in files:
            ct = (f.content_type or "").lower()
            if ct not in _ALLOWED_IMAGE_TYPES:
                raise HTTPException(
                    400,
                    f"Unsupported file type '{ct}' for {f.filename}. "
                    f"Allowed: {', '.join(sorted(_ALLOWED_IMAGE_TYPES))}"
                )
    elif file_type == "pdf":
        if len(files) != 1:
            raise HTTPException(400, "Exactly 1 PDF file required")
        ct = (files[0].content_type or "").lower()
        fname = (files[0].filename or "").lower()
        if ct != _ALLOWED_PDF_TYPE and not fname.endswith(".pdf"):
            raise HTTPException(400, f"Expected PDF, got content-type '{ct}'")
    else:
        raise HTTPException(400, f"Invalid file_type '{file_type}'. Must be 'image' or 'pdf'")


# ── SSE stream ────────────────────────────────────────────────────────────────

async def _detect_stream(
    files_data: List[tuple],   # [(filename: str, content_type: str, data: bytes), ...]
    user_id: str,
    subject: str,
    medium: str,
    file_type: str,
):
    """
    files_data contains pre-read bytes from the route handler.
    Sizes are already validated before this stream starts, so no
    ValueError for FILE_TOO_LARGE can occur here.
    """
    loop = asyncio.get_running_loop()
    test_id = str(uuid.uuid4())
    start_ts = time.perf_counter()

    log.info("detect.start",
             test_id=test_id, user_id=user_id,
             subject=subject, medium=medium, file_type=file_type,
             file_count=len(files_data))

    s3_paths: list = []
    media_items: list = []

    # ── Step 1: Upload to S3 ──────────────────────────────────────────────

    yield {"event": "heartbeat",
           "data": json.dumps({"status": "alive", "step": "uploading_to_s3"})}

    try:
        async def _upload_one(orig_filename: str, orig_ct: str,
                              data: bytes, idx: int) -> None:
            ext = os.path.splitext(orig_filename or "")[1] or (
                ".pdf" if file_type == "pdf" else ".png"
            )
            if file_type == "image":
                # Time-prefixed so multiple images in one detection sort
                # chronologically in a bucket browser; idx breaks ties for
                # images uploaded in the same microsecond.
                upload_time = datetime.now(timezone.utc).strftime("%H%M%S%f")
                filename = f"images/{upload_time}_image_{idx}{ext}"
                ct = orig_ct or "image/jpeg"
            else:
                filename = f"pdfs/file{ext}"
                ct = "application/pdf"

            uri = await loop.run_in_executor(
                None,
                lambda d=data, uid=user_id, tid=test_id, fn=filename, c=ct:
                    upload_bytes(d, uid, tid, fn, content_type=c),
            )
            s3_paths.append(uri)

            url = await loop.run_in_executor(
                None, lambda u=uri: get_presigned_url(u, expires=3600),
            )

            if file_type == "image":
                media_items.append({"url": url, "mime_type": ct})

            elif DETECTOR_PROVIDER == "gemini":
                media_items.append({"url": url, "mime_type": "application/pdf"})
                log.info("detect.pdf_via_url", user_id=user_id, test_id=test_id)

            else:
                # OpenAI path: upload PDF to OpenAI Files API
                import tempfile, os as _os
                with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                    tmp.write(data)
                    tmp_path = tmp.name
                try:
                    from openai import OpenAI as _OpenAI
                    client = _OpenAI()
                    file_id = await loop.run_in_executor(
                        None,
                        lambda p=tmp_path: upload_chunk(client, p),
                    )
                    media_items.append({
                        "url": url,
                        "mime_type": "application/pdf",
                        "file_id": file_id,
                    })
                    log.info("detect.pdf_uploaded_to_openai",
                             user_id=user_id, test_id=test_id, file_id=file_id)
                finally:
                    _os.unlink(tmp_path)

        await asyncio.gather(*[
            _upload_one(fname, ct, data, i)
            for i, (fname, ct, data) in enumerate(files_data)
        ])
        upload_elapsed = round(time.perf_counter() - start_ts, 2)
        log.info("detect.uploaded",
                 user_id=user_id, test_id=test_id, file_count=len(s3_paths),
                 provider=DETECTOR_PROVIDER, elapsed_seconds=upload_elapsed)

    except ValueError as exc:
        # File too large — 400 per spec, do NOT save to DB
        log.warning("detect.file_too_large", user_id=user_id, test_id=test_id, error=str(exc))
        yield {"event": "error",
               "data": json.dumps({"error": str(exc), "code": "FILE_TOO_LARGE"})}
        return

    except Exception as exc:
        err = f"S3 upload failed: {exc}"
        log.error("detect.s3_error", user_id=user_id, test_id=test_id, error=str(exc))
        yield {"event": "error", "data": json.dumps({"error": err, "code": "S3_ERROR"})}
        await loop.run_in_executor(None, lambda: create_detection_result(
            test_id=test_id, user_id=user_id, subject=subject, medium=medium,
            file_type=file_type, s3_paths=s3_paths, results={},
            success=False, error_message=err,
        ))
        return

    # ── Step 2: Run Detector ──────────────────────────────────────────────

    yield {"event": "heartbeat",
           "data": json.dumps({"status": "alive", "step": "running_detector"})}

    detector_start = time.perf_counter()

    try:
        future = loop.run_in_executor(
            None,
            lambda: run_detector(media_items=media_items),
        )

        while not future.done():
            await asyncio.sleep(SSE_HEARTBEAT_INTERVAL)
            elapsed_total = time.perf_counter() - start_ts
            if elapsed_total > DETECTOR_TIMEOUT_SECONDS:
                future.cancel()
                err = f"Detection timed out after {DETECTOR_TIMEOUT_SECONDS}s"
                log.error("detect.timeout", user_id=user_id, test_id=test_id,
                          elapsed_seconds=round(elapsed_total, 1),
                          timeout_limit_seconds=DETECTOR_TIMEOUT_SECONDS)
                yield {"event": "error",
                       "data": json.dumps({"error": err, "code": "TIMEOUT"})}
                await loop.run_in_executor(None, lambda: create_detection_result(
                    test_id=test_id, user_id=user_id, subject=subject, medium=medium,
                    file_type=file_type, s3_paths=s3_paths, results={},
                    success=False, error_message=err,
                ))
                return
            if not future.done():
                yield {"event": "heartbeat",
                       "data": json.dumps({"status": "alive", "step": "running_detector"})}

        _raw_response, parsed = await future
        detector_elapsed = round(time.perf_counter() - detector_start, 2)
        log.info("detect.detector_done",
                 user_id=user_id, test_id=test_id, provider=DETECTOR_PROVIDER,
                 elapsed_seconds=detector_elapsed)

    except Exception as exc:
        err = f"Detector API failed ({DETECTOR_PROVIDER}): {exc}"
        log.error("detect.detector_error", user_id=user_id, test_id=test_id,
                  provider=DETECTOR_PROVIDER, error=str(exc))
        yield {"event": "error", "data": json.dumps({"error": err, "code": "DETECTOR_ERROR"})}
        await loop.run_in_executor(None, lambda: create_detection_result(
            test_id=test_id, user_id=user_id, subject=subject, medium=medium,
            file_type=file_type, s3_paths=s3_paths, results={},
            success=False, error_message=err,
        ))
        return

    # ── Step 3: Parse + Compute Ranges ───────────────────────────────────

    yield {"event": "heartbeat",
           "data": json.dumps({"status": "alive", "step": "computing_ranges"})}
    if "error" in parsed:
        err = f"JSON parse failed: {parsed['error']}"
        log.error("detect.parse_error", user_id=user_id, test_id=test_id, error=err)
        yield {"event": "error", "data": json.dumps({"error": err, "code": "PARSE_ERROR"})}
        await loop.run_in_executor(None, lambda: create_detection_result(
            test_id=test_id, user_id=user_id, subject=subject, medium=medium,
            file_type=file_type, s3_paths=s3_paths, results={},
            success=False, error_message=err,
        ))
        return

    results = _build_results(parsed)

    # ── Step 4: Save to DynamoDB ──────────────────────────────────────────

    try:
        saved_id = await loop.run_in_executor(
            None,
            lambda: create_detection_result(
                test_id=test_id, user_id=user_id, subject=subject, medium=medium,
                file_type=file_type, s3_paths=s3_paths, results=results, success=True,
            ),
        )
    except Exception as exc:
        # DB write failure doesn't kill the response — user still gets result
        log.error("detect.dynamo_error", user_id=user_id, test_id=test_id, error=str(exc))
        saved_id = test_id

    total_elapsed = round(time.perf_counter() - start_ts, 2)
    total_questions_detected = sum(
        cell.get("count", 0)
        for by_type in results.values()
        for cell in by_type.values()
    )
    log.info("detect.complete",
             user_id=user_id, test_id=saved_id,
             subject=subject, medium=medium, file_type=file_type,
             total_questions_detected=total_questions_detected,
             elapsed_seconds=total_elapsed,
             target_seconds=DETECTOR_TARGET_SECONDS,
             slower_than_target=total_elapsed > DETECTOR_TARGET_SECONDS)

    yield {
        "event": "complete",
        "data": json.dumps({
            "test_id": saved_id,
            "subject": subject,
            "medium": medium,
            "file_type": file_type,
            "elapsed_seconds": total_elapsed,
            "results": results,
            "s3_paths": s3_paths,
        }),
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post(
    "/detect",
    summary="Analyse content and return question count ranges",
    response_description=(
        "SSE stream. Events:\n"
        "  heartbeat — {status: 'alive', step: 'uploading_to_s3'|'running_detector'|'computing_ranges'}\n"
        "  complete  — {test_id, subject, medium, file_type, elapsed_seconds, results}\n"
        "  error     — {error: str, code: 'FILE_TOO_LARGE'|'S3_ERROR'|'OPENAI_ERROR'|'PARSE_ERROR'|'TIMEOUT'}"
    ),
    openapi_extra={
        "requestBody": {
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["files", "user_id", "subject"],
                        "properties": {
                            "files": {
                                "type": "array",
                                "items": {"type": "string", "format": "binary"},
                                "description": "1–5 image files (JPEG/PNG/WEBP) OR exactly 1 PDF",
                            },
                            "user_id": {
                                "type": "string",
                                "description": "User ID from NeetTestGenerator_User table",
                            },
                            "subject": {
                                "type": "string",
                                "description": "Subject: biology / chemistry",
                                "enum": ["biology", "chemistry"],
                            },
                            "medium": {
                                "type": "string",
                                "default": "english",
                                "description": "Language medium: english / hindi",
                                "enum": ["english", "hindi"],
                            },
                            "file_type": {
                                "type": "string",
                                "default": "image",
                                "description": "Type of uploaded files: image / pdf",
                                "enum": ["image", "pdf"],
                            },
                        },
                    }
                }
            },
            "required": True,
        }
    },
)
async def detect(
    files: List[UploadFile] = File(
        ...,
        description="1–5 image files (JPEG/PNG/WEBP) OR exactly 1 PDF",
    ),
    user_id: str = Form(..., description="User ID from NeetTestGenerator_User table"),
    subject: str = Form(..., description="Subject: biology / chemistry"),
    medium: str = Form(
        ...,
        description="Language medium: english / hindi (required — routes Hindi vs English prompt flow)",
    ),
    file_type: str = Form(
        default="image",
        description="Type of uploaded files: image / pdf",
    ),
):
    """
    **Upload study material and get question count ranges via SSE.**

    Send a multipart/form-data POST with your files and metadata.
    The response is a text/event-stream that delivers:

    1. `heartbeat` events every 2 seconds showing the current step
    2. A single `complete` event with the full results when done
    3. An `error` event if anything fails

    **Result shape (complete event):**
    ```json
    {
      "test_id": "abc-123",
      "subject": "biology",
      "medium": "english",
      "file_type": "image",
      "elapsed_seconds": 9.3,
      "results": {
        "easy":   {"mcq": {"count": 8, "min": 6, "max": 8}, "ar": {...}, "mtc": {...}},
        "medium": {"mcq": {...}, "ar": {...}, "mtc": {...}},
        "hard":   {"mcq": {...}, "ar": {...}, "mtc": {...}}
      }
    }
    ```

    `test_id` is the ID to pass to `/generate` — it identifies this
    detection, the resulting job, and (once generation finishes) the
    finished test, all under the same ID.
    """
    if not user_id or not user_id.strip():
        raise HTTPException(400, "user_id cannot be empty")

    if not get_user(user_id):
        raise HTTPException(400, f"User '{user_id}' not found")

    if not files:
        raise HTTPException(400, "At least one file is required")

    if medium.lower() not in _VALID_MEDIUMS:
        raise HTTPException(400, f"Invalid medium '{medium}'. Allowed: {sorted(_VALID_MEDIUMS)}")

    _validate_files(files, file_type)

    max_mb = _MAX_IMAGE_MB if file_type == "image" else _MAX_PDF_MB
    files_data: List[tuple] = []
    for f in files:
        data = await f.read()
        size_mb = len(data) / (1024 * 1024)
        if size_mb > max_mb:
            raise HTTPException(
                400,
                f"File '{f.filename}' is {size_mb:.1f}MB; max allowed is {max_mb}MB"
            )
        files_data.append((f.filename or "", f.content_type or "", data))

    return EventSourceResponse(
        _detect_stream(files_data, user_id, subject, medium, file_type)
    )


# ── Standalone app (for direct uvicorn api.detect:app) ────────────────────────

app = FastAPI(
    title="NEET Test Generator — Detector API",
    description=(
        "Analyses uploaded study material (images or PDF) and returns "
        "how many questions of each difficulty and type can be generated. "
        "Responses are streamed as Server-Sent Events (SSE).\n\n"
        "**SSE Event types:**\n"
        "- `heartbeat` — fires every 2s with `{status, step}`\n"
        "- `complete` — final result with test_id and results\n"
        "- `error` — on failure with error message and code\n\n"
        "**Steps:** `uploading_to_s3` → `running_detector` → `computing_ranges`"
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "Detector", "description": "Content analysis endpoints"},
        {"name": "System",   "description": "Health and status"},
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
def health_detect():
    """Returns 200 when the service is up."""
    return {"status": "ok", "service": "detector", "version": "1.0.0"}

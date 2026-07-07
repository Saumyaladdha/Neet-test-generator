"""
NEET Test Generator — Combined API Service

Endpoints:
  POST /detect              — analyse content, stream SSE question count ranges
  POST /generate            — submit generation job (202 Accepted)
  GET  /status/{test_id}    — poll job status
  GET  /answers/{test_id}   — fetch correct answers (async, after generation)
  GET  /health              — shallow liveness (always fast)
  GET  /health/deep         — full dependency probe (S3 + DynamoDB + SQS)
  GET  /docs                — Swagger UI
  GET  /redoc               — ReDoc UI

Run locally:
  uvicorn api.main:app --reload --port 8000

Run in production:
  uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2
"""

import json
import os
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone

import boto3
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from api.answers import router as answers_router
from api.detect import router as detect_router
from api.generate import router as generate_router
from core.config import (
    API_KEYS, CORS_ORIGINS,
    AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
    S3_BUCKET, DYNAMO_JOBS_TABLE, SQS_GENERATOR_QUEUE_URL,
)
from core.logger import get_logger

log = get_logger(__name__)

# ── Request ID middleware ─────────────────────────────────────────────────────
# Minted before auth/validation even run, so every response — success, 401,
# 422, or 500 — carries a trace handle independent of user_id/test_id (which
# may themselves be the thing that's missing/wrong). Echoed as X-Request-Id
# and folded into every error body and log line for that request.

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid.uuid4())


# ── Auth middleware ───────────────────────────────────────────────────────────

_NO_AUTH_PATHS = {"/health", "/health/deep", "/docs", "/redoc", "/openapi.json"}

class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not API_KEYS:
            return await call_next(request)
        if request.url.path in _NO_AUTH_PATHS:
            return await call_next(request)
        key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
        if key not in API_KEYS:
            return JSONResponse(status_code=401, content=_error_body(
                request, 401, "Invalid or missing API key.", error_code="UNAUTHORIZED",
            ))
        return await call_next(request)

# ── Unified error envelope ────────────────────────────────────────────────────
# Every error response — validation, HTTPException, or an unhandled crash —
# shares this exact shape, so a client can rely on error_code/detail/request_id
# always being present regardless of which failure path produced them.

_ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


def _error_code_for(status_code: int) -> str:
    return _ERROR_CODES.get(status_code, "ERROR")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error_body(request: Request, status_code: int, detail: str, error_code: str = None,
                 errors: list = None, received: dict = None) -> dict:
    body = {
        "error_code": error_code or _error_code_for(status_code),
        "detail": detail,
        "request_id": _request_id(request),
        "timestamp": _now_iso(),
    }
    if errors is not None:
        body["errors"] = errors
    if received is not None:
        body["received"] = received
    return body


def _json_safe(value):
    """Best-effort JSON-serialisable copy — non-serialisable values (e.g. an
    UploadFile from a multipart field) become a readable placeholder instead
    of breaking the error response."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return f"<{type(value).__name__}>"


def _field_name(loc: tuple) -> str:
    # loc looks like ("body", "user_id") or ("body", "components", 0, "question_type")
    return ".".join(str(p) for p in loc if p not in ("body", "query", "path"))


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="NEET Test Generator API",
    description=(
        "Two APIs in one service:\n\n"
        "**Detector** — Analyse study material and get question count ranges (SSE stream)\n\n"
        "**Generator** — Submit generation jobs and poll status\n\n"
        "---\n"
        "**Auth:** Pass `X-API-Key` header on all requests (except /health).\n\n"
        "**SSE event types (POST /detect):**\n"
        "- `heartbeat` — `{status: 'alive', step: '...'}`\n"
        "- `complete`  — `{test_id, results, elapsed_seconds, ...}`\n"
        "- `error`     — `{error, code}`\n\n"
        "**Generator status lifecycle:**\n"
        "`Pending` → `In progress` → `Done` | `Partially Failed` | `Failed`"
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "Detector",  "description": "Content analysis — returns question count ranges via SSE"},
        {"name": "Generator", "description": "Question generation — async job queue"},
        {"name": "Answers",   "description": "Correct answers — served after async answer generation"},
        {"name": "System",    "description": "Health and liveness"},
    ],
)

app.add_middleware(APIKeyMiddleware)

# Added last so it's outermost — request_id is set before CORS, auth, and
# routing all run, guaranteeing every response (including 401s from
# APIKeyMiddleware) has one.
app.add_middleware(RequestIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Replaces FastAPI's default nested {loc, msg, input} error shape with a
    single readable sentence naming exactly which field(s) the caller failed
    to send, plus the full per-field breakdown and a safe echo of what was
    actually received — so a UI bug ("we forgot to send medium") is visible
    in one line instead of requiring the caller to parse `loc` arrays.
    """
    field_errors = []
    fields = []
    for e in exc.errors():
        field = _field_name(tuple(e.get("loc", ()))) or "body"
        fields.append(field)
        field_errors.append({"field": field, "issue": e.get("msg", ""), "type": e.get("type", "")})

    received = exc.body if isinstance(exc.body, dict) else None
    if received is not None:
        received = {k: _json_safe(v) for k, v in received.items()}

    body = _error_body(
        request, 422,
        detail=f"Missing or invalid field(s): {', '.join(fields) or 'unknown'}",
        error_code="VALIDATION_ERROR",
        errors=field_errors,
        received=received,
    )
    log.warning("request.validation_failed",
                request_id=body["request_id"], path=request.url.path, fields=fields, received=received)
    return JSONResponse(status_code=422, content=body)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """
    Catches every HTTPException raised anywhere in the app (400s, 404s, 429s,
    500s we raise ourselves) and wraps it in the same envelope as validation
    errors, instead of leaving FastAPI's bare {"detail": "..."} default —
    two different error shapes in one API is exactly what breaks client-side
    error handling in prod.
    """
    body = _error_body(request, exc.status_code, detail=exc.detail)
    log.warning("request.http_exception",
                request_id=body["request_id"], path=request.url.path,
                status_code=exc.status_code, detail=exc.detail)
    return JSONResponse(status_code=exc.status_code, content=body, headers=getattr(exc, "headers", None))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """
    Last-resort catch-all for anything that isn't an HTTPException — without
    this, an unexpected bug raises a raw traceback (or a bare 500 with no
    body) straight to the client. Logs the real error server-side with full
    request_id context; the client only ever sees a generic safe message.
    """
    body = _error_body(request, 500, detail="Something went wrong. Please try again.",
                        error_code="INTERNAL_ERROR")
    log.error("request.unhandled_exception",
              request_id=body["request_id"], path=request.url.path,
              error=str(exc), traceback=traceback.format_exc())
    return JSONResponse(status_code=500, content=body)


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(detect_router)
app.include_router(generate_router)
app.include_router(answers_router)

# ── Health endpoints ──────────────────────────────────────────────────────────

@app.get("/health", tags=["System"], summary="Shallow liveness check")
def health():
    return {"status": "ok", "service": "neet-test-generator", "version": "1.0.0"}


@app.get("/health/deep", tags=["System"], summary="Full dependency probe")
def health_deep():
    results = {}
    ok = True

    # DynamoDB
    try:
        t = time.perf_counter()
        boto3.resource(
            "dynamodb",
            region_name=AWS_REGION,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        ).Table(DYNAMO_JOBS_TABLE).load()
        results["dynamodb"] = {"ok": True, "ms": round((time.perf_counter() - t) * 1000)}
    except Exception as e:
        results["dynamodb"] = {"ok": False, "error": str(e)[:120]}
        ok = False

    # S3
    try:
        t = time.perf_counter()
        boto3.client(
            "s3",
            region_name=AWS_REGION,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        ).head_bucket(Bucket=S3_BUCKET)
        results["s3"] = {"ok": True, "ms": round((time.perf_counter() - t) * 1000)}
    except Exception as e:
        results["s3"] = {"ok": False, "error": str(e)[:120]}
        ok = False

    # SQS
    try:
        t = time.perf_counter()
        boto3.client(
            "sqs",
            region_name=AWS_REGION,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        ).get_queue_attributes(
            QueueUrl=SQS_GENERATOR_QUEUE_URL,
            AttributeNames=["ApproximateNumberOfMessages"],
        )
        results["sqs"] = {"ok": True, "ms": round((time.perf_counter() - t) * 1000)}
    except Exception as e:
        results["sqs"] = {"ok": False, "error": str(e)[:120]}
        ok = False

    status_code = 200 if ok else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ok" if ok else "degraded", "checks": results},
    )

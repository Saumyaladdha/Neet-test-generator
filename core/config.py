"""
Central configuration — all env vars live here.
Every other module imports from this file. Never call os.getenv() elsewhere.
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Required env var '{key}' is not set. Add it to your .env file.")
    return val


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# ── OpenAI ────────────────────────────────────────────────────────────────────
OPENAI_API_KEY: str = _require("OPENAI_API_KEY")

# Additional keys for rotation on rate-limit/auth failure — OPENAI_API_KEY is
# always key index 0. Unset OPENAI_API_KEY_2/_3 are simply skipped.
OPENAI_API_KEYS: list = [
    k for k in (OPENAI_API_KEY, _optional("OPENAI_API_KEY_2"), _optional("OPENAI_API_KEY_3"))
    if k
]

# Model used for generator (topic detection, question generation)
OPENAI_MODEL: str = _optional("OPENAI_MODEL", "gpt-5.4-mini")

# ── Gemini ────────────────────────────────────────────────────────────────────
GEMINI_API_KEY: str = _optional("GEMINI_API_KEY")

# Model used for Gemini detector path
GEMINI_MODEL: str = _optional("GEMINI_MODEL", "gemini-2.5-flash")

# ── Detector provider ─────────────────────────────────────────────────────────
# "gemini"  → Gemini 2.5 Flash, PDF via S3 presigned URL directly (faster)
# "openai"  → OpenAI gpt-5.4-mini, PDF uploaded to OpenAI Files API
DETECTOR_PROVIDER: str = _optional("DETECTOR_PROVIDER", "gemini")

# Max output tokens per API call
MAX_OUTPUT_TOKENS: int = int(_optional("MAX_OUTPUT_TOKENS", "10000"))

# ── AWS ───────────────────────────────────────────────────────────────────────
AWS_REGION: str = _optional("AWS_REGION", "us-east-1")   # single default for all services
AWS_ACCESS_KEY_ID: str = _optional("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY: str = _optional("AWS_SECRET_ACCESS_KEY")

# ── S3 ────────────────────────────────────────────────────────────────────────
# Bucket physically lives in ap-south-1.
# Uploads use AWS_REGION (us-east-1) — boto3 auto-redirects for PUT/GET.
# Presigned URLs MUST use the bucket's actual region (S3_BUCKET_REGION) because
# the region is baked into the HMAC signature.
S3_BUCKET: str = _optional("S3_BUCKET", "mldatabase")
S3_PREFIX: str = _optional("S3_PREFIX", "neetTestGenerator")
S3_BUCKET_REGION: str = _optional("S3_BUCKET_REGION", "ap-south-1")

# ── DynamoDB ──────────────────────────────────────────────────────────────────
# Tables are in us-east-1 — inherits AWS_REGION default above.

# ── SQS ───────────────────────────────────────────────────────────────────────
SQS_GENERATOR_QUEUE_URL: str = _optional("SQS_GENERATOR_QUEUE_URL")
SQS_ANSWERS_QUEUE_URL: str = _optional("SQS_ANSWERS_QUEUE_URL")
SQS_DLQ_URL: str = _optional("SQS_DLQ_URL")

# How long SQS hides a message while it is being processed (seconds)
SQS_VISIBILITY_TIMEOUT: int = int(_optional("SQS_VISIBILITY_TIMEOUT", "300"))

# Long-poll wait time for SQS receives (seconds, max 20)
SQS_WAIT_TIME: int = int(_optional("SQS_WAIT_TIME", "20"))

# ── DynamoDB ──────────────────────────────────────────────────────────────────
DYNAMO_USERS_TABLE: str = _optional("DYNAMO_USERS_TABLE", "NeetTestGenerator_User")
DYNAMO_JOBS_TABLE: str = _optional("DYNAMO_JOBS_TABLE", "NeetTestGenerator_Jobs")
DYNAMO_DETECTOR_TABLE: str = _optional("DYNAMO_DETECTOR_TABLE", "NeetTestGenerator_Detector")
DYNAMO_TEST_TABLE: str = _optional("DYNAMO_TEST_TABLE", "NeetTestGenerator_Test")

# Timezone used for the daily-usage-counter reset boundary (NEET is an India product).
DAILY_RESET_TIMEZONE: str = "Asia/Kolkata"

# Daily generation limits per plan tier. Plan is passed in per-request from the UI —
# never stored in DynamoDB. Update these two numbers as pricing changes.
PLAN_DAILY_LIMITS: dict = {
    "free": int(_optional("FREE_PLAN_DAILY_LIMIT", "5")),
    "pro":  int(_optional("PRO_PLAN_DAILY_LIMIT", "50")),
}

# ── Redis ─────────────────────────────────────────────────────────────────────
# Local dev: plain standalone Redis via REDIS_HOST/REDIS_PORT (each dev/service
# runs its own, no auth). Staging/prod: set REDIS_SENTINEL_HOSTS to point at
# the centralized Sentinel deployment instead — REDIS_HOST/PORT are then
# ignored and REDIS_PASSWORD becomes the Sentinel-protected master's own
# (separate, prod-only) credential.
REDIS_HOST: str = _optional("REDIS_HOST", "localhost")
REDIS_PORT: int = int(_optional("REDIS_PORT", "6379"))
REDIS_DB: int = int(_optional("REDIS_DB", "0"))
REDIS_PASSWORD: str = _optional("REDIS_PASSWORD")

# Comma-separated "host:port" pairs, e.g. "sentinel1:26379,sentinel2:26379,sentinel3:26379".
# Empty = standalone mode (REDIS_HOST/REDIS_PORT above).
def _parse_sentinel_hosts(raw: str) -> list:
    out = []
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        host, _, port = pair.partition(":")
        out.append((host, int(port) if port else 26379))
    return out


REDIS_SENTINEL_HOSTS: list = _parse_sentinel_hosts(_optional("REDIS_SENTINEL_HOSTS", ""))
REDIS_SENTINEL_MASTER_NAME: str = _optional("REDIS_SENTINEL_MASTER_NAME", "mymaster")

# TTL for job state keys in Redis (seconds)
REDIS_JOB_TTL: int = int(_optional("REDIS_JOB_TTL", "3600"))

# ── ClickHouse ────────────────────────────────────────────────────────────────
CLICKHOUSE_HOST: str = _optional("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_PORT: int = int(_optional("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_DB: str = _optional("CLICKHOUSE_DB", "neet_analytics")
CLICKHOUSE_USER: str = _optional("CLICKHOUSE_USER", "default")
CLICKHOUSE_PASSWORD: str = _optional("CLICKHOUSE_PASSWORD")

# ── Generator worker ──────────────────────────────────────────────────────────
# Number of questions per batch sent to the model
BATCH_SIZE: int = int(_optional("BATCH_SIZE", "3"))

# Max retries per batch before marking it failed
BATCH_MAX_RETRIES: int = int(_optional("BATCH_MAX_RETRIES", "3"))

# Backoff delays in seconds between batch retries
BATCH_RETRY_DELAYS: list = [2, 4]

# PDF page threshold — above this, split into chunks
PDF_CHUNK_THRESHOLD: int = int(_optional("PDF_CHUNK_THRESHOLD", "20"))

# Pages per chunk when splitting large PDFs
PDF_CHUNK_SIZE: int = int(_optional("PDF_CHUNK_SIZE", "10"))

# ── Detector API ──────────────────────────────────────────────────────────────
# Target response time in seconds — used for logging/alerting only
DETECTOR_TARGET_SECONDS: int = int(_optional("DETECTOR_TARGET_SECONDS", "8"))

# Hard timeout: return 504 if detection takes longer than this
# Images typically take 10-25s; PDFs (upload to OpenAI + analysis) take 30-60s
DETECTOR_TIMEOUT_SECONDS: int = int(_optional("DETECTOR_TIMEOUT_SECONDS", "120"))

# SSE heartbeat interval in seconds
SSE_HEARTBEAT_INTERVAL: int = int(_optional("SSE_HEARTBEAT_INTERVAL", "2"))

# ── Logging ───────────────────────────────────────────────────────────────────
# Level: DEBUG / INFO / WARNING / ERROR
LOG_LEVEL: str = _optional("LOG_LEVEL", "INFO")

# Format: "pretty" for human-readable dev terminal, "json" for production
LOG_FORMAT: str = _optional("LOG_FORMAT", "pretty")

# ── Auth ──────────────────────────────────────────────────────────────────────
# Comma-separated valid API keys. Empty = auth disabled (local dev only).
API_KEYS: list = [k.strip() for k in _optional("API_KEYS", "").split(",") if k.strip()]

# ── CORS ──────────────────────────────────────────────────────────────────────
# Comma-separated allowed origins. "*" = allow all (local dev only).
CORS_ORIGINS: list = [o.strip() for o in _optional("CORS_ORIGINS", "*").split(",") if o.strip()]

# ── Ops alerting (SES) ──────────────────────────────────────────────────────────
# Fires when OpenAI generation exhausts every rotated key. Empty
# SES_ALERT_EMAIL_TO disables sending (graceful no-op, same pattern as Redis).
SES_ALERT_EMAIL_FROM: str = _optional("SES_ALERT_EMAIL_FROM")
SES_ALERT_EMAIL_TO: list = [e.strip() for e in _optional("SES_ALERT_EMAIL_TO", "").split(",") if e.strip()]
SES_REGION: str = _optional("SES_REGION", "us-east-1")

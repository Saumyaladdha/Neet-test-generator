"""
User generation-usage operations — NeetTestGenerator_User table.

Responsibilities:
  - User lookup and creation
  - Daily reset of usage counters (lazy — no cron, no stored last_reset_date)
  - Daily quota enforcement (tests_generated_today vs plan-tier limit)
  - Post-generation usage recording

Plan tier is passed in per-request from the UI on every call — it is never
stored here. Daily limits per tier are hardcoded in core.config.PLAN_DAILY_LIMITS.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from core.config import DYNAMO_USERS_TABLE, DAILY_RESET_TIMEZONE, PLAN_DAILY_LIMITS
from core.db_base import dynamo, now_iso

_TZ = ZoneInfo(DAILY_RESET_TIMEZONE)

_TODAY_FIELDS = [
    "questions_generated_today",
    "tests_generated_today",
    "tests_generated_from_images_today",
    "tests_generated_from_pdf_today",
]


def _today_str() -> str:
    """Today's date in the configured reset timezone (IST), as YYYY-MM-DD."""
    return datetime.now(_TZ).date().isoformat()


def _date_of(iso_ts: str) -> str:
    """Date portion (in the reset timezone) of an ISO timestamp string."""
    return datetime.fromisoformat(iso_ts).astimezone(_TZ).date().isoformat()


def get_user(user_id: str) -> dict | None:
    table = dynamo().Table(DYNAMO_USERS_TABLE)
    resp = table.get_item(Key={"user_id": user_id})
    return resp.get("Item")


def create_user(user_id: str) -> dict:
    table = dynamo().Table(DYNAMO_USERS_TABLE)
    now = now_iso()
    item = {
        "user_id":                            user_id,
        "questions_generated_total":          0,
        "tests_generated_total":              0,
        "tests_generated_from_images_total":  0,
        "tests_generated_from_pdf_total":     0,
        "questions_generated_today":          0,
        "tests_generated_today":              0,
        "tests_generated_from_images_today":  0,
        "tests_generated_from_pdf_today":     0,
        "created_at":                         now,
        "updated_at":                         now,
    }
    table.put_item(Item=item, ConditionExpression="attribute_not_exists(user_id)")
    return item


def _lazy_reset(table, user_id: str, item: dict) -> dict:
    """Zero every *_today field if the item's last update fell on an earlier IST date."""
    if _date_of(item["updated_at"]) == _today_str():
        return item

    table.update_item(
        Key={"user_id": user_id},
        UpdateExpression="SET " + ", ".join(f"{f} = :zero" for f in _TODAY_FIELDS) +
                          ", updated_at = :ts",
        ExpressionAttributeValues={":zero": 0, ":ts": now_iso()},
    )
    reset_fields = {f: 0 for f in _TODAY_FIELDS}
    return dict(item, **reset_fields, updated_at=now_iso())


def check_and_reserve_quota(user_id: str, plan: str) -> dict:
    """
    Verify the user hasn't hit their plan's daily test limit yet.
    Does NOT increment — worker records usage after successful completion.
    Raises LookupError if user not found.
    Raises ValueError if the plan is unknown or the daily limit is already reached.
    Returns the (possibly lazy-reset) user item.
    """
    table = dynamo().Table(DYNAMO_USERS_TABLE)
    item = get_user(user_id)
    if not item:
        raise LookupError(f"User '{user_id}' not found")

    item = _lazy_reset(table, user_id, item)

    limit = PLAN_DAILY_LIMITS.get(plan)
    if limit is None:
        raise ValueError(f"Unknown plan '{plan}'. Allowed: {sorted(PLAN_DAILY_LIMITS)}")

    used = int(item.get("tests_generated_today", 0))
    if used >= limit:
        raise ValueError(f"Daily limit reached ({limit} tests/day on '{plan}' plan)")
    return item


def record_test_generated(user_id: str, question_count: int, source: str) -> None:
    """
    Atomically record a completed generation: bumps questions_generated_*,
    tests_generated_*, and tests_generated_from_{source}_* (total + today).
    source: "image" | "pdf"
    """
    if source not in ("image", "pdf"):
        raise ValueError(f"Invalid source '{source}'. Must be 'image' or 'pdf'.")

    table = dynamo().Table(DYNAMO_USERS_TABLE)
    item = get_user(user_id)
    if not item:
        raise LookupError(f"User '{user_id}' not found")
    _lazy_reset(table, user_id, item)

    source_field = "tests_generated_from_images" if source == "image" else "tests_generated_from_pdf"

    table.update_item(
        Key={"user_id": user_id},
        UpdateExpression=(
            "ADD questions_generated_total :q, questions_generated_today :q, "
            "tests_generated_total :one, tests_generated_today :one, "
            f"{source_field}_total :one, {source_field}_today :one "
            "SET updated_at = :ts"
        ),
        ExpressionAttributeValues={
            ":q":   question_count,
            ":one": 1,
            ":ts":  now_iso(),
        },
    )

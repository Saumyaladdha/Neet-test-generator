"""
Redis client — Sentinel-aware, live chunk-level job progress.

Key schema:
  test:{test_id}  →  Hash
    chunk_0  →  JSON {"trace_id", "topic", "status", "generated", "started_at", "completed_at", "error"}
    chunk_1  →  ...

Status values per chunk: "pending" | "processing" | "done" | "failed"

Connection modes:
  - Sentinel (REDIS_SENTINEL_HOSTS set) — the centralized deployment used on
    staging/prod. Several Redis nodes run in parallel (one master, the rest
    replicas); Sentinel tracks which is current master and this client always
    resolves against that pointer. If the master dies, Sentinel promotes a
    replica and the next _redis() call transparently picks up the new master
    — no manual intervention, no separate Redis per service.
  - Standalone (default) — a single REDIS_HOST/REDIS_PORT, for local dev
    where each developer/service just runs its own local Redis container.

All functions are no-ops if Redis is unavailable (graceful degradation), but
a failed connection is retried after a short cooldown rather than being
marked unavailable for the lifetime of the process — so a Sentinel failover,
or Redis simply coming back up, is picked up without a worker restart.
"""

import json
import time

from core.config import (
    REDIS_HOST, REDIS_PORT, REDIS_DB, REDIS_PASSWORD, REDIS_JOB_TTL,
    REDIS_SENTINEL_HOSTS, REDIS_SENTINEL_MASTER_NAME,
)
from core.logger import get_logger

_log = get_logger(__name__)

_client = None
_last_failure_ts = 0.0
_RECONNECT_COOLDOWN_SECONDS = 30


def _connect():
    import redis  # lazy import — redis may not be installed in all envs

    if REDIS_SENTINEL_HOSTS:
        from redis.sentinel import Sentinel
        sentinel = Sentinel(
            REDIS_SENTINEL_HOSTS,
            socket_timeout=2,
            socket_connect_timeout=2,
        )
        r = sentinel.master_for(
            REDIS_SENTINEL_MASTER_NAME,
            db=REDIS_DB,
            password=REDIS_PASSWORD or None,
            socket_timeout=2,
            decode_responses=True,
        )
    else:
        r = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            password=REDIS_PASSWORD or None,
            socket_timeout=2,
            socket_connect_timeout=2,
            decode_responses=True,
        )
    r.ping()
    return r


def _redis():
    global _client, _last_failure_ts
    if _client is not None:
        return _client

    now = time.time()
    if now - _last_failure_ts < _RECONNECT_COOLDOWN_SECONDS:
        return None

    try:
        _client = _connect()
        if REDIS_SENTINEL_HOSTS:
            _log.info("redis.connected", mode="sentinel",
                      master=REDIS_SENTINEL_MASTER_NAME, sentinels=REDIS_SENTINEL_HOSTS, db=REDIS_DB)
        else:
            _log.info("redis.connected", mode="standalone",
                      host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB)
        return _client
    except Exception as exc:
        _last_failure_ts = now
        _client = None
        _log.warning("redis.unavailable", error=str(exc), retry_in_s=_RECONNECT_COOLDOWN_SECONDS)
        return None


def _invalidate(operation: str, test_id: str = None, user_id: str = None, error: str = None) -> None:
    """
    Drop the cached client so the next call reconnects from scratch — a
    mid-operation failure can mean the connection died or (under Sentinel)
    that the master changed; either way the next _redis() call re-resolves
    it instead of reusing a stale/dead connection for RECONNECT_COOLDOWN.
    """
    global _client
    _client = None
    _log.warning("redis.operation_failed",
                 user_id=user_id, test_id=test_id, operation=operation, error=error)


# ── Public API ────────────────────────────────────────────────────────────────

def init_job_progress(test_id: str, chunks: list, user_id: str = None) -> None:
    """
    Initialise Redis state for a test before processing starts.
    chunks = [{"topic": "...", "trace_id": "..."(optional)}, ...]
    Sets chunk_0 … chunk_N with status=pending, TTL=REDIS_JOB_TTL.
    """
    r = _redis()
    if r is None:
        return
    key = f"test:{test_id}"
    mapping = {}
    for i, c in enumerate(chunks):
        mapping[f"chunk_{i}"] = json.dumps({
            "trace_id": c.get("trace_id", ""),
            "topic":    c.get("topic", f"chunk_{i}"),
            "status":   "pending",
            "generated": 0,
        })
    try:
        r.hset(key, mapping=mapping)
        r.expire(key, REDIS_JOB_TTL)
    except Exception as exc:
        _invalidate("init_job_progress", test_id=test_id, user_id=user_id, error=str(exc))


def update_chunk_progress(test_id: str, chunk_key: str, update: dict, user_id: str = None) -> None:
    """
    Merge `update` into the existing chunk hash field (read-modify-write).
    Creates the field if it doesn't exist yet.
    """
    r = _redis()
    if r is None:
        return
    key = f"test:{test_id}"
    try:
        existing_raw = r.hget(key, chunk_key)
        data = json.loads(existing_raw) if existing_raw else {}
        data.update(update)
        r.hset(key, chunk_key, json.dumps(data))
        r.expire(key, REDIS_JOB_TTL)
    except Exception as exc:
        _invalidate("update_chunk_progress", test_id=test_id, user_id=user_id, error=str(exc))


def get_job_progress(test_id: str, user_id: str = None) -> dict | None:
    """
    Returns {chunk_0: {...}, chunk_1: {...}, ...} or None if key absent / Redis down.
    """
    r = _redis()
    if r is None:
        return None
    try:
        raw = r.hgetall(f"test:{test_id}")
        if not raw:
            return None
        return {k: json.loads(v) for k, v in raw.items()}
    except Exception as exc:
        _invalidate("get_job_progress", test_id=test_id, user_id=user_id, error=str(exc))
        return None


def delete_job_progress(test_id: str, user_id: str = None) -> None:
    """Remove job progress key after job completes (cleanup)."""
    r = _redis()
    if r is None:
        return
    try:
        r.delete(f"test:{test_id}")
    except Exception as exc:
        _invalidate("delete_job_progress", test_id=test_id, user_id=user_id, error=str(exc))

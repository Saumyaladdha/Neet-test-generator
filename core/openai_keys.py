"""
OpenAI key rotation.

Several keys are configured (OPENAI_API_KEY, OPENAI_API_KEY_2, OPENAI_API_KEY_3
— unset ones are skipped). A single shared, process-wide pointer tracks which
key is "current". Callers ask for a client offset from the current pointer
(so retry attempt N naturally tries a different key from attempt N-1), and on
a rate-limit/auth error call rotate() to permanently advance the pointer so
later chunks and jobs in this worker also stop using the bad key.

This does not persist across processes/pods — each worker rediscovers a bad
key independently the first time it hits one. That's an accepted tradeoff:
correctness (never use a known-bad key again *within this process*) over
cross-process coordination, which would need a shared store.
"""

import threading

from openai import OpenAI

try:
    from openai import RateLimitError, AuthenticationError, PermissionDeniedError
    _KEY_ERROR_TYPES = (RateLimitError, AuthenticationError, PermissionDeniedError)
except ImportError:  # pragma: no cover - older openai SDK versions
    _KEY_ERROR_TYPES = ()

from core.config import OPENAI_API_KEYS
from core.logger import get_logger

log = get_logger(__name__)

_lock = threading.Lock()
_idx = 0


def key_count() -> int:
    return len(OPENAI_API_KEYS)


def current_index() -> int:
    with _lock:
        return _idx


def get_client(offset: int = 0) -> OpenAI:
    """Client bound to the key at (current pointer + offset), wrapping around."""
    with _lock:
        idx = (_idx + offset) % len(OPENAI_API_KEYS)
    return OpenAI(api_key=OPENAI_API_KEYS[idx])


def is_key_error(exc: Exception) -> bool:
    """True if this looks like a rate-limit/auth/quota error tied to the key itself."""
    if _KEY_ERROR_TYPES and isinstance(exc, _KEY_ERROR_TYPES):
        return True
    # Fallback string match in case the installed openai SDK version doesn't
    # expose the typed exceptions above.
    msg = str(exc).lower()
    return any(s in msg for s in ("rate limit", "429", "invalid_api_key", "401", "insufficient_quota"))


def rotate(user_id: str = None, test_id: str = None, batch_id: str = None, reason: str = "") -> None:
    """Advance the shared pointer to the next key. No-op with a single key."""
    global _idx
    if len(OPENAI_API_KEYS) <= 1:
        return
    with _lock:
        old = _idx
        _idx = (_idx + 1) % len(OPENAI_API_KEYS)
        new = _idx
    log.warning("openai_keys.rotated",
                user_id=user_id, test_id=test_id, batch_id=batch_id,
                from_key_index=old, to_key_index=new, reason=reason)

"""
Backward-compat re-export hub.

All production code should import directly from the domain modules:
  core.db_users      — user usage tracking, quota management
  core.db_detection  — detection results
  core.db_jobs       — job lifecycle, batch tracking
  core.db_test       — finished test content and answers

This file re-exports everything so existing callers continue working
during incremental migration.
"""

from core.db_base import dynamo as _dynamo, now_iso as _now_iso  # noqa: F401
from core.db_users import (  # noqa: F401
    get_user,
    create_user,
    check_and_reserve_quota,
    record_test_generated,
)
from core.db_detection import (  # noqa: F401
    create_detection_result,
    get_detection_result,
)
from core.db_jobs import (  # noqa: F401
    create_job,
    get_job,
    update_job_status,
    complete_job,
    init_batches,
    update_batch_in_job,
)
from core.db_test import (  # noqa: F401
    create_test,
    get_test,
    save_answers,
    update_answers_status,
)

"""
Generation job operations — NeetTestGenerator_Jobs table.

Responsibilities:
  - Job lifecycle: create, read, status updates, completion
  - Batch-processing-detail tracking (per-chunk trace + aggregate counts)

Answers live in NeetTestGenerator_Test (core.db_test), not here — they're
test content, not pipeline state. Rows have no TTL — kept indefinitely.

test_id is this job's own ID — also the primary key of the finished row in
NeetTestGenerator_Test once generation completes. It is minted fresh for
every job, even when several jobs come from the same detection (one PDF
commonly feeds several generate calls: easy/medium/hard x mcq/ar/mtc) —
reusing the detection's own ID here would let one job silently overwrite
another's row. source_test_id (optional) is the originating detection's
ID in NeetTestGenerator_Detector, kept only for traceability.
"""

from core.config import DYNAMO_JOBS_TABLE
from core.db_base import dynamo, now_iso
from core.enums import JobStatus, BatchStatus, Difficulty, QuestionTypeInput, validate, validate_list


def create_job(
    test_id: str,
    user_id: str,
    subject: str,
    medium: str,
    components: list,
    question_count: int,
    file_reference: str,
    file_type: str,
    test_series_name: str,
    source_test_id: str = None,
) -> str:
    """
    Create a new generator job row, keyed by test_id (must be unique per job —
    caller mints a fresh one even when several jobs share one detection).

    components: [{"question_type": "mcq", "difficulty": "easy", "question_count": 5}, ...]
    — one entry per single-component job, several for a mixed test. question_type
    and difficulty at the top level are always lists (sorted, deduplicated across
    components) — e.g. ["mcq"] for a single-component job, ["mcq", "assertion_reason"]
    for a mixed one. Never a "mixed" sentinel string.

    Returns test_id.
    """
    question_types = sorted({c["question_type"] for c in components})
    difficulties = sorted({c["difficulty"] for c in components})
    validate_list(QuestionTypeInput, question_types, "component question_type")
    validate_list(Difficulty, difficulties, "component difficulty")

    table = dynamo().Table(DYNAMO_JOBS_TABLE)
    now = now_iso()
    item = {
        "test_id":                test_id,
        "user_id":                user_id,
        "subject":                subject,
        "medium":                 medium,
        "components":             components,
        "question_type":          question_types,
        "difficulty":             difficulties,
        "question_count":         question_count,
        "file_reference":         file_reference,
        "file_type":              file_type,
        "test_series_name":       test_series_name,
        "status":                 JobStatus.PENDING.value,
        "batch_processing_detail": {},
        "total_batches":          0,
        "active_batches":         0,
        "failed_batches":         0,
        "successful_batches":     0,
        "final_question_count":   0,
        "retry_count":            0,
        "created_at":             now,
        "updated_at":             now,
    }
    if source_test_id:
        item["source_test_id"] = source_test_id
    table.put_item(Item=item, ConditionExpression="attribute_not_exists(test_id)")
    return test_id


def get_job(test_id: str) -> dict | None:
    table = dynamo().Table(DYNAMO_JOBS_TABLE)
    resp = table.get_item(Key={"test_id": test_id})
    return resp.get("Item")


def update_job_status(test_id: str, status: str, **kwargs) -> None:
    """Update job status and any extra top-level fields passed as kwargs."""
    validate(JobStatus, status, "status")
    table = dynamo().Table(DYNAMO_JOBS_TABLE)
    set_parts = ["#st = :status", "updated_at = :ts"]
    values = {":status": status, ":ts": now_iso()}
    names = {"#st": "status"}

    for k, v in kwargs.items():
        placeholder = f":kw_{k}"
        set_parts.append(f"{k} = {placeholder}")
        values[placeholder] = v

    table.update_item(
        Key={"test_id": test_id},
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def complete_job(
    test_id: str,
    status: str,
    final_question_count: int = 0,
    partial_message: str = None,
    error_message: str = None,
) -> None:
    """
    Write final job outcome. Question content itself lives in
    NeetTestGenerator_Test — this only records the outcome and count.
    status: one of core.enums.JobStatus
    """
    validate(JobStatus, status, "status")
    table = dynamo().Table(DYNAMO_JOBS_TABLE)
    now = now_iso()

    exp_names = {"#st": "status"}
    exp_values = {
        ":status": status,
        ":qcount": final_question_count,
        ":now":    now,
    }
    set_parts = [
        "#st = :status",
        "final_question_count = :qcount",
        "completed_at = :now",
        "updated_at = :now",
    ]

    if partial_message:
        set_parts.append("partial_message = :pm")
        exp_values[":pm"] = partial_message
    if error_message:
        set_parts.append("error_message = :em")
        exp_values[":em"] = error_message

    table.update_item(
        Key={"test_id": test_id},
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=exp_names,
        ExpressionAttributeValues=exp_values,
    )


def init_batches(test_id: str, total_batches: int) -> None:
    """Call once, before chunk processing starts, so aggregate counts are known upfront."""
    dynamo().Table(DYNAMO_JOBS_TABLE).update_item(
        Key={"test_id": test_id},
        UpdateExpression=(
            "SET total_batches = :t, active_batches = :t, "
            "failed_batches = :z, successful_batches = :z, updated_at = :ts"
        ),
        ExpressionAttributeValues={":t": total_batches, ":z": 0, ":ts": now_iso()},
    )


def update_batch_in_job(
    test_id: str,
    batch_key: str,
    batch_data: dict,
    delta_active: int = 0,
    delta_failed: int = 0,
    delta_successful: int = 0,
) -> None:
    """
    Write a single batch trace inside job.batch_processing_detail, and atomically
    adjust the aggregate active/failed/successful counters by the given deltas.
    """
    if "status" in batch_data:
        validate(BatchStatus, batch_data["status"], "batch status")

    dynamo().Table(DYNAMO_JOBS_TABLE).update_item(
        Key={"test_id": test_id},
        UpdateExpression=(
            "SET batch_processing_detail.#bk = :b, updated_at = :ts "
            "ADD active_batches :da, failed_batches :df, successful_batches :ds"
        ),
        ExpressionAttributeNames={"#bk": batch_key},
        ExpressionAttributeValues={
            ":b":  batch_data,
            ":ts": now_iso(),
            ":da": delta_active,
            ":df": delta_failed,
            ":ds": delta_successful,
        },
    )

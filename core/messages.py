"""
SQS message schemas for the NEET Test Generator.

Single source of truth for message structure — both producers (api/generate.py)
and consumers (worker/main.py, worker/answer_worker.py) import from here.
"""

import json


def build_generator_message(
    test_id: str,
    user_id: str,
    subject: str,
    medium: str,
    components: list,
    file_reference: str,
    file_type: str,
    test_series_name: str,
) -> str:
    """
    Return a JSON string ready to be sent as SQS MessageBody.
    components: [{"question_type": "mcq", "difficulty": "easy", "question_count": 5}, ...]
    """
    msg = {
        "test_id":          test_id,
        "user_id":          user_id,
        "subject":          subject,
        "medium":           medium,
        "components":       components,
        "file_reference":   file_reference,
        "file_type":        file_type,
        "test_series_name": test_series_name,
    }
    return json.dumps(msg)


def build_answer_message(test_id: str) -> str:
    """Return a JSON string ready to be sent as SQS MessageBody for answer generation."""
    return json.dumps({"test_id": test_id})

"""
Shared DynamoDB singleton and utilities.
All domain db modules import from here — never call boto3 directly.
"""

from datetime import datetime, timezone

import boto3
from botocore.config import Config

from core.config import AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY

_DYNAMO_CONFIG = Config(connect_timeout=5, read_timeout=5, retries={"max_attempts": 1})

_resource = None


def dynamo():
    global _resource
    if _resource is None:
        _resource = boto3.resource(
            "dynamodb",
            region_name=AWS_REGION,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            config=_DYNAMO_CONFIG,
        )
    return _resource


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

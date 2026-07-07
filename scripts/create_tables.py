"""
One-time script — creates the four DynamoDB tables.
Safe to re-run: skips tables that already exist.

Usage:
    python3.12 scripts/create_tables.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import boto3
from botocore.exceptions import ClientError
from core.config import (
    AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
    DYNAMO_USERS_TABLE, DYNAMO_JOBS_TABLE, DYNAMO_DETECTOR_TABLE, DYNAMO_TEST_TABLE,
)

client = boto3.client(
    "dynamodb",
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
)


def table_exists(name):
    try:
        client.describe_table(TableName=name)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return False
        raise


def create(name, schema):
    if table_exists(name):
        print(f"  SKIP  {name}  (already exists)")
        return
    client.create_table(**schema)
    waiter = client.get_waiter("table_exists")
    waiter.wait(TableName=name)
    print(f"  OK    {name}  created")


# ── NeetTestGenerator_User ───────────────────────────────────────────────────
# PK: user_id
create(DYNAMO_USERS_TABLE, {
    "TableName": DYNAMO_USERS_TABLE,
    "BillingMode": "PAY_PER_REQUEST",
    "AttributeDefinitions": [
        {"AttributeName": "user_id", "AttributeType": "S"},
    ],
    "KeySchema": [
        {"AttributeName": "user_id", "KeyType": "HASH"},
    ],
})

# ── NeetTestGenerator_Detector ───────────────────────────────────────────────
# PK: test_id
# GSI: user-created-index  (list all detections for a user, sorted by date)
create(DYNAMO_DETECTOR_TABLE, {
    "TableName": DYNAMO_DETECTOR_TABLE,
    "BillingMode": "PAY_PER_REQUEST",
    "AttributeDefinitions": [
        {"AttributeName": "test_id",    "AttributeType": "S"},
        {"AttributeName": "user_id",    "AttributeType": "S"},
        {"AttributeName": "created_at", "AttributeType": "S"},
    ],
    "KeySchema": [
        {"AttributeName": "test_id", "KeyType": "HASH"},
    ],
    "GlobalSecondaryIndexes": [
        {
            "IndexName": "user-created-index",
            "KeySchema": [
                {"AttributeName": "user_id",    "KeyType": "HASH"},
                {"AttributeName": "created_at", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }
    ],
})

# ── NeetTestGenerator_Jobs ───────────────────────────────────────────────────
# PK: test_id
# GSI: user-created-index  (list jobs for a user sorted by date)
create(DYNAMO_JOBS_TABLE, {
    "TableName": DYNAMO_JOBS_TABLE,
    "BillingMode": "PAY_PER_REQUEST",
    "AttributeDefinitions": [
        {"AttributeName": "test_id",    "AttributeType": "S"},
        {"AttributeName": "user_id",    "AttributeType": "S"},
        {"AttributeName": "created_at", "AttributeType": "S"},
    ],
    "KeySchema": [
        {"AttributeName": "test_id", "KeyType": "HASH"},
    ],
    "GlobalSecondaryIndexes": [
        {
            "IndexName": "user-created-index",
            "KeySchema": [
                {"AttributeName": "user_id",    "KeyType": "HASH"},
                {"AttributeName": "created_at", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }
    ],
})

# ── NeetTestGenerator_Test ───────────────────────────────────────────────────
# PK: test_id
create(DYNAMO_TEST_TABLE, {
    "TableName": DYNAMO_TEST_TABLE,
    "BillingMode": "PAY_PER_REQUEST",
    "AttributeDefinitions": [
        {"AttributeName": "test_id", "AttributeType": "S"},
    ],
    "KeySchema": [
        {"AttributeName": "test_id", "KeyType": "HASH"},
    ],
})

print("\nDone.")

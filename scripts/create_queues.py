"""
Create SQS queues for the NEET Test Generator.

Creates:
  NeetTestGeneratorDLQ        — dead-letter queue (receives jobs after 3 failed attempts)
  NeetTestGenerator           — main generator queue (already exists; updated to point to DLQ)

Run once:
  python scripts/create_queues.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import boto3
from dotenv import load_dotenv

load_dotenv()

from core.config import AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY

MAIN_QUEUE_NAME = "NeetTestGenerator"
DLQ_NAME        = "NeetTestGeneratorDLQ"
MAX_RECEIVE     = 3      # job retried 3 times before going to DLQ
VISIBILITY_SEC  = 300    # 5 min — worker heartbeat must beat this
RETENTION_SEC   = 345600 # 4 days


def main():
    sqs = boto3.client(
        "sqs",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )

    # ── 1. Create DLQ ─────────────────────────────────────────────────────────
    print(f"Creating DLQ: {DLQ_NAME}...", end=" ")
    try:
        resp = sqs.create_queue(
            QueueName=DLQ_NAME,
            Attributes={
                "MessageRetentionPeriod": str(RETENTION_SEC),
            },
        )
        dlq_url = resp["QueueUrl"]
        print(f"OK  {dlq_url}")
    except sqs.exceptions.QueueNameExists:
        dlq_url = sqs.get_queue_url(QueueName=DLQ_NAME)["QueueUrl"]
        print(f"already exists  {dlq_url}")

    # Get DLQ ARN
    dlq_arn = sqs.get_queue_attributes(
        QueueUrl=dlq_url,
        AttributeNames=["QueueArn"],
    )["Attributes"]["QueueArn"]
    print(f"  DLQ ARN: {dlq_arn}")

    # ── 2. Create / update main queue with DLQ wired in ───────────────────────
    redrive_policy = json.dumps({
        "deadLetterTargetArn": dlq_arn,
        "maxReceiveCount": str(MAX_RECEIVE),
    })

    print(f"\nCreating/updating main queue: {MAIN_QUEUE_NAME}...", end=" ")
    try:
        resp = sqs.create_queue(
            QueueName=MAIN_QUEUE_NAME,
            Attributes={
                "VisibilityTimeout":    str(VISIBILITY_SEC),
                "MessageRetentionPeriod": str(RETENTION_SEC),
                "RedrivePolicy":        redrive_policy,
            },
        )
        main_url = resp["QueueUrl"]
        print(f"OK  {main_url}")
    except sqs.exceptions.QueueNameExists:
        main_url = sqs.get_queue_url(QueueName=MAIN_QUEUE_NAME)["QueueUrl"]
        # Update existing queue attributes
        sqs.set_queue_attributes(
            QueueUrl=main_url,
            Attributes={
                "VisibilityTimeout":    str(VISIBILITY_SEC),
                "RedrivePolicy":        redrive_policy,
            },
        )
        print(f"already exists — attributes updated  {main_url}")

    # ── 3. Print .env values ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Add these to your .env:")
    print(f"  SQS_GENERATOR_QUEUE_URL={main_url}")
    print(f"  SQS_DLQ_URL={dlq_url}")
    print("=" * 60)

    return main_url, dlq_url


if __name__ == "__main__":
    main()

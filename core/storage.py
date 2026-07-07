"""
S3 helpers — upload files for both Detector and Generator APIs.

Bucket: mldatabase  (physically in ap-south-1)
Key structure: neetTestGenerator/{user_id}/{upload_date}/{context_id}/{filename}
upload_date is YYYY-MM-DD (UTC), so a bucket browser groups uploads by day.

boto3 auto-redirects to the bucket's actual region, so the client
uses AWS_REGION (us-east-1) — no special region override needed.
"""

import os
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from core.config import (
    AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
    S3_BUCKET, S3_PREFIX, S3_BUCKET_REGION,
)

_client = None
_presign_client = None


def _s3():
    """Upload client — uses AWS_REGION, boto3 auto-redirects to bucket region."""
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            region_name=AWS_REGION,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        )
    return _client


def _s3_presign():
    """
    Presigned URL client — must use the bucket's actual region.
    Presigned URLs embed the region in the signature, so the region here
    must match where the bucket physically lives.
    """
    global _presign_client
    if _presign_client is None:
        _presign_client = boto3.client(
            "s3",
            region_name=S3_BUCKET_REGION,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        )
    return _presign_client


def _build_key(user_id: str, context_id: str, filename: str) -> str:
    """e.g. neetTestGenerator/user-123/2026-07-03/detect-abc/image_1.png"""
    upload_date = datetime.now(timezone.utc).date().isoformat()
    return f"{S3_PREFIX}/{user_id}/{upload_date}/{context_id}/{filename}"


def upload_file(local_path: str, user_id: str, context_id: str,
                filename: str = None) -> str:
    """
    Upload a local file to S3.
    Returns the full s3:// URI.
    """
    if filename is None:
        filename = Path(local_path).name
    key = _build_key(user_id, context_id, filename)
    content_type, _ = mimetypes.guess_type(local_path)
    extra = {"ContentType": content_type} if content_type else {}
    _s3().upload_file(local_path, S3_BUCKET, key, ExtraArgs=extra)
    return f"s3://{S3_BUCKET}/{key}"


def upload_bytes(data: bytes, user_id: str, context_id: str,
                 filename: str, content_type: str = "application/octet-stream") -> str:
    """
    Upload raw bytes to S3 (e.g. in-memory file from FastAPI UploadFile).
    Returns the full s3:// URI.
    """
    key = _build_key(user_id, context_id, filename)
    _s3().put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    return f"s3://{S3_BUCKET}/{key}"


def get_presigned_url(s3_uri: str, expires: int = 3600) -> str:
    """
    Generate a pre-signed GET URL for an s3:// URI.
    e.g. get_presigned_url("s3://mldatabase/neetTestGenerator/...")
    """
    if s3_uri.startswith("s3://"):
        without_prefix = s3_uri[5:]
        bucket, _, key = without_prefix.partition("/")
    else:
        raise ValueError(f"Expected s3:// URI, got: {s3_uri}")

    return _s3_presign().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires,
    )


def s3_uri_to_https_url(s3_uri: str) -> str:
    """
    Convert s3://bucket/key to https://bucket.s3.amazonaws.com/key
    Useful when passing image URLs to OpenAI (must be public HTTPS).
    """
    if s3_uri.startswith("s3://"):
        without_prefix = s3_uri[5:]
        bucket, _, key = without_prefix.partition("/")
        return f"https://{bucket}.s3.amazonaws.com/{key}"
    raise ValueError(f"Expected s3:// URI, got: {s3_uri}")

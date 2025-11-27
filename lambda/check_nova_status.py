# =============================================================================
# lambda/check_nova_status.py
# =============================================================================
"""
Lambda function: check_nova_status

Poll Bedrock Nova Reel async job status.
Called by Step Functions workflow.

Input:
{
    "jobId": "uuid"
}

Output:
- While Nova job is still running:
  {
    "jobId": "uuid",
    "status": "PENDING"
  }

- When Nova raw video is ready:
  {
    "jobId": "uuid",
    "status": "READY",
    "novaVideoKey": "renders/raw-video/....mp4"
  }

- When Nova job fails:
  {
    "jobId": "uuid",
    "status": "FAILED"
  }
"""

import os
import logging

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

ddb = boto3.client("dynamodb")
bedrock = boto3.client("bedrock-runtime")
s3 = boto3.client("s3")

BUCKET_NAME = os.environ["BUCKET_NAME"]
JOBS_TABLE_NAME = os.environ["JOBS_TABLE_NAME"]



def check_nova_status_handler(event, context):
    """Check status of Bedrock Nova Reel async job."""
    job_id = event.get("jobId")
    if not job_id:
        raise ValueError("Missing jobId in input")

    logger.info(f"Checking Nova status for job: {job_id}")

    # Get job metadata from DynamoDB
    response = ddb.get_item(
        TableName=JOBS_TABLE_NAME,
        Key={"jobId": {"S": job_id}},
    )

    item = response.get("Item")
    if not item:
        logger.error(f"Job not found: {job_id}")
        return {"jobId": job_id, "status": "FAILED", "reason": "JobNotFound"}

    invocation_arn = item["novaInvocationArn"]["S"]

    # If we already discovered the raw Nova video key, we can just return READY again
    existing_nova_key = item.get("novaVideoKey", {}).get("S")
    if existing_nova_key:
        logger.info(
            f"Nova video key already stored for {job_id}: {existing_nova_key}"
        )
        return {
            "jobId": job_id,
            "status": "READY",
            "novaVideoKey": existing_nova_key,
        }

    # Query Bedrock async invoke status
    try:
        status_resp = bedrock.get_async_invoke(invocationArn=invocation_arn)
    except ClientError as e:
        logger.warning(f"Bedrock get_async_invoke error: {e}")
        # Treat as still pending so Step Functions will retry
        return {"jobId": job_id, "status": "PENDING"}

    status = status_resp.get("status")
    logger.info(f"Bedrock async status for {job_id}: {status}")

    # Still running?
    if status in ("IN_PROGRESS", "SUBMITTED", None):
        return {"jobId": job_id, "status": "PENDING"}

    # Failed?
    if status == "FAILED":
        ddb.update_item(
            TableName=JOBS_TABLE_NAME,
            Key={"jobId": {"S": job_id}},
            UpdateExpression="SET #s = :failed",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":failed": {"S": "FAILED"}},
        )
        return {"jobId": job_id, "status": "FAILED"}

    # COMPLETED: find the raw Nova video in S3
    output_config = status_resp.get("outputDataConfig", {})
    s3_output_uri = output_config.get("s3OutputDataConfig", {}).get("s3Uri")

    if not s3_output_uri:
        logger.error("No S3 output URI in Bedrock response")
        return {"jobId": job_id, "status": "PENDING"}

    video_bucket, nova_video_key = find_video_file(s3_output_uri)

    if not nova_video_key:
        # Nova job says COMPLETED, but file not visible yet – wait and retry
        logger.info(
            f"Nova status COMPLETED but no .mp4 yet for job {job_id}; "
            f"returning PENDING so Step Functions will retry."
        )
        return {"jobId": job_id, "status": "PENDING"}

    logger.info(
        f"Nova raw video ready for job {job_id}: bucket={video_bucket}, key={nova_video_key}"
    )

    # Store the raw Nova video key in DynamoDB
    ddb.update_item(
        TableName=JOBS_TABLE_NAME,
        Key={"jobId": {"S": job_id}},
        UpdateExpression="SET novaVideoKey = :rawKey",
        ExpressionAttributeValues={":rawKey": {"S": nova_video_key}},
    )

    # Tell Step Functions that Nova is READY; next state is mux_audio_video
    return {
        "jobId": job_id,
        "status": "READY",
        "novaVideoKey": nova_video_key,
    }


def find_video_file(s3_uri):
    """Find .mp4 file in S3 output prefix.

    Returns:
        (bucket, key) if found,
        (bucket, None) if not found yet.
    """
    if not s3_uri or not s3_uri.startswith("s3://"):
        raise ValueError(f"Invalid S3 URI: {s3_uri}")

    # Parse s3://bucket/prefix
    parts = s3_uri.replace("s3://", "").split("/", 1)
    bucket = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""

    logger.info(f"Searching for video in {bucket}/{prefix}")

    response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    contents = response.get("Contents", [])

    if not contents:
        logger.info(f"No objects yet under prefix {prefix}")
        return bucket, None

    for obj in contents:
        key = obj["Key"]
        if key.lower().endswith(".mp4"):
            logger.info(f"Found video file: {key}")
            return bucket, key

    logger.info(f"No .mp4 file found under {prefix} yet.")
    return bucket, None

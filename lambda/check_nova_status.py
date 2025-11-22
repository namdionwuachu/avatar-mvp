# =============================================================================
# lambda/check_nova_status.py
# =============================================================================

Lambda function: check_nova_status

Poll Bedrock Nova Reel async job status.
Called by Step Functions workflow.

Input:
{
    "jobId": "uuid"
}

Output:
{
    "jobId": "uuid",
    "status": "PENDING" | "READY" | "FAILED",
    "novaVideoKey": "renders/raw-video/..."  # if READY
}
"""

import os
import json
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
    try:
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
            return {"jobId": job_id, "status": "FAILED", "reason": "JobNotFound"}
        
        invocation_arn = item["novaInvocationArn"]["S"]
        
        # Query Bedrock async invoke status
        try:
            status_resp = bedrock.get_async_invoke(invocationArn=invocation_arn)
        except ClientError as e:
            logger.warning(f"Bedrock get_async_invoke error: {e}")
            # Job might still be initializing
            return {"jobId": job_id, "status": "PENDING"}
        
        status = status_resp.get("status")
        logger.info(f"Bedrock async status: {status}")
        
        # Handle different statuses
        if status in ("IN_PROGRESS", "SUBMITTED", None):
            return {"jobId": job_id, "status": "PENDING"}
        
        if status == "FAILED":
            # Update DynamoDB
            ddb.update_item(
                TableName=JOBS_TABLE_NAME,
                Key={"jobId": {"S": job_id}},
                UpdateExpression="SET #s = :failed",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":failed": {"S": "FAILED"}},
            )
            return {"jobId": job_id, "status": "FAILED"}
        
        # Status is COMPLETED - find video file
        output_config = status_resp.get("outputDataConfig", {})
        s3_output_uri = output_config.get("s3OutputDataConfig", {}).get("s3Uri")
        
        if not s3_output_uri:
            logger.error("No S3 output URI in Bedrock response")
            return {"jobId": job_id, "status": "FAILED"}
        
        # Find .mp4 file in S3 output prefix
        nova_video_key = find_video_file(s3_output_uri)
        
        # Update DynamoDB with video key
        ddb.update_item(
            TableName=JOBS_TABLE_NAME,
            Key={"jobId": {"S": job_id}},
            UpdateExpression="SET #s = :ready, novaVideoKey = :key",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":ready": {"S": "READY"},
                ":key": {"S": nova_video_key},
            },
        )
        
        logger.info(f"Nova video ready: {nova_video_key}")
        return {
            "jobId": job_id,
            "status": "READY",
            "novaVideoKey": nova_video_key,
        }
        
    except Exception as e:
        logger.exception("Error checking Nova status")
        return {"jobId": job_id, "status": "FAILED", "error": str(e)}


def find_video_file(s3_uri):
    """Find .mp4 file in S3 output prefix."""
    if not s3_uri or not s3_uri.startswith("s3://"):
        raise ValueError(f"Invalid S3 URI: {s3_uri}")
    
    # Parse s3://bucket/prefix
    parts = s3_uri.replace("s3://", "").split("/", 1)
    bucket = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""
    
    logger.info(f"Searching for video in {bucket}/{prefix}")
    
    # List objects in prefix
    response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    contents = response.get("Contents", [])
    
    # Find .mp4 file
    for obj in contents:
        key = obj["Key"]
        if key.lower().endswith(".mp4"):
            logger.info(f"Found video file: {key}")
            return key
    
    raise RuntimeError(f"No .mp4 file found under {prefix}")

# =============================================================================
# lambda/get_job.py
# =============================================================================

Lambda function: get_job

Query job status and retrieve signed video URL when complete.

Request: GET /jobs/{jobId}

Response (PENDING):
{
    "jobId": "uuid",
    "status": "PENDING"
}

Response (COMPLETED):
{
    "jobId": "uuid",
    "status": "COMPLETED",
    "videoUrl": "https://..."
}
"""

import json
import os
import logging
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

s3 = boto3.client("s3")
ddb = boto3.client("dynamodb")

BUCKET_NAME = os.environ["BUCKET_NAME"]
JOBS_TABLE_NAME = os.environ["JOBS_TABLE_NAME"]


def get_job_handler(event, context):
    """Retrieve job status and video URL."""
    try:
        # Extract jobId from path parameters
        path_params = event.get("pathParameters") or {}
        job_id = path_params.get("jobId")
        
        if not job_id:
            return create_response(400, {"error": "Missing jobId in path"})
        
        # Query DynamoDB
        response = ddb.get_item(
            TableName=JOBS_TABLE_NAME,
            Key={"jobId": {"S": job_id}},
        )
        
        item = response.get("Item")
        if not item:
            return create_response(404, {"error": "Job not found"})
        
        # Extract status
        raw_status = item.get("status", {}).get("S", "UNKNOWN")

        # Map internal statuses to what the frontend expects
        # check_nova_status sets status = "READY" when the raw Nova video is ready
        if raw_status == "READY":
            ui_status = "COMPLETED"
        else:
            ui_status = raw_status

        result = {
            "jobId": job_id,
            "status": ui_status,
            "userId": item.get("userId", {}).get("S", "unknown"),
        }

        # If completed/ready, generate signed URL for video
        if ui_status == "COMPLETED":
            # Prefer final muxed video if present, fall back to raw nova video
            final_key = item.get("finalVideoKey", {}).get("S")
            nova_key = item.get("novaVideoKey", {}).get("S")

            video_key = final_key or nova_key
            if video_key:
                video_url = s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": BUCKET_NAME, "Key": video_key},
                    ExpiresIn=3600,
                )
                result["videoUrl"] = video_url
                logger.info(f"Generated video URL for job: {job_id} key={video_key}")

        return create_response(200, result)

        
    except ClientError as e:
        logger.error(f"AWS error: {e}")
        return create_response(500, {"error": "Database error", "details": str(e)})
    except Exception as e:
        logger.exception("Unexpected error")
        return create_response(500, {"error": "Internal error", "details": str(e)})


def create_response(status_code, body):
    """Create HTTP response with CORS headers."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }


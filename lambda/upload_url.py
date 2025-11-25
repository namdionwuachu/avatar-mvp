# =============================================================================
# lambda/upload_url.py
# =============================================================================

"""

Lambda function: upload_url

Generate presigned S3 URLs for avatar images and voice samples.

Request:
{
    "userId": "user-123",
    "fileName": "avatar.png",
    "fileType": "avatar" | "voice"
}

Response:
{
    "uploadUrl": "https://bucket.s3.amazonaws.com/...",
    "objectKey": "uploads/user-123/avatar.png"
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
BUCKET_NAME = os.environ["BUCKET_NAME"]


def upload_url_handler(event, context):
    """Generate presigned S3 upload URL."""
    try:
        body = json.loads(event.get("body", "{}"))
        
        user_id = body.get("userId", "anonymous")
        file_name = body.get("fileName")
        file_type = body.get("fileType", "avatar")
        
        if not file_name:
            return create_response(400, {"error": "Missing 'fileName' in request body"})
        
        # Determine S3 key based on file type
        if file_type == "avatar":
            key = f"uploads/{user_id}/{file_name}"
        elif file_type == "voice":
            key = f"voice-samples/{user_id}/{file_name}"
        else:
            return create_response(400, {"error": "fileType must be 'avatar' or 'voice'"})
        
        # Generate presigned URL (1 hour expiration)
        url = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": BUCKET_NAME, "Key": key},
            ExpiresIn=3600,
        )
        
        logger.info(f"Generated presigned URL for: {key}")
        return create_response(200, {"uploadUrl": url, "objectKey": key})
        
    except ClientError as e:
        logger.error(f"S3 error: {e}")
        return create_response(500, {"error": "S3 error", "details": str(e)})
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
            "Access-Control-Allow-Headers": "Content-Type",
        },
        "body": json.dumps(body),
    }


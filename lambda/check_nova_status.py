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

Output (when READY):
{
    "jobId": "uuid",
    "status": "READY",
    "novaVideoKey": "renders/final/...",
    "downloadUrl": "https://presigned-s3-url..."
}
"""

import os
import json
import logging
import tempfile
import subprocess

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

ddb = boto3.client("dynamodb")
bedrock = boto3.client("bedrock-runtime")
s3 = boto3.client("s3")

BUCKET_NAME = os.environ["BUCKET_NAME"]
JOBS_TABLE_NAME = os.environ["JOBS_TABLE_NAME"]

# Path to ffmpeg binary (in a Lambda layer by default)
FFMPEG_PATH = os.environ.get("FFMPEG_PATH", "/opt/bin/ffmpeg")

# Where we store the final muxed videos
FINAL_PREFIX = os.environ.get("FINAL_PREFIX", "renders/final/")


def check_nova_status_handler(event, context):
    """Check status of Bedrock Nova Reel async job and mux audio when ready."""
    job_id = None
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
        audio_key = item.get("audioKey", {}).get("S")

        if not audio_key:
            logger.error(f"No audioKey stored for job {job_id}")
            return {"jobId": job_id, "status": "FAILED", "reason": "AudioMissing"}

        # If we've already produced a final video, short-circuit and just return it
        existing_final = item.get("finalVideoKey", {}).get("S")
        if existing_final:
            logger.info(f"Final video already exists for {job_id}: {existing_final}")
            download_url = generate_presigned_url(BUCKET_NAME, existing_final)
            return {
                "jobId": job_id,
                "status": "READY",
                "novaVideoKey": existing_final,
                "downloadUrl": download_url,
            }

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

        # Status is COMPLETED - find raw Nova video file
        output_config = status_resp.get("outputDataConfig", {})
        s3_output_uri = output_config.get("s3OutputDataConfig", {}).get("s3Uri")

        if not s3_output_uri:
            logger.error("No S3 output URI in Bedrock response")
            return {"jobId": job_id, "status": "FAILED"}

        video_bucket, nova_video_key = find_video_file(s3_output_uri)

        if not nova_video_key:
            # Video not visible yet – keep polling
            logger.info(
                f"Nova status COMPLETED but no .mp4 yet for job {job_id}; "
                f"returning PENDING so Step Functions will retry."
            )
            return {"jobId": job_id, "status": "PENDING"}

        logger.info(
            f"Nova raw video ready for job {job_id}: bucket={video_bucket}, "
            f"key={nova_video_key}, audioKey={audio_key}"
        )

        # Mux audio + video into a final MP4
        final_key = mux_audio_and_video(
            job_id=job_id,
            video_bucket=video_bucket,
            video_key=nova_video_key,
            audio_bucket=BUCKET_NAME,
            audio_key=audio_key,
        )

        # Update DynamoDB with video keys & READY status
        ddb.update_item(
            TableName=JOBS_TABLE_NAME,
            Key={"jobId": {"S": job_id}},
            UpdateExpression=(
                "SET #s = :ready, novaVideoKey = :rawKey, finalVideoKey = :finalKey"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":ready": {"S": "READY"},
                ":rawKey": {"S": nova_video_key},
                ":finalKey": {"S": final_key},
            },
        )

        download_url = generate_presigned_url(BUCKET_NAME, final_key)
        logger.info(f"Final muxed video ready for {job_id}: {final_key}")

        return {
            "jobId": job_id,
            "status": "READY",
            "novaVideoKey": final_key,
            "downloadUrl": download_url,
        }

    except Exception as e:
        logger.exception("Error checking Nova status")
        return {"jobId": job_id, "status": "FAILED", "error": str(e)}


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

    # List objects in prefix
    response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    contents = response.get("Contents", [])

    if not contents:
        logger.info(f"No objects yet under prefix {prefix} – Nova output not visible yet.")
        return bucket, None

    # Find .mp4 file
    for obj in contents:
        key = obj["Key"]
        if key.lower().endswith(".mp4"):
            logger.info(f"Found video file: {key}")
            return bucket, key

    logger.info(f"No .mp4 file found under {prefix} yet.")
    return bucket, None


def mux_audio_and_video(job_id, video_bucket, video_key, audio_bucket, audio_key):
    """Download raw video + audio, mux with ffmpeg, upload final MP4.

    Returns:
        final_key (S3 key of merged video)
    """
    final_key = f"{FINAL_PREFIX}{job_id}.mp4"

    with tempfile.TemporaryDirectory() as tmpdir:
        raw_video_path = os.path.join(tmpdir, "raw.mp4")
        audio_path = os.path.join(tmpdir, "audio.mp3")
        final_path = os.path.join(tmpdir, "final.mp4")

        logger.info(f"Downloading video {video_bucket}/{video_key} to {raw_video_path}")
        s3.download_file(video_bucket, video_key, raw_video_path)

        logger.info(f"Downloading audio {audio_bucket}/{audio_key} to {audio_path}")
        s3.download_file(audio_bucket, audio_key, audio_path)

        # Run ffmpeg to mux (video copied, audio encoded as AAC).
        # -shortest ensures we don't extend beyond whichever is shorter.
        cmd = [
            FFMPEG_PATH,
            "-y",
            "-i",
            raw_video_path,
            "-i",
            audio_path,
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            final_path,
        ]
        logger.info(f"Running ffmpeg: {' '.join(cmd)}")
        subprocess.check_call(cmd)

        logger.info(f"Uploading final muxed video to {BUCKET_NAME}/{final_key}")
        s3.upload_file(
            final_path,
            BUCKET_NAME,
            final_key,
            ExtraArgs={"ContentType": "video/mp4"},
        )

    return final_key


def generate_presigned_url(bucket, key):
    """Generate a pre-signed S3 URL."""
    url_ttl = int(os.environ.get("DOWNLOAD_URL_TTL", "3600"))  # seconds
    try:
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=url_ttl,
        )
    except ClientError as e:
        logger.error(f"Error generating presigned URL for {bucket}/{key}: {e}")
        return None

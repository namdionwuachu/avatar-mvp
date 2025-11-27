# =============================================================================
# lambda/mux_audio_video/mux_audio_video.py
# =============================================================================
"""
Lambda function: mux_audio_video (Docker container)

Merge audio and video using FFmpeg.
Called by Step Functions when video is ready.

Input:
{
    "jobId": "uuid",
    "novaVideoKey": "renders/raw-video/...",
    "status": "READY"
}

Output:
{
    "jobId": "uuid",
    "status": "COMPLETED",
    "finalVideoKey": "renders/final/uuid.mp4"
}

Dockerfile should be in lambda/mux_audio_video/Dockerfile
"""

import os
import json
import uuid
import subprocess
import logging
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

s3 = boto3.client("s3")
ddb = boto3.client("dynamodb")

BUCKET_NAME = os.environ["BUCKET_NAME"]
JOBS_TABLE_NAME = os.environ["JOBS_TABLE_NAME"]


def mux_handler(event, context):
    """Merge audio and video with FFmpeg."""
    try:
        job_id = event.get("jobId")
        nova_video_key = event.get("novaVideoKey")
        
        if not job_id or not nova_video_key:
            raise ValueError("Missing jobId or novaVideoKey in input")
        
        logger.info(f"Starting mux for job: {job_id}")
        
        # Get job metadata
        response = ddb.get_item(
            TableName=JOBS_TABLE_NAME,
            Key={"jobId": {"S": job_id}},
        )
        
        item = response.get("Item")
        if not item:
            raise ValueError(f"Job not found: {job_id}")
        
        audio_key = item["audioKey"]["S"]
        
        # Download files to /tmp
        video_local = f"/tmp/video-{uuid.uuid4()}.mp4"
        audio_local = f"/tmp/audio-{uuid.uuid4()}.mp3"
        final_local = f"/tmp/final-{uuid.uuid4()}.mp4"
        
        logger.info(f"Downloading video: {nova_video_key}")
        s3.download_file(BUCKET_NAME, nova_video_key, video_local)
        
        logger.info(f"Downloading audio: {audio_key}")
        s3.download_file(BUCKET_NAME, audio_key, audio_local)
        
        # Run FFmpeg to merge audio and video
        logger.info("Running FFmpeg mux...")
        ffmpeg_command = [
            "ffmpeg",         # ← use PATH, no hard-coded /usr/bin
            "-y",
            "-i", video_local,
            "-i", audio_local,
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            "-strict", "experimental",
            final_local,
        ]
        
        subprocess.check_call(ffmpeg_command, stderr=subprocess.STDOUT)
        logger.info("FFmpeg mux completed")
        
        # Upload final video to S3
        final_key = f"{FINAL_PREFIX}{job_id}.mp4"
        logger.info(f"Uploading final video: {final_key}")
        
        s3.upload_file(
            final_local,
            BUCKET_NAME,
            final_key,
            ExtraArgs={"ContentType": "video/mp4"},
        )
        
        # Update DynamoDB
        ddb.update_item(
            TableName=JOBS_TABLE_NAME,
            Key={"jobId": {"S": job_id}},
            UpdateExpression="SET #s = :completed, finalVideoKey = :key",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":completed": {"S": "COMPLETED"},
                ":key": {"S": final_key},
            },
        )
        
        logger.info(f"Job completed: {job_id}")
        
        # Cleanup temp files
        for f in [video_local, audio_local, final_local]:
            if os.path.exists(f):
                os.remove(f)
        
        return {
            "jobId": job_id,
            "status": "COMPLETED",
            "finalVideoKey": final_key,
        }
        
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg failed: {e}")
        raise RuntimeError(f"FFmpeg mux failed: {e}")
    except ClientError as e:
        logger.error(f"AWS error: {e}")
        raise
    except Exception as e:
        logger.exception("Unexpected error in mux")
        raise



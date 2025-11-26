"""
Lambda function: create_job

Orchestrates avatar video generation:
1. Generate audio via Polly or voice cloning
2. Start Bedrock Nova Reel async video job
3. Store job metadata in DynamoDB
4. Trigger Step Functions workflow

Environment variables:
- BUCKET_NAME: S3 bucket name
- JOBS_TABLE_NAME: DynamoDB table name
- STATE_MACHINE_ARN: Step Functions ARN
- VOICE_CLONE_ENDPOINT_NAME: SageMaker endpoint name (optional)
- LOG_LEVEL: Logging level (default: INFO)
"""

import json
import os
import uuid
import logging
import time
import base64
from typing import Dict, Any
import boto3
from botocore.exceptions import ClientError, BotoCoreError

# Configure logging
logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# AWS clients
s3 = boto3.client("s3")
ddb = boto3.client("dynamodb")
polly = boto3.client("polly")
bedrock = boto3.client("bedrock-runtime")
stepfn = boto3.client("stepfunctions")
sagemaker_runtime = boto3.client("sagemaker-runtime")

# Environment configuration
BUCKET_NAME = os.environ["BUCKET_NAME"]
JOBS_TABLE_NAME = os.environ["JOBS_TABLE_NAME"]
STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
VOICE_CLONE_ENDPOINT_NAME = os.environ.get("VOICE_CLONE_ENDPOINT_NAME")

# Constants
NOVA_REEL_MODEL_ID = "amazon.nova-reel-v1:0"
DEFAULT_DURATION = 18
DEFAULT_FRAME_RATE = 24
DEFAULT_ASPECT_RATIO = "16:9"


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main handler for job creation.
    
    Request body:
    {
        "userId": "user-123",
        "script": "Hello, welcome...",
        "avatarKey": "uploads/user-123/avatar.png",
        "durationSeconds": 18,
        "voiceMode": "polly" | "cloned",
        "gestureMode": "subtle" | "expressive"
    }
    
    Response:
    {
        "jobId": "uuid-string"
    }
    """
    try:
        # Parse request body
        body = parse_request_body(event)
        logger.info(f"Processing job creation for user: {body.get('userId')}")
        
        # Validate required fields
        validate_request(body)
        
        user_id = body.get("userId", "anonymous")
        script = body["script"]
        avatar_key = body["avatarKey"]
        duration_seconds = int(body.get("durationSeconds", DEFAULT_DURATION))
        voice_mode = body.get("voiceMode", "polly")
        gesture_mode = body.get("gestureMode", "subtle")
        
        # Generate unique job ID
        job_id = str(uuid.uuid4())
        
        # Step 1: Generate audio (Polly or cloned voice)
        logger.info(f"Generating audio with mode: {voice_mode}")
        audio_bytes = generate_audio(script, voice_mode, user_id)
        
        # Upload audio to S3
        audio_key = f"renders/audio/{job_id}.mp3"
        upload_to_s3(audio_bytes, audio_key, "audio/mpeg")
        logger.info(f"Audio uploaded to: {audio_key}")
        
        # Step 2: Start Bedrock Nova Reel async job
        logger.info(f"Starting Nova Reel job with gesture mode: {gesture_mode}")
        invocation_arn = start_nova_reel_job(
            avatar_key=avatar_key,
            duration_seconds=duration_seconds,
            gesture_mode=gesture_mode,
            job_id=job_id,
        )
        logger.info(f"Nova Reel job started: {invocation_arn}")
        
        # Step 3: Store job metadata in DynamoDB
        store_job_metadata(
            job_id=job_id,
            user_id=user_id,
            audio_key=audio_key,
            avatar_key=avatar_key,
            voice_mode=voice_mode,
            gesture_mode=gesture_mode,
            invocation_arn=invocation_arn,
        )
        logger.info(f"Job metadata stored: {job_id}")
        
        # Step 4: Start Step Functions workflow
        start_step_functions(job_id)
        logger.info(f"Step Functions workflow started for job: {job_id}")
        
        return create_response(200, {"jobId": job_id})
        
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        return create_response(400, {"error": "Invalid request", "details": str(e)})
    except (ClientError, BotoCoreError) as e:
        logger.error(f"AWS service error: {e}")
        return create_response(500, {"error": "AWS service error", "details": str(e)})
    except Exception as e:
        logger.exception("Unexpected error")
        return create_response(500, {"error": "Internal server error", "details": str(e)})


def parse_request_body(event: Dict[str, Any]) -> Dict[str, Any]:
    """Parse and return request body from API Gateway event."""
    body = event.get("body")
    if isinstance(body, str):
        return json.loads(body or "{}")
    return body or {}


def validate_request(body: Dict[str, Any]) -> None:
    """Validate required fields in request body."""
    if not body.get("script"):
        raise ValueError("Missing required field: 'script'")
    if not body.get("avatarKey"):
        raise ValueError("Missing required field: 'avatarKey'")
    
    # Validate voice mode
    voice_mode = body.get("voiceMode", "polly")
    if voice_mode not in ("polly", "cloned"):
        raise ValueError(f"Invalid voiceMode: {voice_mode}. Must be 'polly' or 'cloned'")
    
    # Validate gesture mode
    gesture_mode = body.get("gestureMode", "subtle")
    if gesture_mode not in ("subtle", "expressive"):
        raise ValueError(f"Invalid gestureMode: {gesture_mode}. Must be 'subtle' or 'expressive'")
    
    # Validate duration
    duration = int(body.get("durationSeconds", DEFAULT_DURATION))
    if duration < 5 or duration > 120:
        raise ValueError("durationSeconds must be between 5 and 120")


def generate_audio(script: str, voice_mode: str, user_id: str) -> bytes:
    """
    Generate audio via Polly or voice cloning.
    
    Args:
        script: Text to synthesize
        voice_mode: "polly" or "cloned"
        user_id: User identifier for voice cloning
        
    Returns:
        Audio bytes (MP3 format)
    """
    if voice_mode == "polly":
        return generate_polly_audio(script)
    elif voice_mode == "cloned":
        if not VOICE_CLONE_ENDPOINT_NAME:
            raise ValueError("Voice cloning not configured: VOICE_CLONE_ENDPOINT_NAME missing")
        return generate_cloned_audio(script, user_id)
    else:
        raise ValueError(f"Unknown voice mode: {voice_mode}")


def generate_polly_audio(text: str) -> bytes:
    """
    Generate audio using Amazon Polly Neural TTS.
    
    Args:
        text: Text to synthesize
        
    Returns:
        Audio bytes (MP3 format)
    """
    try:
        response = polly.synthesize_speech(
            Text=text,
            OutputFormat="mp3",
            Engine="neural",
            VoiceId="Joanna",
            SampleRate="24000",
        )
        audio_bytes = response["AudioStream"].read()
        logger.info(f"Generated Polly audio: {len(audio_bytes)} bytes")
        return audio_bytes
    except ClientError as e:
        logger.error(f"Polly synthesis failed: {e}")
        raise


def generate_cloned_audio(text: str, user_id: str) -> bytes:
    """
    Generate audio using SageMaker voice cloning endpoint.
    
    The endpoint receives:
    {
        "userId": "user-123",
        "text": "Script text",
        "bucket": "bucket-name",
        "voiceSamplesPrefix": "voice-samples/user-123/"
    }
    
    And returns raw audio bytes (MP3 or WAV).
    
    Args:
        text: Text to synthesize
        user_id: User identifier to locate voice samples
        
    Returns:
        Audio bytes
    """
    try:
        payload = {
            "userId": user_id,
            "text": text,
            "bucket": BUCKET_NAME,
            "voiceSamplesPrefix": f"voice-samples/{user_id}/",
        }
        
        logger.info(f"Invoking voice clone endpoint: {VOICE_CLONE_ENDPOINT_NAME}")
        response = sagemaker_runtime.invoke_endpoint(
            EndpointName=VOICE_CLONE_ENDPOINT_NAME,
            ContentType="application/json",
            Accept="audio/mpeg",
            Body=json.dumps(payload).encode("utf-8"),
        )
        
        audio_bytes = response["Body"].read()
        if not audio_bytes:
            raise RuntimeError("Voice clone endpoint returned empty audio")
        
        logger.info(f"Generated cloned audio: {len(audio_bytes)} bytes")
        return audio_bytes
        
    except ClientError as e:
        logger.error(f"SageMaker endpoint invocation failed: {e}")
        raise RuntimeError(f"Voice cloning failed: {e}")




def upload_to_s3(data: bytes, key: str, content_type: str) -> None:
    """Upload data to S3 bucket."""
    try:
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
    except ClientError as e:
        logger.error(f"S3 upload failed for {key}: {e}")
        raise



def load_avatar_image_source(avatar_key: str) -> Dict[str, Any]:
    """
    Load the avatar image from S3 and convert it to the ImageSource
    structure expected by Nova Reel.

    - Reads bytes from s3://BUCKET_NAME/<avatar_key>
    - Base64 encodes them
    - Infers format from file extension (png/jpeg)
    """
    try:
        obj = s3.get_object(Bucket=BUCKET_NAME, Key=avatar_key)
        data = obj["Body"].read()
    except ClientError as e:
        logger.error(f"Failed to read avatar from S3: {BUCKET_NAME}/{avatar_key}: {e}")
        raise

    # Infer image format from file extension
    ext = avatar_key.lower().rsplit(".", 1)[-1] if "." in avatar_key else "png"
    if ext in ("jpg", "jpeg"):
        img_format = "jpeg"
    else:
        img_format = "png"

    b64 = base64.b64encode(data).decode("utf-8")

    image_source: Dict[str, Any] = {
        "format": img_format,
        "source": {
            # Nova Reel expects base64-encoded bytes here
            "bytes": b64
        },
    }
    return image_source

def start_nova_reel_job(
    avatar_key: str,
    duration_seconds: int,
    gesture_mode: str,
    job_id: str,
) -> str:
    """
    Start Bedrock Nova Reel async video generation job.
    
    Args:
        avatar_key: S3 key of avatar image
        duration_seconds: Requested video length from client (we clamp to 6s)
        gesture_mode: "subtle" or "expressive"
        job_id: Job identifier for output prefix
        
    Returns:
        Invocation ARN for tracking
    """
    # Build gesture-aware prompt
    gesture_description = build_gesture_prompt(gesture_mode)
    
    prompt = (
        f"Medium shot of a professional presenter looking directly into the camera, "
        f"{gesture_description}, "
        f"calm confident body language, slight head movement and occasional nods while speaking, "
        f"natural facial expressions with subtle smile, professional demeanor."
    )
    
    # Construct model input for Nova Reel
    # Nova Reel TEXT_VIDEO currently expects:
    # - durationSeconds: fixed supported values (e.g., 6)
    # - fps: 24
    # - dimension: "1280x720"
    #
    # We clamp duration to 6s here to satisfy the model constraints.
    output_prefix = f"s3://{BUCKET_NAME}/renders/raw-video/{job_id}/"

    # Load the uploaded avatar image as an ImageSource
    avatar_image_source = load_avatar_image_source(avatar_key)

    model_input = {
        "taskType": "TEXT_VIDEO",
        "textToVideoParams": {
            # High-level description of the shot / presenter
            "text": prompt,
            # Use the uploaded avatar image as the visual conditioning
            "images": [avatar_image_source],
        },
        "videoGenerationConfig": {
            # Clamp duration to 6s for TEXT_VIDEO to avoid enum validation errors
            "durationSeconds": 6,
            "fps": 24,
            "dimension": "1280x720",
            # Optionally: "seed": 0 for reproducibility
        },
    }
    
    logger.info(f"Nova Reel prompt: {prompt}")
    logger.info(f"Video duration: 6s, output: {output_prefix}")
    
    try:
        response = bedrock.start_async_invoke(
            modelId=NOVA_REEL_MODEL_ID,
            modelInput=model_input,
            outputDataConfig={
                "s3OutputDataConfig": {
                    "s3Uri": output_prefix,
                }
            },
        )
        return response["invocationArn"]
    except ClientError as e:
        logger.error(f"Nova Reel job start failed: {e}")
        raise


def build_gesture_prompt(gesture_mode: str) -> str:
    """Build gesture description for Nova Reel prompt."""
    if gesture_mode == "subtle":
        return "subtle natural hand gestures, minimal movement"
    elif gesture_mode == "expressive":
        return "clear expressive hand gestures, dynamic movement"
    else:
        return "natural hand gestures"


def store_job_metadata(
    job_id: str,
    user_id: str,
    audio_key: str,
    avatar_key: str,
    voice_mode: str,
    gesture_mode: str,
    invocation_arn: str,
) -> None:
    """Store job metadata in DynamoDB."""
    try:
        ddb.put_item(
            TableName=JOBS_TABLE_NAME,
            Item={
                "jobId": {"S": job_id},
                "userId": {"S": user_id},
                "status": {"S": "PENDING"},
                "audioKey": {"S": audio_key},
                "avatarKey": {"S": avatar_key},
                "voiceMode": {"S": voice_mode},
                "gestureMode": {"S": gesture_mode},
                "novaInvocationArn": {"S": invocation_arn},
                "createdAt": {"N": str(int(time.time()))},
            },
        )
    except ClientError as e:
        logger.error(f"DynamoDB put_item failed: {e}")
        raise


def start_step_functions(job_id: str) -> None:
    """Start Step Functions workflow to poll and mux."""
    try:
        stepfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=f"job-{job_id}",
            input=json.dumps({"jobId": job_id}),
        )
    except ClientError as e:
        logger.error(f"Step Functions start_execution failed: {e}")
        raise


def create_response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """Create HTTP response with CORS headers."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key",
            "Access-Control-Allow-Methods": "OPTIONS,POST,GET",
        },
        "body": json.dumps(body),
    }

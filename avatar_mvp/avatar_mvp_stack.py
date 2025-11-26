"""
Avatar MVP CDK Stack

Deploys complete infrastructure for AI-powered talking avatar generation:
- S3 bucket for uploads and renders
- DynamoDB for job tracking
- Lambda functions for API logic
- Step Functions for async workflow
- API Gateway for REST endpoints
"""

from constructs import Construct
import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
    aws_lambda as _lambda,
    aws_lambda_python_alpha as lambda_python,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
    aws_iam as iam,
    aws_apigateway as apigw,
    aws_logs as logs,
)
import os


class AvatarMvpStack(cdk.Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ====================================================================
        # S3 BUCKET
        # ====================================================================
        bucket = s3.Bucket(
            self,
            "AvatarBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            cors=[
                s3.CorsRule(
                    allowed_methods=[
                        s3.HttpMethods.GET,
                        s3.HttpMethods.PUT,
                        s3.HttpMethods.POST,
                    ],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                    max_age=3600,
                )
            ],
        )

        # ====================================================================
        # DYNAMODB TABLE
        # ====================================================================
        jobs_table = dynamodb.Table(
            self,
            "AvatarJobsTable",
            partition_key=dynamodb.Attribute(
                name="jobId", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ====================================================================
        # IAM POLICIES
        # ====================================================================
        bedrock_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "bedrock:InvokeModel",
                "bedrock:StartAsyncInvoke",
                "bedrock:GetAsyncInvoke",
                "bedrock:ListAsyncInvokes",
            ],
            resources=["*"],
        )

        polly_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["polly:SynthesizeSpeech"],
            resources=["*"],
        )

        s3_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "s3:GetObject",
                "s3:PutObject",
                "s3:DeleteObject",
                "s3:ListBucket",
            ],
            resources=[
                bucket.bucket_arn,
                bucket.arn_for_objects("*"),
            ],
        )

        ddb_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:UpdateItem",
                "dynamodb:Query",
            ],
            resources=[jobs_table.table_arn],
        )

        sagemaker_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["sagemaker:InvokeEndpoint"],
            resources=["*"],
        )

        # Common Lambda environment
        lambda_env = {
            "BUCKET_NAME": bucket.bucket_name,
            "JOBS_TABLE_NAME": jobs_table.table_name,
            "LOG_LEVEL": "INFO",
        }

        # ====================================================================
        # LAMBDA: upload_url
        # ====================================================================
    
        upload_url_fn = lambda_python.PythonFunction(
            self,
            "UploadUrlFn",
            entry=os.path.join(os.path.dirname(__file__), "..", "lambda"),
            index="upload_url.py",
            handler="handler", 
            runtime=_lambda.Runtime.PYTHON_3_12,
            timeout=Duration.seconds(30),
            memory_size=256,
            environment=lambda_env,
        )
        upload_url_fn.add_to_role_policy(s3_policy)

        # ====================================================================
        # LAMBDA LAYER: ffmpeg (built by CDK)
        # ====================================================================
        
        ffmpeg_layer = _lambda.LayerVersion(
            self,
            "FfmpegLayer",
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_12],
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "..", "ffmpeg-layer"),
                bundling=cdk.BundlingOptions(
                    image=_lambda.Runtime.PYTHON_3_12.bundling_image,
                    command=[
                        "bash",
                        "-c",
                        # Inside the bundling container:
                        #  - download static ffmpeg
                        #  - put it into /asset-output/bin/ffmpeg (Lambda layer layout)
                        """
                        set -e

                        mkdir -p /asset-output/bin

                        curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz \
                          -o /tmp/ffmpeg.tar.xz

                        cd /tmp
                        tar -xf ffmpeg.tar.xz
                        FF_DIR=$(find . -maxdepth 1 -type d -name 'ffmpeg-*amd64-static' | head -n1)

                        cp "$FF_DIR/ffmpeg" /asset-output/bin/ffmpeg
                        chmod +x /asset-output/bin/ffmpeg
                        """,
                    ],
                ),
            ),
            description="FFmpeg static binary layer for avatar muxing",
        )



        # ====================================================================
        # LAMBDA: create_job
        # ====================================================================
        create_job_fn = lambda_python.PythonFunction(
            self,
            "CreateJobFn",
            entry=os.path.join(os.path.dirname(__file__), "..", "lambda"),
            index="create_job.py",
            handler="handler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            timeout=Duration.seconds(60),
            memory_size=512,
            environment={
                **lambda_env,
                "VOICE_CLONE_ENDPOINT_NAME": "voice-clone-endpoint",
            },
        )
        create_job_fn.add_to_role_policy(bedrock_policy)
        create_job_fn.add_to_role_policy(polly_policy)
        create_job_fn.add_to_role_policy(s3_policy)
        create_job_fn.add_to_role_policy(ddb_policy)
        create_job_fn.add_to_role_policy(sagemaker_policy)

        # ====================================================================
        # LAMBDA: get_job
        # ====================================================================
        get_job_fn = lambda_python.PythonFunction(
            self,
            "GetJobFn",
            entry=os.path.join(os.path.dirname(__file__), "..", "lambda"),
            index="get_job.py",
            handler="get_job_handler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            timeout=Duration.seconds(10),
            memory_size=256,
            environment=lambda_env,
        )
        get_job_fn.add_to_role_policy(s3_policy)
        get_job_fn.add_to_role_policy(ddb_policy)

        # ====================================================================
        # LAMBDA: check_nova_status
        # ====================================================================
        check_status_fn = lambda_python.PythonFunction(
            self,
            "CheckNovaStatusFn",
            entry=os.path.join(os.path.dirname(__file__), "..", "lambda"),
            index="check_nova_status.py",
            handler="check_nova_status_handler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            timeout=Duration.seconds(30),
            memory_size=256,
            environment={
                **lambda_env,                 # bring in BUCKET_NAME, JOBS_TABLE_NAME, LOG_LEVEL
                "FFMPEG_PATH": "/opt/bin/ffmpeg", # where ffmpeg lives in the layer
                "FINAL_PREFIX": "renders/final/",  # where muxed videos will be stored
                "DOWNLOAD_URL_TTL": "3600",       # presigned URL lifetime in seconds
            },
            layers=[ffmpeg_layer],  # attach the ffmpeg layer
        )

        check_status_fn.add_to_role_policy(bedrock_policy)
        check_status_fn.add_to_role_policy(s3_policy)
        check_status_fn.add_to_role_policy(ddb_policy)
        
        # ====================================================================
        # LAMBDA: mux_audio_video (Docker)
        # ====================================================================
        mux_fn = _lambda.DockerImageFunction(
            self,
            "MuxAudioVideoFn",
            code=_lambda.DockerImageCode.from_image_asset(
                os.path.join(
                    os.path.dirname(__file__), "..", "lambda", "mux_audio_video"
                )
            ),
            timeout=Duration.minutes(5),
            memory_size=2048,
            environment=lambda_env,
        )
        mux_fn.add_to_role_policy(s3_policy)
        mux_fn.add_to_role_policy(ddb_policy)

        # ====================================================================
        # STEP FUNCTIONS
        # ====================================================================
        
        # Wait state
        wait_state = sfn.Wait(
            self,
            "WaitForVideo",
            time=sfn.WaitTime.duration(Duration.seconds(30)),
        )

        # Check status task
        check_task = tasks.LambdaInvoke(
            self,
            "CheckNovaStatus",
            lambda_function=check_status_fn,
            output_path="$.Payload",
        )

        # Mux task
        mux_task = tasks.LambdaInvoke(
            self,
            "MuxAudioAndVideo",
            lambda_function=mux_fn,
            output_path="$.Payload",
        )

        # Success state
        success_state = sfn.Succeed(self, "JobCompleted")

        # Fail state
        fail_state = sfn.Fail(
            self,
            "JobFailed",
            cause="Nova Reel video generation failed",
            error="VIDEO_GENERATION_ERROR",
        )

        # Define workflow
        definition = (
            wait_state
            .next(check_task)
            .next(
                sfn.Choice(self, "IsDone?")
                .when(
                    sfn.Condition.string_equals("$.status", "PENDING"),
                    wait_state,
                )
                .when(
                    sfn.Condition.string_equals("$.status", "READY"),
                    success_state,        # go directly to success
                )
                .otherwise(fail_state)
            )
        )


        # Create state machine
        state_machine = sfn.StateMachine(
            self,
            "AvatarStateMachine",
            definition=definition,
            timeout=Duration.minutes(20),
        )

        # Grant create_job permission to start state machine
        state_machine.grant_start_execution(create_job_fn)
        create_job_fn.add_environment(
            "STATE_MACHINE_ARN", state_machine.state_machine_arn
        )

        # ====================================================================
        # API GATEWAY
        # ====================================================================
        api = apigw.RestApi(
            self,
            "AvatarApi",
            rest_api_name="avatar-mvp-api",
            description="Avatar MVP REST API",
            deploy_options=apigw.StageOptions(
                throttling_rate_limit=10,
                throttling_burst_limit=20,
            ),
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,
                allow_methods=apigw.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "Authorization"],
            ),
        )

        # POST /upload-url
        upload_resource = api.root.add_resource("upload-url")
        upload_resource.add_method(
            "POST",
            apigw.LambdaIntegration(upload_url_fn),
        )

        # POST /jobs
        jobs_resource = api.root.add_resource("jobs")
        jobs_resource.add_method(
            "POST",
            apigw.LambdaIntegration(create_job_fn),
        )

        # GET /jobs/{jobId}
        job_item = jobs_resource.add_resource("{jobId}")
        job_item.add_method(
            "GET",
            apigw.LambdaIntegration(get_job_fn),
        )

        # ====================================================================
        # OUTPUTS
        # ====================================================================
        cdk.CfnOutput(
            self,
            "ApiUrl",
            value=api.url,
            description="API Gateway URL - use this in index.html",
        )

        cdk.CfnOutput(
            self,
            "BucketName",
            value=bucket.bucket_name,
            description="S3 bucket name",
        )

        cdk.CfnOutput(
            self,
            "JobsTableName",
            value=jobs_table.table_name,
            description="DynamoDB table name",
        )

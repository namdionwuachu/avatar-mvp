# =============================================================================
# app.py (root directory) - CDK App Entry Point
# =============================================================================

#!/usr/bin/env python3
import aws_cdk as cdk
from avatar_mvp.avatar_mvp_stack import AvatarMvpStack

app = cdk.App()

AvatarMvpStack(
    app,
    "AvatarMvpStack",
    env=cdk.Environment(
        account=app.node.try_get_context("account"),
        region=app.node.try_get_context("region") or "us-east-1",
    ),
    description="Avatar MVP - Bedrock Nova Reel + Polly/Voice Cloning Stack",
)

app.synth()

# =============================================================================
# lambda/mux_audio_video/Dockerfile
# =============================================================================

FROM public.ecr.aws/lambda/python:3.10

# Install FFmpeg and clean up
RUN yum install -y ffmpeg && yum clean all && rm -rf /var/cache/yum

# Copy function code
COPY mux_audio_video.py ${LAMBDA_TASK_ROOT}

# Set handler
CMD [ "mux_audio_video.handler" ]

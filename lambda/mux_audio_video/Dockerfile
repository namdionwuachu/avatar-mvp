# =============================================================================
# lambda/mux_audio_video/Dockerfile
# =============================================================================

FROM public.ecr.aws/lambda/python:3.10

# Install FFmpeg (static build) and clean up
RUN yum install -y tar xz curl \
    && curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz -o /tmp/ffmpeg.tar.xz \
    && mkdir -p /opt/ffmpeg \
    && tar -xJf /tmp/ffmpeg.tar.xz -C /opt/ffmpeg --strip-components=1 \
    && ln -s /opt/ffmpeg/ffmpeg /usr/local/bin/ffmpeg \
    && ln -s /opt/ffmpeg/ffprobe /usr/local/bin/ffprobe \
    && rm -rf /tmp/ffmpeg.tar.xz \
    && yum clean all && rm -rf /var/cache/yum

# Copy function code
COPY mux_audio_video.py ${LAMBDA_TASK_ROOT}

# Set handler
CMD [ "mux_audio_video.mux_handler" ]


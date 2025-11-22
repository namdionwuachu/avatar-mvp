# =============================================================================
# sagemaker-voice-clone/Dockerfile
# =============================================================================

FROM python:3.10-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \\
    libsndfile1 \\
    ffmpeg \\
    && rm -rf /var/lib/apt/lists/*

# Install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy inference script
COPY inference.py /opt/ml/model/code/inference.py

# Set environment
ENV SAGEMAKER_PROGRAM inference.py

# Expose port
EXPOSE 8080

# Set entrypoint
ENTRYPOINT ["python", "-m", "sagemaker_inference", "serve"]



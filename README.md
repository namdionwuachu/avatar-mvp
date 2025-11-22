# Avatar MVP – AI Talking Avatar Generator

**Generate talking avatar videos with AWS Bedrock Nova Reel + Amazon Polly**

Upload your portrait, type a script, get a professional video with natural gestures and speech.

---

## 🎯 What This Does

1. **Upload** a portrait image (your face)
2. **Type** a script (what you want to say)
3. **Select** gesture style (subtle or expressive)
4. **Generate** an MP4 video with:
   - AI-animated facial expressions and head movements
   - Natural hand gestures
   - Professional neural voice (Amazon Polly)

**Processing time:** ~4-6 minutes per video

---

## 🏗️ Architecture




┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│   Browser   │────▶│  API Gateway │────▶│   Lambda Fns    │
│             │     │              │     │  (Python 3.12)  │
└─────────────┘     └──────────────┘     └────────┬────────┘
                                                   │
                         ┌─────────────────────────┼─────────────────┐
                         ▼                         ▼                 ▼
                   ┌──────────┐            ┌─────────────┐   ┌──────────┐
                   │    S3    │            │  DynamoDB   │   │ Bedrock  │
                   │  Bucket  │            │   (Jobs)    │   │  (Nova)  │
                   └──────────┘            └─────────────┘   └──────────┘
                         │                                          │
                         │                                          │
                         └──────────┬──────────────────────────────┘
                                    ▼
                           ┌──────────────────┐
                           │ Step Functions   │
                           │  (Poll + Mux)    │
                           └──────────────────┘


### AWS Services

| Service | Purpose |
|---------|---------|
| **Amazon Bedrock (Nova Reel)** | Video generation from image + text prompt |
| **Amazon Polly (Neural)** | Text-to-speech |
| **AWS Lambda** | Backend logic (5 functions) |
| **API Gateway** | REST API |
| **S3** | File storage |
| **DynamoDB** | Job tracking |
| **Step Functions** | Async workflow orchestration |

### Data Flow

```
Browser → API Gateway → Lambda → Polly (audio)
                              → Bedrock Nova Reel (video)
                              → Step Functions (poll & mux)
                              → S3 (final video)
```

### Project Structure

```
avatar-mvp/
├── app.py                      # CDK entry point
├── requirements.txt            # Python dependencies
├── cdk.json                    # CDK config
├── avatar_mvp/
│   ├── __init__.py
│   └── avatar_mvp_stack.py     # Infrastructure (CDK)
├── lambda/
│   ├── upload_url.py           # Presigned S3 URLs
│   ├── create_job.py           # Start video generation
│   ├── get_job.py              # Check job status
│   ├── check_nova_status.py    # Poll Bedrock
│   └── mux_audio_video/
│       ├── Dockerfile          # FFmpeg container
│       └── mux_audio_video.py  # Merge audio + video
└── web/
    └── index.html              # Frontend UI
```

---

## ⚙️ Prerequisites

- **AWS Account** with access to:
  - Amazon Bedrock (Nova Reel) - **must request access**
  - Amazon Polly
  - Lambda, API Gateway, S3, DynamoDB, Step Functions
- **AWS CLI** configured
- **Python 3.11+**
- **Node.js 18+**
- **Docker**

---

## 🚀 Setup

### 1. Clone & Install

```bash
git clone <your-repo>
cd avatar-mvp

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -r requirements.txt
npm install -g aws-cdk
```

### 2. Enable Bedrock Nova Reel

1. Go to **AWS Console** → **Bedrock** → **Model access**
2. Request access to `amazon.nova-reel-v1:0`
3. Wait for **"Access granted"**

### 3. Bootstrap & Deploy

```bash
# First time only
cdk bootstrap

# Deploy
cdk deploy
```

### 4. Configure Frontend

Copy the `ApiUrl` from deployment output and update `web/index.html`:

```javascript
const API_BASE = "https://abc123.execute-api.us-east-1.amazonaws.com/prod";
```

### 5. Test

```bash
cd web
python -m http.server 8000
```

Open http://localhost:8000

---

## 📱 Usage

1. **Upload** a portrait image (PNG/JPG, clear face, good lighting)
2. **Write** your script (max 500 characters)
3. **Choose** gesture style:
   - **Subtle** - minimal, professional
   - **Expressive** - dynamic, engaging
4. Click **Generate**
5. Wait 4-6 minutes
6. Watch your video!

---

## 🔧 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/upload-url` | Get presigned S3 upload URL |
| POST | `/jobs` | Create video generation job |
| GET | `/jobs/{jobId}` | Check job status |

### Example: Create Job

```bash
curl -X POST https://your-api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "userId": "user-123",
    "script": "Hello! Welcome to my channel.",
    "avatarKey": "uploads/user-123/avatar.png",
    "voiceMode": "polly",
    "gestureMode": "subtle"
  }'
```

---

## 💰 Cost Estimate

| Component | Cost per Video |
|-----------|----------------|
| Bedrock Nova Reel | ~$0.30 - $0.50 |
| Amazon Polly | ~$0.02 |
| Lambda + S3 + DynamoDB | ~$0.001 |
| **Total** | **~$0.32 - $0.52** |

---

## 🔒 Safety & Limits

| Protection | Setting |
|------------|---------|
| Step Functions timeout | 20 minutes max |
| Polling interval | 30 seconds |
| Lambda timeouts | 10 sec - 5 min |
| S3 auto-cleanup | 30 days (renders/) |

---

## 🐛 Troubleshooting

| Issue | Solution |
|-------|----------|
| "Bedrock access denied" | Request Nova Reel access in Bedrock console |
| "Video generation timeout" | Normal - Nova Reel takes 3-5 min |
| "FFmpeg mux fails" | Check CloudWatch logs for Lambda |
| Job stuck on PENDING | Check Step Functions execution in console |

### View Logs

```bash
# Lambda logs
aws logs tail /aws/lambda/AvatarMvpStack-CreateJobFn --follow

# Step Functions
aws stepfunctions list-executions --state-machine-arn <arn>
```

---

## 🗺️ Roadmap

### Current (v1.0)
- ✅ Amazon Polly voice
- ✅ Nova Reel video generation
- ✅ Subtle/Expressive gestures
- ✅ Simple web UI

### Planned
- [ ] Voice cloning (ElevenLabs API)
- [ ] Self-hosted voice cloning (SageMaker)
- [ ] Multiple Polly voice options
- [ ] Longer video support (30s, 60s)
- [ ] API rate limiting
- [ ] User authentication

---

## 📄 License

MIT License

---

## 🙏 Acknowledgments

- AWS Bedrock team (Nova Reel)
- Amazon Polly team
- AWS CDK team

---

**Built with AWS Bedrock, Polly, Lambda, and CDK (Python)**






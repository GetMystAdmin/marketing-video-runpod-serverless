# RunPod Serverless Migration Plan

## Executive Summary

This plan outlines the migration of the current ComfyUI LTX-2 **Pod deployment** to **RunPod Serverless**. The key difference is that Serverless workers are ephemeral, API-driven, and auto-scale based on demand—unlike Pods which run continuously.

---

## Current State (Pod Deployment)

```
┌─────────────────────────────────────────┐
│     RunPod GPU Pod (Always Running)     │
├─────────────────────────────────────────┤
│  • ComfyUI UI on port 8188              │
│  • JupyterLab on port 8888              │
│  • Models downloaded on startup         │
│  • Interactive workflow editing         │
│  • Persistent container                 │
└─────────────────────────────────────────┘
```

**Pros:** Interactive, UI access, persistent state
**Cons:** Pays for idle time, manual scaling, always-on costs

---

## Target State (Serverless)

```
┌─────────────────────────────────────────┐
│   RunPod Serverless (On-Demand)         │
├─────────────────────────────────────────┤
│  • API-only (no UI)                     │
│  • Auto-scales 0→N workers              │
│  • Pay per execution (not idle)         │
│  • Flash boot with pre-baked models     │
│  • Queue-based job processing           │
└─────────────────────────────────────────┘
```

**Pros:** Cost-efficient, auto-scaling, pay-per-use
**Cons:** No UI, cold starts, API-only interaction

---

## Architecture Comparison

| Aspect | Pod (Current) | Serverless (Target) |
|--------|---------------|---------------------|
| **Entry Point** | `start_script.sh` | `handler.py` |
| **Scaling** | Manual | Auto (0→N workers) |
| **Billing** | Per hour (running) | Per second (executing) |
| **UI Access** | Yes (port 8188) | No |
| **Cold Start** | N/A | 10-60s (optimize with flash boot) |
| **Models** | Downloaded on boot | Pre-baked or network volume |
| **API** | ComfyUI native | RunPod + ComfyUI bridge |

---

## Implementation Plan

### Phase 1: Core Handler Development

#### 1.1 Create `src/handler.py`

The serverless handler is the entry point for all job processing:

```python
import runpod
import subprocess
import requests
import time
import json
import os
import base64

# ComfyUI server URL (internal)
COMFY_HOST = "127.0.0.1:8188"

def wait_for_comfyui(timeout=300):
    """Wait for ComfyUI server to be ready."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"http://{COMFY_HOST}/system_stats")
            if r.status_code == 200:
                return True
        except:
            pass
        time.sleep(1)
    return False

def queue_prompt(workflow):
    """Queue a workflow prompt to ComfyUI."""
    p = {"prompt": workflow}
    r = requests.post(f"http://{COMFY_HOST}/prompt", json=p)
    return r.json()

def get_history(prompt_id):
    """Get execution history for a prompt."""
    r = requests.get(f"http://{COMFY_HOST}/history/{prompt_id}")
    return r.json()

def get_output_files(history, prompt_id):
    """Extract output files from history."""
    outputs = history[prompt_id]["outputs"]
    files = []
    for node_id, node_output in outputs.items():
        if "gifs" in node_output:  # Video output
            for item in node_output["gifs"]:
                files.append({"type": "video", "filename": item["filename"]})
        if "images" in node_output:  # Image output
            for item in node_output["images"]:
                files.append({"type": "image", "filename": item["filename"]})
    return files

def handler(job):
    """Main serverless handler."""
    job_input = job["input"]

    # Extract workflow (required)
    workflow = job_input.get("workflow")
    if not workflow:
        return {"error": "Missing 'workflow' in input"}

    # Optional: dynamic parameters to inject
    params = job_input.get("params", {})

    # Inject dynamic parameters into workflow
    for node_id, node_params in params.items():
        if node_id in workflow:
            workflow[node_id]["inputs"].update(node_params)

    # Queue the workflow
    try:
        result = queue_prompt(workflow)
        prompt_id = result["prompt_id"]
    except Exception as e:
        return {"error": f"Failed to queue prompt: {str(e)}"}

    # Poll for completion
    max_wait = job_input.get("timeout", 600)  # 10 min default
    start = time.time()

    while time.time() - start < max_wait:
        history = get_history(prompt_id)
        if prompt_id in history:
            outputs = get_output_files(history, prompt_id)

            # Return base64-encoded outputs or URLs
            result_files = []
            for out in outputs:
                filepath = f"/workspace/ComfyUI/output/{out['filename']}"
                if os.path.exists(filepath):
                    with open(filepath, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode()
                    result_files.append({
                        "type": out["type"],
                        "filename": out["filename"],
                        "data": b64
                    })

            return {"status": "success", "outputs": result_files}

        # Send progress update
        runpod.serverless.progress_update(job, {"status": "processing"})
        time.sleep(2)

    return {"error": "Timeout waiting for workflow completion"}

# Start ComfyUI in background before accepting jobs
def start_comfyui():
    """Start ComfyUI server in background."""
    subprocess.Popen([
        "python3", "/workspace/ComfyUI/main.py",
        "--listen", "127.0.0.1",
        "--port", "8188"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if not wait_for_comfyui():
        raise RuntimeError("ComfyUI failed to start")

# Initialize on cold start
start_comfyui()

# Start serverless worker
runpod.serverless.start({"handler": handler})
```

#### 1.2 Key Handler Features

| Feature | Implementation |
|---------|----------------|
| **Workflow Execution** | Queue to ComfyUI `/prompt` API |
| **Dynamic Parameters** | Inject via `params` input |
| **Progress Updates** | `runpod.serverless.progress_update()` |
| **Output Handling** | Base64 encode videos/images |
| **Timeout** | Configurable per-job |
| **Error Handling** | Structured error responses |

---

### Phase 2: Dockerfile Modification

#### 2.1 Create `Dockerfile.serverless`

```dockerfile
# Base: NVIDIA CUDA with Python
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# System dependencies
RUN apt-get update && apt-get install -y \
    python3.11 python3.11-venv python3-pip \
    git git-lfs aria2 ffmpeg curl \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python3.11 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python packages
RUN pip install --upgrade pip && \
    pip install --no-cache-dir \
    torch torchvision torchaudio --index-url https://download.pytorch.org/whl/nightly/cu128 && \
    pip install --no-cache-dir \
    runpod \
    requests \
    comfy-cli

# Install ComfyUI
RUN comfy --skip-prompt install --nvidia

# Install custom nodes (same as current Dockerfile)
WORKDIR /workspace/ComfyUI/custom_nodes
RUN git clone https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes.git && \
    git clone https://github.com/cubiq/ComfyUI_essentials.git && \
    git clone https://github.com/kijai/ComfyUI-KJNodes.git && \
    git clone https://github.com/Lightricks/ComfyUI-LTXVideo.git && \
    # ... (rest of custom nodes)
    pip install -r ComfyUI-KJNodes/requirements.txt || true

# Pre-download models (bake into image for fast cold start)
WORKDIR /workspace/ComfyUI/models

# LTX-2 Main Model
RUN aria2c -x 16 -s 16 -d checkpoints -o ltx-2-19b-dev.safetensors \
    "https://huggingface.co/Lightricks/ltx-2/resolve/main/ltx-2-19b-dev.safetensors"

# Text Encoder
RUN aria2c -x 16 -s 16 -d text_encoders -o gemma_3_12B_it.safetensors \
    "https://huggingface.co/google/gemma-3-12b-it/resolve/main/model.safetensors"

# ... (additional models as needed)

# Copy handler and workflows
WORKDIR /
COPY src/handler.py /handler.py
COPY workflows/ /workflows/

# Set entrypoint
CMD ["python3", "-u", "/handler.py"]
```

#### 2.2 Dockerfile Variants

| Variant | Models Baked | Use Case |
|---------|--------------|----------|
| `Dockerfile.serverless` | All models | Fast cold start, larger image |
| `Dockerfile.serverless-slim` | None | Uses network volume, smaller image |
| `Dockerfile.serverless-fp8` | FP8 models | Lower VRAM, faster inference |

---

### Phase 3: Network Volume Strategy

For large models (LTX-2 is ~38GB), consider network volumes:

#### 3.1 Option A: Baked Models (Recommended for Production)
- Models built into Docker image
- ~50GB image size
- Fast cold start (no download)
- Higher storage cost

#### 3.2 Option B: Network Volume
- Models on RunPod network volume
- ~5GB image size
- Slower cold start (mount time)
- Shared across endpoints

#### 3.3 Hybrid Approach
```
Docker Image (baked):
├── checkpoints/ltx-2-19b-dev.safetensors  (main model)
├── text_encoders/gemma_3_12B_it.safetensors

Network Volume (/runpod-volume):
├── loras/                (user LoRAs)
├── custom_checkpoints/   (additional models)
└── outputs/              (persistent outputs)
```

---

### Phase 4: API Contract

#### 4.1 Request Format

```json
{
  "input": {
    "workflow": {
      "6": {
        "inputs": {
          "text": "A cat playing piano",
          "clip": ["30", 1]
        },
        "class_type": "CLIPTextEncode"
      }
      // ... full ComfyUI workflow JSON
    },
    "params": {
      "6": {"text": "Override prompt here"},
      "27": {"width": 768, "height": 512}
    },
    "timeout": 300
  }
}
```

#### 4.2 Response Format

```json
{
  "status": "success",
  "outputs": [
    {
      "type": "video",
      "filename": "ComfyUI_00001.mp4",
      "data": "base64_encoded_video_data..."
    }
  ]
}
```

#### 4.3 Error Response

```json
{
  "error": "Timeout waiting for workflow completion"
}
```

---

### Phase 5: Workflow Templates

#### 5.1 Pre-configured Workflow Endpoints

Create simplified API wrappers for common use cases:

| Endpoint | Workflow | Required Params |
|----------|----------|-----------------|
| `/t2v` | LTX2_T2V.json | `prompt`, `duration` |
| `/i2v` | LTX2_I2V.json | `prompt`, `image_base64` |
| `/canny` | LTX2_canny_to_video.json | `prompt`, `reference_image` |
| `/depth` | LTX2_depth_to_video.json | `prompt`, `depth_image` |

#### 5.2 Simplified Handler Extension

```python
WORKFLOW_TEMPLATES = {
    "t2v": "/workflows/LTX2_T2V.json",
    "i2v": "/workflows/LTX2_I2V.json",
    "canny": "/workflows/LTX2_canny_to_video.json",
    "depth": "/workflows/LTX2_depth_to_video.json",
}

def handler(job):
    job_input = job["input"]

    # Support template mode
    if "template" in job_input:
        template_name = job_input["template"]
        if template_name not in WORKFLOW_TEMPLATES:
            return {"error": f"Unknown template: {template_name}"}

        with open(WORKFLOW_TEMPLATES[template_name]) as f:
            workflow = json.load(f)

        # Inject user params
        prompt = job_input.get("prompt", "")
        # ... map simplified params to workflow nodes
    else:
        workflow = job_input.get("workflow")

    # ... rest of handler
```

---

### Phase 6: CI/CD Updates

#### 6.1 CircleCI Configuration

```yaml
# .circleci/config.yml
version: 2.1

jobs:
  build-serverless:
    docker:
      - image: cimg/base:current
    steps:
      - checkout
      - setup_remote_docker:
          docker_layer_caching: true
      - run:
          name: Build Serverless Image
          command: |
            docker build -f Dockerfile.serverless \
              -t $DOCKERHUB_USER/comfyui-ltx2-serverless:$CIRCLE_TAG .
      - run:
          name: Push to Docker Hub
          command: |
            echo $DOCKERHUB_PAT | docker login -u $DOCKERHUB_USER --password-stdin
            docker push $DOCKERHUB_USER/comfyui-ltx2-serverless:$CIRCLE_TAG

workflows:
  deploy:
    jobs:
      - build-serverless:
          filters:
            tags:
              only: /^v[0-9]+(\.[0-9]+)*-serverless$/
```

---

### Phase 7: Testing Strategy

#### 7.1 Local Testing

```bash
# Run handler locally
python src/handler.py --rp_serve_api --rp_api_port 8000

# Test request
curl -X POST http://localhost:8000/runsync \
  -H "Content-Type: application/json" \
  -d @test/test_workflow.json
```

#### 7.2 Test Files

```
tests/
├── test_workflow.json      # Sample workflow input
├── test_handler.py         # Unit tests for handler
├── test_integration.py     # End-to-end tests
└── fixtures/
    ├── sample_image.png    # Test image for I2V
    └── expected_output/    # Expected outputs
```

---

## File Structure (Final)

```
comfyui-ltx2/
├── Dockerfile                    # Original Pod Dockerfile
├── Dockerfile.serverless         # NEW: Serverless Dockerfile
├── Dockerfile.serverless-slim    # NEW: Slim variant (network volume)
├── docker-compose.yml            # Local dev (unchanged)
├── docker-compose.serverless.yml # NEW: Serverless local testing
├── src/
│   ├── handler.py               # NEW: Serverless handler
│   ├── start_script.sh          # Original Pod startup
│   ├── comfy_api.py             # NEW: ComfyUI API wrapper
│   └── extra_model_paths.yaml   # Model paths config
├── workflows/
│   ├── LTX2_T2V.json
│   ├── LTX2_I2V.json
│   ├── LTX2_canny_to_video.json
│   └── LTX2_depth_to_video.json
├── tests/
│   ├── test_handler.py          # NEW: Handler tests
│   └── fixtures/
├── .circleci/
│   └── config.yml               # Updated for serverless builds
└── plans/
    └── runpod-serverless-migration.md  # This document
```

---

## Rollout Checklist

### Pre-Implementation
- [ ] Understand current workflow node structures
- [ ] Identify required model files and sizes
- [ ] Choose network volume vs baked strategy
- [ ] Set up RunPod account with serverless access

### Phase 1: Handler
- [ ] Create `src/handler.py`
- [ ] Create `src/comfy_api.py` (optional abstraction)
- [ ] Test locally with ComfyUI running

### Phase 2: Dockerfile
- [ ] Create `Dockerfile.serverless`
- [ ] Test Docker build locally
- [ ] Optimize layer caching

### Phase 3: Models
- [ ] Decide baked vs network volume
- [ ] Configure model paths
- [ ] Test cold start time

### Phase 4: Testing
- [ ] Create test fixtures
- [ ] Write unit tests
- [ ] Write integration tests
- [ ] Test on RunPod

### Phase 5: CI/CD
- [ ] Update CircleCI config
- [ ] Set up Docker Hub credentials
- [ ] Test automated builds

### Phase 6: Deployment
- [ ] Create RunPod serverless endpoint
- [ ] Configure GPU types
- [ ] Set scaling parameters
- [ ] Monitor cold start performance

---

## Cost Comparison (Estimated)

| Metric | Pod (24/7) | Serverless (On-Demand) |
|--------|------------|------------------------|
| Idle Time | Pays full rate | Free |
| Active Processing | ~$0.50/hr (4090) | ~$0.0004/sec |
| Monthly (light use) | ~$360 | ~$20-50 |
| Monthly (heavy use) | ~$360 | ~$100-200 |
| Cold Start | N/A | 10-30s |

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Cold start latency | Use flash boot, pre-bake models |
| Large image size | Use multi-stage builds, slim variants |
| Model download failures | Health checks, retry logic |
| Memory limits | FP8 quantization option |
| Timeout on long videos | Configurable timeout, progress updates |

---

## Next Steps

1. **Approve this plan** or request modifications
2. **Delegate to Luna** (RunPod Supervisor) for implementation
3. **Track progress** in Kanban

---

*Plan created: 2024-02-10*
*Author: Orchestrator (Claude)*

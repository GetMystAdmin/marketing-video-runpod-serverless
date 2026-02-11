"""
RunPod Serverless Handler for ComfyUI LTX-2

This handler bridges RunPod's serverless infrastructure with ComfyUI,
allowing video generation workflows to be executed on-demand.

Usage:
    # Deploy to RunPod Serverless
    CMD ["python3", "-u", "/handler.py"]

    # Local testing
    python handler.py --rp_serve_api --rp_api_port 8000
"""

import os
import sys
import json
import base64
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

import runpod

from comfy_bridge import (
    ComfyClient,
    ComfyAPIError,
    extract_output_files,
    load_workflow,
    inject_params,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("handler")

# Configuration
COMFY_HOST = os.getenv("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.getenv("COMFY_PORT", "8188"))
COMFY_OUTPUT_DIR = os.getenv("COMFY_OUTPUT_DIR", "/workspace/output")
COMFY_INPUT_DIR = os.getenv("COMFY_INPUT_DIR", "/workspace/input")
WORKFLOW_DIR = os.getenv("WORKFLOW_DIR", "/workflows")
STARTUP_TIMEOUT = int(os.getenv("STARTUP_TIMEOUT", "300"))

# Workflow templates mapping
WORKFLOW_TEMPLATES = {
    "t2v": "LTX2_T2V.json",
    "i2v": "LTX2_I2V.json",
    "canny": "LTX2_canny_to_video.json",
    "depth": "LTX2_depth_to_video.json",
}

# Template parameter mappings
# Maps simplified input params to workflow node IDs and their input names
# Format: {template: {simple_param: (node_id, node_input_name)}}
TEMPLATE_PARAM_MAPPING = {
    "t2v": {
        "width": ("43", "width"),
        "height": ("43", "height"),
        "frames": ("27", "int"),  # Frame count node
        "prompt": ("6", "text"),  # Positive prompt
        "negative_prompt": ("7", "text"),  # Negative prompt (if exists)
        "seed": ("31", "seed"),  # Sampler seed
        "steps": ("31", "steps"),  # Sampling steps
        "cfg": ("31", "cfg"),  # CFG scale
    },
    "i2v": {
        "width": ("43", "width"),
        "height": ("43", "height"),
        "frames": ("27", "int"),
        "prompt": ("6", "text"),
        "seed": ("31", "seed"),
    },
    "canny": {
        "width": ("43", "width"),
        "height": ("43", "height"),
        "prompt": ("6", "text"),
    },
    "depth": {
        "width": ("43", "width"),
        "height": ("43", "height"),
        "prompt": ("6", "text"),
    },
}

# Resolution presets for convenience
RESOLUTION_PRESETS = {
    "480p": (854, 480),
    "720p": (1280, 720),
    "768p": (1360, 768),  # LTX-2 optimized
    "1080p": (1920, 1080),
    # Portrait orientations
    "480p_portrait": (480, 854),
    "720p_portrait": (720, 1280),
    "1080p_portrait": (1080, 1920),
    # Square
    "512": (512, 512),
    "768": (768, 768),
    "1024": (1024, 1024),
}

# Global ComfyUI client
comfy_client: ComfyClient = None


def start_comfyui() -> bool:
    """
    Start ComfyUI server in background.

    Returns:
        True if server started successfully
    """
    global comfy_client

    logger.info("Starting ComfyUI server...")

    # Check if already running
    comfy_client = ComfyClient(host=COMFY_HOST, port=COMFY_PORT)
    if comfy_client.is_ready():
        logger.info("ComfyUI already running")
        return True

    # Build command
    comfy_cmd = [
        "python3", "/workspace/main.py",
        "--listen", COMFY_HOST,
        "--port", str(COMFY_PORT),
        "--disable-auto-launch",
    ]

    # Add extra model paths if configured
    extra_paths = os.getenv("EXTRA_MODEL_PATHS")
    if extra_paths and os.path.exists(extra_paths):
        comfy_cmd.extend(["--extra-model-paths-config", extra_paths])

    # Start ComfyUI process (log to stdout so RunPod captures it)
    logger.info(f"Running: {' '.join(comfy_cmd)}")
    subprocess.Popen(
        comfy_cmd,
        stdout=sys.stdout,
        stderr=sys.stderr,
        start_new_session=True
    )

    # Wait for server to be ready
    if not comfy_client.wait_for_ready(timeout=STARTUP_TIMEOUT):
        logger.error("ComfyUI failed to start within timeout")
        return False

    logger.info("ComfyUI server started successfully")
    return True


def encode_file_base64(filepath: str | Path) -> str:
    """Read a file and return base64 encoded string."""
    with open(filepath, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def decode_base64_to_file(b64_data: str, filepath: str | Path) -> None:
    """Decode base64 string and write to file."""
    with open(filepath, "wb") as f:
        f.write(base64.b64decode(b64_data))


def process_input_images(job_input: dict[str, Any]) -> dict[str, str]:
    """
    Process any base64 encoded images in the input and save them.

    Args:
        job_input: The job input dict

    Returns:
        Mapping of input names to saved filenames
    """
    saved_files = {}

    # Check for images dict
    images = job_input.get("images", {})
    for name, b64_data in images.items():
        if b64_data:
            # Generate unique filename
            timestamp = int(time.time() * 1000)
            filename = f"input_{name}_{timestamp}.png"
            filepath = Path(COMFY_INPUT_DIR) / filename

            # Ensure directory exists
            filepath.parent.mkdir(parents=True, exist_ok=True)

            # Save the image
            decode_base64_to_file(b64_data, filepath)
            saved_files[name] = filename
            logger.info(f"Saved input image: {filename}")

    return saved_files


def collect_outputs(output_files: list[dict]) -> list[dict[str, Any]]:
    """
    Collect output files and encode them as base64.

    Args:
        output_files: List of output file info dicts

    Returns:
        List of output dicts with base64 encoded data
    """
    results = []

    for output in output_files:
        filename = output.get("filename")
        subfolder = output.get("subfolder", "")

        if not filename:
            continue

        # Build full path
        if subfolder:
            filepath = Path(COMFY_OUTPUT_DIR) / subfolder / filename
        else:
            filepath = Path(COMFY_OUTPUT_DIR) / filename

        if not filepath.exists():
            logger.warning(f"Output file not found: {filepath}")
            continue

        # Get file size for logging
        size_mb = filepath.stat().st_size / (1024 * 1024)
        logger.info(f"Encoding output: {filename} ({size_mb:.2f} MB)")

        results.append({
            "type": output.get("type", "unknown"),
            "filename": filename,
            "data": encode_file_base64(filepath),
            "size_bytes": filepath.stat().st_size,
        })

    return results


def apply_template_params(
    workflow: dict[str, Any],
    template_name: str,
    job_input: dict[str, Any]
) -> dict[str, Any]:
    """
    Apply simplified parameters to a workflow using template mappings.

    This allows users to specify parameters like 'width', 'height', 'prompt'
    directly instead of knowing the specific node IDs.

    Args:
        workflow: The workflow dict to modify
        template_name: Name of the template (e.g., 't2v', 'i2v')
        job_input: The job input containing simplified params

    Returns:
        Modified workflow with injected parameters
    """
    mapping = TEMPLATE_PARAM_MAPPING.get(template_name, {})

    # Handle resolution preset
    resolution = job_input.get("resolution")
    if resolution and resolution in RESOLUTION_PRESETS:
        width, height = RESOLUTION_PRESETS[resolution]
        job_input["width"] = width
        job_input["height"] = height
        logger.info(f"Applied resolution preset '{resolution}': {width}x{height}")

    # Apply each simplified param
    for param_name, (node_id, input_name) in mapping.items():
        if param_name in job_input:
            value = job_input[param_name]
            if node_id in workflow:
                if "inputs" not in workflow[node_id]:
                    workflow[node_id]["inputs"] = {}
                workflow[node_id]["inputs"][input_name] = value
                logger.info(f"Set {param_name}={value} on node {node_id}.{input_name}")
            else:
                logger.warning(f"Node {node_id} not found for param {param_name}")

    return workflow


def progress_update(job: dict, progress: int, message: str) -> None:
    """Send progress update to RunPod."""
    try:
        runpod.serverless.progress_update(
            job,
            {"progress": progress, "message": message}
        )
    except Exception as e:
        logger.warning(f"Failed to send progress update: {e}")


def handler(job: dict[str, Any]) -> dict[str, Any]:
    """
    Main serverless handler for ComfyUI workflow execution.

    Args:
        job: RunPod job dict containing:
            - id: Job ID
            - input: Dict with workflow or template and params

    Input formats:
        1. Direct workflow:
            {
                "workflow": {...},  # Full ComfyUI workflow JSON
                "params": {"node_id": {"param": "value"}},  # Optional overrides
                "timeout": 600  # Optional timeout in seconds
            }

        2. Template mode (simplified):
            {
                "template": "t2v",  # Template name: t2v, i2v, canny, depth
                "prompt": "A cat playing piano",
                "width": 1280,  # Video width (default: 768)
                "height": 720,  # Video height (default: 512)
                "frames": 97,  # Frame count (default varies)
                "seed": 12345,  # Random seed (optional)
                "timeout": 600
            }

        3. Template mode with resolution preset:
            {
                "template": "t2v",
                "prompt": "A beautiful sunset",
                "resolution": "720p",  # Preset: 480p, 720p, 1080p, 768, etc.
                "timeout": 600
            }

        4. With input images (i2v):
            {
                "template": "i2v",
                "prompt": "Make this image come alive",
                "resolution": "720p",
                "images": {
                    "input_image": "base64_encoded_image..."
                }
            }

        Available resolution presets:
            - 480p (854x480), 720p (1280x720), 1080p (1920x1080)
            - 480p_portrait, 720p_portrait, 1080p_portrait
            - 512 (512x512), 768 (768x768), 1024 (1024x1024)

    Returns:
        {
            "status": "success" | "error",
            "outputs": [...],  # Base64 encoded outputs
            "error": "..."  # If status is error
        }
    """
    job_id = job.get("id", "unknown")
    job_input = job.get("input", {})

    logger.info(f"Processing job: {job_id}")

    try:
        # Validate ComfyUI is running
        if not comfy_client or not comfy_client.is_ready():
            return {"status": "error", "error": "ComfyUI server not available"}

        # Process input images
        saved_images = process_input_images(job_input)

        # Get or load workflow
        workflow = None

        if "workflow" in job_input:
            # Direct workflow mode
            workflow = job_input["workflow"]
            logger.info("Using direct workflow from input")

        elif "template" in job_input:
            # Template mode
            template_name = job_input["template"]
            if template_name not in WORKFLOW_TEMPLATES:
                return {
                    "status": "error",
                    "error": f"Unknown template: {template_name}. Available: {list(WORKFLOW_TEMPLATES.keys())}"
                }

            workflow_path = Path(WORKFLOW_DIR) / WORKFLOW_TEMPLATES[template_name]
            if not workflow_path.exists():
                return {
                    "status": "error",
                    "error": f"Workflow file not found: {workflow_path}"
                }

            workflow = load_workflow(workflow_path)
            logger.info(f"Loaded template: {template_name}")

            # Apply simplified parameters (width, height, prompt, etc.)
            workflow = apply_template_params(workflow, template_name, job_input)

        else:
            return {
                "status": "error",
                "error": "Must provide 'workflow' or 'template' in input"
            }

        # Inject custom parameters
        params = job_input.get("params", {})
        if params:
            workflow = inject_params(workflow, params)
            logger.info(f"Injected params for nodes: {list(params.keys())}")

        # Inject saved input images into workflow
        for name, filename in saved_images.items():
            # Update LoadImage nodes that reference this input
            for node_id, node in workflow.items():
                if node.get("class_type") == "LoadImage":
                    if node.get("inputs", {}).get("image") == name:
                        node["inputs"]["image"] = filename

        # Queue the workflow
        progress_update(job, 5, "Queuing workflow...")
        prompt_id = comfy_client.queue_prompt(workflow)

        # Wait for completion with progress updates
        timeout = job_input.get("timeout", 600)

        def on_progress(progress: int, message: str):
            # Map to 10-90% range (5% for queue, 95-100% for output)
            scaled = 10 + int(progress * 0.8)
            progress_update(job, scaled, message)

        progress_update(job, 10, "Executing workflow...")
        history = comfy_client.wait_for_completion(
            prompt_id,
            timeout=timeout,
            progress_callback=on_progress
        )

        # Extract and encode outputs
        progress_update(job, 95, "Collecting outputs...")
        output_files = extract_output_files(history)

        if not output_files:
            return {
                "status": "error",
                "error": "Workflow completed but no outputs found"
            }

        outputs = collect_outputs(output_files)
        progress_update(job, 100, "Complete")

        logger.info(f"Job {job_id} completed with {len(outputs)} outputs")

        return {
            "status": "success",
            "prompt_id": prompt_id,
            "outputs": outputs,
        }

    except ComfyAPIError as e:
        logger.error(f"ComfyUI error: {e}")
        return {"status": "error", "error": str(e)}

    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return {"status": "error", "error": f"Internal error: {str(e)}"}


def cleanup_old_outputs(max_age_hours: int = 24) -> None:
    """Clean up old output files to prevent disk space issues."""
    output_dir = Path(COMFY_OUTPUT_DIR)
    if not output_dir.exists():
        return

    cutoff = time.time() - (max_age_hours * 3600)
    cleaned = 0

    for filepath in output_dir.glob("**/*"):
        if filepath.is_file() and filepath.stat().st_mtime < cutoff:
            try:
                filepath.unlink()
                cleaned += 1
            except Exception as e:
                logger.warning(f"Failed to delete {filepath}: {e}")

    if cleaned:
        logger.info(f"Cleaned up {cleaned} old output files")


# Initialize on cold start
if __name__ == "__main__":
    logger.info("Initializing serverless worker...")

    # Clean up old outputs
    cleanup_old_outputs()

    # Start ComfyUI
    if not start_comfyui():
        logger.error("Failed to start ComfyUI, exiting")
        sys.exit(1)

    # Start the serverless worker
    logger.info("Starting RunPod serverless handler...")
    runpod.serverless.start({
        "handler": handler,
        "return_aggregate_stream": True,
    })

"""
ComfyUI API Client

Provides a clean interface to interact with ComfyUI's HTTP API.
Used by the serverless handler to queue workflows and retrieve outputs.
"""

import json
import time
import requests
import logging
from typing import Any
from pathlib import Path

logger = logging.getLogger(__name__)


class ComfyAPIError(Exception):
    """Exception raised for ComfyUI API errors."""
    pass


class ComfyClient:
    """Client for interacting with ComfyUI's HTTP API."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8188, timeout: int = 30):
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout

    def is_ready(self) -> bool:
        """Check if ComfyUI server is ready to accept requests."""
        try:
            r = requests.get(f"{self.base_url}/system_stats", timeout=5)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def wait_for_ready(self, timeout: int = 300) -> bool:
        """Wait for ComfyUI server to be ready."""
        start = time.time()
        while time.time() - start < timeout:
            if self.is_ready():
                logger.info("ComfyUI server is ready")
                return True
            time.sleep(1)
        logger.error(f"ComfyUI server not ready after {timeout}s")
        return False

    def queue_prompt(self, workflow: dict[str, Any]) -> str:
        """
        Queue a workflow for execution.

        Args:
            workflow: ComfyUI workflow dictionary (node graph)

        Returns:
            prompt_id: Unique identifier for tracking execution

        Raises:
            ComfyAPIError: If the request fails
        """
        payload = {"prompt": workflow}
        try:
            r = requests.post(
                f"{self.base_url}/prompt",
                json=payload,
                timeout=self.timeout
            )
            r.raise_for_status()
            result = r.json()
            prompt_id = result.get("prompt_id")
            if not prompt_id:
                raise ComfyAPIError(f"No prompt_id in response: {result}")
            logger.info(f"Queued prompt: {prompt_id}")
            return prompt_id
        except requests.RequestException as e:
            raise ComfyAPIError(f"Failed to queue prompt: {e}")

    def get_history(self, prompt_id: str) -> dict[str, Any] | None:
        """
        Get execution history for a prompt.

        Args:
            prompt_id: The prompt ID to look up

        Returns:
            History dict if available, None if not yet complete
        """
        try:
            r = requests.get(
                f"{self.base_url}/history/{prompt_id}",
                timeout=self.timeout
            )
            r.raise_for_status()
            history = r.json()
            return history.get(prompt_id)
        except requests.RequestException as e:
            logger.warning(f"Failed to get history: {e}")
            return None

    def get_queue(self) -> dict[str, Any]:
        """Get current queue status."""
        try:
            r = requests.get(f"{self.base_url}/queue", timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            raise ComfyAPIError(f"Failed to get queue: {e}")

    def interrupt(self) -> bool:
        """Interrupt current execution."""
        try:
            r = requests.post(f"{self.base_url}/interrupt", timeout=self.timeout)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def get_system_stats(self) -> dict[str, Any]:
        """Get system statistics (GPU memory, etc.)."""
        try:
            r = requests.get(f"{self.base_url}/system_stats", timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            raise ComfyAPIError(f"Failed to get system stats: {e}")

    def upload_image(self, image_data: bytes, filename: str, subfolder: str = "") -> dict[str, str]:
        """
        Upload an image to ComfyUI's input directory.

        Args:
            image_data: Raw image bytes
            filename: Name for the uploaded file
            subfolder: Optional subfolder within input directory

        Returns:
            Dict with name, subfolder, and type of uploaded file
        """
        try:
            files = {"image": (filename, image_data)}
            data = {"subfolder": subfolder, "type": "input"}
            r = requests.post(
                f"{self.base_url}/upload/image",
                files=files,
                data=data,
                timeout=self.timeout
            )
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            raise ComfyAPIError(f"Failed to upload image: {e}")

    def wait_for_completion(
        self,
        prompt_id: str,
        timeout: int = 600,
        poll_interval: float = 2.0,
        progress_callback: callable = None
    ) -> dict[str, Any]:
        """
        Wait for a prompt to complete execution.

        Args:
            prompt_id: The prompt ID to wait for
            timeout: Maximum wait time in seconds
            poll_interval: Time between status checks
            progress_callback: Optional callback for progress updates

        Returns:
            History dict with outputs

        Raises:
            ComfyAPIError: If execution fails or times out
        """
        start = time.time()
        last_progress = 0

        while time.time() - start < timeout:
            history = self.get_history(prompt_id)

            if history is not None:
                # Check for errors
                if "status" in history:
                    status = history["status"]
                    if status.get("status_str") == "error":
                        messages = status.get("messages", [])
                        raise ComfyAPIError(f"Execution failed: {messages}")

                # Check for outputs (indicates completion)
                if "outputs" in history and history["outputs"]:
                    logger.info(f"Prompt {prompt_id} completed")
                    return history

            # Send progress update if callback provided
            if progress_callback:
                elapsed = int(time.time() - start)
                progress = min(int((elapsed / timeout) * 100), 99)
                if progress > last_progress:
                    progress_callback(progress, f"Processing... ({elapsed}s)")
                    last_progress = progress

            time.sleep(poll_interval)

        raise ComfyAPIError(f"Timeout after {timeout}s waiting for prompt {prompt_id}")


def extract_output_files(history: dict[str, Any]) -> list[dict[str, str]]:
    """
    Extract output file information from execution history.

    Args:
        history: Execution history dict

    Returns:
        List of dicts with type, filename, and subfolder for each output
    """
    outputs = history.get("outputs", {})
    files = []

    for node_id, node_output in outputs.items():
        # Video outputs (gifs in ComfyUI terminology)
        if "gifs" in node_output:
            for item in node_output["gifs"]:
                files.append({
                    "type": "video",
                    "filename": item.get("filename"),
                    "subfolder": item.get("subfolder", ""),
                    "node_id": node_id
                })

        # Image outputs
        if "images" in node_output:
            for item in node_output["images"]:
                files.append({
                    "type": "image",
                    "filename": item.get("filename"),
                    "subfolder": item.get("subfolder", ""),
                    "node_id": node_id
                })

    return files


def load_workflow(path: str | Path) -> dict[str, Any]:
    """Load a workflow from a JSON file."""
    with open(path, "r") as f:
        return json.load(f)


def inject_params(workflow: dict[str, Any], params: dict[str, dict]) -> dict[str, Any]:
    """
    Inject parameters into workflow nodes.

    Args:
        workflow: The workflow dict to modify
        params: Dict mapping node_id -> {param_name: value}

    Returns:
        Modified workflow (also modifies in place)

    Example:
        params = {
            "6": {"text": "A cat playing piano"},
            "27": {"width": 768, "height": 512}
        }
    """
    for node_id, node_params in params.items():
        if node_id in workflow:
            if "inputs" not in workflow[node_id]:
                workflow[node_id]["inputs"] = {}
            workflow[node_id]["inputs"].update(node_params)
        else:
            logger.warning(f"Node {node_id} not found in workflow")

    return workflow

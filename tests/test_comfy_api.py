"""
Tests for the ComfyUI API client.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import json
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from comfy_bridge import (
    ComfyClient,
    ComfyAPIError,
    extract_output_files,
    load_workflow,
    inject_params,
)


class TestComfyClient:
    """Tests for ComfyClient class."""

    def test_init_default_values(self):
        """Test client initialization with defaults."""
        client = ComfyClient()
        assert client.base_url == "http://127.0.0.1:8188"
        assert client.timeout == 30

    def test_init_custom_values(self):
        """Test client initialization with custom values."""
        client = ComfyClient(host="localhost", port=9000, timeout=60)
        assert client.base_url == "http://localhost:9000"
        assert client.timeout == 60

    @patch('comfy_bridge.requests.get')
    def test_is_ready_success(self, mock_get):
        """Test is_ready returns True when server responds."""
        mock_get.return_value.status_code = 200
        client = ComfyClient()
        assert client.is_ready() is True

    @patch('comfy_bridge.requests.get')
    def test_is_ready_failure(self, mock_get):
        """Test is_ready returns False when server doesn't respond."""
        mock_get.side_effect = Exception("Connection refused")
        client = ComfyClient()
        assert client.is_ready() is False

    @patch('comfy_bridge.requests.post')
    def test_queue_prompt_success(self, mock_post):
        """Test successful prompt queuing."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"prompt_id": "abc123"}
        mock_post.return_value.raise_for_status = Mock()

        client = ComfyClient()
        prompt_id = client.queue_prompt({"test": "workflow"})

        assert prompt_id == "abc123"
        mock_post.assert_called_once()

    @patch('comfy_bridge.requests.post')
    def test_queue_prompt_no_prompt_id(self, mock_post):
        """Test queue_prompt raises error when no prompt_id returned."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {}
        mock_post.return_value.raise_for_status = Mock()

        client = ComfyClient()
        with pytest.raises(ComfyAPIError, match="No prompt_id"):
            client.queue_prompt({"test": "workflow"})

    @patch('comfy_bridge.requests.get')
    def test_get_history_success(self, mock_get):
        """Test successful history retrieval."""
        expected = {"outputs": {"1": {"images": []}}}
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"abc123": expected}
        mock_get.return_value.raise_for_status = Mock()

        client = ComfyClient()
        history = client.get_history("abc123")

        assert history == expected

    @patch('comfy_bridge.requests.get')
    def test_get_history_not_found(self, mock_get):
        """Test history returns None when prompt not found."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {}
        mock_get.return_value.raise_for_status = Mock()

        client = ComfyClient()
        history = client.get_history("nonexistent")

        assert history is None


class TestExtractOutputFiles:
    """Tests for extract_output_files function."""

    def test_extract_images(self):
        """Test extracting image outputs."""
        history = {
            "outputs": {
                "9": {
                    "images": [
                        {"filename": "test.png", "subfolder": ""},
                    ]
                }
            }
        }

        files = extract_output_files(history)

        assert len(files) == 1
        assert files[0]["type"] == "image"
        assert files[0]["filename"] == "test.png"

    def test_extract_videos(self):
        """Test extracting video outputs (gifs in ComfyUI)."""
        history = {
            "outputs": {
                "10": {
                    "gifs": [
                        {"filename": "video.mp4", "subfolder": "videos"},
                    ]
                }
            }
        }

        files = extract_output_files(history)

        assert len(files) == 1
        assert files[0]["type"] == "video"
        assert files[0]["filename"] == "video.mp4"
        assert files[0]["subfolder"] == "videos"

    def test_extract_mixed_outputs(self):
        """Test extracting both images and videos."""
        history = {
            "outputs": {
                "9": {"images": [{"filename": "img.png", "subfolder": ""}]},
                "10": {"gifs": [{"filename": "vid.mp4", "subfolder": ""}]},
            }
        }

        files = extract_output_files(history)

        assert len(files) == 2
        types = {f["type"] for f in files}
        assert types == {"image", "video"}

    def test_extract_empty_outputs(self):
        """Test with no outputs."""
        history = {"outputs": {}}
        files = extract_output_files(history)
        assert files == []


class TestInjectParams:
    """Tests for inject_params function."""

    def test_inject_single_param(self):
        """Test injecting a single parameter."""
        workflow = {
            "6": {"inputs": {"text": "original"}, "class_type": "CLIPTextEncode"}
        }
        params = {"6": {"text": "modified"}}

        result = inject_params(workflow, params)

        assert result["6"]["inputs"]["text"] == "modified"

    def test_inject_multiple_params(self):
        """Test injecting multiple parameters to same node."""
        workflow = {
            "27": {"inputs": {"width": 512, "height": 512}, "class_type": "EmptyImage"}
        }
        params = {"27": {"width": 768, "height": 432}}

        result = inject_params(workflow, params)

        assert result["27"]["inputs"]["width"] == 768
        assert result["27"]["inputs"]["height"] == 432

    def test_inject_to_nonexistent_node(self):
        """Test injecting to a node that doesn't exist (should warn, not fail)."""
        workflow = {"1": {"inputs": {}}}
        params = {"999": {"text": "test"}}

        # Should not raise, just log warning
        result = inject_params(workflow, params)
        assert "999" not in result

    def test_inject_creates_inputs_if_missing(self):
        """Test that inputs dict is created if missing."""
        workflow = {"1": {"class_type": "Test"}}
        params = {"1": {"value": 42}}

        result = inject_params(workflow, params)

        assert result["1"]["inputs"]["value"] == 42


class TestLoadWorkflow:
    """Tests for load_workflow function."""

    def test_load_valid_workflow(self, tmp_path):
        """Test loading a valid workflow JSON file."""
        workflow_data = {"1": {"inputs": {}, "class_type": "Test"}}
        workflow_file = tmp_path / "test_workflow.json"
        workflow_file.write_text(json.dumps(workflow_data))

        result = load_workflow(workflow_file)

        assert result == workflow_data

    def test_load_nonexistent_file(self):
        """Test loading a file that doesn't exist."""
        with pytest.raises(FileNotFoundError):
            load_workflow("/nonexistent/path.json")

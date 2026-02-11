"""
Tests for the RunPod serverless handler.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import json
import base64
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestHandlerInputValidation:
    """Tests for handler input validation."""

    @patch('handler.comfy_client')
    def test_missing_workflow_and_template(self, mock_client):
        """Test error when neither workflow nor template provided."""
        from handler import handler

        mock_client.is_ready.return_value = True

        job = {"id": "test-job", "input": {}}
        result = handler(job)

        assert result["status"] == "error"
        assert "workflow" in result["error"] or "template" in result["error"]

    @patch('handler.comfy_client')
    def test_invalid_template_name(self, mock_client):
        """Test error for unknown template name."""
        from handler import handler

        mock_client.is_ready.return_value = True

        job = {"id": "test-job", "input": {"template": "nonexistent"}}
        result = handler(job)

        assert result["status"] == "error"
        assert "Unknown template" in result["error"]

    @patch('handler.comfy_client', None)
    def test_comfy_not_available(self):
        """Test error when ComfyUI is not available."""
        from handler import handler

        job = {"id": "test-job", "input": {"workflow": {}}}
        result = handler(job)

        assert result["status"] == "error"
        assert "not available" in result["error"]


class TestEncodeDecodeBase64:
    """Tests for base64 encoding/decoding utilities."""

    def test_encode_file_base64(self, tmp_path):
        """Test encoding a file to base64."""
        from handler import encode_file_base64

        test_content = b"Hello, World!"
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(test_content)

        result = encode_file_base64(test_file)
        decoded = base64.b64decode(result)

        assert decoded == test_content

    def test_decode_base64_to_file(self, tmp_path):
        """Test decoding base64 to a file."""
        from handler import decode_base64_to_file

        original = b"Test data for encoding"
        encoded = base64.b64encode(original).decode()
        output_file = tmp_path / "output.txt"

        decode_base64_to_file(encoded, output_file)

        assert output_file.read_bytes() == original


class TestProcessInputImages:
    """Tests for input image processing."""

    def test_process_empty_images(self, tmp_path):
        """Test processing when no images provided."""
        from handler import process_input_images

        with patch('handler.COMFY_INPUT_DIR', str(tmp_path)):
            result = process_input_images({})
            assert result == {}

    def test_process_single_image(self, tmp_path):
        """Test processing a single input image."""
        from handler import process_input_images

        # Create test image data
        image_data = base64.b64encode(b"fake image data").decode()
        job_input = {"images": {"test_image": image_data}}

        with patch('handler.COMFY_INPUT_DIR', str(tmp_path)):
            result = process_input_images(job_input)

            assert len(result) == 1
            assert "test_image" in result
            # Check file was created
            saved_files = list(tmp_path.glob("input_test_image_*.png"))
            assert len(saved_files) == 1


class TestCollectOutputs:
    """Tests for output collection."""

    def test_collect_single_output(self, tmp_path):
        """Test collecting a single output file."""
        from handler import collect_outputs

        # Create a test output file
        output_file = tmp_path / "test_output.mp4"
        output_file.write_bytes(b"fake video data")

        output_files = [{"type": "video", "filename": "test_output.mp4", "subfolder": ""}]

        with patch('handler.COMFY_OUTPUT_DIR', str(tmp_path)):
            result = collect_outputs(output_files)

            assert len(result) == 1
            assert result[0]["type"] == "video"
            assert result[0]["filename"] == "test_output.mp4"
            assert "data" in result[0]
            # Verify base64 decodes correctly
            decoded = base64.b64decode(result[0]["data"])
            assert decoded == b"fake video data"

    def test_collect_missing_file(self, tmp_path):
        """Test handling of missing output file."""
        from handler import collect_outputs

        output_files = [{"type": "image", "filename": "nonexistent.png", "subfolder": ""}]

        with patch('handler.COMFY_OUTPUT_DIR', str(tmp_path)):
            result = collect_outputs(output_files)

            assert len(result) == 0  # Missing files are skipped

    def test_collect_with_subfolder(self, tmp_path):
        """Test collecting output from subfolder."""
        from handler import collect_outputs

        # Create subfolder and file
        subfolder = tmp_path / "videos"
        subfolder.mkdir()
        output_file = subfolder / "output.mp4"
        output_file.write_bytes(b"video data")

        output_files = [{"type": "video", "filename": "output.mp4", "subfolder": "videos"}]

        with patch('handler.COMFY_OUTPUT_DIR', str(tmp_path)):
            result = collect_outputs(output_files)

            assert len(result) == 1
            assert result[0]["filename"] == "output.mp4"


class TestWorkflowTemplates:
    """Tests for workflow template handling."""

    def test_template_mapping_exists(self):
        """Test that all expected templates are defined."""
        from handler import WORKFLOW_TEMPLATES

        expected_templates = ["t2v", "i2v", "canny", "depth"]
        for template in expected_templates:
            assert template in WORKFLOW_TEMPLATES

    def test_template_files_are_json(self):
        """Test that template values are JSON filenames."""
        from handler import WORKFLOW_TEMPLATES

        for name, filename in WORKFLOW_TEMPLATES.items():
            assert filename.endswith(".json"), f"Template {name} should be a JSON file"


class TestCleanupOldOutputs:
    """Tests for output cleanup."""

    def test_cleanup_old_files(self, tmp_path):
        """Test that old files are cleaned up."""
        from handler import cleanup_old_outputs
        import time

        # Create an "old" file by setting mtime to past
        old_file = tmp_path / "old_output.mp4"
        old_file.write_bytes(b"old data")
        # Set mtime to 48 hours ago
        old_time = time.time() - (48 * 3600)
        os.utime(old_file, (old_time, old_time))

        # Create a "new" file
        new_file = tmp_path / "new_output.mp4"
        new_file.write_bytes(b"new data")

        with patch('handler.COMFY_OUTPUT_DIR', str(tmp_path)):
            cleanup_old_outputs(max_age_hours=24)

        assert not old_file.exists()  # Should be deleted
        assert new_file.exists()  # Should remain

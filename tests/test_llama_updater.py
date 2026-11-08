#!/usr/bin/env python3
"""
Unit tests for llama_updater module.

Tests the GitHub API helpers, config management, and update_backends function
with mocked network calls.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from llama_updater import (
    _get_latest_nightly_tag,
    _get_release_assets,
    _load_config,
    _save_config,
    _get_platform_backend_dir,
    _set_platform_backend_dir,
    PLATFORM_BACKEND_COMBINATIONS,
    update_backends,
)


class TestGetLatestNightlyTag(unittest.TestCase):
    """Test the _get_latest_nightly_tag function."""
    
    @patch("llama_updater._github_request")
    def test_returns_latest_nightly(self, mock_request):
        mock_request.return_value = [
            {"tag_name": "v0.9.0", "published_at": "2026-11-04"},
            {"tag_name": "b12189", "published_at": "2026-11-07"},
            {"tag_name": "b12100", "published_at": "2026-11-06"},
            {"tag_name": "b11928", "published_at": "2026-10-27"},
        ]
        result = _get_latest_nightly_tag()
        self.assertEqual(result, "b12189")
    
    @patch("llama_updater._github_request")
    def test_ignores_non_nightly_tags(self, mock_request):
        mock_request.return_value = [
            {"tag_name": "v0.9.0", "published_at": "2026-11-04"},
            {"tag_name": "v0.8.0", "published_at": "2026-10-01"},
        ]
        result = _get_latest_nightly_tag()
        self.assertIsNone(result)
    
    @patch("llama_updater._github_request")
    def test_handles_api_failure(self, mock_request):
        mock_request.return_value = None
        result = _get_latest_nightly_tag()
        self.assertIsNone(result)
    
    @patch("llama_updater._github_request")
    def test_picks_highest_number(self, mock_request):
        mock_request.return_value = [
            {"tag_name": "b999", "published_at": "2026-01-01"},
            {"tag_name": "b12189", "published_at": "2026-11-07"},
            {"tag_name": "b12190", "published_at": "2026-11-08"},
        ]
        result = _get_latest_nightly_tag()
        self.assertEqual(result, "b12190")


class TestGetReleaseAssets(unittest.TestCase):
    """Test the _get_release_assets function."""
    
    @patch("llama_updater._github_request")
    def test_returns_asset_dict(self, mock_request):
        mock_request.return_value = {
            "assets": [
                {"name": "llama-b12189-bin-win-sycl-x64.zip", "browser_download_url": "https://example.com/sycl.zip", "size": 1000},
                {"name": "llama-b12189-bin-ubuntu-vulkan-x64.tar.gz", "browser_download_url": "https://example.com/vulkan.tar.gz", "size": 2000},
            ]
        }
        result = _get_release_assets("b12189")
        self.assertIn("llama-b12189-bin-win-sycl-x64.zip", result)
        self.assertEqual(result["llama-b12189-bin-win-sycl-x64.zip"]["url"], "https://example.com/sycl.zip")
        self.assertEqual(result["llama-b12189-bin-win-sycl-x64.zip"]["size"], 1000)
    
    @patch("llama_updater._github_request")
    def test_handles_api_failure(self, mock_request):
        mock_request.return_value = None
        result = _get_release_assets("b12189")
        self.assertEqual(result, {})


class TestConfigManagement(unittest.TestCase):
    """Test config load/save functions."""
    
    def setUp(self):
        # Use a temp config file
        self.tmp_dir = tempfile.mkdtemp()
        self.config_file = Path(self.tmp_dir) / ".llama-platform-config.json"
        self._orig_config_file = None
    
    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
    
    @patch("llama_updater._get_config_file")
    def test_load_empty_config(self, mock_get_config_file):
        mock_get_config_file.return_value = Path(self.tmp_dir) / "nonexistent.json"
        config = _load_config()
        # Should have all combinations with empty strings
        for combo in PLATFORM_BACKEND_COMBINATIONS:
            self.assertIn(combo, config)
            self.assertEqual(config[combo], "")
        self.assertIn("global_default", config)
    
    @patch("llama_updater._get_config_file")
    def test_save_and_load_config(self, mock_get_config_file):
        mock_get_config_file.return_value = self.config_file
        _save_config({"windows-sycl": "C:/test/bin", "global_default": "windows-sycl"})
        config = _load_config()
        self.assertEqual(config["windows-sycl"], "C:/test/bin")
        self.assertEqual(config["global_default"], "windows-sycl")
    
    @patch("llama_updater._get_config_file")
    def test_set_and_get_platform_backend_dir(self, mock_get_config_file):
        mock_get_config_file.return_value = self.config_file
        _set_platform_backend_dir("windows", "sycl", "C:/llama.cpp-sycl/bin")
        result = _get_platform_backend_dir("windows", "sycl")
        self.assertEqual(result, "C:/llama.cpp-sycl/bin")


class TestUpdateBackends(unittest.TestCase):
    """Test the update_backends function with mocked downloads.

    IMPORTANT: this test must never touch the real filesystem. Patching only
    ``Path.mkdir`` is not enough — ``Path.home()``, ``Path.exists()`` and
    ``shutil.rmtree`` all still hit the real disk, so an incomplete mock set
    made ``update_backends`` resolve ``$HOME/llama.cpp-vulkan/bin`` and delete
    the user's installed binaries. Every filesystem-touching helper is therefore
    redirected into a temp directory below.
    """

    def _run_update(self, mock_latest_tag, mock_assets, progress_cb):
        """Run update_backends against a fully faked filesystem + network."""
        fake_home = Path(tempfile.mkdtemp(prefix="llama_updater_test_"))
        try:
            with patch("llama_updater._get_latest_nightly_tag", mock_latest_tag), \
                 patch("llama_updater._get_release_assets", mock_assets), \
                 patch("llama_updater.urllib.request.urlretrieve") as mock_urlretrieve, \
                 patch("llama_updater._extract_zip", return_value=True), \
                 patch("llama_updater._extract_tar_gz", return_value=True), \
                 patch("llama_updater._find_binary_in_dir") as mock_find, \
                 patch("llama_updater.shutil.copy2") as mock_copy, \
                 patch("llama_updater._set_platform_backend_dir") as mock_set_dir, \
                 patch("llama_updater._save_config") as mock_save, \
                 patch("llama_updater.Path.home", return_value=fake_home), \
                 patch("llama_updater.shutil.rmtree") as mock_rmtree, \
                 patch("llama_updater.shutil.move") as mock_move:

                mock_find.return_value = fake_home / "fake" / "llama-server.exe"

                # Simulate a downloaded archive: one top-level dir holding a
                # llama-server binary. iterdir() drives the strip-components
                # logic, so it must be redirected or the real disk is read.
                def fake_iterdir(self):
                    if "extract" in str(self):
                        inner = MagicMock()
                        inner.is_dir.return_value = True
                        inner.iterdir.return_value = [fake_home / "fake" / "llama-server"]
                        return [inner]
                    return []

                # Only the post-extraction "did llama-server land in the target
                # dir?" check should succeed. Everything else (e.g. the
                # "does the target already exist, rmtree it?" check) must fail
                # so no real directory is ever considered a valid target.
                def fake_exists(self):
                    return str(self).endswith("llama-server")

                with patch("pathlib.Path.iterdir", fake_iterdir, create=True), \
                     patch("pathlib.Path.exists", fake_exists, create=True), \
                     patch("pathlib.Path.rename") as mock_rename, \
                     patch("pathlib.Path.symlink_to") as mock_symlink, \
                     patch("pathlib.Path.write_text") as mock_write, \
                     patch("pathlib.Path.chmod") as mock_chmod, \
                     patch("pathlib.Path.unlink") as mock_unlink:

                    result = update_backends(tag="b12189", progress_callback=progress_cb)

                return result, {
                    "urlretrieve": mock_urlretrieve,
                    "find": mock_find,
                    "copy": mock_copy,
                    "set_dir": mock_set_dir,
                    "save": mock_save,
                    "rmtree": mock_rmtree,
                    "move": mock_move,
                    "rename": mock_rename,
                    "symlink": mock_symlink,
                    "write": mock_write,
                    "chmod": mock_chmod,
                }
        finally:
            shutil.rmtree(fake_home, ignore_errors=True)

    def test_update_with_explicit_tag(self):
        """All six combinations are processed and the config is saved."""
        mock_assets_data = {
            "llama-b12189-bin-win-vulkan-x64.zip": {"name": "llama-b12189-bin-win-vulkan-x64.zip", "url": "https://example.com/win-vulkan.zip", "size": 1000},
            "llama-b12189-bin-win-sycl-x64.zip": {"name": "llama-b12189-bin-win-sycl-x64.zip", "url": "https://example.com/win-sycl.zip", "size": 1000},
            "llama-b12189-bin-win-openvino-2026.4.1-x64.zip": {"name": "llama-b12189-bin-win-openvino-2026.4.1-x64.zip", "url": "https://example.com/win-openvino.zip", "size": 1000},
            "llama-b12189-bin-ubuntu-vulkan-x64.tar.gz": {"name": "llama-b12189-bin-ubuntu-vulkan-x64.tar.gz", "url": "https://example.com/linux-vulkan.tar.gz", "size": 1000},
            "llama-b12189-bin-ubuntu-sycl-fp32-x64.tar.gz": {"name": "llama-b12189-bin-ubuntu-sycl-fp32-x64.tar.gz", "url": "https://example.com/linux-sycl.tar.gz", "size": 1000},
            "llama-b12189-bin-ubuntu-openvino-2026.4.1-x64.tar.gz": {"name": "llama-b12189-bin-ubuntu-openvino-2026.4.1-x64.tar.gz", "url": "https://example.com/linux-openvino.tar.gz", "size": 1000},
        }

        progress_calls = []
        result, mocks = self._run_update(
            MagicMock(return_value="b12189"),
            MagicMock(return_value=mock_assets_data),
            lambda msg, pct: progress_calls.append((msg, pct)),
        )

        self.assertTrue(result["success"], f"Errors: {result.get('errors')}")
        self.assertEqual(result["tag"], "b12189")
        self.assertEqual(len(result["updated_combinations"]), 6)
        self.assertTrue(result["config_updated"])
        self.assertGreater(len(progress_calls), 0)

        # Every combination must have been downloaded and recorded
        self.assertEqual(mocks["urlretrieve"].call_count, 6)
        self.assertEqual(mocks["set_dir"].call_count, 6)
        self.assertTrue(mocks["save"].called)
        # Each Linux archive's contents are moved into the target dir
        self.assertEqual(mocks["move"].call_count, 3)

    def test_update_records_errors_when_asset_missing(self):
        """A missing asset is reported as an error, not raised."""
        mock_assets_data = {
            "llama-b12189-bin-win-vulkan-x64.zip": {"name": "llama-b12189-bin-win-vulkan-x64.zip", "url": "https://example.com/win-vulkan.zip", "size": 1000},
        }

        result, _ = self._run_update(
            MagicMock(return_value="b12189"),
            MagicMock(return_value=mock_assets_data),
            None,
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["updated_combinations"], ["windows-vulkan"])
        self.assertTrue(any("not found in assets" in e for e in result["errors"]))

    def test_update_creates_sycl_wrapper_for_linux(self):
        """The Linux SYCL backend gets a wrapper script and a symlink."""
        mock_assets_data = {
            "llama-b12189-bin-ubuntu-sycl-fp32-x64.tar.gz": {"name": "llama-b12189-bin-ubuntu-sycl-fp32-x64.tar.gz", "url": "https://example.com/linux-sycl.tar.gz", "size": 1000},
        }

        result, mocks = self._run_update(
            MagicMock(return_value="b12189"),
            MagicMock(return_value=mock_assets_data),
            None,
        )

        # Other combinations legitimately fail (their assets aren't provided),
        # but linux-sycl must have been processed and wrapped.
        self.assertEqual(result["updated_combinations"], ["linux-sycl"])
        # rename original -> llama-server-orig, write wrapper, symlink back
        self.assertTrue(mocks["rename"].called)
        self.assertTrue(mocks["symlink"].called)
        self.assertTrue(mocks["write"].called)
        self.assertTrue(mocks["set_dir"].called)

    @patch("llama_updater._get_latest_nightly_tag")
    def test_update_uses_latest_tag_when_none_provided(self, mock_latest_tag):
        mock_latest_tag.return_value = "b12189"
        # This will fail at asset fetching, but we just want to verify tag detection
        with patch("llama_updater._get_release_assets") as mock_assets:
            mock_assets.return_value = {}
            result = update_backends(tag=None)
            self.assertFalse(result["success"])
            self.assertEqual(result["tag"], "b12189")
    
    @patch("llama_updater._get_latest_nightly_tag")
    def test_update_fails_when_no_nightly_found(self, mock_latest_tag):
        mock_latest_tag.return_value = None
        result = update_backends(tag=None)
        self.assertFalse(result["success"])
        self.assertIsNone(result["tag"])
        self.assertIn("Failed to fetch latest nightly tag from GitHub", result["errors"])


class TestAPIEndpoints(unittest.TestCase):
    """Test the new API endpoints."""
    
    def setUp(self):
        from flask import Flask
        from routes.models import models_bp
        app = Flask(__name__)
        app.register_blueprint(models_bp)
        app.config['TESTING'] = True
        self.client = app.test_client()
    
    def test_get_update_status(self):
        resp = self.client.get("/api/llama-update-status")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("running", data)
        self.assertIn("progress", data)
        self.assertIn("message", data)
    
    @patch("llama_updater._get_latest_nightly_tag")
    def test_get_latest_release(self, mock_tag):
        mock_tag.return_value = "b12189"
        resp = self.client.get("/api/llama-latest-release")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["tag"], "b12189")
    
    @patch("llama_updater.update_backends")
    def test_post_update_backends_starts_thread(self, mock_update):
        mock_update.return_value = {
            "success": True,
            "tag": "b12189",
            "errors": [],
            "updated_combinations": ["windows-sycl"],
            "config_updated": True,
            "total_combinations": 6,
        }
        resp = self.client.post("/api/llama-update-backends", 
                                json={"release_tag": "b12189"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "update started")
        self.assertEqual(data["release_tag"], "b12189")


if __name__ == "__main__":
    unittest.main()
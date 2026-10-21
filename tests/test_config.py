#!/usr/bin/env python3
"""
Unit tests for Config class in config.py.

Tests cover:
- Default local mode behavior
- Remote mode with valid URL
- Remote mode validation failures
- Various truthy/falsy environment values
"""

import unittest
from unittest.mock import patch
import os

from config import Config


class TestConfig(unittest.TestCase):
    """Test suite for Config class."""

    def setUp(self) -> None:
        """Ensure clean environment for each test."""
        # Clear relevant environment variables
        self.env_vars_to_clear = [
            "REMOTE_MODE",
            "LLAMA_CPP_SERVER_URL"
        ]
        self.original_env = {}
        for var in self.env_vars_to_clear:
            if var in os.environ:
                self.original_env[var] = os.environ[var]
                del os.environ[var]

    def tearDown(self) -> None:
        """Restore environment after each test."""
        for var in self.env_vars_to_clear:
            if var in os.environ:
                del os.environ[var]
            if var in self.original_env:
                os.environ[var] = self.original_env[var]

    def test_default_local_mode(self) -> None:
        """Test default behavior when no environment variables are set."""
        with patch.dict(os.environ, {}, clear=True):
            cfg = Config()
            self.assertFalse(cfg.is_remote_mode)
            self.assertEqual(cfg.remote_url, "http://localhost:8080")

    def test_explicit_false_local_mode(self) -> None:
        """Test explicit false values for REMOTE_MODE."""
        falsy_values = ["false", "0", "no", "off", "anything", ""]
        for value in falsy_values:
            with self.subTest(value=value):
                with patch.dict(os.environ, {"REMOTE_MODE": value}, clear=True):
                    cfg = Config()
                    self.assertFalse(cfg.is_remote_mode)
                    self.assertEqual(cfg.remote_url, "http://localhost:8080")

    def test_truthy_remote_mode_values(self) -> None:
        """Test various truthy values for REMOTE_MODE."""
        truthy_values = ["true", "1", "yes", "on", "True", "YES", "ON"]
        for value in truthy_values:
            with self.subTest(value=value):
                with patch.dict(os.environ, {
                    "REMOTE_MODE": value,
                    "LLAMA_CPP_SERVER_URL": "http://remote.example.com:8080"
                }, clear=True):
                    cfg = Config()
                    self.assertTrue(cfg.is_remote_mode)
                    self.assertEqual(cfg.remote_url, "http://remote.example.com:8080")

    def test_remote_mode_without_url_raises(self) -> None:
        """Test that remote mode without URL raises ValueError."""
        with patch.dict(os.environ, {"REMOTE_MODE": "true"}, clear=True):
            with self.assertRaises(ValueError) as cm:
                Config()
            self.assertIn("LLAMA_CPP_SERVER_URL", str(cm.exception))

    def test_remote_mode_with_empty_url_raises(self) -> None:
        """Test that remote mode with empty URL raises ValueError."""
        with patch.dict(os.environ, {
            "REMOTE_MODE": "true",
            "LLAMA_CPP_SERVER_URL": ""
        }, clear=True):
            with self.assertRaises(ValueError) as cm:
                Config()
            self.assertIn("LLAMA_CPP_SERVER_URL", str(cm.exception))

    def test_remote_mode_with_whitespace_url_raises(self) -> None:
        """Test that remote mode with whitespace-only URL raises ValueError."""
        with patch.dict(os.environ, {
            "REMOTE_MODE": "true",
            "LLAMA_CPP_SERVER_URL": "   "
        }, clear=True):
            with self.assertRaises(ValueError) as cm:
                Config()
            self.assertIn("LLAMA_CPP_SERVER_URL", str(cm.exception))

    def test_local_mode_with_custom_url(self) -> None:
        """Test local mode with a custom URL (should still be accessible)."""
        with patch.dict(os.environ, {
            "REMOTE_MODE": "false",
            "LLAMA_CPP_SERVER_URL": "http://custom.local:8000"
        }, clear=True):
            cfg = Config()
            self.assertFalse(cfg.is_remote_mode)
            self.assertEqual(cfg.remote_url, "http://custom.local:8000")

    def test_remote_mode_exact_url_preservation(self) -> None:
        """Test that URL whitespace is stripped correctly."""
        with patch.dict(os.environ, {
            "REMOTE_MODE": "true",
            "LLAMA_CPP_SERVER_URL": "  http://example.com:8080  "
        }, clear=True):
            cfg = Config()
            self.assertTrue(cfg.is_remote_mode)
            self.assertEqual(cfg.remote_url, "http://example.com:8080")


class TestNormalizePathForBinary(unittest.TestCase):
    """Tests for the path normalisation used for llama.cpp binary arguments."""

    WINDOWS_BINARY = '/mnt/c/llama.cpp-vulkan/bin/llama-server.exe'
    LINUX_BINARY = '/usr/local/bin/llama-server'

    def test_wsl_mount_path_becomes_windows_path(self) -> None:
        """A WSL mount path is converted when a Windows binary is used.

        llama-server.exe runs in a Windows context, so /mnt/c/... paths have
        to be handed over as C:/...
        """
        from config import normalize_path_for_binary

        with patch('config.WSL', True), \
             patch('config.LLAMA_SERVER', self.WINDOWS_BINARY), \
             patch('config.LLAMA_BENCH', None), \
             patch('config.LLAMA_BATCHED_BENCH', None):
            self.assertEqual(
                normalize_path_for_binary('/mnt/c/llama.cpp-models/draft.gguf'),
                'C:/llama.cpp-models/draft.gguf',
            )

    def test_native_linux_binary_keeps_posix_path(self) -> None:
        """With a native Linux binary the path stays in POSIX form."""
        from config import normalize_path_for_binary

        with patch('config.WSL', True), \
             patch('config.LLAMA_SERVER', self.LINUX_BINARY), \
             patch('config.LLAMA_BENCH', None), \
             patch('config.LLAMA_BATCHED_BENCH', None):
            self.assertEqual(
                normalize_path_for_binary('/mnt/c/llama.cpp-models/draft.gguf'),
                '/mnt/c/llama.cpp-models/draft.gguf',
            )

    def test_windows_path_keeps_windows_form(self) -> None:
        """A Windows path stays a Windows path, with forward slashes."""
        from config import normalize_path_for_binary

        self.assertEqual(
            normalize_path_for_binary('C:/llama.cpp-models/draft.gguf'),
            'C:/llama.cpp-models/draft.gguf',
        )
        self.assertEqual(
            normalize_path_for_binary('C:\\llama.cpp-models\\draft.gguf'),
            'C:/llama.cpp-models/draft.gguf',
        )

    def test_empty_path_is_unchanged(self) -> None:
        """An empty path (no draft model) is left alone."""
        from config import normalize_path_for_binary

        self.assertEqual(normalize_path_for_binary(''), '')


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""
Unit tests for the llama-server command line built by processes.build_cmd.

Focus: RoPE / YaRN parameter handling.

llama.cpp only applies the RoPE scaling parameters when a scaling method is
active. When a model's "RoPE Scaling Type" is "none", none of the other
rope/yarn parameters may be added to the model start command line.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from webapp.processes import build_cmd


def _rope_flags(cmd: list[str]) -> set[str]:
    """Return the set of rope/yarn flags present in a command."""
    return {arg for arg in cmd if arg.startswith(("--rope", "--yarn"))}


class TestRopeScalingCommandLine(unittest.TestCase):
    """Test suite for RoPE/YaRN flag emission in build_cmd."""

    def setUp(self) -> None:
        """Ensure build_cmd runs in local mode for each test."""
        env_patch = patch.dict(os.environ, {})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        os.environ.pop("REMOTE_MODE", None)

    def _meta(self, **overrides) -> dict:
        """Build a meta dict with defaults, overridden by the given kwargs."""
        meta = {
            "model_path": "models/test-model.gguf",
            "threads": 4,
            "threads_batch": 4,
            "timeout": 60,
            "rope_scaling_type": "none",
            "rope_freq_base": 10000.0,
            "rope_freq_scale": 1.0,
            "rope_scale": 1.0,
            "yarn_orig_ctx": 0,
            "yarn_ext_factor": -1.0,
            "yarn_attn_factor": 1.0,
            "yarn_beta_slow": 1.0,
            "yarn_beta_fast": 32.0,
        }
        meta.update(overrides)
        return meta

    def test_none_omits_all_rope_and_yarn_params(self) -> None:
        """No rope/yarn flag is emitted when the scaling type is none."""
        meta = self._meta(
            rope_scaling_type="none",
            rope_freq_base=10000000.0,
            rope_freq_scale=0.5,
            rope_scale=2.0,
            yarn_orig_ctx=32768,
            yarn_ext_factor=1.0,
            yarn_attn_factor=1.5,
            yarn_beta_slow=32.0,
            yarn_beta_fast=1.0,
        )
        cmd = build_cmd("test-model", 8080, meta)
        self.assertEqual(_rope_flags(cmd), set())
        self.assertNotIn("--rope-scaling", cmd)

    def test_none_omits_stale_yarn_params(self) -> None:
        """Leftover YaRN values from a previous config are not emitted."""
        meta = self._meta(
            rope_scaling_type="none",
            yarn_orig_ctx=32768,
            yarn_ext_factor=1.0,
            yarn_beta_slow=32.0,
            yarn_beta_fast=1.0,
        )
        cmd = build_cmd("test-model", 8080, meta)
        self.assertEqual(_rope_flags(cmd), set())

    def test_none_omits_rope_freq_params(self) -> None:
        """RoPE frequency parameters are omitted when scaling type is none."""
        meta = self._meta(
            rope_scaling_type="none",
            rope_freq_base=1000000.0,
            rope_freq_scale=0.25,
        )
        cmd = build_cmd("test-model", 8080, meta)
        self.assertEqual(_rope_flags(cmd), set())

    def test_missing_scaling_type_defaults_to_none(self) -> None:
        """A model without a scaling type is treated as none."""
        meta = self._meta(rope_scale=4.0, yarn_orig_ctx=32768)
        del meta["rope_scaling_type"]
        cmd = build_cmd("test-model", 8080, meta)
        self.assertEqual(_rope_flags(cmd), set())

    def test_scaling_type_is_case_and_whitespace_insensitive(self) -> None:
        """'NONE' / ' none ' are treated as none, 'YARN' as yarn."""
        for value in ("NONE", " none ", "None"):
            with self.subTest(value=value):
                cmd = build_cmd("test-model", 8080, self._meta(rope_scaling_type=value))
                self.assertEqual(_rope_flags(cmd), set())

        cmd = build_cmd("test-model", 8080, self._meta(rope_scaling_type="YARN"))
        self.assertEqual(cmd[cmd.index("--rope-scaling") + 1], "yarn")

    def test_yarn_adds_rope_and_yarn_params(self) -> None:
        """A yarn scaling type emits the rope and yarn parameters."""
        meta = self._meta(
            rope_scaling_type="yarn",
            rope_freq_base=1000000.0,
            rope_freq_scale=0.5,
            rope_scale=8.0,
            yarn_orig_ctx=32768,
            yarn_ext_factor=1.0,
            yarn_attn_factor=1.5,
            yarn_beta_slow=32.0,
            yarn_beta_fast=1.0,
        )
        cmd = build_cmd("test-model", 8080, meta)
        self.assertEqual(cmd[cmd.index("--rope-scaling") + 1], "yarn")
        self.assertEqual(
            _rope_flags(cmd),
            {
                "--rope-scaling",
                "--rope-freq-base",
                "--rope-freq-scale",
                "--rope-scale",
                "--yarn-orig-ctx",
                "--yarn-ext-factor",
                "--yarn-attn-factor",
                "--yarn-beta-slow",
                "--yarn-beta-fast",
            },
        )

    def test_linear_adds_rope_scaling_flag(self) -> None:
        """A linear scaling type emits the rope scaling flag and its params."""
        meta = self._meta(rope_scaling_type="linear", rope_scale=2.0)
        cmd = build_cmd("test-model", 8080, meta)
        self.assertEqual(cmd[cmd.index("--rope-scaling") + 1], "linear")
        self.assertEqual(_rope_flags(cmd), {"--rope-scaling", "--rope-scale"})


class TestContextSizeUnits(unittest.TestCase):
    """Context-related flags are passed to llama-server in tokens."""

    def setUp(self) -> None:
        """Ensure build_cmd runs in local mode for each test."""
        env_patch = patch.dict(os.environ, {})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        os.environ.pop("REMOTE_MODE", None)

    def _meta(self, **overrides) -> dict:
        """Build a meta dict with defaults, overridden by the given kwargs."""
        meta = {
            "model_path": "models/test-model.gguf",
            "threads": 4,
            "threads_batch": 4,
            "timeout": 60,
            "rope_scaling_type": "none",
            "rope_freq_base": 10000.0,
            "rope_freq_scale": 1.0,
            "rope_scale": 1.0,
            "yarn_orig_ctx": 0,
            "yarn_ext_factor": -1.0,
            "yarn_attn_factor": 1.0,
            "yarn_beta_slow": 1.0,
            "yarn_beta_fast": 32.0,
        }
        meta.update(overrides)
        return meta

    def test_ctx_size_is_passed_in_tokens(self) -> None:
        """-c must be the persisted token count, not bytes."""
        meta = self._meta(ctx_size=131072)
        cmd = build_cmd("test-model", 8080, meta)
        self.assertEqual(cmd[cmd.index("-c") + 1], "131072")

    def test_ctx_size_is_not_scaled(self) -> None:
        """A 128K token context must not become 134217728."""
        meta = self._meta(ctx_size=131072)
        cmd = build_cmd("test-model", 8080, meta)
        self.assertNotIn("134217728", cmd)

    def test_non_standard_ctx_size_is_passed_through(self) -> None:
        """Arbitrary persisted token counts are passed through unchanged."""
        meta = self._meta(ctx_size=128000)
        cmd = build_cmd("test-model", 8080, meta)
        self.assertEqual(cmd[cmd.index("-c") + 1], "128000")

    def test_yarn_orig_ctx_is_passed_in_tokens(self) -> None:
        """--yarn-orig-ctx must be the persisted token count, not bytes."""
        meta = self._meta(rope_scaling_type="yarn", yarn_orig_ctx=32768)
        cmd = build_cmd("test-model", 8080, meta)
        self.assertEqual(cmd[cmd.index("--yarn-orig-ctx") + 1], "32768")

    def test_yarn_orig_ctx_zero_is_omitted(self) -> None:
        """A zero yarn original context means 'use the model default'."""
        meta = self._meta(rope_scaling_type="yarn", yarn_orig_ctx=0)
        cmd = build_cmd("test-model", 8080, meta)
        self.assertNotIn("--yarn-orig-ctx", cmd)


if __name__ == "__main__":
    unittest.main()

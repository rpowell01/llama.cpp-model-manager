#!/usr/bin/env python3
"""
Unit tests for benchmarks module.

Tests cover:
- BenchmarkRunner initialization and binary discovery
- run_llama_bench with mocked subprocess
- run_llama_batched_bench with mocked subprocess
- parse_output for JSON and text formats
- Error handling (missing binaries, timeouts, execution errors)
- Configuration dict to BenchmarkConfig conversion
"""

import json
import os
import tempfile
import unittest
from pathlib import Path


from benchmarks import (
    BenchmarkConfig,
    BenchmarkError,
    BinaryNotFoundError,
    run_llama_batched_bench,
)


class TestBenchmarkConfig(unittest.TestCase):
    """Tests for BenchmarkConfig dataclass."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        config = BenchmarkConfig(model_path="/path/to/model.gguf")

        self.assertEqual(config.n_gpu_layers, 99)
        self.assertEqual(config.batch_size, 4096)
        self.assertEqual(config.ubatch_size, 1024)
        self.assertEqual(config.threads, 16)
        self.assertEqual(config.threads_batch, 16)
        self.assertEqual(config.cache_type_k, "q4_0")
        self.assertEqual(config.cache_type_v, "q4_0")
        self.assertEqual(config.flash_attn, "auto")
        self.assertEqual(config.numa, True)
        self.assertEqual(config.numa_value, "distribute")
        self.assertEqual(config.cpu_affinity, "auto")
        self.assertEqual(config.cpu_strict, False)
        self.assertEqual(config.ctx_size, 128000)
        self.assertEqual(config.parallel, 1)
        self.assertEqual(config.spec_type, "none")
        self.assertEqual(config.spec_draft_n_max, 8)
        self.assertEqual(config.repetitions, 3)
        self.assertEqual(config.timeout_sec, 30)
        self.assertEqual(config.output_format, "json")

    def test_custom_values(self) -> None:
        """Test custom configuration values."""
        config = BenchmarkConfig(
            model_path="/path/to/model.gguf",
            n_gpu_layers=50,
            batch_size=2048,
            threads=8,
            cache_type_k="fp16",
            flash_attn="off",
        )

        self.assertEqual(config.n_gpu_layers, 50)
        self.assertEqual(config.batch_size, 2048)
        self.assertEqual(config.threads, 8)
        self.assertEqual(config.cache_type_k, "fp16")
        self.assertEqual(config.flash_attn, "off")


class TestBenchmarkRunnerInit(unittest.TestCase):
    """Tests for BenchmarkRunner initialization and binary discovery."""

    @patch("config.BASE_DIR", new_callable=lambda: Path("/fake/base"))
    @patch("config.LLAMA_SERVER", None)
    @patch("config.WINDOWS", False)
    @patch("config.WSL", False)
    def test_init_with_explicit_dir(self, _mock_base_dir: Mock) -> None:
        """Test initialization with explicit directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            bench_bin = tmpdir_path / "llama-bench"
            batched_bin = tmpdir_path / "llama-batched-bench"
            bench_bin.touch(mode=0o755)
            batched_bin.touch(mode=0o755)

            runner = BenchmarkRunner(llama_server_dir=tmpdir)

            self.assertTrue(runner.has_llama_bench)
            self.assertTrue(runner.has_llama_batched_bench)
            self.assertEqual(runner._bench_binary, bench_bin)
            self.assertEqual(runner._batched_bench_binary, batched_bin)

    @patch("config.BASE_DIR", new_callable=lambda: Path("/fake/base"))
    @patch("config.LLAMA_SERVER", None)
    @patch("config.WINDOWS", False)
    @patch("config.WSL", False)
    def test_init_missing_binaries(self, _mock_base_dir: Mock) -> None:
        """Test initialization when binaries are missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = BenchmarkRunner(llama_server_dir=tmpdir)

            self.assertFalse(runner.has_llama_bench)
            self.assertFalse(runner.has_llama_batched_bench)

    @patch("config.BASE_DIR", new_callable=lambda: Path("/fake/base"))
    @patch("config.LLAMA_SERVER", None)
    @patch("config.WINDOWS", True)
    @patch("config.WSL", False)
    def test_init_windows_binaries(self, _mock_base_dir: Mock) -> None:
        """Test initialization finds .exe binaries on Windows."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            bench_bin = tmpdir_path / "llama-bench.exe"
            batched_bin = tmpdir_path / "llama-batched-bench.exe"
            bench_bin.touch(mode=0o755)
            batched_bin.touch(mode=0o755)

            runner = BenchmarkRunner(llama_server_dir=tmpdir)

            self.assertTrue(runner.has_llama_bench)
            self.assertTrue(runner.has_llama_batched_bench)

    @patch("config.WINDOWS", False)
    @patch("config.WSL", False)
    def test_find_bench_binaries(self) -> None:
        """Test find_bench_binaries method."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            bench_bin = tmpdir_path / "llama-bench"
            bench_bin.touch(mode=0o755)

            runner = BenchmarkRunner(llama_server_dir=tmpdir)
            result = runner.find_bench_binaries()

            self.assertEqual(result["llama_bench"], bench_bin)
            self.assertIsNone(result["llama_batched_bench"])


class TestBenchmarkRunnerCommands(unittest.TestCase):
    """Tests for command building."""

    def setUp(self) -> None:
        self.runner = BenchmarkRunner(llama_server_dir="/fake/dir")
        self.runner._bench_binary = Path("/fake/dir/llama-bench")
        self.runner._batched_bench_binary = Path("/fake/dir/llama-batched-bench")

    def test_build_bench_command_basic(self) -> None:
        """Test basic command building for llama-bench."""
        config = BenchmarkConfig(
            model_path="/models/model.gguf",
            n_gpu_layers=50,
            batch_size=2048,
            threads=8,
        )

        cmd = self.runner._build_bench_command(
            Path("/fake/dir/llama-bench"),
            config,
            is_batched=False,
        )

        self.assertEqual(cmd[0], "/fake/dir/llama-bench")
        self.assertIn("-m", cmd)
        self.assertIn("/models/model.gguf", cmd)
        self.assertIn("-ngl", cmd)
        self.assertIn("50", cmd)
        self.assertIn("-b", cmd)
        self.assertIn("2048", cmd)
        self.assertIn("-t", cmd)
        self.assertIn("8", cmd)
        self.assertIn("--output", cmd)
        self.assertIn("json", cmd)

    def test_build_batched_bench_command(self) -> None:
        """Test command building for llama-batched-bench."""
        config = BenchmarkConfig(
            model_path="/models/model.gguf",
            n_gpu_layers=0,
            batch_size=512,
        )

        cmd = self.runner._build_bench_command(
            Path("/fake/dir/llama-batched-bench"),
            config,
            is_batched=True,
        )

        self.assertEqual(cmd[0], "/fake/dir/llama-batched-bench")
        self.assertIn("--output", cmd)
        self.assertIn("json", cmd)

    def test_build_command_with_numa(self) -> None:
        """Test command includes NUMA options."""
        config = BenchmarkConfig(
            model_path="/models/model.gguf",
            numa=True,
            numa_value="isolate",
        )

        cmd = self.runner._build_bench_command(
            Path("/fake/dir/llama-bench"),
            config,
            is_batched=False,
        )

        self.assertIn("--numa", cmd)
        self.assertIn("isolate", cmd)

    def test_build_command_numa_disabled(self) -> None:
        """Test command excludes NUMA when disabled."""
        config = BenchmarkConfig(
            model_path="/models/model.gguf",
            numa=False,
        )

        cmd = self.runner._build_bench_command(
            Path("/fake/dir/llama-bench"),
            config,
            is_batched=False,
        )

        self.assertNotIn("--numa", cmd)

    def test_build_command_with_speculative(self) -> None:
        """Test command includes speculative decoding options."""
        config = BenchmarkConfig(
            model_path="/models/model.gguf",
            spec_type="ngram-simple",
            spec_draft_n_max=5,
        )

        cmd = self.runner._build_bench_command(
            Path("/fake/dir/llama-bench"),
            config,
            is_batched=False,
        )

        self.assertIn("--spec-type", cmd)
        self.assertIn("ngram-simple", cmd)
        self.assertIn("--spec-draft-n-max", cmd)
        self.assertIn("5", cmd)

    def test_build_command_spec_type_none(self) -> None:
        """Test command excludes speculative when type is none."""
        config = BenchmarkConfig(
            model_path="/models/model.gguf",
            spec_type="none",
        )

        cmd = self.runner._build_bench_command(
            Path("/fake/dir/llama-bench"),
            config,
            is_batched=False,
        )

        self.assertNotIn("--spec-type", cmd)

    def test_build_command_parallel_auto(self) -> None:
        """Test command with parallel=auto uses --kv-unified."""
        config = BenchmarkConfig(
            model_path="/models/model.gguf",
            parallel="auto",
        )

        cmd = self.runner._build_bench_command(
            Path("/fake/dir/llama-bench"),
            config,
            is_batched=False,
        )

        self.assertIn("--kv-unified", cmd)

    def test_build_command_parallel_int(self) -> None:
        """Test command with parallel=int uses -p."""
        config = BenchmarkConfig(
            model_path="/models/model.gguf",
            parallel=4,
        )

        cmd = self.runner._build_bench_command(
            Path("/fake/dir/llama-bench"),
            config,
            is_batched=False,
        )

        self.assertIn("-p", cmd)
        self.assertIn("4", cmd)

    def test_build_command_parallel_one(self) -> None:
        """Test command with parallel=1 uses neither -p nor --kv-unified."""
        config = BenchmarkConfig(
            model_path="/models/model.gguf",
            parallel=1,
        )

        cmd = self.runner._build_bench_command(
            Path("/fake/dir/llama-bench"),
            config,
            is_batched=False,
        )

        self.assertNotIn("-p", cmd)
        self.assertNotIn("--kv-unified", cmd)

    def test_build_command_cpu_affinity(self) -> None:
        """Test command includes CPU affinity when set."""
        config = BenchmarkConfig(
            model_path="/models/model.gguf",
            cpu_affinity="0,1,2,3",
        )

        cmd = self.runner._build_bench_command(
            Path("/fake/dir/llama-bench"),
            config,
            is_batched=False,
        )

        self.assertIn("--cpu-affinity", cmd)
        self.assertIn("0,1,2,3", cmd)

    def test_build_command_cpu_affinity_auto(self) -> None:
        """Test command excludes CPU affinity when auto."""
        config = BenchmarkConfig(
            model_path="/models/model.gguf",
            cpu_affinity="auto",
        )

        cmd = self.runner._build_bench_command(
            Path("/fake/dir/llama-bench"),
            config,
            is_batched=False,
        )

        self.assertNotIn("--cpu-affinity", cmd)

    def test_build_command_cpu_strict(self) -> None:
        """Test command includes --cpu-strict when enabled."""
        config = BenchmarkConfig(
            model_path="/models/model.gguf",
            cpu_strict=True,
        )

        cmd = self.runner._build_bench_command(
            Path("/fake/dir/llama-bench"),
            config,
            is_batched=False,
        )

        self.assertIn("--cpu-strict", cmd)

    def test_build_command_swa_full(self) -> None:
        """Test command includes --swa-full when enabled."""
        config = BenchmarkConfig(
            model_path="/models/model.gguf",
            swa_full=True,
        )

        cmd = self.runner._build_bench_command(
            Path("/fake/dir/llama-bench"),
            config,
            is_batched=False,
        )

        self.assertIn("--swa-full", cmd)

    def test_build_command_cache_reuse_zero(self) -> None:
        """Test command excludes --cache-reuse when zero."""
        config = BenchmarkConfig(
            model_path="/models/model.gguf",
            cache_reuse=0,
        )

        cmd = self.runner._build_bench_command(
            Path("/fake/dir/llama-bench"),
            config,
            is_batched=False,
        )

        self.assertNotIn("--cache-reuse", cmd)

    def test_build_command_cache_ram_zero(self) -> None:
        """Test command excludes --cache-ram when zero."""
        config = BenchmarkConfig(
            model_path="/models/model.gguf",
            cache_ram=0,
        )

        cmd = self.runner._build_bench_command(
            Path("/fake/dir/llama-bench"),
            config,
            is_batched=False,
        )

        self.assertNotIn("--cache-ram", cmd)

    def test_build_command_output_format_text(self) -> None:
        """Test command excludes --output json when format is text."""
        config = BenchmarkConfig(
            model_path="/models/model.gguf",
            output_format="text",
        )

        cmd = self.runner._build_bench_command(
            Path("/fake/dir/llama-bench"),
            config,
            is_batched=False,
        )

        self.assertNotIn("--output", cmd)

    def test_build_command_extra_args(self) -> None:
        """Test command includes extra args."""
        config = BenchmarkConfig(
            model_path="/models/model.gguf",
            extra_args=["--verbose", "--debug"],
        )

        cmd = self.runner._build_bench_command(
            Path("/fake/dir/llama-bench"),
            config,
            is_batched=False,
        )

        self.assertIn("--verbose", cmd)
        self.assertIn("--debug", cmd)


class TestParseOutput(unittest.TestCase):
    """Tests for parse_output method."""

    def test_parse_regular_json_complete(self) -> None:
        """Test parsing complete llama-bench JSON output."""
        json_output = json.dumps({
            "pp_avg": 1500.5,
            "tg_avg": 85.3,
            "pp_std": 12.4,
            "tg_std": 2.1,
            "latency_p50": 11.5,
            "latency_p90": 15.2,
            "latency_p99": 22.8,
            "mem_used_mb": 4096,
            "n_tokens": 128,
            "n_batch": 512,
            "n_threads": 16,
        })

        result = BenchmarkRunner.parse_output(json_output, "json", is_batched=False)

        self.assertEqual(result["pp_avg"], 1500.5)
        self.assertEqual(result["tg_avg"], 85.3)
        self.assertEqual(result["pp_std"], 12.4)
        self.assertEqual(result["tg_std"], 2.1)
        self.assertEqual(result["latency_p50"], 11.5)
        self.assertEqual(result["latency_p90"], 15.2)
        self.assertEqual(result["latency_p99"], 22.8)
        self.assertEqual(result["memory_usage_mb"], 4096)
        self.assertEqual(result["n_tokens"], 128)

    def test_parse_regular_json_partial(self) -> None:
        """Test parsing partial llama-bench JSON output."""
        json_output = json.dumps({
            "pp_avg": 1000.0,
            "tg_avg": 50.0,
        })

        result = BenchmarkRunner.parse_output(json_output, "json", is_batched=False)

        self.assertEqual(result["pp_avg"], 1000.0)
        self.assertEqual(result["tg_avg"], 50.0)
        self.assertNotIn("latency_p50", result)

    def test_parse_batched_json_complete(self) -> None:
        """Test parsing complete llama-batched-bench JSON output."""
        json_output = json.dumps({
            "throughput": 5000.0,
            "latency_p50": 10.5,
            "latency_p90": 14.2,
            "latency_p99": 20.1,
            "memory_mb": 8192,
            "n_sequences": 4,
            "batch_size": 2048,
            "results": [
                {"parallel": 1, "throughput": 1200.0, "latency_p50": 10.5, "latency_p99": 20.1},
                {"parallel": 2, "throughput": 2300.0, "latency_p50": 11.0, "latency_p99": 21.0},
                {"parallel": 4, "throughput": 5000.0, "latency_p50": 10.5, "latency_p99": 20.1},
            ],
        })

        result = BenchmarkRunner.parse_output(json_output, "json", is_batched=True)

        self.assertEqual(result["batch_throughput"], 5000.0)
        self.assertEqual(result["latency_p50"], 10.5)
        self.assertEqual(result["latency_p99"], 20.1)
        self.assertEqual(result["memory_usage_mb"], 8192)
        self.assertEqual(len(result["parallel_results"]), 3)
        self.assertEqual(result["parallel_results"][0]["parallel"], 1)

    def test_parse_invalid_json(self) -> None:
        """Test parsing invalid JSON returns empty dict."""
        result = BenchmarkRunner.parse_output("not json at all", "json", is_batched=False)
        self.assertEqual(result, {})

    def test_parse_mixed_output_with_json(self) -> None:
        """Test parsing output with log lines and JSON."""
        output = """Starting benchmark...
Running with config: ...
{"pp_avg": 1200.0, "tg_avg": 60.0}
Benchmark complete."""

        result = BenchmarkRunner.parse_output(output, "json", is_batched=False)

        self.assertEqual(result["pp_avg"], 1200.0)
        self.assertEqual(result["tg_avg"], 60.0)

    def test_parse_text_output(self) -> None:
        """Test parsing text output (fallback)."""
        text_output = """
pp avg: 1000.5 t/s
tg avg: 55.2 t/s
p50 = 12.3 ms
p99 = 25.6 ms
"""

        result = BenchmarkRunner.parse_output(text_output, "text", is_batched=False)

        self.assertEqual(result["pp_avg"], 1000.5)
        self.assertEqual(result["tg_avg"], 55.2)
        self.assertEqual(result["latency_p50"], 12.3)
        self.assertEqual(result["latency_p99"], 25.6)

    def test_parse_empty_output(self) -> None:
        """Test parsing empty output returns empty dict."""
        result = BenchmarkRunner.parse_output("", "json", is_batched=False)
        self.assertEqual(result, {})

        result = BenchmarkRunner.parse_output("   ", "json", is_batched=False)
        self.assertEqual(result, {})


class TestRunLlamaBench(unittest.TestCase):
    """Tests for run_llama_bench method."""

    def setUp(self) -> None:
        self.runner = BenchmarkRunner(llama_server_dir="/fake/dir")
        self.runner._bench_binary = Path("/fake/dir/llama-bench")

    @patch("benchmarks.tempfile.TemporaryDirectory")
    @patch("benchmarks.subprocess.run")
    def test_run_llama_bench_success(self, mock_run: Mock, mock_tempdir: Mock) -> None:
        """Test successful llama-bench execution."""
        # Setup mock temp dir
        mock_tempdir.return_value.__enter__.return_value = "/tmp/test_bench"

        # Setup mock subprocess result
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({
            "pp_avg": 1500.0,
            "tg_avg": 80.0,
            "latency_p50": 12.0,
            "latency_p99": 25.0,
        })
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        # Create a fake model file
        with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as f:
            model_path = f.name

        try:
            result = self.runner.run_llama_bench(model_path, {"repetitions": 1})

            self.assertEqual(result["pp_avg"], 1500.0)
            self.assertEqual(result["tg_avg"], 80.0)
            self.assertEqual(result["latency_p50"], 12.0)
            self.assertEqual(result["latency_p99"], 25.0)
            self.assertEqual(result["repetitions_completed"], 1)
        finally:
            os.unlink(model_path)

    @patch("benchmarks.tempfile.TemporaryDirectory")
    @patch("benchmarks.subprocess.run")
    def test_run_llama_bench_multiple_repetitions_median(
        self, mock_run: Mock, mock_tempdir: Mock
    ) -> None:
        """Test median aggregation across multiple repetitions."""
        mock_tempdir.return_value.__enter__.return_value = "/tmp/test_bench"

        # Return different values for each repetition
        outputs = [
            json.dumps({"pp_avg": 1000.0, "tg_avg": 50.0}),
            json.dumps({"pp_avg": 1500.0, "tg_avg": 80.0}),
            json.dumps({"pp_avg": 1200.0, "tg_avg": 65.0}),
        ]

        mock_results = []
        for out in outputs:
            mr = Mock()
            mr.returncode = 0
            mr.stdout = out
            mr.stderr = ""
            mock_results.append(mr)

        mock_run.side_effect = mock_results

        with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as f:
            model_path = f.name

        try:
            result = self.runner.run_llama_bench(model_path, {"repetitions": 3})

            # Median of [1000, 1200, 1500] = 1200
            # Median of [50, 65, 80] = 65
            self.assertEqual(result["pp_avg"], 1200.0)
            self.assertEqual(result["tg_avg"], 65.0)
            self.assertEqual(result["repetitions_completed"], 3)
        finally:
            os.unlink(model_path)

    @patch("benchmarks.tempfile.TemporaryDirectory")
    @patch("benchmarks.subprocess.run")
    def test_run_llama_bench_timeout(self, mock_run: Mock, mock_tempdir: Mock) -> None:
        """Test llama-bench timeout handling."""
        mock_tempdir.return_value.__enter__.return_value = "/tmp/test_bench"

        import subprocess as sp
        mock_run.side_effect = sp.TimeoutExpired(cmd=["llama-bench"], timeout=30)

        with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as f:
            model_path = f.name

        try:
            with self.assertRaises(BenchmarkError) as cm:
                self.runner.run_llama_bench(model_path, {"repetitions": 1, "timeout_sec": 30})

            self.assertIn("timed out", str(cm.exception).lower())
        finally:
            os.unlink(model_path)

    @patch("benchmarks.tempfile.TemporaryDirectory")
    @patch("benchmarks.subprocess.run")
    def test_run_llama_bench_execution_error(
        self, mock_run: Mock, mock_tempdir: Mock
    ) -> None:
        """Test llama-bench execution error handling."""
        mock_tempdir.return_value.__enter__.return_value = "/tmp/test_bench"

        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error: model not found"
        mock_run.return_value = mock_result

        with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as f:
            model_path = f.name

        try:
            with self.assertRaises(BenchmarkError) as cm:
                self.runner.run_llama_bench(model_path, {"repetitions": 1})

            self.assertIn("failed", str(cm.exception).lower())
        finally:
            os.unlink(model_path)

    def test_run_llama_bench_missing_binary(self) -> None:
        """Test BinaryNotFoundError when llama-bench binary is missing."""
        runner = BenchmarkRunner(llama_server_dir="/fake/dir")
        runner._bench_binary = None

        with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as f:
            model_path = f.name

        try:
            with self.assertRaises(BinaryNotFoundError) as cm:
                runner.run_llama_bench(model_path)

            self.assertIn("not found", str(cm.exception).lower())
        finally:
            os.unlink(model_path)

    def test_run_llama_bench_missing_model(self) -> None:
        """Test BenchmarkError when model file is missing."""
        with self.assertRaises(BenchmarkError) as cm:
            self.runner.run_llama_bench("/nonexistent/model.gguf")

        self.assertIn("not found", str(cm.exception).lower())

    def test_run_llama_bench_repetitions_override(self) -> None:
        """Test repetitions parameter overrides config_dict."""
        self.runner._bench_binary = Path("/fake/dir/llama-bench")

        with patch("benchmarks.tempfile.TemporaryDirectory") as mock_tempdir:
            mock_tempdir.return_value.__enter__.return_value = "/tmp/test_bench"

            mock_result = Mock()
            mock_result.returncode = 0
            mock_result.stdout = json.dumps({"pp_avg": 1000.0, "tg_avg": 50.0})
            mock_result.stderr = ""

            with patch("benchmarks.subprocess.run", return_value=mock_result) as mock_run:
                with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as f:
                    model_path = f.name

                try:
                    # Pass repetitions=2 but config_dict has repetitions=5
                    # Should use 2
                    self.runner.run_llama_bench(
                        model_path,
                        {"repetitions": 5},
                        repetitions=2,
                    )
                    # Called 2 times
                    self.assertEqual(mock_run.call_count, 2)
                finally:
                    os.unlink(model_path)


class TestRunLlamaBatchedBench(unittest.TestCase):
    """Tests for run_llama_batched_bench method."""

    def setUp(self) -> None:
        self.runner = BenchmarkRunner(llama_server_dir="/fake/dir")
        self.runner._batched_bench_binary = Path("/fake/dir/llama-batched-bench")

    @patch("benchmarks.tempfile.TemporaryDirectory")
    @patch("benchmarks.subprocess.run")
    def test_run_llama_batched_bench_success(self, mock_run: Mock, mock_tempdir: Mock) -> None:
        """Test successful llama-batched-bench execution."""
        mock_tempdir.return_value.__enter__.return_value = "/tmp/test_bench"

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({
            "throughput": 5000.0,
            "latency_p50": 10.0,
            "latency_p99": 20.0,
            "memory_mb": 8192,
        })
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as f:
            model_path = f.name

        try:
            result = self.runner.run_llama_batched_bench(model_path, {"repetitions": 1})

            self.assertEqual(result["batch_throughput"], 5000.0)
            self.assertEqual(result["latency_p50"], 10.0)
            self.assertEqual(result["latency_p99"], 20.0)
            self.assertEqual(result["memory_usage_mb"], 8192)
        finally:
            os.unlink(model_path)

    @patch("benchmarks.tempfile.TemporaryDirectory")
    @patch("benchmarks.subprocess.run")
    def test_run_llama_batched_bench_timeout(self, mock_run: Mock, mock_tempdir: Mock) -> None:
        """Test llama-batched-bench timeout handling."""
        mock_tempdir.return_value.__enter__.return_value = "/tmp/test_bench"

        import subprocess as sp
        mock_run.side_effect = sp.TimeoutExpired(cmd=["llama-batched-bench"], timeout=30)

        with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as f:
            model_path = f.name

        try:
            with self.assertRaises(BenchmarkError) as cm:
                self.runner.run_llama_batched_bench(model_path, {"timeout_sec": 30})

            self.assertIn("timed out", str(cm.exception).lower())
        finally:
            os.unlink(model_path)

    @patch("benchmarks.tempfile.TemporaryDirectory")
    @patch("benchmarks.subprocess.run")
    def test_run_llama_batched_bench_execution_error(
        self, mock_run: Mock, mock_tempdir: Mock
    ) -> None:
        """Test llama-batched-bench execution error handling."""
        mock_tempdir.return_value.__enter__.return_value = "/tmp/test_bench"

        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error: invalid model"
        mock_run.return_value = mock_result

        with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as f:
            model_path = f.name

        try:
            with self.assertRaises(BenchmarkError) as cm:
                self.runner.run_llama_batched_bench(model_path)

            self.assertIn("failed", str(cm.exception).lower())
        finally:
            os.unlink(model_path)

    def test_run_llama_batched_bench_missing_binary(self) -> None:
        """Test BinaryNotFoundError when llama-batched-bench binary is missing."""
        runner = BenchmarkRunner(llama_server_dir="/fake/dir")
        runner._batched_bench_binary = None

        with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as f:
            model_path = f.name

        try:
            with self.assertRaises(BinaryNotFoundError) as cm:
                runner.run_llama_batched_bench(model_path)

            self.assertIn("not found", str(cm.exception).lower())
        finally:
            os.unlink(model_path)

    def test_run_llama_batched_bench_missing_model(self) -> None:
        """Test BenchmarkError when model file is missing."""
        with self.assertRaises(BenchmarkError) as cm:
            self.runner.run_llama_batched_bench("/nonexistent/model.gguf")

        self.assertIn("not found", str(cm.exception).lower())

    def test_run_llama_batched_bench_multiple_repetitions_median(
        self
    ) -> None:
        """Test median aggregation across multiple repetitions for batched bench."""
        with patch("benchmarks.tempfile.TemporaryDirectory") as mock_tempdir:
            mock_tempdir.return_value.__enter__.return_value = "/tmp/test_bench"

            outputs = [
                json.dumps({"throughput": 1000.0, "latency_p50": 20.0}),
                json.dumps({"throughput": 2000.0, "latency_p50": 15.0}),
                json.dumps({"throughput": 1500.0, "latency_p50": 18.0}),
            ]

            mock_results = []
            for out in outputs:
                mr = Mock()
                mr.returncode = 0
                mr.stdout = out
                mr.stderr = ""
                mock_results.append(mr)

            with patch("benchmarks.subprocess.run", side_effect=mock_results):
                with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as f:
                    model_path = f.name

                try:
                    result = self.runner.run_llama_batched_bench(
                        model_path, {"repetitions": 3}
                    )

                    # Median of [1000, 1500, 2000] = 1500
                    # Median of [15, 18, 20] = 18
                    self.assertEqual(result["batch_throughput"], 1500.0)
                    self.assertEqual(result["latency_p50"], 18.0)
                    self.assertEqual(result["repetitions_completed"], 3)
                finally:
                    os.unlink(model_path)


class TestDictToConfig(unittest.TestCase):
    """Tests for _dict_to_config conversion."""

    def test_dict_to_config_defaults(self) -> None:
        """Test conversion with empty dict uses defaults."""
        runner = BenchmarkRunner()
        config = runner._dict_to_config({})

        self.assertEqual(config.n_gpu_layers, 99)
        self.assertEqual(config.batch_size, 4096)
        self.assertEqual(config.repetitions, 3)

    def test_dict_to_config_overrides(self) -> None:
        """Test conversion with custom values."""
        runner = BenchmarkRunner()
        config = runner._dict_to_config({
            "n_gpu_layers": 20,
            "batch_size": 1024,
            "threads": 4,
            "repetitions": 5,
            "extra_args": ["--verbose"],
        })

        self.assertEqual(config.n_gpu_layers, 20)
        self.assertEqual(config.batch_size, 1024)
        self.assertEqual(config.threads, 4)
        self.assertEqual(config.repetitions, 5)
        self.assertEqual(config.extra_args, ["--verbose"])


class TestConvenienceFunctions(unittest.TestCase):
    """Tests for convenience functions."""

    @patch("benchmarks.BenchmarkRunner")
    def test_run_llama_bench_convenience(self, mock_runner_class: Mock) -> None:
        """Test run_llama_bench convenience function."""
        mock_runner = Mock()
        mock_runner.run_llama_bench.return_value = {"pp_avg": 1000.0}
        mock_runner_class.return_value = mock_runner

        result = run_llama_bench("/model.gguf", {"threads": 8}, 2, "/custom/dir")

        self.assertEqual(result["pp_avg"], 1000.0)
        mock_runner_class.assert_called_once_with("/custom/dir")
        mock_runner.run_llama_bench.assert_called_once_with(
            "/model.gguf", {"threads": 8}, 2
        )

    @patch("benchmarks.BenchmarkRunner")
    def test_run_llama_batched_bench_convenience(self, mock_runner_class: Mock) -> None:
        """Test run_llama_batched_bench convenience function."""
        mock_runner = Mock()
        mock_runner.run_llama_batched_bench.return_value = {"batch_throughput": 5000.0}
        mock_runner_class.return_value = mock_runner

        result = run_llama_batched_bench("/model.gguf", {"threads": 8}, 2, "/custom/dir")

        self.assertEqual(result["batch_throughput"], 5000.0)
        mock_runner_class.assert_called_once_with("/custom/dir")
        mock_runner.run_llama_batched_bench.assert_called_once_with(
            "/model.gguf", {"threads": 8}, 2
        )

    @patch("benchmarks.BenchmarkRunner")
    def test_find_bench_binaries_convenience(self, mock_runner_class: Mock) -> None:
        """Test find_bench_binaries convenience function."""
        mock_runner = Mock()
        mock_runner.find_bench_binaries.return_value = {
            "llama_bench": Path("/bin/llama-bench"),
            "llama_batched_bench": None,
        }
        mock_runner_class.return_value = mock_runner

        result = find_bench_binaries("/custom/dir")

        self.assertEqual(result["llama_bench"], Path("/bin/llama-bench"))
        mock_runner_class.assert_called_once_with("/custom/dir")


class TestAggregateMetrics(unittest.TestCase):
    """Tests for _aggregate_metrics method."""

    def test_aggregate_odd_count(self) -> None:
        """Test median with odd number of values."""
        runner = BenchmarkRunner()
        metrics_list = [
            {"pp_avg": 1000.0, "tg_avg": 50.0},
            {"pp_avg": 1500.0, "tg_avg": 80.0},
            {"pp_avg": 1200.0, "tg_avg": 65.0},
        ]

        result = runner._aggregate_metrics(metrics_list)

        self.assertEqual(result["pp_avg"], 1200.0)  # median
        self.assertEqual(result["tg_avg"], 65.0)    # median
        self.assertEqual(result["repetitions_completed"], 3)

    def test_aggregate_even_count(self) -> None:
        """Test median with even number of values."""
        runner = BenchmarkRunner()
        metrics_list = [
            {"pp_avg": 1000.0, "tg_avg": 50.0},
            {"pp_avg": 1500.0, "tg_avg": 80.0},
            {"pp_avg": 1200.0, "tg_avg": 65.0},
            {"pp_avg": 1300.0, "tg_avg": 70.0},
        ]

        result = runner._aggregate_metrics(metrics_list)

        # Median of [1000, 1200, 1300, 1500] = (1200 + 1300) / 2 = 1250
        self.assertEqual(result["pp_avg"], 1250.0)
        # Median of [50, 65, 70, 80] = (65 + 70) / 2 = 67.5
        self.assertEqual(result["tg_avg"], 67.5)
        self.assertEqual(result["repetitions_completed"], 4)

    def test_aggregate_empty(self) -> None:
        """Test aggregation with empty list."""
        runner = BenchmarkRunner()
        result = runner._aggregate_metrics([])
        self.assertEqual(result, {})

    def test_aggregate_with_none_values(self) -> None:
        """Test aggregation ignores None values."""
        runner = BenchmarkRunner()
        metrics_list = [
            {"pp_avg": 1000.0, "tg_avg": 50.0},
            {"pp_avg": 1500.0},  # missing tg_avg
            {"pp_avg": 1200.0, "tg_avg": 65.0},
        ]

        result = runner._aggregate_metrics(metrics_list)

        self.assertEqual(result["pp_avg"], 1200.0)
        # Only two values for tg_avg: [50, 65] -> median = 57.5
        self.assertEqual(result["tg_avg"], 57.5)

    def test_aggregate_all_keys(self) -> None:
        """Test aggregation handles all numeric keys."""
        runner = BenchmarkRunner()
        metrics_list = [
            {"pp_avg": 1000.0, "tg_avg": 50.0, "batch_throughput": 5000.0},
            {"pp_avg": 1500.0, "tg_avg": 80.0, "batch_throughput": 6000.0},
            {"pp_avg": 1200.0, "tg_avg": 65.0, "batch_throughput": 5500.0},
        ]

        result = runner._aggregate_metrics(metrics_list)

        self.assertEqual(result["pp_avg"], 1200.0)
        self.assertEqual(result["tg_avg"], 65.0)
        self.assertEqual(result["batch_throughput"], 5500.0)


class TestWSLPathConversion(unittest.TestCase):
    """Tests for WSL path conversion (internal helper)."""

    @patch("config.WSL", True)
    def test_wsl_path_conversion(self, *_args: object, **_kwargs: object) -> None:
        """Test Windows to WSL path conversion."""
        from config import _convert_windows_path_to_wsl

        # Windows path
        win_path = Path("C:/Users/test/model.gguf")
        wsl_path = _convert_windows_path_to_wsl(win_path)
        self.assertEqual(str(wsl_path), "/mnt/c/Users/test/model.gguf")

        # Linux path unchanged
        linux_path = Path("/home/user/model.gguf")
        result = _convert_windows_path_to_wsl(linux_path)
        self.assertEqual(result, linux_path)

    @patch("config.WSL", False)
    def test_non_wsl_no_conversion(self, *_args: object, **_kwargs: object) -> None:
        """Test no conversion when not in WSL."""
        from config import _convert_windows_path_to_wsl

        win_path = Path("C:/Users/test/model.gguf")
        result = _convert_windows_path_to_wsl(win_path)
        self.assertEqual(result, win_path)


class TestConvertPathForBinary(unittest.TestCase):
    """Tests for _convert_path_for_binary method."""

    def setUp(self) -> None:
        self.runner = BenchmarkRunner(llama_server_dir="/fake/dir")

    @patch("benchmarks.config.convert_path_for_binary")
    def test_convert_path_for_binary_calls_config(self, mock_convert: Mock) -> None:
        """Test _convert_path_for_binary delegates to config.convert_path_for_binary."""
        mock_convert.return_value = "/converted/path"
        test_path = Path("/original/path/model.gguf")

        result = self.runner._convert_path_for_binary(test_path)

        mock_convert.assert_called_once_with(test_path)
        self.assertEqual(result, "/converted/path")


class TestPartialFailureScenarios(unittest.TestCase):
    """Tests for partial failure scenarios (some reps succeed, some fail)."""

    def setUp(self) -> None:
        self.runner = BenchmarkRunner(llama_server_dir="/fake/dir")
        self.runner._bench_binary = Path("/fake/dir/llama-bench")
        self.runner._batched_bench_binary = Path("/fake/dir/llama-batched-bench")

    @patch("benchmarks.tempfile.TemporaryDirectory")
    @patch("benchmarks.subprocess.run")
    def test_run_llama_bench_partial_failure(self, mock_run: Mock, mock_tempdir: Mock) -> None:
        """Test llama-bench with some repetitions failing and some succeeding."""
        mock_tempdir.return_value.__enter__.return_value = "/tmp/test_bench"

        # First rep succeeds, second fails, third succeeds
        outputs = [
            json.dumps({"pp_avg": 1000.0, "tg_avg": 50.0}),
            "",  # Failed rep (non-zero exit code)
            json.dumps({"pp_avg": 1500.0, "tg_avg": 80.0}),
        ]

        mock_results = []
        for i, out in enumerate(outputs):
            mr = Mock()
            if i == 1:  # Second rep fails
                mr.returncode = 1
                mr.stdout = ""
                mr.stderr = "Error: temporary failure"
            else:
                mr.returncode = 0
                mr.stdout = out
                mr.stderr = ""
            mock_results.append(mr)

        mock_run.side_effect = mock_results

        with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as f:
            model_path = f.name

        try:
            result = self.runner.run_llama_bench(model_path, {"repetitions": 3})

            # Should aggregate only the 2 successful reps
            # Median of [1000, 1500] = 1250
            # Median of [50, 80] = 65
            self.assertEqual(result["pp_avg"], 1250.0)
            self.assertEqual(result["tg_avg"], 65.0)
            self.assertEqual(result["repetitions_completed"], 2)
        finally:
            os.unlink(model_path)

    @patch("benchmarks.tempfile.TemporaryDirectory")
    @patch("benchmarks.subprocess.run")
    def test_run_llama_batched_bench_partial_failure(self, mock_run: Mock, mock_tempdir: Mock) -> None:
        """Test llama-batched-bench with some repetitions failing and some succeeding."""
        mock_tempdir.return_value.__enter__.return_value = "/tmp/test_bench"

        # First rep succeeds, second fails, third succeeds
        outputs = [
            json.dumps({"throughput": 1000.0, "latency_p50": 20.0}),
            "",  # Failed rep
            json.dumps({"throughput": 2000.0, "latency_p50": 15.0}),
        ]

        mock_results = []
        for i, out in enumerate(outputs):
            mr = Mock()
            if i == 1:  # Second rep fails
                mr.returncode = 1
                mr.stdout = ""
                mr.stderr = "Error: OOM"
            else:
                mr.returncode = 0
                mr.stdout = out
                mr.stderr = ""
            mock_results.append(mr)

        mock_run.side_effect = mock_results

        with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as f:
            model_path = f.name

        try:
            result = self.runner.run_llama_batched_bench(model_path, {"repetitions": 3})

            # Should aggregate only the 2 successful reps
            # Median of [1000, 2000] = 1500
            # Median of [15, 20] = 17.5
            self.assertEqual(result["batch_throughput"], 1500.0)
            self.assertEqual(result["latency_p50"], 17.5)
            self.assertEqual(result["repetitions_completed"], 2)
        finally:
            os.unlink(model_path)


class TestNoParseableMetrics(unittest.TestCase):
    """Tests for scenarios where no parseable metrics are produced."""

    def setUp(self) -> None:
        self.runner = BenchmarkRunner(llama_server_dir="/fake/dir")
        self.runner._bench_binary = Path("/fake/dir/llama-bench")
        self.runner._batched_bench_binary = Path("/fake/dir/llama-batched-bench")

    @patch("benchmarks.tempfile.TemporaryDirectory")
    @patch("benchmarks.subprocess.run")
    def test_run_llama_bench_no_parseable_metrics(self, mock_run: Mock, mock_tempdir: Mock) -> None:
        """Test llama-bench when all repetitions produce no parseable metrics."""
        mock_tempdir.return_value.__enter__.return_value = "/tmp/test_bench"

        # All reps return valid exit code but empty/unparseable output
        mock_results = []
        for _ in range(3):
            mr = Mock()
            mr.returncode = 0
            mr.stdout = "Starting benchmark...\nBenchmark complete.\n"  # No JSON
            mr.stderr = ""
            mock_results.append(mr)

        mock_run.side_effect = mock_results

        with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as f:
            model_path = f.name

        try:
            with self.assertRaises(BenchmarkError) as cm:
                self.runner.run_llama_bench(model_path, {"repetitions": 3})

            self.assertIn("failed", str(cm.exception).lower())
        finally:
            os.unlink(model_path)

    @patch("benchmarks.tempfile.TemporaryDirectory")
    @patch("benchmarks.subprocess.run")
    def test_run_llama_batched_bench_no_parseable_metrics(self, mock_run: Mock, mock_tempdir: Mock) -> None:
        """Test llama-batched-bench when all repetitions produce no parseable metrics."""
        mock_tempdir.return_value.__enter__.return_value = "/tmp/test_bench"

        # All reps return valid exit code but empty/unparseable output
        mock_results = []
        for _ in range(3):
            mr = Mock()
            mr.returncode = 0
            mr.stdout = "Running batched benchmark...\nDone.\n"  # No JSON
            mr.stderr = ""
            mock_results.append(mr)

        mock_run.side_effect = mock_results

        with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as f:
            model_path = f.name

        try:
            with self.assertRaises(BenchmarkError) as cm:
                self.runner.run_llama_batched_bench(model_path, {"repetitions": 3})

            self.assertIn("failed", str(cm.exception).lower())
        finally:
            os.unlink(model_path)


if __name__ == "__main__":
    unittest.main()
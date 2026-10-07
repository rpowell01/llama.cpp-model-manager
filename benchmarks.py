#!/usr/bin/env python3
"""
Benchmark runner module for llama.cpp benchmarks.

This module provides functionality to execute llama-bench and llama-batched-bench
benchmarks, parse their output, and return structured metrics.

Supports both Windows (.exe) and Linux binaries. Handles subprocess execution
with timeout, error capture, and resource cleanup.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import config
from config import WSL, WINDOWS, LLAMA_BENCH, LLAMA_BATCHED_BENCH

log = logging.getLogger("llamaweb.benchmarks")


@dataclass
class BenchmarkResult:
    """Structured result from a benchmark run."""
    success: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    raw_output: str = ""
    duration_sec: float = 0.0
    command: list[str] = field(default_factory=list)


@dataclass
class BenchmarkConfig:
    """Configuration for benchmark execution."""
    # Model parameters
    model_path: str

    # Core llama.cpp server parameters
    n_gpu_layers: int = 99
    batch_size: int = 4096
    ubatch_size: int = 1024
    threads: int = 16
    threads_batch: int = 16

    # Cache parameters
    cache_type_k: str = "q4_0"
    cache_type_v: str = "q4_0"
    flash_attn: str = "auto"
    cache_reuse: int = 4096
    cache_ram: int = 8192
    swa_full: bool = False

    # NUMA and CPU affinity
    numa: bool = True
    numa_value: str = "distribute"
    cpu_affinity: str = "auto"
    cpu_strict: bool = False

    # Context and parallel
    ctx_size: int = 128000
    parallel: int | str = 1
    spec_type: str = "none"
    spec_draft_n_max: int = 8

    # Benchmark-specific
    repetitions: int = 3
    timeout_sec: int = 30
    output_format: str = "json"

    # Additional parameters (passed through to binary)
    extra_args: list[str] = field(default_factory=list)


class BenchmarkError(Exception):
    """Exception raised for benchmark execution errors."""


class BinaryNotFoundError(BenchmarkError):
    """Exception raised when benchmark binary is not found."""


class BenchmarkRunner:
    """
    Runner for llama-bench and llama-batched-bench benchmarks.

    Locates benchmark binaries, executes them with given configuration,
    and parses structured output metrics.
    """

    def __init__(self, llama_server_dir: str | Path | None = None) -> None:
        """
        Initialize BenchmarkRunner.

        Args:
            llama_server_dir: Directory containing llama.cpp binaries.
                If None, uses LLAMA_SERVER_DIR env var or config.BASE_DIR.
        """
        self._llama_server_dir = self._resolve_server_dir(llama_server_dir)
        # Use pre-resolved binaries from config (handles WSL/Windows path conversion)
        self._bench_binary = LLAMA_BENCH
        self._batched_bench_binary = LLAMA_BATCHED_BENCH
        
        # Log what we found
        if self._bench_binary:
            log.info(f"Found llama-bench: {self._bench_binary}")
        else:
            log.warning("llama-bench not found")
        if self._batched_bench_binary:
            log.info(f"Found llama-batched-bench: {self._batched_bench_binary}")
        else:
            log.warning("llama-batched-bench not found")

    def _resolve_server_dir(self, llama_server_dir: str | Path | None) -> Path:
        """Resolve the llama.cpp server directory."""
        if llama_server_dir:
            return Path(llama_server_dir)

        # Check environment variable
        env_dir = os.getenv("LLAMA_SERVER_DIR")
        if env_dir:
            path = Path(env_dir)
            if WSL and config._is_windows_absolute_path(str(path)):
                path = config._convert_windows_path_to_wsl(path)
            return path

        # Fall back to BASE_DIR
        return config.BASE_DIR

    @property
    def has_llama_bench(self) -> bool:
        """Check if llama-bench binary is available."""
        return self._bench_binary is not None

    @property
    def has_llama_batched_bench(self) -> bool:
        """Check if llama-batched-bench binary is available."""
        return self._batched_bench_binary is not None

    def find_bench_binaries(self) -> dict[str, Path | None]:
        """
        Locate llama-bench and llama-batched-bench binaries.

        Returns:
            Dictionary with 'llama_bench' and 'llama_batched_bench' keys
            mapping to Path objects or None if not found.
        """
        return {
            "llama_bench": self._bench_binary,
            "llama_batched_bench": self._batched_bench_binary,
        }

    def _convert_path_for_binary(self, path: Path) -> str:
        """Convert path for the binary being used (WSL Windows binary support)."""
        return config.convert_path_for_binary(path)

    def _build_bench_command(
        self,
        binary: Path,
        config: BenchmarkConfig,
        is_batched: bool = False,
    ) -> list[str]:
        """
        Build command line for benchmark binary.

        Args:
            binary: Path to benchmark binary
            config: Benchmark configuration
            is_batched: Whether this is for llama-batched-bench

        Returns:
            List of command arguments
        """
        cmd = [str(binary)]

        # Model path - convert for binary if needed
        model_path = self._convert_path_for_binary(Path(config.model_path))
        cmd.extend(["-m", model_path])

        # Common parameters supported by both llama-bench and llama-batched-bench
        cmd.extend(["-ngl", str(config.n_gpu_layers)])
        cmd.extend(["-b", str(config.batch_size)])
        cmd.extend(["-ub", str(config.ubatch_size)])
        cmd.extend(["-t", str(config.threads)])
        cmd.extend(["--cache-type-k", config.cache_type_k])
        cmd.extend(["--cache-type-v", config.cache_type_v])
        cmd.extend(["--flash-attn", config.flash_attn])

        # Parameters ONLY supported by llama-batched-bench (and llama-server)
        if is_batched:
            # threads_batch (-tb) is a llama-server/llama-batched-bench parameter
            cmd.extend(["-tb", str(config.threads_batch)])
            # ctx_size (-c) is not supported by llama-bench
            # Use -p/--n-prompt and -n/--n-gen for prompt/gen lengths instead
            # NUMA is supported by both but with different syntax
            # CPU affinity/mask
            if config.cpu_affinity and config.cpu_affinity != "auto":
                cmd.extend(["--cpu-affinity", config.cpu_affinity])
            if config.cpu_strict:
                cmd.append("--cpu-strict")
            # Parallel sequences
            if isinstance(config.parallel, int) and config.parallel > 1:
                cmd.extend(["-p", str(config.parallel)])
            elif config.parallel == "auto":
                cmd.append("--kv-unified")
            # Speculative decoding
            if config.spec_type and config.spec_type != "none":
                cmd.extend(["--spec-type", config.spec_type])
                if config.spec_draft_n_max > 0:
                    cmd.extend(["--spec-draft-n-max", str(config.spec_draft_n_max)])
            # Slot cache persistence
            if config.cache_reuse > 0:
                cmd.extend(["--cache-reuse", str(config.cache_reuse)])
            if config.cache_ram > 0:
                cmd.extend(["--cache-ram", str(config.cache_ram)])
            if config.swa_full:
                cmd.append("--swa-full")
        else:
            # For llama-bench: use -p/--n-prompt and -n/--n-gen for prompt/gen lengths
            # NUMA is supported by both
            if config.numa:
                cmd.extend(["--numa", config.numa_value])

        # Generation length (supported by both)
        cmd.extend(["-n", "128"])

        # Output format
        if config.output_format == "json":
            cmd.extend(["--output", "json"])

        # Extra args
        cmd.extend(config.extra_args)

        return cmd

    def _run_benchmark(
        self,
        binary: Path | None,
        config: BenchmarkConfig,
        is_batched: bool = False,
    ) -> BenchmarkResult:
        """
        Execute a benchmark binary with the given configuration.

        Args:
            binary: Path to benchmark binary
            config: Benchmark configuration
            is_batched: Whether this is llama-batched-bench

        Returns:
            BenchmarkResult with metrics or error
        """
        if binary is None:
            binary_name = "llama-batched-bench" if is_batched else "llama-bench"
            error_msg = f"{binary_name} binary not found in {self._llama_server_dir}"
            log.error(error_msg)
            raise BinaryNotFoundError(error_msg)

        if not Path(config.model_path).exists():
            error_msg = f"Model file not found: {config.model_path}"
            log.error(error_msg)
            return BenchmarkResult(
                success=False,
                error=error_msg,
                command=[],
            )

        # Build command
        cmd = self._build_bench_command(binary, config, is_batched)
        log.info(f"Running {'batched ' if is_batched else ''}benchmark: {' '.join(cmd)}")

        all_metrics: list[dict[str, Any]] = []
        total_duration = 0.0
        raw_outputs: list[str] = []
        last_error: str | None = None

        # Run multiple repetitions
        for rep in range(config.repetitions):
            log.info(f"Starting repetition {rep + 1}/{config.repetitions}")
            rep_start = time.time()

            try:
                # Use binary's directory as working directory for Windows binaries on WSL
                # Running from WSL temp directory breaks SYCL/Level Zero initialization
                if WSL and LLAMA_BENCH and str(LLAMA_BENCH).endswith('.exe'):
                    cwd = LLAMA_BENCH.parent
                elif WSL and LLAMA_BATCHED_BENCH and str(LLAMA_BATCHED_BENCH).endswith('.exe'):
                    cwd = LLAMA_BATCHED_BENCH.parent
                else:
                    cwd = None
                
                result = subprocess.run(
                    cmd,
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=config.timeout_sec,
                    check=False,
                )

                rep_duration = time.time() - rep_start
                total_duration += rep_duration

                stdout = result.stdout.strip()
                stderr = result.stderr.strip()
                raw_outputs.append(stdout)

                if result.returncode != 0:
                    error_msg = f"Benchmark failed (exit code {result.returncode}): {stderr}"
                    log.error(f"Repetition {rep + 1} failed: {error_msg}")
                    last_error = error_msg
                    # Continue to next repetition to see if it's a fluke
                    continue

                # Parse output
                try:
                    metrics = self.parse_output(stdout, config.output_format, is_batched)
                    if metrics:
                        all_metrics.append(metrics)
                        log.info(f"Repetition {rep + 1} completed in {rep_duration:.2f}s: {metrics}")
                    else:
                        log.warning(f"Repetition {rep + 1} produced no parseable metrics")
                except (json.JSONDecodeError, ValueError) as e:
                    log.error(f"Failed to parse output from repetition {rep + 1}: {e}")
                    last_error = str(e)

            except subprocess.TimeoutExpired:
                error_msg = f"Benchmark timed out after {config.timeout_sec}s"
                log.error(f"Repetition {rep + 1}: {error_msg}")
                last_error = error_msg
            except OSError as e:
                error_msg = f"Benchmark execution error: {e}"
                log.error(f"Repetition {rep + 1}: {error_msg}")
                last_error = error_msg

        # Aggregate results
        if not all_metrics:
            return BenchmarkResult(
                success=False,
                error=last_error or "All repetitions failed",
                raw_output="\n".join(raw_outputs),
                duration_sec=total_duration,
                command=cmd,
            )

        # Take median of key metrics across repetitions
        aggregated = self._aggregate_metrics(all_metrics)

        return BenchmarkResult(
            success=True,
            metrics=aggregated,
            raw_output="\n".join(raw_outputs),
            duration_sec=total_duration,
            command=cmd,
        )

    def _aggregate_metrics(self, metrics_list: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Aggregate metrics from multiple repetitions using median.

        Args:
            metrics_list: List of metric dictionaries from each repetition

        Returns:
            Aggregated metrics with median values
        """
        if not metrics_list:
            return {}

        # Keys that should use median aggregation
        numeric_keys = [
            "pp_avg", "tg_avg", "pp_std", "tg_std",
            "latency_p50", "latency_p90", "latency_p99",
            "batch_throughput", "memory_usage_mb",
        ]

        aggregated = {}

        for key in numeric_keys:
            values = [m.get(key) for m in metrics_list if key in m and m[key] is not None]
            if values:
                sorted_values = sorted(values)
                mid = len(sorted_values) // 2
                if len(sorted_values) % 2 == 0:
                    aggregated[key] = (sorted_values[mid - 1] + sorted_values[mid]) / 2
                else:
                    aggregated[key] = sorted_values[mid]

        # Also include the raw values for transparency
        aggregated["_all_runs"] = metrics_list
        aggregated["repetitions_completed"] = len(metrics_list)

        return aggregated

    def run_llama_bench(
        self,
        model_path: str,
        config_dict: dict[str, Any] | None = None,
        repetitions: int | None = None,
    ) -> dict[str, Any]:
        """
        Run llama-bench benchmark and return parsed metrics.

        Args:
            model_path: Path to model file
            config_dict: Configuration dictionary with benchmark parameters
            repetitions: Number of times to run the benchmark (overrides config_dict if provided)

        Returns:
            Dictionary with benchmark metrics (pp_avg, tg_avg, latency, etc.)

        Raises:
            BinaryNotFoundError: If llama-bench binary is not found
            BenchmarkError: If benchmark execution fails
        """
        config = self._dict_to_config(config_dict or {})
        config.model_path = model_path
        if repetitions is not None:
            config.repetitions = repetitions

        log.info(f"Starting llama-bench for model: {model_path}")
        result = self._run_benchmark(self._bench_binary, config, is_batched=False)

        if not result.success:
            raise BenchmarkError(f"llama-bench failed: {result.error}")

        log.info(f"llama-bench completed: {result.metrics}")
        return result.metrics

    def run_llama_batched_bench(
        self,
        model_path: str,
        config_dict: dict[str, Any] | None = None,
        repetitions: int | None = None,
    ) -> dict[str, Any]:
        """
        Run llama-batched-bench benchmark and return parsed metrics.

        Args:
            model_path: Path to model file
            config_dict: Configuration dictionary with benchmark parameters
            repetitions: Number of times to run the benchmark (overrides config_dict if provided)

        Returns:
            Dictionary with batched benchmark metrics (batch throughput, latency, etc.)

        Raises:
            BinaryNotFoundError: If llama-batched-bench binary is not found
            BenchmarkError: If benchmark execution fails
        """
        config = self._dict_to_config(config_dict or {})
        config.model_path = model_path
        if repetitions is not None:
            config.repetitions = repetitions

        log.info(f"Starting llama-batched-bench for model: {model_path}")
        result = self._run_benchmark(self._batched_bench_binary, config, is_batched=True)

        if not result.success:
            raise BenchmarkError(f"llama-batched-bench failed: {result.error}")

        log.info(f"llama-batched-bench completed: {result.metrics}")
        return result.metrics

    def _dict_to_config(self, config_dict: dict[str, Any]) -> BenchmarkConfig:
        """Convert configuration dictionary to BenchmarkConfig object."""
        return BenchmarkConfig(
            model_path=config_dict.get("model_path", ""),
            n_gpu_layers=config_dict.get("n_gpu_layers", 99),
            batch_size=config_dict.get("batch_size", 4096),
            ubatch_size=config_dict.get("ubatch_size", 1024),
            threads=config_dict.get("threads", 16),
            threads_batch=config_dict.get("threads_batch", 16),
            cache_type_k=config_dict.get("cache_type_k", "q4_0"),
            cache_type_v=config_dict.get("cache_type_v", "q4_0"),
            flash_attn=config_dict.get("flash_attn", "auto"),
            numa=config_dict.get("numa", True),
            numa_value=config_dict.get("numa_value", "distribute"),
            cpu_affinity=config_dict.get("cpu_affinity", "auto"),
            cpu_strict=config_dict.get("cpu_strict", False),
            ctx_size=config_dict.get("ctx_size", 128000),
            parallel=config_dict.get("parallel", 1),
            spec_type=config_dict.get("spec_type", "none"),
            spec_draft_n_max=config_dict.get("spec_draft_n_max", 8),
            cache_reuse=config_dict.get("cache_reuse", 4096),
            cache_ram=config_dict.get("cache_ram", 8192),
            swa_full=config_dict.get("swa_full", False),
            repetitions=config_dict.get("repetitions", 3),
            timeout_sec=config_dict.get("timeout_sec", 30),
            output_format=config_dict.get("output_format", "json"),
            extra_args=config_dict.get("extra_args", []),
        )

    @staticmethod
    def parse_output(
        output: str,
        output_format: str = "json",
        is_batched: bool = False,
    ) -> dict[str, Any]:
        """
        Parse benchmark output into structured metrics.

        Args:
            output: Raw output from benchmark binary
            output_format: Output format ('json' or 'text')
            is_batched: Whether this is llama-batched-bench output

        Returns:
            Dictionary with structured metrics
        """
        if not output.strip():
            return {}

        if output_format == "json":
            return BenchmarkRunner._parse_json_output(output, is_batched)
        else:
            return BenchmarkRunner._parse_text_output(output, is_batched)

    @staticmethod
    def _parse_json_output(output: str, is_batched: bool) -> dict[str, Any]:
        """Parse JSON output from benchmark."""
        # llama-bench outputs multiple JSON objects (one per test phase)
        # Try to parse as array first, then try individual objects
        parsed_objects = []
        
        # Try to parse as JSON array
        try:
            data = json.loads(output)
            if isinstance(data, list):
                parsed_objects = data
            else:
                parsed_objects = [data]
        except json.JSONDecodeError:
            # Try to extract multiple JSON objects from mixed output
            # llama-bench outputs one JSON object per line for each test phase
            for line in output.splitlines():
                line = line.strip()
                if line.startswith(("{", "[")):
                    try:
                        obj = json.loads(line)
                        if isinstance(obj, list):
                            parsed_objects.extend(obj)
                        else:
                            parsed_objects.append(obj)
                    except json.JSONDecodeError:
                        continue
        
        if not parsed_objects:
            return {}
        
        # Aggregate metrics from all parsed objects
        aggregated = {}
        for obj in parsed_objects:
            if is_batched:
                metrics = BenchmarkRunner._parse_batched_json(obj)
            else:
                metrics = BenchmarkRunner._parse_regular_json(obj)
            # Merge metrics, preferring non-zero values
            for k, v in metrics.items():
                if k not in aggregated or (v and not aggregated.get(k)):
                    aggregated[k] = v
        
        return aggregated

    @staticmethod
    def _parse_regular_json(data: dict[str, Any]) -> dict[str, Any]:
        """Parse JSON output from llama-bench."""
        metrics = {}

        # llama-bench JSON output structure
        # Contains: pp_avg, tg_avg, pp_std, tg_std, latency percentiles, etc.
        if "pp_avg" in data:
            metrics["pp_avg"] = data["pp_avg"]  # Prompt processing tokens/sec
        if "tg_avg" in data:
            metrics["tg_avg"] = data["tg_avg"]  # Token generation tokens/sec
        if "pp_std" in data:
            metrics["pp_std"] = data["pp_std"]
        if "tg_std" in data:
            metrics["tg_std"] = data["tg_std"]

        # Latency percentiles (in ms)
        for p in ["p50", "p90", "p99"]:
            key = f"latency_{p}"
            if key in data:
                metrics[key] = data[key]

        # Memory usage
        if "mem_used_mb" in data:
            metrics["memory_usage_mb"] = data["mem_used_mb"]

        # Additional fields that may be present
        for key in ["n_tokens", "n_batch", "n_ubatch", "n_threads", "n_gpu_layers"]:
            if key in data:
                metrics[key] = data[key]

        return metrics

    @staticmethod
    def _parse_batched_json(data: dict[str, Any]) -> dict[str, Any]:
        """Parse JSON output from llama-batched-bench."""
        metrics = {}

        # llama-batched-bench JSON output structure
        # Contains batch throughput at different parallel levels
        if "throughput" in data:
            metrics["batch_throughput"] = data["throughput"]  # tokens/sec
        if "latency_p50" in data:
            metrics["latency_p50"] = data["latency_p50"]
        if "latency_p90" in data:
            metrics["latency_p90"] = data["latency_p90"]
        if "latency_p99" in data:
            metrics["latency_p99"] = data["latency_p99"]
        if "memory_mb" in data:
            metrics["memory_usage_mb"] = data["memory_mb"]

        # Per-parallel results if available
        if "results" in data and isinstance(data["results"], list):
            parallel_results = []
            for r in data["results"]:
                parallel_results.append({
                    "parallel": r.get("parallel"),
                    "throughput": r.get("throughput"),
                    "latency_p50": r.get("latency_p50"),
                    "latency_p99": r.get("latency_p99"),
                })
            metrics["parallel_results"] = parallel_results

        # Additional fields
        for key in ["n_sequences", "n_tokens", "batch_size"]:
            if key in data:
                metrics[key] = data[key]

        return metrics

    @staticmethod
    def _parse_text_output(output: str, is_batched: bool) -> dict[str, Any]:
        """Parse text output from benchmark (fallback when JSON not available)."""
        metrics = {}

        # Common patterns in llama-bench text output
        # Matches formats like: "pp avg: 1000.5 t/s", "tg avg: 55.2 t/s", "p50 = 12.3 ms"
        patterns = {
            "pp_avg": r"pp\s+(?:avg|average)?\s*[:=]\s*(\d+\.?\d*)\s*t/s",
            "tg_avg": r"tg\s+(avg|average)?\s*[:=]\s*(\d+\.?\d*)\s*t/s",
            "latency_p50": r"p50\s*[:=]\s*(\d+\.?\d*)\s*ms",
            "latency_p90": r"p90\s*[:=]\s*(\d+\.?\d*)\s*ms",
            "latency_p99": r"p99\s*[:=]\s*(\d+\.?\d*)\s*ms",
        }

        for key, pattern in patterns.items():
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                # For tg_avg pattern with optional group, use group 2
                group_idx = 2 if key == "tg_avg" else 1
                metrics[key] = float(match.group(group_idx))

        return metrics


# Convenience functions for direct usage
def run_llama_bench(
    model_path: str,
    config_dict: dict[str, Any] | None = None,
    repetitions: int | None = None,
    llama_server_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    Convenience function to run llama-bench.

    Args:
        model_path: Path to model file
        config_dict: Configuration dictionary
        repetitions: Number of repetitions (overrides config_dict if provided)
        llama_server_dir: Directory containing llama.cpp binaries

    Returns:
        Dictionary with benchmark metrics
    """
    runner = BenchmarkRunner(llama_server_dir)
    return runner.run_llama_bench(model_path, config_dict, repetitions)


def run_llama_batched_bench(
    model_path: str,
    config_dict: dict[str, Any] | None = None,
    repetitions: int | None = None,
    llama_server_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    Convenience function to run llama-batched-bench.

    Args:
        model_path: Path to model file
        config_dict: Configuration dictionary
        repetitions: Number of repetitions (overrides config_dict if provided)
        llama_server_dir: Directory containing llama.cpp binaries

    Returns:
        Dictionary with batched benchmark metrics
    """
    runner = BenchmarkRunner(llama_server_dir)
    return runner.run_llama_batched_bench(model_path, config_dict, repetitions)


def find_bench_binaries(llama_server_dir: str | Path | None = None) -> dict[str, Path | None]:
    """
    Convenience function to find benchmark binaries.

    Args:
        llama_server_dir: Directory containing llama.cpp binaries

    Returns:
        Dictionary with 'llama_bench' and 'llama_batched_bench' paths
    """
    runner = BenchmarkRunner(llama_server_dir)
    return runner.find_bench_binaries()


if __name__ == "__main__":
    # Simple test when run directly
    logging.basicConfig(level=logging.INFO)

    runner = BenchmarkRunner()
    print("Available binaries:", runner.find_bench_binaries())

    if runner.has_llama_bench:
        print("llama-bench available")
    if runner.has_llama_batched_bench:
        print("llama-batched-bench available")
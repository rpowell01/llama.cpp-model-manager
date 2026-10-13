#!/usr/bin/env python3
"""
Benchmark optimization engine for llama.cpp model manager.

This module provides functionality to automatically find optimal
llama.cpp server parameters through benchmark sweeping and caching.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from benchmarks import BenchmarkRunner, BenchmarkConfig, BenchmarkError
from routes.system import compute_optimal_settings, get_hardware_info
from config import LLAMA_CLI, WSL

log = logging.getLogger("llamaweb.optimizer")

# Global registry of running benchmark optimizers, keyed by model path
# This allows the cancel endpoint to find and cancel the running optimizer
_running_optimizers: Dict[str, 'BenchmarkOptimizer'] = {}
_running_optimizers_lock = threading.Lock()


def _get_running_optimizer(model_path: str) -> Optional['BenchmarkOptimizer']:
    """Get the running optimizer for a model path, if any."""
    global _running_optimizers
    # Normalize path for consistent lookup
    normalized_path = str(Path(model_path).resolve())
    with _running_optimizers_lock:
        return _running_optimizers.get(normalized_path)


def _register_optimizer(model_path: str, optimizer: 'BenchmarkOptimizer') -> None:
    """Register a running optimizer for a model path."""
    global _running_optimizers
    normalized_path = str(Path(model_path).resolve())
    with _running_optimizers_lock:
        _running_optimizers[normalized_path] = optimizer
    log.debug(f"Registered optimizer for model: {normalized_path}")


def _unregister_optimizer(model_path: str) -> None:
    """Unregister a running optimizer for a model path."""
    global _running_optimizers
    normalized_path = str(Path(model_path).resolve())
    with _running_optimizers_lock:
        if normalized_path in _running_optimizers:
            del _running_optimizers[normalized_path]
    log.debug(f"Unregistered optimizer for model: {normalized_path}")


class BenchmarkOptimizer:
    """
    Optimization engine that runs benchmark sweeps and selects optimal settings.
    
    Features:
    - Generates config combinations based on hardware and model info
    - Runs benchmark sweeps with time limits
    - Selects optimal config based on target metric
    - Caches results to avoid repeated benchmarks
    - Falls back to heuristic settings on failure
    """

    def __init__(self, cache_dir: Optional[str | Path] = None) -> None:
        """
        Initialize BenchmarkOptimizer.
        
        Args:
            cache_dir: Directory to store cache files.
                       If None, uses ~/.cache/llama-web/benchmarks/
        """
        if cache_dir is None:
            cache_dir = Path.home() / ".cache" / "llama-web" / "benchmarks"
         
        self.cache_dir = Path(cache_dir)
        # Only create directory if we have permission (for testing)
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            # In test environments, we might not have permission to create dirs in home
            # Use a temporary directory instead
            import tempfile
            self.cache_dir = Path(tempfile.gettempdir()) / "llama-web-benchmarks"
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._benchmark_runner = BenchmarkRunner()
          
        # Cancellation support for running benchmarks
        self._cancel_callback: Optional[Callable[[], None]] = None
        self._cancel_requested = False
        self._active_runner: Optional[BenchmarkRunner] = None
         
    def _sanitize_hardware_info(self, hardware_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitize hardware info by replacing None values with safe defaults.
        This prevents issues when passing hardware info to compute_optimal_settings.
        
        Args:
            hardware_info: Raw hardware information that may contain None values
            
        Returns:
            Sanitized hardware information with None values replaced by defaults
        """
        if not isinstance(hardware_info, dict):
            return {}
            
        sanitized = hardware_info.copy()
        
        # Replace None values with safe defaults
        if sanitized.get("cpu_count_logical") is None:
            sanitized["cpu_count_logical"] = 16
            
        # For cpu_count_physical, we need to be careful about the dependency on cpu_count_logical
        cpu_logical = sanitized.get("cpu_count_logical", 16)
        if sanitized.get("cpu_count_physical") is None:
            sanitized["cpu_count_physical"] = cpu_logical // 2
            
        if sanitized.get("memory_total_gb") is None:
            sanitized["memory_total_gb"] = 32
            
        if sanitized.get("total_gpu_vram_gb") is None:
            sanitized["total_gpu_vram_gb"] = 0
            
        # Boolean flags - default to False if None
        if sanitized.get("has_nvidia_gpu") is None:
            sanitized["has_nvidia_gpu"] = False
        if sanitized.get("has_amd_gpu") is None:
            sanitized["has_amd_gpu"] = False
        if sanitized.get("has_intel_gpu") is None:
            sanitized["has_intel_gpu"] = False
            
        # Ensure gpu_info is a list
        if sanitized.get("gpu_info") is None:
            sanitized["gpu_info"] = []
            
        return sanitized
         
    def _get_cache_path(self, cache_key: str) -> Path:
        """Get cache file path for a given key."""
        return self.cache_dir / f"{cache_key}.json"
        
    def _generate_cache_key(self, model_path: str, hardware_info: Dict[str, Any], 
                           optimization_target: str = 'balanced') -> str:
        """
        Generate cache key based on model, hardware fingerprint, and optimization target.
        Duration (max_duration_sec) is NOT included so that longer runs can 
        incrementally build on shorter runs. But optimization_target IS included
        so different targets (balanced/throughput/latency) have separate caches.
        
        Args:
            model_path: Path to model file
            hardware_info: Hardware information dictionary
            optimization_target: Target metric ('balanced', 'throughput', 'latency')
            
        Returns:
            MD5 hash string for cache key
        """
        # Create a fingerprint of the model file (size + modification time)
        try:
            model_stat = os.stat(model_path)
            model_fingerprint = f"{model_path}:{model_stat.st_size}:{model_stat.st_mtime}"
        except OSError:
            # If we can't stat the file, just use the path
            model_fingerprint = model_path
            
        # Create a fingerprint of relevant hardware info
        hw_fingerprint_parts = [
            str(hardware_info.get("cpu_count_logical", 0)),
            str(hardware_info.get("cpu_count_physical", 0)),
            str(hardware_info.get("memory_total_gb", 0)),
            str(hardware_info.get("total_gpu_vram_gb", 0)),
            str(hardware_info.get("has_nvidia_gpu", False)),
            str(hardware_info.get("has_amd_gpu", False)),
            str(hardware_info.get("has_intel_gpu", False)),
        ]
        
        # Add GPU details if available
        gpu_info = hardware_info.get("gpu_info", [])
        for gpu in sorted(gpu_info, key=lambda x: x.get("name", "")):
            hw_fingerprint_parts.append(
                f"{gpu.get('name', '')}:{gpu.get('vendor', '')}:{gpu.get('memory_mb', 0)}"
            )
            
        hw_fingerprint = "|".join(hw_fingerprint_parts)
        
        # Include optimization target in cache key (but NOT duration)
        opt_fingerprint = f"target:{optimization_target}"
        
        # Combine model, hardware, and optimization target fingerprints
        combined = f"{model_fingerprint}|{hw_fingerprint}|{opt_fingerprint}"
        return hashlib.md5(combined.encode()).hexdigest()
        
    def _load_from_cache(self, cache_key: str, current_hw_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Load optimization result from cache if valid.
        
        Args:
            cache_key: Cache key to lookup
            current_hw_info: Current hardware information to validate against
            
        Returns:
            Cached result dict or None if not found/expired
        """
        cache_path = self._get_cache_path(cache_key)
        if not cache_path.exists():
            return None
             
        try:
            with open(cache_path, 'r') as f:
                cached = json.load(f)
                 
            # Check if cache is expired (TTL: 30 days)
            timestamp = cached.get("timestamp", 0)
            if time.time() - timestamp > 30 * 24 * 3600:  # 30 days in seconds
                log.info(f"Cache expired for key {cache_key}")
                return None
                 
            # Check if hardware has changed
            cached_hw_info = cached.get("hardware_info", {})
             
            # Simple hardware change detection - compare key attributes
            hw_changed = False
            key_attrs = [
                "cpu_count_logical", "cpu_count_physical", "memory_total_gb",
                "total_gpu_vram_gb", "has_nvidia_gpu", "has_amd_gpu", "has_intel_gpu"
            ]
             
            for attr in key_attrs:
                if cached_hw_info.get(attr) != current_hw_info.get(attr):
                    hw_changed = True
                    break
                     
            if hw_changed:
                log.info(f"Hardware changed, invalidating cache for key {cache_key}")
                return None
                 
            log.info(f"Loaded optimization result from cache: {cache_key}")
            return cached
            
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"Failed to load cache {cache_path}: {e}")
            return None

    def _save_to_cache(self, cache_key: str, result: Dict[str, Any], hardware_info: Dict[str, Any]) -> None:
        """
        Save optimization result to cache.
        
        Args:
            cache_key: Cache key to store under
            result: Result dictionary to cache
            hardware_info: Current hardware information to store in cache
        """
        log.info(f"Attempting to save cache: {cache_key}")
        cache_path = self._get_cache_path(cache_key)
        try:
            # Add timestamp and hardware info to result for cache validation
            result_to_cache = result.copy()
            result_to_cache["timestamp"] = time.time()
            result_to_cache["hardware_info"] = hardware_info
            
            with open(cache_path, 'w') as f:
                json.dump(result_to_cache, f, indent=2)
            
            log.info(f"Saved optimization result to cache: {cache_key}")
        except OSError as e:
            log.warning(f"Failed to save cache {cache_path}: {e}")
        except Exception as e:
            log.exception(f"Unexpected error saving cache {cache_path}: {e}")
            raise

    def generate_config_combinations(
        self,
        hardware_info: Dict[str, Any],
        model_info: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Generate benchmark configuration combinations based on parameter space.
        
        Args:
            hardware_info: Hardware information from get_hardware_info()
            model_info: Optional model information (size, context, etc.)
            
        Returns:
            List of configuration dictionaries to benchmark
        """
        configs = []
        
        # Coerce None hardware values to safe defaults BEFORE using them
        # This prevents crashes when hardware detection returns None for some values
        cpu_count_logical = hardware_info.get("cpu_count_logical") or 16
        cpu_count_physical = hardware_info.get("cpu_count_physical") or (cpu_count_logical // 2)
        memory_total_gb = hardware_info.get("memory_total_gb") or 32
        total_gpu_vram_gb = hardware_info.get("total_gpu_vram_gb") or 0
        
        # Extract hardware info (using coerced values)
        has_gpu = hardware_info.get("has_nvidia_gpu", False) or \
                  hardware_info.get("has_amd_gpu", False) or \
                  hardware_info.get("has_intel_gpu", False)
        total_vram = total_gpu_vram_gb
        cpu_logical = cpu_count_logical
        cpu_physical = cpu_count_physical
        mem_total = memory_total_gb
        
        # Model info
        model_size_gb = 0
        model_ctx = 4096
        if model_info:
            model_size_gb = model_info.get("file_size_gb", 0)
            model_ctx = model_info.get("ctx_size", 4096)
        
        # Extract GPU flags for speculative decoding
        has_nvidia = hardware_info.get("has_nvidia_gpu", False)
        has_amd = hardware_info.get("has_amd_gpu", False)
        has_intel = hardware_info.get("has_intel_gpu", False)
        
        # Speculative decoding options (from BENCHMARK_DESIGN.md)
        spec_type_options = ["none"]
        spec_draft_n_max_options = [0]  # Only relevant when spec_type != "none"
        spec_ngram_size_n_options = [0]
        spec_ngram_size_m_options = [0]
        spec_draft_p_split_options = [0.0]
        if (has_nvidia or has_amd or has_intel) and model_size_gb >= 6:
            spec_type_options = ["none", "ngram-simple"]
            spec_draft_n_max_options = [0, 3, 5, 8]  # 0 means disabled
            spec_ngram_size_n_options = [0, 12, 16]  # 0 means disabled
            spec_ngram_size_m_options = [0, 48, 64]  # 0 means disabled
            spec_draft_p_split_options = [0.0, 0.1]  # 0.0 means disabled
        
        # GPU layers to test - based on available VRAM and model size
        # Skip CPU-only (0) when GPU is available - it's always slower
        gpu_layers_options = []
        if has_gpu and total_vram > 0:
            # Test specific meaningful GPU layer configurations
            if total_vram >= 24:
                gpu_layers_options = [99, 35, 20]
            elif total_vram >= 16:
                gpu_layers_options = [99, 35, 20]
            elif total_vram >= 8:
                gpu_layers_options = [99, 35, 20]
            else:
                gpu_layers_options = [20, 10]
        else:
            # No GPU available - use CPU only
            gpu_layers_options = [0]
            
        # Filter based on model size - don't test configs that clearly won't fit
        if model_size_gb > 0:
            filtered_layers = []
            for layers in gpu_layers_options:
                # Very rough estimate: if offloading many layers, need VRAM for model
                # This is conservative - actual usage depends on quantization
                estimated_vram_needed = model_size_gb * (layers / 100) * 1.5  # rough factor
                if estimated_vram_needed <= total_vram * 0.9:  # leave some headroom
                    filtered_layers.append(layers)
            gpu_layers_options = filtered_layers if filtered_layers else [0]
        
        # Batch/UBatch pairs - from benchmark design
        batch_ubatch_pairs = [
            (256, 128),
            (512, 256),
            (1024, 512),
            (2048, 512),
            (4096, 1024),
        ]
        
        # Filter batch sizes based on available memory
        filtered_pairs = []
        for batch, ubatch in batch_ubatch_pairs:
            # Very rough estimate: batch size * 2 bytes per token (for KV cache) 
            # plus model memory usage
            estimated_mem_gb = (batch * 2 * 2048) / (1024**3)  # 2048 token context example
            if estimated_mem_gb <= mem_total * 0.5:  # use at most 50% of RAM for batch
                filtered_pairs.append((batch, ubatch))
                
        if not filtered_pairs:
            filtered_pairs = [(256, 128)]  # fallback to smallest
            
        # Threads options
        threads_options = [
            max(1, cpu_physical // 2),
            cpu_physical,
            min(cpu_logical, cpu_physical * 2)
        ]
        threads_options = list(set(threads_options))  # remove duplicates
        threads_options.sort()
        
        # Cache types
        cache_type_options = []
        if total_vram >= 16:
            cache_type_options = [("f16", "f16")]
        elif total_vram >= 8:
            cache_type_options = [("q8_0", "q8_0"), ("q4_0", "q4_0")]
        else:
            cache_type_options = [("q4_0", "q4_0")]
            
        # Flash attention options
        flash_attn_options = ["auto", "off"] if has_gpu else ["off"]
        
        # Context size options
        ctx_size_options = [512, 1024, 2048, 4096, 8192, 16384, 32768]
        # Filter by model context and available memory
        max_ctx_by_mem = int((mem_total * 0.6) * 1024)  # rough estimate
        ctx_size_options = [
            ctx for ctx in ctx_size_options 
            if ctx <= model_ctx and ctx <= max_ctx_by_mem
        ]
        if not ctx_size_options:
            ctx_size_options = [min(model_ctx, 4096)]
            
        # Generate all combinations (but limit to reasonable number)
        # We'll use a smarter approach: generate a curated set rather than full Cartesian product
        
        # Start with heuristic optimal settings as baseline
        baseline = compute_optimal_settings(self._sanitize_hardware_info(hardware_info), model_info)
        
        # Convert baseline to use BenchmarkConfig-compatible keys
        baseline_config = baseline.copy()
        # Map gpu_layers to n_gpu_layers for BenchmarkConfig compatibility
        if "gpu_layers" in baseline_config:
            baseline_config["n_gpu_layers"] = baseline_config.pop("gpu_layers")
        
        # Generate variations around the baseline
        configs.append(baseline_config)  # Always include the heuristic optimum
        
        # Generate GPU layer variations
        if has_gpu and total_vram > 0:
            for gpu_layers in gpu_layers_options:
                if gpu_layers != baseline_config.get("n_gpu_layers", 0):
                    config = baseline_config.copy()
                    config["n_gpu_layers"] = gpu_layers
                    configs.append(config)
        
        # Generate batch size variations
        for batch, ubatch in filtered_pairs[:3]:  # Limit to top 3 pairs
            if batch != baseline_config.get("batch_size", 4096):
                config = baseline_config.copy()
                config["batch_size"] = batch
                config["ubatch_size"] = ubatch
                configs.append(config)
                
        # Generate threads variations
        for threads in threads_options:
            if threads != baseline_config.get("threads", cpu_physical):
                config = baseline_config.copy()
                config["threads"] = threads
                config["threads_batch"] = threads
                configs.append(config)
                
        # Generate cache type variations
        for cache_k, cache_v in cache_type_options:
            if cache_k != baseline_config.get("cache_type_k", "q4_0"):
                config = baseline_config.copy()
                config["cache_type_k"] = cache_k
                config["cache_type_v"] = cache_v
                configs.append(config)
                
        # Generate flash attention variations
        for flash_attn in flash_attn_options:
            if flash_attn != baseline_config.get("flash_attn", "auto"):
                config = baseline_config.copy()
                config["flash_attn"] = flash_attn
                configs.append(config)
                
# Generate context size variations
        for ctx_size in ctx_size_options[:3]:  # Limit to top 3
            if ctx_size != baseline_config.get("ctx_size", 4096):
                config = baseline_config.copy()
                config["ctx_size"] = ctx_size
                configs.append(config)
        
        # Generate prompt size variations (for agentic coding large contexts)
        prompt_size_options = [8192, 16384, 32768]
        # Filter by model context size
        prompt_size_options = [p for p in prompt_size_options if p <= model_ctx]
        if not prompt_size_options:
            prompt_size_options = [min(model_ctx, 4096)]
        
        for n_prompt in prompt_size_options:
            if n_prompt != baseline_config.get("n_prompt", 512):
                config = baseline_config.copy()
                config["n_prompt"] = n_prompt
                config["n_gen"] = 128  # Keep generation size fixed
                configs.append(config)
         
        # Generate speculative decoding variations
        if (has_nvidia or has_amd or has_intel) and model_size_gb >= 6:
            # Generate all combinations of speculative decoding parameters
            for spec_type in spec_type_options:
                for spec_draft_n_max in spec_draft_n_max_options:
                    for spec_ngram_size_n in spec_ngram_size_n_options:
                        for spec_ngram_size_m in spec_ngram_size_m_options:
                            for spec_draft_p_split in spec_draft_p_split_options:
                                # Skip invalid combinations
                                if spec_type == "none":
                                    # When spec_type is none, all other params must be disabled (0)
                                    if spec_draft_n_max != 0 or spec_ngram_size_n != 0 or spec_ngram_size_m != 0 or spec_draft_p_split != 0.0:
                                        continue
                                else:  # spec_type == "ngram-simple"
                                    # When spec_type is ngram-simple, draft_n_max must be > 0
                                    # and at least one of ngram sizes must be > 0
                                    if spec_draft_n_max <= 0:
                                        continue
                                    if spec_ngram_size_n <= 0 and spec_ngram_size_m <= 0:
                                        continue
                                
                                # Only add if different from baseline
                                baseline_spec_type = baseline_config.get("spec_type", "none")
                                baseline_spec_draft_n_max = baseline_config.get("spec_draft_n_max", 0)
                                baseline_spec_ngram_size_n = baseline_config.get("spec_ngram_size_n", 0)
                                baseline_spec_ngram_size_m = baseline_config.get("spec_ngram_size_m", 0)
                                baseline_spec_draft_p_split = baseline_config.get("spec_draft_p_split", 0.0)
                                
                                if (spec_type != baseline_spec_type or 
                                    spec_draft_n_max != baseline_spec_draft_n_max or
                                    spec_ngram_size_n != baseline_spec_ngram_size_n or
                                    spec_ngram_size_m != baseline_spec_ngram_size_m or
                                    spec_draft_p_split != baseline_spec_draft_p_split):
                                    
                                    config = baseline_config.copy()
                                    config["spec_type"] = spec_type
                                    config["spec_draft_n_max"] = spec_draft_n_max
                                    config["spec_ngram_size_n"] = spec_ngram_size_n
                                    config["spec_ngram_size_m"] = spec_ngram_size_m
                                    config["spec_draft_p_split"] = spec_draft_p_split
                                    configs.append(config)
        
        # Generate NUMA variations
        numa_value_options = ["distribute", "isolate", "off"]
        for numa_value in numa_value_options:
            if numa_value != baseline_config.get("numa_value", "distribute"):
                config = baseline_config.copy()
                config["numa_value"] = numa_value
                configs.append(config)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_configs = []
        for config in configs:
            # Create a hashable representation of the config
            config_tuple = tuple(sorted(config.items()))
            if config_tuple not in seen:
                seen.add(config_tuple)
                unique_configs.append(config)
                
        log.info(f"Generated {len(unique_configs)} benchmark configurations")
        return unique_configs
        
    def run_benchmark_sweep(
        self, 
        model_path: str, 
        config_list: List[Dict[str, Any]], 
        max_duration_sec: int = 100,
        optimization_target: str = 'balanced'
    ) -> List[Dict[str, Any]]:
        """
        Run benchmark sweep across multiple configurations.
        
        Args:
            model_path: Path to model file
            config_list: List of configuration dictionaries to test
            max_duration_sec: Maximum time to spend on benchmarking
            
        Returns:
            List of benchmark result dictionaries
        """
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Model file not found: {model_path}")
            
        results = []
        start_time = time.time()
        
        # Reset cancellation flag at start
        self._cancel_requested = False
        
        log.info(f"Starting benchmark sweep with {len(config_list)} configurations "
                f"(max {max_duration_sec}s)")
        
        def cancel_callback():
            """Callback to signal cancellation to the runner."""
            self._cancel_requested = True
        
        for i, config in enumerate(config_list):
            # Check for cancellation before starting
            if self._cancel_requested:
                log.info(f"Benchmark sweep cancelled before configuration {i+1}")
                break
            
            # Check if we've exceeded time limit (only when max_duration_sec > 0)
            elapsed = time.time() - start_time
            if max_duration_sec > 0 and elapsed >= max_duration_sec:
                log.info(f"Time limit reached after {len(results)} configurations "
                        f"(elapsed: {elapsed:.1f}s)")
                break
            
            # Handle timeout: 0 = no timeout (None), otherwise use max_duration_sec or 600s default
            if max_duration_sec == 0:
                timeout_sec = None  # No timeout for individual benchmarks
            else:
                timeout_sec = max_duration_sec if max_duration_sec > 0 else 600
            
            log.info(f"Testing configuration {i+1}/{len(config_list)}: "
                    f"ngl={config.get('n_gpu_layers')}, "
                    f"batch={config.get('batch_size')}, "
                    f"threads={config.get('threads')}, "
                    f"timeout={timeout_sec}s")
            
            # Create benchmark config
            bench_config = BenchmarkConfig(
                model_path=model_path,
                n_gpu_layers=config.get("n_gpu_layers", 99),
                batch_size=config.get("batch_size", 4096),
                ubatch_size=config.get("ubatch_size", 1024),
                threads=config.get("threads", 16),
                threads_batch=config.get("threads_batch", 16),
                cache_type_k=config.get("cache_type_k", "q4_0"),
                cache_type_v=config.get("cache_type_v", "q4_0"),
                flash_attn=config.get("flash_attn", "auto"),
                ctx_size=config.get("ctx_size", 128000),
                n_prompt=config.get("n_prompt", 512),
                n_gen=config.get("n_gen", 128),
                repetitions=2,  # Fewer repetitions for sweep speed
                timeout_sec=timeout_sec,
            )
            
            # Run llama-bench (single sequence) for sweep with cancellation support
            runner = BenchmarkRunner(cancel_callback=cancel_callback)
            self._active_runner = runner
            try:
                result = runner.run_llama_bench(model_path, {
                    "n_gpu_layers": bench_config.n_gpu_layers,
                    "batch_size": bench_config.batch_size,
                    "ubatch_size": bench_config.ubatch_size,
                    "threads": bench_config.threads,
                    "threads_batch": bench_config.threads_batch,
                    "cache_type_k": bench_config.cache_type_k,
                    "cache_type_v": bench_config.cache_type_v,
                    "flash_attn": bench_config.flash_attn,
                    "ctx_size": bench_config.ctx_size,
                    "n_prompt": bench_config.n_prompt,
                    "n_gen": bench_config.n_gen,
                    "repetitions": bench_config.repetitions,
                    "timeout_sec": bench_config.timeout_sec,
                })
            except Exception as e:
                self._active_runner = None
                log.warning(f"Configuration {i+1} failed: {e}")
                # Add failed result for completeness
                results.append({
                    "config": config,
                    "success": False,
                    "error": str(e),
                    "pp_avg": 0,
                    "tg_avg": 0,
                    "timestamp": time.time(),
                    "test_type": "automatic",
                    "optimization_target": optimization_target,
                })
                continue
            finally:
                self._active_runner = None
            
            # Check if cancelled during run
            if self._cancel_requested:
                log.info(f"Configuration {i+1} cancelled during execution")
                break
            
            # Extract key metrics for optimization
            result_dict = {
                "config": config,
                "metrics": result,
                "success": True,
                "pp_avg": result.get("pp_avg", 0),
                "tg_avg": result.get("tg_avg", 0),
                "latency_p50": result.get("latency_p50"),
                "latency_p90": result.get("latency_p90"),
                "latency_p99": result.get("latency_p99"),
                "timestamp": time.time(),
                "test_type": "automatic",
                "optimization_target": optimization_target,
                # For optimization, we'll use a combined score later
            }
            
            results.append(result_dict)
            log.info(f"Configuration {i+1} completed: "
                    f"pp_avg={result_dict['pp_avg']:.1f}, "
                    f"tg_avg={result_dict['tg_avg']:.1f}")
        
        log.info(f"Benchmark sweep completed: {len(results)} configurations tested "
                f"(elapsed: {time.time() - start_time:.1f}s)")
        
        # Phase 2: Run llama-batched-bench for top configurations (if time permits)
        # This measures multi-sequence/batched performance
        elapsed = time.time() - start_time
        if max_duration_sec == 0:
            log.info(f"Phase 2: No time limit (max_duration=0), running llama-batched-bench")
        elif elapsed < max_duration_sec * 0.7:
            log.info(f"Phase 2: Time budget OK ({elapsed:.1f}s < {max_duration_sec * 0.7:.1f}s), running llama-batched-bench")
        else:
            log.info(f"Phase 2: SKIPPED - time budget exceeded ({elapsed:.1f}s >= {max_duration_sec * 0.7:.1f}s)")
        
        if max_duration_sec == 0 or elapsed < max_duration_sec * 0.7:
            # Run batched bench for top 3 configurations by score
            try:
                log.info(f"Phase 2: Starting llama-batched-bench for top configs")
                batched_results = self._run_batched_bench_phase(
                    model_path, results, max_duration_sec, optimization_target
                )
                log.info(f"Phase 2: Completed llama-batched-bench, got {len(batched_results)} results")
                # Merge batched results into main results
                for i, r in enumerate(results):
                    if r.get("success", False) and i < len(batched_results):
                        r["batched_bench"] = batched_results[i]
            except Exception as e:
                log.warning(f"Batched bench phase failed: {e}")
        else:
            log.info(f"Phase 2: Not running due to time constraints")
        
        return results

    def _run_batched_bench_phase(
        self,
        model_path: str,
        llama_bench_results: List[Dict[str, Any]],
        max_duration_sec: int,
        optimization_target: str
    ) -> List[Dict[str, Any]]:
        """
        Run llama-batched-bench for top configurations from llama-bench phase.
        Batched bench runs a full sweep with comma-separated parameters in one command.
        
        Args:
            model_path: Path to model file
            llama_bench_results: Results from llama-bench phase
            max_duration_sec: Remaining time budget
            optimization_target: Target metric for optimization
            
        Returns:
            List of batched bench result dictionaries
        """
        # Check for cancellation before starting phase 2
        if self._cancel_requested:
            log.info("Batched bench phase cancelled before starting")
            return []
        
        # Sort results by score (composite of pp_avg and tg_avg based on optimization_target)
        def score_func(r):
            if not r.get("success", False):
                return -1
            if optimization_target == 'throughput':
                return r.get("tg_avg", 0)
            elif optimization_target == 'latency':
                # For latency, lower is better - use inverse
                tg = r.get("tg_avg", 0)
                return 1000.0 / tg if tg > 0 else 0
            else:  # balanced
                return r.get("pp_avg", 0) * 0.3 + r.get("tg_avg", 0) * 0.7
        
        # Get top 3 successful configs
        successful = [r for r in llama_bench_results if r.get("success", False)]
        successful.sort(key=score_func, reverse=True)
        top_configs = successful[:3]
        
        if not top_configs:
            return []
        
        # Collect sweep parameters from top configs
        n_prompt_values = sorted(set(c.get("config", {}).get("n_prompt", 512) for c in top_configs))
        n_gen_values = sorted(set(c.get("config", {}).get("n_gen", 128) for c in top_configs))
        parallel_values = sorted(set(c.get("config", {}).get("parallel", 1) for c in top_configs))
        
        # Use first config as base (they should have similar ngl, batch, etc.)
        base_config = top_configs[0].get("config", {})
        
        log.info(f"Running llama-batched-bench sweep: "
                f"ngl={base_config.get('n_gpu_layers')}, batch={base_config.get('batch_size')}, "
                f"npp={n_prompt_values}, ntg={n_gen_values}, npl={parallel_values}")
        
        def cancel_callback():
            """Callback to signal cancellation to the runner."""
            self._cancel_requested = True
        
        try:
            runner = BenchmarkRunner(cancel_callback=cancel_callback)
            self._active_runner = runner
            try:
                bench_result = runner.run_llama_batched_bench(model_path, {
                    "n_gpu_layers": base_config.get("n_gpu_layers", 99),
                    "batch_size": base_config.get("batch_size", 4096),
                    "ubatch_size": base_config.get("ubatch_size", 1024),
                    "threads": base_config.get("threads", 16),
                    "threads_batch": base_config.get("threads_batch", 16),
                    "cache_type_k": base_config.get("cache_type_k", "q4_0"),
                    "cache_type_v": base_config.get("cache_type_v", "q4_0"),
                    "flash_attn": base_config.get("flash_attn", "auto"),
                    "ctx_size": base_config.get("ctx_size", 4096),
                    "parallel": parallel_values,  # List for sweep
                    "n_prompt": n_prompt_values,  # List for sweep
                    "n_gen": n_gen_values,        # List for sweep
                    "timeout_sec": 600,
                })
            finally:
                self._active_runner = None
            
            # Check if cancelled during run
            if self._cancel_requested:
                log.info("Batched bench phase cancelled during execution")
                return []
            
            # Parse batched bench results and map back to individual configs
            batched_results = self._parse_batched_bench_results(
                bench_result, top_configs, n_prompt_values, n_gen_values, parallel_values
            )
            
            return batched_results
                
        except Exception as e:
            self._active_runner = None
            log.warning(f"Batched bench phase failed: {e}")
            # Return empty results for each top config
            return [{"config": c.get("config", {}), "success": False, "error": str(e)} for c in top_configs]

    def _parse_batched_bench_results(
        self,
        bench_result: Dict[str, Any],
        top_configs: List[Dict[str, Any]],
        n_prompt_values: List[int],
        n_gen_values: List[int],
        parallel_values: List[int]
    ) -> List[Dict[str, Any]]:
        """
        Parse batched bench results and map to individual configs.
        
        Batched bench returns results for all combinations of npp, ntg, npl.
        We need to map these back to the original configs from phase 1.
        """
        batched_results = []
        
        # Extract per-parallel results if available
        parallel_results = bench_result.get("parallel_results", [])
        
        for i, result in enumerate(top_configs):
            config = result.get("config", {})
            target_npp = config.get("n_prompt", 512)
            target_ntg = config.get("n_gen", 128)
            target_npl = config.get("parallel", 1)
            
            # Find matching result from batched bench sweep
            matched = None
            for pr in parallel_results:
                if (pr.get("n_prompt") == target_npp and 
                    pr.get("n_gen") == target_ntg and
                    pr.get("parallel") == target_npl):
                    matched = pr
                    break
            
            if matched:
                batched_results.append({
                    "config": config,
                    "success": True,
                    "batched_throughput": matched.get("throughput", 0),
                    "max_throughput": bench_result.get("max_throughput", 0),
                    "optimal_parallel": bench_result.get("optimal_parallel", target_npl),
                    "speed_pp": bench_result.get("speed_pp", []),
                    "speed_tg": bench_result.get("speed_tg", []),
                    "timestamp": time.time(),
                })
            else:
                # No exact match, use overall metrics
                batched_results.append({
                    "config": config,
                    "success": True,
                    "batched_throughput": bench_result.get("speed", bench_result.get("throughput", 0)),
                    "max_throughput": bench_result.get("max_throughput", 0),
                    "optimal_parallel": bench_result.get("optimal_parallel", target_npl),
                    "speed_pp": bench_result.get("speed_pp", []),
                    "speed_tg": bench_result.get("speed_tg", []),
                    "timestamp": time.time(),
                })
        
        return batched_results
        
    def deep_sweep(
        self,
        model_path: str,
        max_duration_sec: int = 0,  # 0 = no limit
        optimization_target: str = 'throughput',
        progress_callback=None,
    ) -> Dict[str, Any]:
        """
        Run a focused deep sweep using llama-batched-bench native sweep capability.
        
        Designed for agentic coding use case (~30k context):
        - Tests ctx_size at max, max/2, max/4 of model's max context
        - Tests batch_size [8192, 16384, 32768] (filtered by available memory)
        - Tests threads [12, 16]
        - Tests cache_type [f16, q4_0]
        - Uses native batched-bench sweep for npp, ntg, npl
        
        Args:
            model_path: Path to model file
            max_duration_sec: Max time (0 = unlimited)
            optimization_target: Target metric for selecting best result
            progress_callback: Optional callback(run_idx, total_runs, config, result)
            
        Returns:
            Dictionary with all sweep results and best configuration
        """
        import os
        import time
        
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Model file not found: {model_path}")
        
# Register this optimizer instance for cancellation support
        _register_optimizer(model_path, self)
        try:
            # Get hardware info
            hardware_info = get_hardware_info()
            
            # Extract model info for cache key and config generation
            model_info = {}
            try:
                model_stat = os.stat(model_path)
                model_info["file_size_gb"] = round(model_stat.st_size / (1024**3), 2)
                model_info["file_path"] = model_path
                model_info["ctx_size"] = 4096  # default, will be updated after getting max context
            except OSError:
                model_info["file_size_gb"] = 0
                model_info["file_path"] = model_path
                model_info["ctx_size"] = 4096
            
            total_vram = hardware_info.get("total_gpu_vram_gb", 0)
            cpu_logical = hardware_info.get("cpu_count_logical", 16)
            cpu_physical = hardware_info.get("cpu_count_physical", 8)
            mem_total = hardware_info.get("memory_total_gb", 32)
            
            # Get model max context size
            model_ctx = self._get_model_max_context(model_path, hardware_info)
            log.info(f"Model max context size: {model_ctx}")
            
            # Update model_info with actual context size
            model_info["ctx_size"] = model_ctx
            
            # Context sizes to test: max, max/2, max/4 (minimum 4096)
            ctx_sizes = []
            for divisor in [1, 2, 4]:
                ctx = max(4096, model_ctx // divisor)
                if ctx not in ctx_sizes:
                    ctx_sizes.append(ctx)
            
            # Batch sizes to test - focus on practical sizes for agentic coding (large context)
            # Filter by estimated VRAM: batch * ctx * 2 bytes * num_layers ≈ VRAM needed
            # For 32 layers, head_dim=128, n_kv_heads=8: KV per token = 2 * 128 * 8 = 2048 bytes
            # Total KV = batch * ctx * 2048 bytes (for non-shared prompt) or ctx * 2048 + batch * ctx * 2048 (shared)
            # We'll use a conservative estimate: batch * ctx * 4 KB = GB
            batch_sizes = [8192, 16384, 32768]
            max_ctx = max(ctx_sizes)
            filtered_batch = []
            for bs in batch_sizes:
                # Batch size must fit within context size
                if bs > max_ctx:
                    continue
                # Conservative VRAM estimate: batch * ctx * 4 KB (covers KV cache for all layers)
                for ctx in ctx_sizes:
                    est_gb = (bs * ctx * 4) / 1e9
                    if total_vram > 0:
                        if est_gb <= total_vram * 0.7:  # Leave headroom for model weights
                            if bs not in filtered_batch:
                                filtered_batch.append(bs)
                            break
                    else:
                        # No GPU - use system RAM
                        if est_gb <= mem_total * 0.3:
                            if bs not in filtered_batch:
                                filtered_batch.append(bs)
                            break
            # Fallback batch size proportional to context size
            # For small context models, use smaller batch sizes
            max_ctx = max(ctx_sizes)
            if max_ctx <= 4096:
                fallback_batch = 1024
            elif max_ctx <= 8192:
                fallback_batch = 2048
            elif max_ctx <= 16384:
                fallback_batch = 4096
            else:
                fallback_batch = 8192
            batch_sizes = filtered_batch or [fallback_batch]  # Fallback to smaller batch
            
            # Threads to test - adapt to available CPUs
            # Use a range of thread counts up to cpu_logical
            if cpu_logical >= 16:
                threads_options = [cpu_logical, cpu_logical // 2, 12]
            elif cpu_logical >= 8:
                threads_options = [cpu_logical, cpu_logical // 2]
            else:
                threads_options = [cpu_logical]
            threads_options = [t for t in threads_options if t > 0 and t <= cpu_logical]
            threads_options = list(dict.fromkeys(threads_options))  # Deduplicate while preserving order
            
            # Cache types to test
            cache_types = [("f16", "f16"), ("q4_0", "q4_0")]
            if total_vram >= 16:
                cache_types.insert(0, ("q8_0", "q8_0"))  # Add q8_0 for high VRAM
            
            # Fixed parameters - adapt ubatch_size to batch sizes
            # ubatch_size should be <= batch_size
            max_batch = max(batch_sizes) if batch_sizes else 4096
            ubatch_size = min(4096, max_batch)
            fixed_params = {
                "n_gpu_layers": 99,
                "ubatch_size": ubatch_size,
                "flash_attn": "auto",
            }
            
            # Native sweep parameters for batched-bench
            # Adjust npp based on model's max context
            max_ctx = max(ctx_sizes)
            if max_ctx >= 32768:
                sweep_npp = [8192, 16384, 32768]
            elif max_ctx >= 16384:
                sweep_npp = [4096, 8192, 16384]
            elif max_ctx >= 8192:
                sweep_npp = [2048, 4096, 8192]
            else:
                sweep_npp = [1024, 2048, 4096]
            sweep_ntg = [128, 256, 512]
            sweep_npl = [1, 4]  # auto (kv-unified) handled separately
            
            # Filter sweep params by max context size
            sweep_npp = [p for p in sweep_npp if p <= max(ctx_sizes)]
            
            # Build FOCUSED run configurations (~10-15 runs)
            # Strategy: Test each ctx_size with a few representative batch/thread/cache combos
            run_configs = []
            
            for ctx in ctx_sizes:
                # For each context size, test a focused set of combinations
                # Prioritize: max batch that fits, then half batch
                valid_batches = [b for b in batch_sizes if b <= ctx]
                if not valid_batches:
                    continue
                
                max_batch = max(valid_batches)
                mid_batch = valid_batches[len(valid_batches) // 2] if len(valid_batches) > 1 else max_batch
                
                # Representative combinations per ctx size - use adaptive thread counts
                primary_threads = threads_options[0] if threads_options else cpu_logical
                secondary_threads = threads_options[1] if len(threads_options) > 1 else primary_threads
                
                ctx_configs = [
                    # 1. Max batch, primary threads, f16 cache (best performance)
                    {"batch_size": max_batch, "threads": primary_threads, "cache_type_k": "f16", "cache_type_v": "f16"},
                    # 2. Max batch, primary threads, q4_0 cache (memory efficient)
                    {"batch_size": max_batch, "threads": primary_threads, "cache_type_k": "q4_0", "cache_type_v": "q4_0"},
                    # 3. Mid batch, primary threads, f16 cache
                    {"batch_size": mid_batch, "threads": primary_threads, "cache_type_k": "f16", "cache_type_v": "f16"},
                    # 4. Mid batch, secondary threads, f16 cache (test thread scaling)
                    {"batch_size": mid_batch, "threads": secondary_threads, "cache_type_k": "f16", "cache_type_v": "f16"},
                ]
                
                # Add q8_0 for high VRAM
                if total_vram >= 16:
                    ctx_configs.append({"batch_size": max_batch, "threads": primary_threads, "cache_type_k": "q8_0", "cache_type_v": "q8_0"})
                
                # Add configs for this ctx_size
                for combo in ctx_configs:
                    if combo["threads"] > cpu_logical:
                        continue
                    config = {
                        **fixed_params,
                        "ctx_size": ctx,
                        "batch_size": combo["batch_size"],
                        "threads": combo["threads"],
                        "threads_batch": combo["threads"],
                        "cache_type_k": combo["cache_type_k"],
                        "cache_type_v": combo["cache_type_v"],
                    }
                    run_configs.append(config)
            
            # Deduplicate
            seen = set()
            unique_configs = []
            for config in run_configs:
                key = tuple(sorted(config.items()))
                if key not in seen:
                    seen.add(key)
                    unique_configs.append(config)
            
            run_configs = unique_configs
            log.info(f"Deep sweep: {len(run_configs)} batched-bench runs planned")
            
            # Reset cancellation flag at start
            self._cancel_requested = False
            
            # Run batched-bench for each config with per-run sweep parameters
            all_results = []
            start_time = time.time()
            
            def cancel_callback():
                """Callback to signal cancellation to the runner."""
                self._cancel_requested = True
            
            for i, config in enumerate(run_configs):
                # Check for cancellation before starting
                if self._cancel_requested:
                    log.info(f"Deep sweep cancelled before run {i+1}")
                    break
                
                if max_duration_sec > 0 and (time.time() - start_time) >= max_duration_sec:
                    log.info(f"Deep sweep time limit reached ({max_duration_sec}s), stopping")
                    break
                
                ctx = config["ctx_size"]
                batch = config["batch_size"]
                
                # Filter sweep parameters per run to ensure KV cache fits in context
                # For llama-batched-bench: n_kv = batch * (npp + ntg) * npl (prompt not shared)
                # or n_kv = npp + batch * ntg * npl (prompt shared, -pps)
                # We'll use conservative estimate without -pps
                
                # Max npp for this ctx and batch (with npl=4, ntg=128 minimum)
                max_npp = max(512, (ctx // (batch * 4)) - 512) if batch > 0 else ctx
                max_npp = min(max_npp, ctx)  # Can't exceed context
                
                # Filter sweep params for this run
                run_npp = [p for p in sweep_npp if p <= max_npp]
                run_ntg = [p for p in sweep_ntg if p <= 512]  # Keep reasonable generation sizes
                run_npl = [p for p in sweep_npl if p <= 4]
                
                if not run_npp:
                    run_npp = [min(512, ctx)]
                if not run_ntg:
                    run_ntg = [128]
                if not run_npl:
                    run_npl = [1]
                
                log.info(f"Deep sweep run {i+1}/{len(run_configs)}: "
                        f"ctx={ctx}, batch={batch}, threads={config['threads']}, cache={config['cache_type_k']}, "
                        f"sweep: npp={run_npp}, ntg={run_ntg}, npl={run_npl}")
                
                # Create runner with cancellation support
                runner = BenchmarkRunner(cancel_callback=cancel_callback)
                self._active_runner = runner
                try:
                    result = runner.run_llama_batched_bench(model_path, {
                        **config,
                        "n_prompt": run_npp,
                        "n_gen": run_ntg,
                        "parallel": run_npl,
                        "timeout_sec": 600,
                    })
                except Exception as e:
                    self._active_runner = None
                    log.warning(f"Deep sweep run {i+1} failed: {e}")
                    all_results.append({
                        "config": config,
                        "success": False,
                        "error": str(e),
                        "timestamp": time.time(),
                    })
                    continue
                finally:
                    self._active_runner = None
                
                # Check if cancelled during run
                if self._cancel_requested:
                    log.info(f"Deep sweep run {i+1} cancelled during execution")
                    break
                
                # Result contains aggregated metrics from sweep
                run_result = {
                    "config": config,
                    "success": True,
                    "batched_throughput": result.get("batch_throughput", 0),
                    "pp_avg": result.get("pp_avg", 0),
                    "tg_avg": result.get("tg_avg", 0),
                    "latency_p50": result.get("latency_p50"),
                    "parallel_results": result.get("parallel_results", []),
                    "timestamp": time.time(),
                }
                all_results.append(run_result)
                
                if progress_callback:
                    progress_callback(i+1, len(run_configs), config, run_result)
            
            # Find best result by optimization target
            successful = [r for r in all_results if r.get("success", False)]
            best_config = {}
            best_score = -1
            
            if successful:
                if optimization_target == 'throughput':
                    best_result = max(successful, key=lambda x: x.get("batched_throughput", 0))
                elif optimization_target == 'latency':
                    best_result = min(successful, key=lambda x: x.get("latency_p50", float('inf')))
                else:  # balanced
                    best_result = max(successful, key=lambda x: 
                        (x.get("pp_avg", 0) * x.get("batched_throughput", 0)) ** 0.5 
                        if x.get("pp_avg", 0) > 0 and x.get("batched_throughput", 0) > 0 else 0)
                
                best_config = best_result.get("config", {})
                best_score = best_result.get("batched_throughput", 0)
            
            # Prepare summary
            elapsed = time.time() - start_time
            summary = {
                "model_path": model_path,
                "model_max_context": model_ctx,
                "total_runs": len(run_configs),
                "completed_runs": len([r for r in all_results if r.get("success", False)]),
                "failed_runs": len([r for r in all_results if not r.get("success", False)]),
                "elapsed_sec": elapsed,
                "optimization_target": optimization_target,
                "best_config": best_config,
                "best_score": best_score,
                "all_results": all_results,
            }
            
            log.info(f"Deep sweep completed in {elapsed:.1f}s: "
                    f"{summary['completed_runs']}/{summary['total_runs']} successful, "
                    f"best: {best_config} (score: {best_score:.1f})")
            
            # Save results to cache so they persist for later UI display
            cache_result = {
                "optimal_settings": best_config,
                "benchmark_results": all_results,
                "hardware_info": hardware_info,
                "model_info": model_info,
                "cache_key": self._generate_cache_key(model_path, hardware_info, optimization_target),
                "optimization_target": optimization_target,
            }
            self._save_to_cache(
                self._generate_cache_key(model_path, hardware_info, optimization_target),
                cache_result,
                hardware_info
            )
            
            return summary
        finally:
            # Unregister this optimizer instance
            _unregister_optimizer(model_path)
    
    def _get_model_max_context(self, model_path: str, hardware_info: Dict[str, Any]) -> int:
        """Extract max context size from GGUF model metadata.
        
        Tries in order:
        1. Python gguf library (most reliable, no binary needed)
        2. llama-cli --dump-gguf (if supported by build)
        3. Filename parsing
        4. File-size heuristic
        """
        import re
        
        # Method 1: Try Python gguf library (most reliable)
        try:
            import gguf
            reader = gguf.GGUFReader(model_path)
            # Handle both old and new gguf library APIs
            # Old API: reader.metadata_kv (dict-like)
            # New API: reader.fields (dict-like) or metadata via get_field
            meta = None
            if hasattr(reader, 'metadata_kv'):
                meta = reader.metadata_kv
            elif hasattr(reader, 'fields'):
                meta = reader.fields
            elif hasattr(reader, 'get_field'):
                # Try to get specific fields
                meta = {}
                for key in ["context_length", "llama.context_length", "qwen.context_length", "phi3.context_length", "mistral.context_length", "gemma.context_length", "falcon.context_length", "mpt.context_length", "baichuan.context_length", "internlm.context_length", "yarn.context_length", "rope.max_position_embeddings"]:
                    try:
                        val = reader.get_field(key)
                        if val is not None:
                            # ReaderField objects need .value or parts[-1].data to get actual value
                            if hasattr(val, 'value'):
                                meta[key] = val.value
                            elif hasattr(val, 'parts') and val.parts:
                                data = val.parts[-1].data
                                # Convert bytes to int if needed (little-endian)
                                if isinstance(data, bytes):
                                    meta[key] = int.from_bytes(data, byteorder='little', signed=False)
                                else:
                                    meta[key] = data
                            else:
                                meta[key] = val
                    except Exception:
                        pass
            
            if meta is not None:
                # INFO: Log raw metadata for ALL context_length keys so we can see what the GGUF library returns
                context_keys_to_log = [k for k in meta.keys() if 'context_length' in k.lower() or 'max_position_embeddings' in k.lower()]
                for k in context_keys_to_log:
                    v = meta[k]
                    log.info(f"GGUF raw {k}: {v!r} (type: {type(v).__name__})")
                    if hasattr(v, 'value'):
                        log.info(f"  .value = {v.value!r} (type: {type(v.value).__name__})")
                    if hasattr(v, 'parts') and v.parts:
                        log.info(f"  .parts[-1].data = {v.parts[-1].data!r} (len={len(v.parts[-1].data) if hasattr(v.parts[-1], 'data') and hasattr(v.parts[-1].data, '__len__') else 'N/A'})")
                        # Also log all parts for debugging
                        for i, part in enumerate(v.parts):
                            log.info(f"    parts[{i}] = {part!r} (type: {type(part).__name__})")
                
                # Convert ReaderField values to primitive types if needed
                def convert_value(v):
                    """Convert a ReaderField or similar object to a primitive Python value."""
                    if hasattr(v, 'value'):
                        val = v.value
                        # If value is memoryview/bytes, try to convert
                        if hasattr(val, 'tobytes'):
                            val = val.tobytes()
                        if isinstance(val, (bytes, bytearray)) and len(val) >= 2:
                            for byteorder in ('little', 'big'):
                                for size in (2, 4, 8):
                                    if len(val) >= size:
                                        try:
                                            return int.from_bytes(val[:size], byteorder=byteorder, signed=False)
                                        except Exception:
                                            continue
                        return val
                    if hasattr(v, 'parts') and v.parts:
                        # The gguf library stores values in parts array
                        # parts[0] = offset, parts[1] = key name, parts[2] = type info, parts[3] = value (as memmap/array)
                        # Try to get the last part which should contain the actual value
                        for part in reversed(v.parts):
                            # Handle numpy memmap/array - extract scalar value
                            try:
                                import numpy as np
                                if isinstance(part, (np.ndarray, np.memmap)):
                                    # If it's a 1-element array, return the scalar
                                    if part.size == 1:
                                        return int(part.item())
                                    elif part.size > 1:
                                        # For multi-element, try first element
                                        return int(part.flat[0])
                            except ImportError:
                                pass
                            # Handle objects with .item() method (numpy scalars)
                            if hasattr(part, 'item'):
                                try:
                                    return int(part.item())
                                except (ValueError, TypeError):
                                    pass
                            # Handle objects with __int__
                            if hasattr(part, '__int__'):
                                try:
                                    return int(part)
                                except (ValueError, TypeError):
                                    pass
                            # Handle bytes
                            if hasattr(part, 'tobytes'):
                                data = part.tobytes()
                                if isinstance(data, (bytes, bytearray)) and len(data) >= 2:
                                    for byteorder in ('little', 'big'):
                                        for size in (2, 4, 8):
                                            if len(data) >= size:
                                                try:
                                                    return int.from_bytes(data[:size], byteorder=byteorder, signed=False)
                                                except Exception:
                                                    continue
                            # Handle .data attribute
                            if hasattr(part, 'data'):
                                data = part.data
                                if isinstance(data, (bytes, bytearray)) and len(data) >= 2:
                                    for byteorder in ('little', 'big'):
                                        for size in (2, 4, 8):
                                            if len(data) >= size:
                                                try:
                                                    return int.from_bytes(data[:size], byteorder=byteorder, signed=False)
                                                except Exception:
                                                    continue
                        # Fallback: try last part's data
                        last_part = v.parts[-1]
                        if hasattr(last_part, 'data'):
                            data = last_part.data
                            if isinstance(data, (bytes, bytearray)) and len(data) >= 2:
                                for byteorder in ('little', 'big'):
                                    for size in (2, 4, 8):
                                        if len(data) >= size:
                                            try:
                                                return int.from_bytes(data[:size], byteorder=byteorder, signed=False)
                                            except Exception:
                                                continue
                    # Handle numpy integers and other numeric types
                    try:
                        import numpy as np
                        if isinstance(v, (np.integer, np.floating)):
                            return int(v)
                        if isinstance(v, (np.ndarray, np.memmap)):
                            if v.size == 1:
                                return int(v.item())
                    except ImportError:
                        pass
                    # Handle any object with __int__ method
                    if hasattr(v, '__int__'):
                        try:
                            return int(v)
                        except (ValueError, TypeError):
                            pass
                    return v
                
                # Apply conversion to all metadata values, handling various GGUF library formats
                meta = {k: convert_value(v) for k, v in meta.items()}
                
                # Additional pass: try to convert string values to int if they look like numbers
                for k, v in meta.items():
                    if isinstance(v, str) and v.isdigit():
                        meta[k] = int(v)
                    elif isinstance(v, str):
                        # Try to parse as int, ignoring any non-numeric prefix/suffix
                        try:
                            meta[k] = int(float(v))
                        except (ValueError, TypeError):
                            pass
                
                # INFO: Log converted metadata for all context keys
                context_keys_to_log = [k for k in meta.keys() if 'context_length' in k.lower() or 'max_position_embeddings' in k.lower()]
                for k in context_keys_to_log:
                    log.info(f"GGUF converted {k}: {meta[k]!r} (type: {type(meta[k]).__name__})")
                
                # Keys to check in order of preference
                # Includes architecture-specific keys (e.g., phi3.context_length, mistral.context_length, etc.)
                context_keys = [
                    "context_length",
                    "llama.context_length",
                    "qwen.context_length",
                    "qwen35.context_length",  # Qwen 3.5 uses this prefix
                    "phi3.context_length",
                    "mistral.context_length",
                    "gemma.context_length",
                    "falcon.context_length",
                    "mpt.context_length",
                    "baichuan.context_length",
                    "internlm.context_length",
                    "yarn.context_length",
                    "rope.max_position_embeddings",
                ]
                
                for key in context_keys:
                    if key in meta:
                        try:
                            ctx = int(meta[key])
                            # Accept any reasonable context size (powers of 2 between 512 and 1M)
                            if ctx >= 512 and ctx <= 1048576 and (ctx & (ctx - 1)) == 0:
                                log.info(f"Found model max context from GGUF metadata (python): {ctx} (key: {key})")
                                return ctx
                            elif ctx >= 512 and ctx <= 1048576:
                                # Also accept non-power-of-2 values in reasonable range
                                log.info(f"Found model max context from GGUF metadata (python): {ctx} (key: {key})")
                                return ctx
                            else:
                                log.debug(f"Key {key} has value {ctx} which is outside reasonable range (512-1048576)")
                        except (ValueError, TypeError) as e:
                            log.debug(f"Could not convert {key}={meta[key]!r} to int: {e}")
                            continue
                
                log.warning(f"Could not find valid context_length in GGUF metadata via python library. Available keys: {list(meta.keys())[:20]}...")
            else:
                log.warning("Could not access GGUF metadata - unknown gguf library API")
        except ImportError:
            log.debug("gguf Python library not available")
        except Exception as e:
            log.warning(f"Failed to read GGUF metadata via python: {e}")
        
        # Method 2: Try llama-cli --dump-gguf
        if LLAMA_CLI and Path(LLAMA_CLI).exists():
            try:
                # Run llama-cli with --dump-gguf to get all metadata
                cmd = [str(LLAMA_CLI), "-m", model_path, "--dump-gguf"]
                log.debug(f"Running llama-cli to dump GGUF metadata: {' '.join(cmd)}")
                
                # Use binary's directory as working directory for Windows binaries on WSL
                cwd = None
                if WSL and str(LLAMA_CLI).endswith('.exe'):
                    cwd = Path(LLAMA_CLI).parent
                
                result = subprocess.run(
                    cmd,
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                
                if result.returncode == 0:
                    output = result.stdout
                    # Parse for context length fields
                    patterns = [
                        r'context_length\s*=\s*(\d+)',
                        r'llama\.context_length\s*=\s*(\d+)',
                        r'qwen\.context_length\s*=\s*(\d+)',
                        r'rope\.max_position_embeddings\s*=\s*(\d+)',
                    ]
                    
                    for pattern in patterns:
                        match = re.search(pattern, output, re.IGNORECASE)
                        if match:
                            ctx = int(match.group(1))
                            if ctx in [512, 1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144, 524288]:
                                log.info(f"Found model max context from GGUF metadata: {ctx} (pattern: {pattern})")
                                return ctx
                    
                    log.warning(f"Could not find context_length in GGUF metadata output")
                else:
                    log.warning(f"llama-cli --dump-gguf failed: {result.stderr}")
                    
            except subprocess.TimeoutExpired:
                log.warning(f"llama-cli --dump-gguf timed out")
            except Exception as e:
                log.warning(f"Failed to run llama-cli --dump-gguf: {e}")
        
        # Method 3: Fallback - try to extract from filename
        filename = os.path.basename(model_path).lower()
        
        # Pattern: 32k, 64k, 128k, 256k, etc.
        match = re.search(r'(\d+)k', filename)
        if match:
            return int(match.group(1)) * 1024
        
        # Pattern: 32768, 65536, 131072
        match = re.search(r'(\d{5,})', filename)
        if match:
            val = int(match.group(1))
            if val in [4096, 8192, 16384, 32768, 65536, 131072, 262144]:
                return val
        
        # Method 4: Default based on common model sizes
        file_size_gb = 0
        try:
            file_size_gb = os.path.getsize(model_path) / (1024**3)
        except OSError:
            pass
        
        if file_size_gb >= 15:  # 70B+
            return 131072
        elif file_size_gb >= 8:  # 30B+
            return 65536
        elif file_size_gb >= 4:  # 9B-14B
            return 32768
        else:
            return 32768  # Default

    def select_optimal_config(
        self, 
        results: List[Dict[str, Any]], 
        optimization_target: str = 'balanced'
    ) -> Dict[str, Any]:
        """
        Select optimal configuration from benchmark results.
        
        Args:
            results: List of benchmark result dictionaries from run_benchmark_sweep
            optimization_target: Target metric ('throughput', 'latency', or 'balanced')
            
        Returns:
            Best configuration dictionary
        """
        # Filter successful results
        successful_results = [r for r in results if r.get("success", False)]
        
        if not successful_results:
            log.warning("No successful benchmark results, returning first attempted config")
            if results:
                return results[0].get("config", {})
            return {}
            
        if optimization_target == 'throughput':
            # Maximize token generation rate (tg_avg)
            best_result = max(successful_results, 
                            key=lambda x: x.get("tg_avg", 0))
        elif optimization_target == 'latency':
            # Minimize latency - look for latency metrics in order of preference
            def latency_score(result):
                # Lower latency is better, so we return negative for max() to work as min()
                # Check latency_p50 first, then p90, then p99, then fall back to inverse tg_avg
                latency_p50 = result.get("latency_p50")
                if latency_p50 is not None:
                    return -latency_p50  # Negative so max() gives us the minimum latency
                latency_p90 = result.get("latency_p90")
                if latency_p90 is not None:
                    return -latency_p90
                latency_p99 = result.get("latency_p99")
                if latency_p99 is not None:
                    return -latency_p99
                # Fall back to inverse of tg_avg (higher tg_avg = lower latency per token)
                # Convert to milliseconds: latency_ms = 1000.0 / tg_avg
                tg_avg = result.get("tg_avg", 0)
                if tg_avg > 0:
                    return -1000.0 / tg_avg  # Negative for max()
                return 0  # Worst possible score (no latency data and no tg_avg)
            best_result = max(successful_results, key=latency_score)
        elif optimization_target == 'balanced':
            # Balance prompt processing and token generation
            # Use geometric mean of pp_avg and tg_avg
            def balanced_score(result):
                pp = result.get("pp_avg", 0)
                tg = result.get("tg_avg", 0)
                if pp <= 0 or tg <= 0:
                    return 0
                return (pp * tg) ** 0.5  # geometric mean
            best_result = max(successful_results, key=balanced_score)
        else:
            # Default to balanced
            return self.select_optimal_config(results, 'balanced')
             
        log.info(f"Selected optimal config: {best_result.get('config', {})} "
                f"(target: {optimization_target}, "
                f"pp_avg={best_result.get('pp_avg', 0):.1f}, "
                f"tg_avg={best_result.get('tg_avg', 0):.1f})")
                 
        return best_result.get("config", {})
        
    def optimize(
        self, 
        model_path: str, 
        max_duration_sec: int = 100,
        optimization_target: str = 'balanced'
    ) -> Dict[str, Any]:
        """
        Main optimization function that orchestrates the entire process.
        
        Args:
            model_path: Path to model file
            max_duration_sec: Maximum time to spend on benchmarking
            optimization_target: Target metric for optimization
            
        Returns:
            Dictionary containing optimal settings and benchmark results
        """
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Model file not found: {model_path}")
        
# Unregister any stale optimizer for this model (in case previous run crashed)
        _unregister_optimizer(model_path)
        # Register this optimizer instance for cancellation support
        _register_optimizer(model_path, self)
        
        # Get hardware and model info (call once and reuse)
        hardware_info = get_hardware_info()
         
        # Extract model info for cache key and config generation
        model_info = {}
        try:
            model_stat = os.stat(model_path)
            model_info["file_size_gb"] = round(model_stat.st_size / (1024**3), 2)
            model_info["file_path"] = model_path
            # Try to get context size from model filename or metadata
            # This is a simplified approach - in practice might need to parse model
            model_info["ctx_size"] = 4096  # default
        except OSError:
            model_info["file_size_gb"] = 0
            model_info["file_path"] = model_path
            model_info["ctx_size"] = 4096
         
        # Generate cache key (model + hardware + optimization target, NOT duration for incremental caching)
        cache_key = self._generate_cache_key(model_path, hardware_info, optimization_target)
         
        # Try to load from cache first (but don't return early - we want incremental runs)
        cached_result = self._load_from_cache(cache_key, hardware_info)
        cached_benchmark_results = []
        if cached_result is not None and cached_result.get("benchmark_results"):
            cached_benchmark_results = cached_result["benchmark_results"]
            log.info(f"Loaded {len(cached_benchmark_results)} cached benchmark results for {model_path}")
         
        log.info(f"Starting optimization for {model_path}")
         
        # Generate configuration combinations
        config_list = self.generate_config_combinations(hardware_info, model_info)
         
        # Check for empty config list - fall back to heuristic immediately
        if not config_list:
            log.warning("No benchmark configurations generated, falling back to heuristic settings")
            heuristic_settings = compute_optimal_settings(self._sanitize_hardware_info(hardware_info), model_info)
            # Convert to BenchmarkConfig-compatible keys
            if "gpu_layers" in heuristic_settings:
                heuristic_settings["n_gpu_layers"] = heuristic_settings.pop("gpu_layers")
            result = {
                "optimal_settings": heuristic_settings,
                "benchmark_results": cached_benchmark_results,  # Preserve any cached results
                "hardware_info": hardware_info,
                "model_info": model_info,
                "cache_key": cache_key,
                "optimization_target": optimization_target,
                "fallback_reason": "no_configurations_generated"
            }
            
            # Save to cache to prevent repeated attempts
            self._save_to_cache(cache_key, result, hardware_info)
            
            log.info(f"Optimization completed for {model_path} (via fallback: no configurations)")
            _unregister_optimizer(model_path)
            return result
         
        # Find which configs from config_list are missing from cached results
        def config_to_key(config: Dict[str, Any]) -> str:
            """Create a comparable key for a config dict."""
            # Only compare benchmark-relevant parameters
            relevant_keys = {
                "n_gpu_layers", "batch_size", "ubatch_size", "threads", "threads_batch",
                "cache_type_k", "cache_type_v", "flash_attn", "ctx_size", "parallel",
                "numa", "numa_value", "cpu_affinity", "cpu_strict",
                "spec_type", "spec_draft_n_max", "spec_ngram_size_n", "spec_ngram_size_m",
                "spec_draft_p_split", "swa_full",
                "n_prompt"
            }
            filtered = {k: v for k, v in config.items() if k in relevant_keys}
            return str(sorted(filtered.items()))
        
        # Build set of cached config keys (ONLY successful results count as valid)
        cached_config_keys = set()
        for r in cached_benchmark_results:
            if r.get("success", False):
                config_key = config_to_key(r.get("config", {}))
                cached_config_keys.add(config_key)
            else:
                log.debug(f"Skipping failed cached config: {config_to_key(r.get('config', {}))}")
        
        # Find missing configs
        missing_configs = []
        for config in config_list:
            if config_to_key(config) not in cached_config_keys:
                missing_configs.append(config)
        
        log.info(f"Configurations: {len(config_list)} total, {len(cached_benchmark_results)} cached, {len(missing_configs)} missing")
        
        # Track if we ran new benchmarks
        new_benchmarks_run = len(missing_configs) > 0
        
        # Run benchmark sweep ONLY for missing configurations
        new_benchmark_results = []
        if missing_configs:
            try:
                new_benchmark_results = self.run_benchmark_sweep(
                    model_path, missing_configs, max_duration_sec, optimization_target
                )
            except Exception as e:
                log.warning(f"Benchmark sweep failed with exception: {e}. Using cached results only.")
                # If we have cached results, use them; otherwise fall back
                if cached_benchmark_results:
                    new_benchmark_results = []
                else:
                    # No cached results - fall back to heuristic
                    heuristic_settings = compute_optimal_settings(self._sanitize_hardware_info(hardware_info), model_info)
                    if "gpu_layers" in heuristic_settings:
                        heuristic_settings["n_gpu_layers"] = heuristic_settings.pop("gpu_layers")
                    result = {
                        "optimal_settings": heuristic_settings,
                        "benchmark_results": [],
                        "hardware_info": hardware_info,
                        "model_info": model_info,
                        "cache_key": cache_key,
                        "optimization_target": optimization_target,
                        "fallback_reason": str(e)
                    }
                    self._save_to_cache(cache_key, result, hardware_info)
                    log.info(f"Optimization completed for {model_path} (via fallback)")
                    _unregister_optimizer(model_path)
                    return result
        
        # Combine cached + new results
        all_benchmark_results = cached_benchmark_results + new_benchmark_results
        
        # Check if any successful cached results are missing phase 2 (batched_bench) data
        # This can happen when phase 1 completed but phase 2 failed or wasn't run
        missing_phase2_configs = []
        for r in all_benchmark_results:
            if r.get("success", False) and "batched_bench" not in r:
                missing_phase2_configs.append(r.get("config", {}))
        
        # Run phase 2 for configs missing batched bench data
        if missing_phase2_configs:
            log.info(f"Phase 2: Running llama-batched-bench for {len(missing_phase2_configs)} configs missing phase 2 data")
            try:
                phase2_results = self._run_batched_bench_phase(
                    model_path, 
                    [r for r in all_benchmark_results if r.get("success", False) and "batched_bench" not in r],
                    max_duration_sec, 
                    optimization_target
                )
                # Merge phase 2 results into all_benchmark_results
                for i, r in enumerate(all_benchmark_results):
                    if r.get("success", False) and "batched_bench" not in r and i < len(phase2_results):
                        r["batched_bench"] = phase2_results[i]
            except Exception as e:
                log.warning(f"Phase 2 (catch-up) failed: {e}")
        
        # Select optimal configuration from ALL results (cached + new)
        optimal_config = self.select_optimal_config(
            all_benchmark_results, optimization_target
        )
        
        # If no successful results, fall back to heuristic settings
        successful_results = [r for r in all_benchmark_results if r.get("success", False)]
        if not optimal_config or not successful_results:
            log.warning("No successful benchmark results, falling back to heuristic settings")
            optimal_config = compute_optimal_settings(self._sanitize_hardware_info(hardware_info), model_info)
            # Convert to BenchmarkConfig-compatible keys
            if "gpu_layers" in optimal_config:
                optimal_config["n_gpu_layers"] = optimal_config.pop("gpu_layers")
        else:
            log.info(f"Selected optimal config from {len(successful_results)} successful results")
    
        # Create and return result with all historical results
        result = {
            "optimal_settings": optimal_config,
            "benchmark_results": all_benchmark_results,
            "hardware_info": hardware_info,
            "model_info": model_info,
            "cache_key": cache_key,
            "optimization_target": optimization_target,
            "new_benchmarks_run": new_benchmarks_run,
        }
        
        # Save to cache with all historical results
        self._save_to_cache(cache_key, result, hardware_info)
        
        log.info(f"Optimization completed for {model_path} ({len(all_benchmark_results)} total benchmark results, new_run: {new_benchmarks_run})")
        _unregister_optimizer(model_path)
        return result

    def run_single_benchmark(
        self,
        model_path: str,
        current_config: Dict[str, Any],
        max_duration_sec: int = 100
    ) -> Dict[str, Any]:
        """
        Run a single benchmark with the provided configuration.
        
        Args:
            model_path: Path to model file
            current_config: Current configuration to benchmark
            max_duration_sec: Maximum time for the benchmark
            
        Returns:
            Dictionary containing benchmark result and optimal settings (same as current config)
        """
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Model file not found: {model_path}")
        
        # Unregister any stale optimizer for this model (in case previous run crashed)
        _unregister_optimizer(model_path)
        # Register this optimizer instance for cancellation support
        _register_optimizer(model_path, self)
        
        # Get hardware and model info
        hardware_info = get_hardware_info()
        model_info = {}
        try:
            model_stat = os.stat(model_path)
            model_info["file_size_gb"] = round(model_stat.st_size / (1024**3), 2)
            model_info["file_path"] = model_path
            model_info["ctx_size"] = current_config.get("ctx_size", 4096)
        except OSError:
            model_info["file_size_gb"] = 0
            model_info["file_path"] = model_path
            model_info["ctx_size"] = 4096
        
        log.info(f"Running single benchmark for {model_path}")
        
        # Reset cancellation flag at start
        self._cancel_requested = False
        
        # Filter current_config to only include valid llama-bench parameters
        # llama-bench (regular) does NOT support: cache_reuse, cache_ram, swa_full, spec_type, spec_draft_n_max
        # Those are only for llama-batched-bench / llama-server
        valid_benchmark_params = {
            "n_gpu_layers", "batch_size", "ubatch_size", "threads", "threads_batch",
            "cache_type_k", "cache_type_v", "flash_attn",
            "numa", "numa_value", "cpu_affinity", "cpu_strict",
            "ctx_size", "parallel",
            "n_prompt", "n_gen",
        }
        
        filtered_config = {k: v for k, v in current_config.items() if k in valid_benchmark_params}
        
        # Set defaults for any missing required parameters
        defaults = {
            "n_gpu_layers": 99,
            "batch_size": 4096,
            "ubatch_size": 1024,
            "threads": 16,
            "threads_batch": 16,
            "cache_type_k": "q4_0",
            "cache_type_v": "q4_0",
            "flash_attn": "auto",
            "numa": False,
            "numa_value": "interleave",
            "cpu_affinity": "auto",
            "cpu_strict": False,
            "ctx_size": 4096,
            "parallel": 1,
            "n_prompt": 512,
            "n_gen": 128,
        }
        
        # Apply defaults for missing values
        for key, default_val in defaults.items():
            if key not in filtered_config:
                filtered_config[key] = default_val
        
        # Use timeout: 0 = no timeout (None), otherwise use max_duration_sec or 600s default
        if max_duration_sec == 0:
            filtered_config["timeout_sec"] = None
        else:
            filtered_config["timeout_sec"] = max_duration_sec if max_duration_sec > 0 else 600
        filtered_config["repetitions"] = 3
        
        # Sweep prompt sizes for agentic coding (large context)
        model_ctx = filtered_config.get("ctx_size", 4096)
        prompt_sizes = [8192, 16384, 32768]
        prompt_sizes = [p for p in prompt_sizes if p <= model_ctx]
        if not prompt_sizes:
            prompt_sizes = [min(model_ctx, 512)]
        
        log.info(f"Running single benchmark sweep for {model_path} with {len(prompt_sizes)} prompt sizes: {prompt_sizes}")
        
        all_benchmark_results = []
        
        def cancel_callback():
            """Callback to signal cancellation to the runner."""
            self._cancel_requested = True
        
        # Run benchmark for each prompt size
        for n_prompt in prompt_sizes:
            # Check for cancellation before starting
            if self._cancel_requested:
                log.info(f"Single benchmark cancelled before prompt size {n_prompt}")
                break
            
            bench_config = BenchmarkConfig(
                model_path=model_path,
                n_gpu_layers=filtered_config["n_gpu_layers"],
                batch_size=filtered_config["batch_size"],
                ubatch_size=filtered_config["ubatch_size"],
                threads=filtered_config["threads"],
                threads_batch=filtered_config["threads_batch"],
                cache_type_k=filtered_config["cache_type_k"],
                cache_type_v=filtered_config["cache_type_v"],
                flash_attn=filtered_config["flash_attn"],
                ctx_size=filtered_config["ctx_size"],
                n_prompt=n_prompt,
                n_gen=filtered_config.get("n_gen", 128),
                repetitions=filtered_config["repetitions"],
                timeout_sec=filtered_config["timeout_sec"],
                numa=filtered_config.get("numa", False),
                numa_value=filtered_config.get("numa_value", "interleave"),
                cpu_affinity=filtered_config.get("cpu_affinity", "auto"),
                cpu_strict=filtered_config.get("cpu_strict", False),
                parallel=filtered_config.get("parallel", 1),
            )
            
            runner = BenchmarkRunner(cancel_callback=cancel_callback)
            self._active_runner = runner
            try:
                result = runner.run_llama_bench(model_path, {
                    "n_gpu_layers": bench_config.n_gpu_layers,
                    "batch_size": bench_config.batch_size,
                    "ubatch_size": bench_config.ubatch_size,
                    "threads": bench_config.threads,
                    "threads_batch": bench_config.threads_batch,
                    "cache_type_k": bench_config.cache_type_k,
                    "cache_type_v": bench_config.cache_type_v,
                    "flash_attn": bench_config.flash_attn,
                    "ctx_size": bench_config.ctx_size,
                    "n_prompt": bench_config.n_prompt,
                    "n_gen": bench_config.n_gen,
                    "repetitions": bench_config.repetitions,
                    "timeout_sec": bench_config.timeout_sec,
                    "numa": bench_config.numa,
                    "numa_value": bench_config.numa_value,
                    "cpu_affinity": bench_config.cpu_affinity,
                    "cpu_strict": bench_config.cpu_strict,
                    "parallel": bench_config.parallel,
                })
            except Exception as e:
                self._active_runner = None
                log.warning(f"Single benchmark for prompt size {n_prompt} failed: {e}")
                all_benchmark_results.append({
                    "config": {**filtered_config, "n_prompt": n_prompt},
                    "success": False,
                    "error": str(e),
                    "timestamp": time.time(),
                    "test_type": "custom",
                    "optimization_target": "current_config_test",
                })
                continue
            finally:
                self._active_runner = None
            
            # Check if cancelled during run
            if self._cancel_requested:
                log.info(f"Single benchmark cancelled during prompt size {n_prompt}")
                break
            
            # run_llama_bench returns metrics dict on success, raises exception on failure
            benchmark_result = {
                "config": {**filtered_config, "n_prompt": n_prompt},
                "success": True,
                "tg_avg": result.get("tg_avg"),
                "pp_avg": result.get("pp_avg"),
                "pg_avg": result.get("pg_avg"),
                "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", ""),
                "duration_sec": result.get("duration_sec", 0),
                "timestamp": time.time(),
                "test_type": "custom",
                "optimization_target": "current_config_test",
            }
            
            all_benchmark_results.append(benchmark_result)
        
        # Load existing cache to preserve historical results and optimal settings
        cache_key = self._generate_cache_key(model_path, hardware_info, "current_config_test")
        cached_result = self._load_from_cache(cache_key, hardware_info)
        
        # Start with existing cached benchmark results (if any)
        if cached_result and cached_result.get("benchmark_results"):
            existing_configs = set()
            for r in all_benchmark_results:
                config_str = str(sorted(r.get("config", {}).items()))
                existing_configs.add(config_str)
            
            for hist_result in cached_result["benchmark_results"]:
                hist_config_str = str(sorted(hist_result.get("config", {}).items()))
                if hist_config_str not in existing_configs:
                    all_benchmark_results.append(hist_result)
        
        # The new benchmark results are already added in the loop above
        # No need to append again
        pass
        
        # Use optimal_settings from cache if available (from full optimization), otherwise current config
        if cached_result and cached_result.get("optimal_settings"):
            optimal_settings = cached_result["optimal_settings"].copy()
        else:
            optimal_settings = current_config.copy()
        
        # Save updated cache with all historical results
        result_to_cache = {
            "optimal_settings": optimal_settings,
            "benchmark_results": all_benchmark_results,
            "hardware_info": hardware_info,
            "model_info": model_info,
            "cache_key": cache_key,
            "optimization_target": cached_result.get("optimization_target", "balanced") if cached_result else "balanced",
        }
        self._save_to_cache(cache_key, result_to_cache, hardware_info)
        
        _unregister_optimizer(model_path)
        return {
            "optimal_settings": optimal_settings,
            "benchmark_results": all_benchmark_results,
            "hardware_info": hardware_info,
            "model_info": model_info,
            "cache_key": cache_key,
            "optimization_target": "current_config_test",
        }
        
    def cancel(self) -> None:
        """
        Signal cancellation of any running benchmark process.
        
        This method:
        1. Sets the cancellation flag
        2. Calls the cancel_callback if provided
        3. Terminates any active subprocess in the benchmark runner
        
        Note: This is called when the user clicks Cancel in the UI.
        It signals cancellation to the currently running benchmark.
        """
        self._cancel_requested = True
        
        if self._cancel_callback:
            self._cancel_callback()
        
        # Signal cancellation to the active benchmark runner
        if self._active_runner is not None:
            try:
                self._active_runner.cancel()
            except Exception as e:
                log.error(f"Failed to cancel benchmark runner: {e}")
        
        log.info("Cancellation requested for benchmark optimization")

    def _run_benchmark_with_cancellation(
        self,
        model_path: str,
        config: BenchmarkConfig,
        is_batched: bool = False,
    ) -> BenchmarkResult:
        """
        Run a benchmark with cancellation support.
        
        This wraps _run_benchmark to support cancellation via a callback.
        
        Args:
            model_path: Path to model file
            config: Benchmark configuration
            is_batched: Whether this is llama-batched-bench
            
        Returns:
            BenchmarkResult with metrics or error
        """
        def cancel_callback():
            """Callback to signal cancellation."""
            self._cancel_requested = True
        
        self._cancel_requested = False
        self._runner = BenchmarkRunner(cancel_callback=cancel_callback)
        
        try:
            result = self._runner._run_benchmark(
                self._runner._bench_binary if not is_batched else self._runner._batched_bench_binary,
                config,
                is_batched,
            )
        except BinaryNotFoundError as e:
            log.error(f"Benchmark binary not found: {e}")
            result = BenchmarkResult(success=False, error=str(e), command=[])
        except Exception as e:
            log.error(f"Failed to run benchmark: {e}")
            result = BenchmarkResult(success=False, error=str(e), command=[])
        
        # If cancelled, return a cancelled result
        if self._cancel_requested:
            result.success = False
            result.error = "Cancelled by user"
            result.raw_output = ""
        
        return result

    def _runner_cancel(self) -> None:
        """
        Internal method to cancel the current benchmark runner.
        """
        if hasattr(self, '_runner') and self._runner is not None:
            try:
                self._runner.cancel()
            except Exception as e:
                log.error(f"Failed to cancel benchmark runner: {e}")

# Convenience function for direct usage
def optimize_model(
    model_path: str,
    max_duration_sec: int = 100,
    optimization_target: str = 'balanced',
    cache_dir: Optional[str | Path] = None
) -> Dict[str, Any]:
    """
    Convenience function to optimize a model's settings.
    
    Args:
        model_path: Path to model file
        max_duration_sec: Maximum time to spend on benchmarking
        optimization_target: Target metric ('throughput', 'latency', or 'balanced')
        cache_dir: Directory to store cache files
        
    Returns:
        Dictionary containing optimal settings and benchmark results
    """
    optimizer = BenchmarkOptimizer(cache_dir)
    return optimizer.optimize(model_path, max_duration_sec, optimization_target)
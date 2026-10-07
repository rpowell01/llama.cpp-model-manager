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
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from benchmarks import BenchmarkRunner, BenchmarkConfig, BenchmarkError
from routes.system import compute_optimal_settings, get_hardware_info

log = logging.getLogger("llamaweb.optimizer")


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
        
    def _generate_cache_key(self, model_path: str, hardware_info: Dict[str, Any]) -> str:
        """
        Generate cache key based on model and hardware fingerprint.
        
        Args:
            model_path: Path to model file
            hardware_info: Hardware information dictionary
            
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
        
        # Combine model and hardware fingerprints
        combined = f"{model_fingerprint}|{hw_fingerprint}"
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
        gpu_layers_options = [0]  # Always include CPU-only option
        if has_gpu and total_vram > 0:
            # Add GPU layers options based on VRAM
            if total_vram >= 24:
                gpu_layers_options.extend([20, 50, 99])
            elif total_vram >= 16:
                gpu_layers_options.extend([20, 50])
            elif total_vram >= 8:
                gpu_layers_options.append(20)
            else:
                gpu_layers_options.append(10)  # Low VRAM
                
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
            cache_type_options = [("fp16", "fp16")]
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
        max_duration_sec: int = 100
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
        
        log.info(f"Starting benchmark sweep with {len(config_list)} configurations "
                f"(max {max_duration_sec}s)")
        
        for i, config in enumerate(config_list):
            # Check if we've exceeded time limit
            elapsed = time.time() - start_time
            if elapsed >= max_duration_sec:
                log.info(f"Time limit reached after {len(results)} configurations "
                        f"(elapsed: {elapsed:.1f}s)")
                break
                
            # Calculate remaining time for this config
            remaining_time = max_duration_sec - elapsed
            # Allocate time fairly but with minimum per config
            # Cap at 60s for first config to allow model loading + SYCL compilation
            time_per_config = max(5, min(60 if i == 0 else 30, remaining_time / (len(config_list) - i)))
            
            # Use more generous timeout for all configs (SYCL compilation on each fresh process)
            # Manual test: 5 reps = 43s (first 23s compilation, rest ~10s each)
            # With 2 reps: ~33s needed. Use 200% of allocated time, max 120s for all.
            timeout_sec = min(120, int(time_per_config * 2.0))
            
            try:
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
                    repetitions=2,  # Fewer repetitions for sweep speed
                    timeout_sec=timeout_sec,
                )
                
                # Run llama-bench (single sequence) for sweep
                runner = BenchmarkRunner()
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
                    "repetitions": bench_config.repetitions,
                    "timeout_sec": bench_config.timeout_sec,
                })
                
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
                    # For optimization, we'll use a combined score later
                }
                
                results.append(result_dict)
                log.info(f"Configuration {i+1} completed: "
                        f"pp_avg={result_dict['pp_avg']:.1f}, "
                        f"tg_avg={result_dict['tg_avg']:.1f}")
                        
            except Exception as e:
                log.warning(f"Configuration {i+1} failed: {e}")
                # Add failed result for completeness
                results.append({
                    "config": config,
                    "success": False,
                    "error": str(e),
                    "pp_avg": 0,
                    "tg_avg": 0,
                })
                
        log.info(f"Benchmark sweep completed: {len(results)} configurations tested "
                f"(elapsed: {time.time() - start_time:.1f}s)")
        return results
        
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
             
        # Generate cache key
        cache_key = self._generate_cache_key(model_path, hardware_info)
         
        # Try to load from cache first
        cached_result = self._load_from_cache(cache_key, hardware_info)
        if cached_result is not None:
            log.info(f"Using cached optimization result for {model_path}")
            return cached_result
             
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
                "benchmark_results": [],
                "hardware_info": hardware_info,
                "model_info": model_info,
                "cache_key": cache_key,
                "optimization_target": optimization_target,
                "fallback_reason": "no_configurations_generated"
            }
            
            # Save to cache to prevent repeated attempts
            self._save_to_cache(cache_key, result, hardware_info)
            
            log.info(f"Optimization completed for {model_path} (via fallback: no configurations)")
            return result
         
        # Run benchmark sweep
        try:
            benchmark_results = self.run_benchmark_sweep(
                model_path, config_list, max_duration_sec
            )
        except Exception as e:
            log.warning(f"Benchmark sweep failed with exception: {e}. Falling back to heuristic settings.")
            # Fall back to heuristic settings on any benchmark failure
            heuristic_settings = compute_optimal_settings(self._sanitize_hardware_info(hardware_info), model_info)
            # Convert to BenchmarkConfig-compatible keys
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
            
            # Save to cache (even failures to prevent repeated attempts)
            self._save_to_cache(cache_key, result, hardware_info)
            
            log.info(f"Optimization completed for {model_path} (via fallback)")
            return result
         
        # Select optimal configuration
        optimal_config = self.select_optimal_config(
            benchmark_results, optimization_target
        )

        # If no successful results, fall back to heuristic settings
        # Note: select_optimal_config may return a non-empty config from the
        # first result even when all benchmarks failed, so we also check for
        # actual successful results here.
        successful_results = [r for r in benchmark_results if r.get("success", False)]
        if not optimal_config or not successful_results:
            log.warning("No successful benchmark results, falling back to heuristic settings")
            optimal_config = compute_optimal_settings(self._sanitize_hardware_info(hardware_info), model_info)
            # Convert to BenchmarkConfig-compatible keys
            if "gpu_layers" in optimal_config:
                optimal_config["n_gpu_layers"] = optimal_config.pop("gpu_layers")
        else:
            # All benchmarks succeeded - use the selected config as-is
            # No fallback needed
            log.info(f"All benchmarks succeeded, using empirical optimal config")
    
        # Create and return result
        result = {
            "optimal_settings": optimal_config,
            "benchmark_results": benchmark_results,
            "hardware_info": hardware_info,
            "model_info": model_info,
            "cache_key": cache_key,
            "optimization_target": optimization_target,
        }
    
        log.info(f"Optimization completed for {model_path}")
        return result


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
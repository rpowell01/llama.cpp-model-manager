#!/usr/bin/env python3
"""
Unit tests for optimizer module.
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from optimizer import (
    BenchmarkOptimizer,
    optimize_model,
)


class TestBenchmarkOptimizer(unittest.TestCase):
    """Tests for BenchmarkOptimizer class."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.test_dir = Path(tempfile.mkdtemp())
        self.model_path = self.test_dir / "test-model.gguf"
        # Create a dummy model file
        self.model_path.write_text("dummy model content")
        
        # Sample hardware info
        self.hardware_info = {
            "platform": "Linux",
            "cpu_count_logical": 8,
            "cpu_count_physical": 4,
            "memory_total_gb": 16,
            "total_gpu_vram_gb": 8,
            "has_nvidia_gpu": True,
            "has_amd_gpu": False,
            "has_intel_gpu": False,
            "gpu_info": [
                {
                    "name": "NVIDIA GeForce RTX 3060",
                    "vendor": "NVIDIA",
                    "memory_mb": 8192,
                }
            ],
        }
        
        # Sample model info
        self.model_info = {
            "file_size_gb": 4.5,
            "file_path": str(self.model_path),
            "ctx_size": 4096,
        }

    def tearDown(self) -> None:
        """Clean up test fixtures."""
        # Clean up test directory
        import shutil
        shutil.rmtree(self.test_dir)

    def test_init_default_cache_dir(self) -> None:
        """Test initialization with default cache directory."""
        with patch("optimizer.Path.home") as mock_home, \
             patch("optimizer.Path.mkdir") as mock_mkdir:
            mock_home.return_value = Path("/fake/home")
            optimizer = BenchmarkOptimizer()
            expected_cache_dir = Path("/fake/home/.cache/llama-web/benchmarks")
            self.assertEqual(optimizer.cache_dir, expected_cache_dir)
            # Verify mkdir was called
            mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)

    def test_init_custom_cache_dir(self) -> None:
        """Test initialization with custom cache directory."""
        custom_dir = Path("/tmp/custom_cache")
        optimizer = BenchmarkOptimizer(cache_dir=custom_dir)
        self.assertEqual(optimizer.cache_dir, custom_dir)

    def test_generate_cache_key(self) -> None:
        """Test cache key generation."""
        optimizer = BenchmarkOptimizer()
        cache_key = optimizer._generate_cache_key(
            str(self.model_path), self.hardware_info
        )
        
        # Should be a 32-character hex string (MD5)
        self.assertIsInstance(cache_key, str)
        self.assertEqual(len(cache_key), 32)
        self.assertTrue(all(c in "0123456789abcdef" for c in cache_key))
        
        # Same inputs should produce same key
        cache_key2 = optimizer._generate_cache_key(
            str(self.model_path), self.hardware_info
        )
        self.assertEqual(cache_key, cache_key2)
        
        # Different model path should produce different key
        different_path = str(self.model_path) + ".different"
        cache_key3 = optimizer._generate_cache_key(
            different_path, self.hardware_info
        )
        self.assertNotEqual(cache_key, cache_key3)

    def test_load_from_cache_miss(self) -> None:
        """Test cache miss when file doesn't exist."""
        optimizer = BenchmarkOptimizer(cache_dir=self.test_dir)
        result = optimizer._load_from_cache("nonexistent_key", {})
        self.assertIsNone(result)

    def test_save_and_load_cache(self) -> None:
        """Test saving and loading from cache."""
        optimizer = BenchmarkOptimizer(cache_dir=self.test_dir)
        test_data = {
            "optimal_settings": {"n_gpu_layers": 50},
            "benchmark_results": [],
        }
        hardware_info = {"test": "info"}
        
        # Save to cache
        cache_key = "test_key"
        optimizer._save_to_cache(cache_key, test_data, hardware_info)
        
        # Load from cache
        loaded = optimizer._load_from_cache(cache_key, hardware_info)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["optimal_settings"], {"n_gpu_layers": 50})
        
        # Check that timestamp was added
        self.assertIn("timestamp", loaded)
        self.assertIn("hardware_info", loaded)

    def test_generate_config_combinations(self) -> None:
        """Test config combination generation."""
        optimizer = BenchmarkOptimizer()
        configs = optimizer.generate_config_combinations(
            self.hardware_info, self.model_info
        )
        
        # Should generate multiple configurations
        self.assertIsInstance(configs, list)
        self.assertGreater(len(configs), 0)
        
        # Each config should be a dict with expected keys
        for config in configs:
            self.assertIsInstance(config, dict)
            # Should have basic benchmark parameters
            self.assertIn("n_gpu_layers", config)
            self.assertIn("batch_size", config)
            self.assertIn("threads", config)

    @patch("optimizer.BenchmarkRunner")
    def test_run_benchmark_sweep(self, mock_runner_class) -> None:
        """Test benchmark sweep execution."""
        # Setup mock runner
        mock_runner = Mock()
        mock_runner.run_llama_bench.return_value = {
            "pp_avg": 1000.0,
            "tg_avg": 50.0,
        }
        mock_runner_class.return_value = mock_runner
        
        optimizer = BenchmarkOptimizer()
        config_list = [
            {"n_gpu_layers": 0, "batch_size": 256, "threads": 4},
            {"n_gpu_layers": 20, "batch_size": 512, "threads": 8},
        ]
        
        # Use a short model path for testing
        results = optimizer.run_benchmark_sweep(
            str(self.model_path), config_list, max_duration_sec=10
        )
        
        # Should return results for each config
        self.assertEqual(len(results), 2)
        for result in results:
            self.assertIn("config", result)
            self.assertIn("metrics", result)
            self.assertTrue(result["success"])
            
        # Verify benchmark runner was called
        self.assertEqual(mock_runner.run_llama_bench.call_count, 2)

    def test_select_optimal_config_throughput(self) -> None:
        """Test optimal config selection for throughput target."""
        results = [
            {
                "config": {"n_gpu_layers": 0, "batch_size": 256},
                "success": True,
                "pp_avg": 500.0,
                "tg_avg": 30.0,
            },
            {
                "config": {"n_gpu_layers": 20, "batch_size": 512},
                "success": True,
                "pp_avg": 400.0,
                "tg_avg": 60.0,  # Higher tg_avg should win
            },
            {
                "config": {"n_gpu_layers": 50, "batch_size": 1024},
                "success": False,  # Failed benchmark
                "pp_avg": 0,
                "tg_avg": 0,
            }
        ]
        
        optimizer = BenchmarkOptimizer()
        best_config = optimizer.select_optimal_config(results, "throughput")
        
        # Should select the config with highest tg_avg
        self.assertEqual(best_config["n_gpu_layers"], 20)
        self.assertEqual(best_config["batch_size"], 512)

    def test_select_optimal_config_balanced(self) -> None:
        """Test optimal config selection for balanced target."""
        results = [
            {
                "config": {"n_gpu_layers": 0, "batch_size": 256},
                "success": True,
                "pp_avg": 1000.0,
                "tg_avg": 30.0,
                # balanced score = sqrt(1000 * 30) = sqrt(30000) ≈ 173.2
            },
            {
                "config": {"n_gpu_layers": 20, "batch_size": 512},
                "success": True,
                "pp_avg": 400.0,
                "tg_avg": 60.0,
                # balanced score = sqrt(400 * 60) = sqrt(24000) ≈ 154.9
            },
            {
                "config": {"n_gpu_layers": 10, "batch_size": 384},
                "success": True,
                "pp_avg": 700.0,
                "tg_avg": 50.0,
                # balanced score = sqrt(700 * 50) = sqrt(35000) ≈ 187.1  # Should win
            }
        ]
        
        optimizer = BenchmarkOptimizer()
        best_config = optimizer.select_optimal_config(results, "balanced")
        
        # Should select the config with highest balanced score
        self.assertEqual(best_config["n_gpu_layers"], 10)
        self.assertEqual(best_config["batch_size"], 384)

    def test_select_optimal_config_no_success(self) -> None:
        """Test optimal config selection when all benchmarks fail."""
        results = [
            {
                "config": {"n_gpu_layers": 0},
                "success": False,
                "error": "timeout",
            },
            {
                "config": {"n_gpu_layers": 20},
                "success": False,
                "error": "failed",
            }
        ]
        
        optimizer = BenchmarkOptimizer()
        best_config = optimizer.select_optimal_config(results, "balanced")
        
        # Should return first config's config dict (even though it failed)
        self.assertEqual(best_config, {"n_gpu_layers": 0})

    def test_select_optimal_config_latency(self) -> None:
        """Test optimal config selection for latency target."""
        results = [
            {
                "config": {"n_gpu_layers": 0, "batch_size": 256},
                "success": True,
                "pp_avg": 1000.0,
                "tg_avg": 30.0,
                "latency_p50": 100.0,  # High latency
                "latency_p90": 150.0,
                "latency_p99": 200.0,
            },
            {
                "config": {"n_gpu_layers": 20, "batch_size": 512},
                "success": True,
                "pp_avg": 800.0,
                "tg_avg": 50.0,
                "latency_p50": 50.0,   # Low latency (better)
                "latency_p90": 75.0,
                "latency_p99": 100.0,
            },
            {
                "config": {"n_gpu_layers": 10, "batch_size": 384},
                "success": True,
                "pp_avg": 900.0,
                "tg_avg": 20.0,  # Lower tg_avg means higher estimated latency
                # No latency metrics at all (keys missing) - should fall back to inverse tg_avg
            }
        ]
        
        optimizer = BenchmarkOptimizer()
        best_config = optimizer.select_optimal_config(results, "latency")
        
        # Should select the config with lowest latency_p50 (second config)
        self.assertEqual(best_config["n_gpu_layers"], 20)
        self.assertEqual(best_config["batch_size"], 512)
        
        # Test fallback to inverse tg_avg when latency metrics are missing
        results_no_latency = [
            {
                "config": {"n_gpu_layers": 0, "batch_size": 256},
                "success": True,
                "pp_avg": 1000.0,
                "tg_avg": 30.0,  # Estimated latency = 1000/30 = 33.3ms
            },
            {
                "config": {"n_gpu_layers": 20, "batch_size": 512},
                "success": True,
                "pp_avg": 800.0,
                "tg_avg": 60.0,  # Estimated latency = 1000/60 = 16.7ms (better - lower latency)
            }
        ]
        
        best_config_fallback = optimizer.select_optimal_config(results_no_latency, "latency")
        # Should select the config with highest tg_avg (lowest inverse latency)
        self.assertEqual(best_config_fallback["n_gpu_layers"], 20)
        self.assertEqual(best_config_fallback["batch_size"], 512)

    def test_select_optimal_config_empty_results(self) -> None:
        """Test optimal config selection with empty results list."""
        optimizer = BenchmarkOptimizer()
        
        # Empty results list should return empty config
        best_config = optimizer.select_optimal_config([], "balanced")
        self.assertEqual(best_config, {})
        
        # Empty results list with latency target should also return empty config
        best_config_latency = optimizer.select_optimal_config([], "latency")
        self.assertEqual(best_config_latency, {})
        
        # Empty results list with throughput target should also return empty config
        best_config_throughput = optimizer.select_optimal_config([], "throughput")
        self.assertEqual(best_config_throughput, {})

    @patch("optimizer.BenchmarkRunner")
    def test_run_benchmark_sweep_time_limit_early_exit(self, mock_runner_class) -> None:
        """Test that run_benchmark_sweep exits early when max_duration_sec is exceeded."""
        # Setup mock runner that takes some time
        mock_runner = Mock()
        
        def slow_benchmark(*args, **kwargs):
            time.sleep(0.05)  # Simulate benchmark taking 50ms
            return {"pp_avg": 1000.0, "tg_avg": 50.0}
        
        mock_runner.run_llama_bench.side_effect = slow_benchmark
        mock_runner_class.return_value = mock_runner
        
        optimizer = BenchmarkOptimizer()
        config_list = [
            {"n_gpu_layers": 0, "batch_size": 256, "threads": 4},
            {"n_gpu_layers": 20, "batch_size": 512, "threads": 8},
            {"n_gpu_layers": 50, "batch_size": 1024, "threads": 16},
            {"n_gpu_layers": 99, "batch_size": 2048, "threads": 32},
        ]
        
        # Set max_duration_sec to 0.1 seconds (100ms) - should only complete ~2 configs
        start_time = time.time()
        results = optimizer.run_benchmark_sweep(
            str(self.model_path), config_list, max_duration_sec=0.1
        )
        elapsed = time.time() - start_time
        
        # Should exit early due to time limit
        # With 50ms per config + overhead, we expect 1-2 configs to complete
        self.assertGreaterEqual(len(results), 1)
        self.assertLessEqual(len(results), 3)  # Should not complete all 4
        self.assertLess(elapsed, 0.5)  # Should not take much longer than limit
        
        # All returned results should be successful
        for result in results:
            self.assertIn("config", result)
            self.assertIn("metrics", result)
            self.assertTrue(result["success"])
        
        # Verify benchmark runner was called for each completed config
        self.assertEqual(mock_runner.run_llama_bench.call_count, len(results))

    def test_load_from_cache_hardware_changed_invalidation(self) -> None:
        """Test that _load_from_cache returns None when hardware has changed."""
        optimizer = BenchmarkOptimizer(cache_dir=self.test_dir)
        
        # Original hardware info
        original_hw = {
            "cpu_count_logical": 8,
            "cpu_count_physical": 4,
            "memory_total_gb": 16,
            "total_gpu_vram_gb": 8,
            "has_nvidia_gpu": True,
            "has_amd_gpu": False,
            "has_intel_gpu": False,
            "gpu_info": [{"name": "RTX 3060", "vendor": "NVIDIA", "memory_mb": 8192}],
        }
        
        # Save a cached result with original hardware
        test_data = {
            "optimal_settings": {"n_gpu_layers": 50},
            "benchmark_results": [],
        }
        cache_key = "test_hw_change_key"
        optimizer._save_to_cache(cache_key, test_data, original_hw)
        
        # Verify we can load it back with same hardware
        loaded_same_hw = optimizer._load_from_cache(cache_key, original_hw)
        self.assertIsNotNone(loaded_same_hw)
        self.assertEqual(loaded_same_hw["optimal_settings"], {"n_gpu_layers": 50})
        
        # Now try with different hardware (CPU count changed)
        changed_hw_cpu = original_hw.copy()
        changed_hw_cpu["cpu_count_logical"] = 16
        changed_hw_cpu["cpu_count_physical"] = 8
        
        loaded_changed_cpu = optimizer._load_from_cache(cache_key, changed_hw_cpu)
        self.assertIsNone(loaded_changed_cpu, "Should return None when CPU count changes")
        
        # Try with different GPU VRAM
        changed_hw_vram = original_hw.copy()
        changed_hw_vram["total_gpu_vram_gb"] = 16
        
        loaded_changed_vram = optimizer._load_from_cache(cache_key, changed_hw_vram)
        self.assertIsNone(loaded_changed_vram, "Should return None when GPU VRAM changes")
        
        # Try with different GPU presence
        changed_hw_gpu = original_hw.copy()
        changed_hw_gpu["has_nvidia_gpu"] = False
        changed_hw_gpu["has_amd_gpu"] = True
        
        loaded_changed_gpu = optimizer._load_from_cache(cache_key, changed_hw_gpu)
        self.assertIsNone(loaded_changed_gpu, "Should return None when GPU vendor changes")
        
        # Try with different memory
        changed_hw_mem = original_hw.copy()
        changed_hw_mem["memory_total_gb"] = 32
        
        loaded_changed_mem = optimizer._load_from_cache(cache_key, changed_hw_mem)
        self.assertIsNone(loaded_changed_mem, "Should return None when memory changes")

    @patch("optimizer.get_hardware_info")
    @patch("optimizer.BenchmarkOptimizer.generate_config_combinations")
    @patch("optimizer.BenchmarkOptimizer.run_benchmark_sweep")
    @patch("optimizer.BenchmarkOptimizer.select_optimal_config")
    def test_optimize_method(
        self, mock_select, mock_sweep, mock_generate, mock_hw_info
    ) -> None:
        """Test the main optimize method orchestrates correctly."""
        # Setup mocks
        mock_hw_info.return_value = self.hardware_info
        mock_generate.return_value = [
            {"n_gpu_layers": 0, "batch_size": 256},
            {"n_gpu_layers": 20, "batch_size": 512},
        ]
        mock_sweep.return_value = [
            {
                "config": {"n_gpu_layers": 0, "batch_size": 256},
                "success": True,
                "pp_avg": 1000.0,
                "tg_avg": 50.0,
            },
            {
                "config": {"n_gpu_layers": 20, "batch_size": 512},
                "success": True,
                "pp_avg": 800.0,
                "tg_avg": 60.0,
            }
        ]
        mock_select.return_value = {"n_gpu_layers": 20, "batch_size": 512}
        
        optimizer = BenchmarkOptimizer(cache_dir=self.test_dir)
        result = optimizer.optimize(str(self.model_path), max_duration_sec=60)
        
        # Verify the flow
        mock_hw_info.assert_called_once()
        mock_generate.assert_called_once_with(self.hardware_info, unittest.mock.ANY)
        mock_sweep.assert_called_once()
        mock_select.assert_called_once()
        
        # Check result structure
        self.assertIn("optimal_settings", result)
        self.assertIn("benchmark_results", result)
        self.assertIn("hardware_info", result)
        self.assertIn("model_info", result)
        self.assertIn("cache_key", result)
        
        # Check that optimal settings were selected correctly
        self.assertEqual(result["optimal_settings"], {"n_gpu_layers": 20, "batch_size": 512})

    @patch("optimizer.get_hardware_info")
    @patch("optimizer.BenchmarkOptimizer._load_from_cache")
    def test_optimize_uses_cache(self, mock_load_cache, mock_hw_info) -> None:
        """Test that optimize uses cached results when available."""
        # Setup mocks
        mock_hw_info.return_value = self.hardware_info
        cached_result = {
            "optimal_settings": {"n_gpu_layers": 30, "batch_size": 1024},
            "benchmark_results": [],
            "hardware_info": self.hardware_info,
            "model_info": self.model_info,
            "cache_key": "cached_key",
        }
        mock_load_cache.return_value = cached_result
        
        optimizer = BenchmarkOptimizer(cache_dir=self.test_dir)
        result = optimizer.optimize(str(self.model_path))
        
        # Should return cached result without doing optimization
        mock_load_cache.assert_called_once()
        self.assertEqual(result["optimal_settings"], {"n_gpu_layers": 30, "batch_size": 1024})
        self.assertEqual(result["cache_key"], "cached_key")

    def test_optimize_model_file_not_found(self) -> None:
        """Test optimize with nonexistent model file."""
        optimizer = BenchmarkOptimizer()
        with self.assertRaises(FileNotFoundError):
            optimizer.optimize("/nonexistent/model.gguf")

    def test_convenience_function(self) -> None:
        """Test the optimize_model convenience function."""
        with patch("optimizer.BenchmarkOptimizer") as mock_optimizer_class:
            mock_optimizer = Mock()
            mock_optimizer.optimize.return_value = {"test": "result"}
            mock_optimizer_class.return_value = mock_optimizer
            
            result = optimize_model(
                str(self.model_path),
                max_duration_sec=30,
                optimization_target="throughput"
            )
            
            mock_optimizer_class.assert_called_once()
            mock_optimizer.optimize.assert_called_once_with(
                str(self.model_path), 30, "throughput"
            )
            self.assertEqual(result, {"test": "result"})


if __name__ == "__main__":
    unittest.main()
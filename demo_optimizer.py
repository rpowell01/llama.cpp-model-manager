#!/usr/bin/env python3
"""
Demo script to show the BenchmarkOptimizer in action.
"""

import tempfile
import os
from pathlib import Path
from unittest.mock import patch, Mock

# Import our optimizer
from optimizer import BenchmarkOptimizer, optimize_model

def demo_optimizer():
    """Demonstrate the optimizer functionality."""
    print("=== BenchmarkOptimizer Demo ===\n")
    
    # Create a temporary model file for demo
    with tempfile.NamedTemporaryFile(suffix='.gguf', delete=False) as f:
        model_path = f.name
        f.write(b'demo model content')
    
    try:
        # Mock the hardware info to avoid actual system calls
        mock_hardware_info = {
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
        
        # Mock the benchmark runner to avoid actual benchmark execution
        with patch('optimizer.get_hardware_info', return_value=mock_hardware_info), \
             patch('optimizer.BenchmarkRunner') as mock_runner_class:
            
            # Setup mock benchmark runner
            mock_runner = Mock()
            mock_runner.run_llama_bench.return_value = {
                "pp_avg": 1200.0,
                "tg_avg": 65.0,
                "latency_p50": 15.0,
            }
            mock_runner_class.return_value = mock_runner
            
            # Create optimizer instance
            print("1. Creating BenchmarkOptimizer instance...")
            optimizer = BenchmarkOptimizer()
            print(f"   Cache directory: {optimizer.cache_dir}\n")
            
            # Demonstrate config generation
            print("2. Generating configuration combinations...")
            configs = optimizer.generate_config_combinations(mock_hardware_info, {
                "file_size_gb": 5.2,
                "ctx_size": 4096
            })
            print(f"   Generated {len(configs)} configurations to test")
            print(f"   First config: {configs[0] if configs else 'None'}\n")
            
            # Demonstrate the full optimization process (will use mocks)
            print("3. Running optimization (with mocked benchmarks)...")
            result = optimizer.optimize(model_path, max_duration_sec=10)
            
            print(f"   Optimal settings: {result['optimal_settings']}")
            print(f"   Number of benchmark results: {len(result['benchmark_results'])}")
            print(f"   Cache key: {result['cache_key'][:16]}...")
            print(f"   Optimization target: {result['optimization_target']}\n")
            
            # Show that caching works
            print("4. Testing cache functionality...")
            result2 = optimizer.optimize(model_path, max_duration_sec=10)
            print(f"   Second optimization returned cached result: {result['cache_key'] == result2['cache_key']}\n")
            
    finally:
        # Clean up temp file
        if os.path.exists(model_path):
            os.unlink(model_path)
    
    print("=== Demo Complete ===")

if __name__ == "__main__":
    demo_optimizer()
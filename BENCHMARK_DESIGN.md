# Benchmark Parameter Space & API Contract Design

## Overview
This document defines the empirical benchmark approach using `llama-bench` and `llama-batched-bench` to replace the heuristic-based `compute_optimal_settings()` function with data-driven optimization.

---

## 1. Current Settings Computed by `compute_optimal_settings()`

Based on analysis of `routes/system.py` (lines 323-454), the function computes the following settings:

### CPU Settings
| Setting | Description | Current Logic |
|---------|-------------|---------------|
| `threads` | CPU threads for computation | `cpu_physical` (physical cores) |
| `threads_batch` | CPU threads for batch processing | `cpu_physical` |

### GPU Settings
| Setting | Description | Current Logic |
|---------|-------------|---------------|
| `gpu_layers` | Number of layers to offload to GPU | Heuristic based on VRAM vs model size |
| `flash_attn` | Flash attention mode | `"auto"` if GPU, `"off"` if CPU |
| `no_mmap` | Disable memory mapping | `False` if GPU, `True` if CPU |
| `mlock` | Lock memory to prevent swapping | `True` |

### Memory/Cache Settings
| Setting | Description | Current Logic |
|---------|-------------|---------------|
| `cache_type_k` | K-cache quantization | `fp16` (≥16GB VRAM), `q8_0` (≥8GB), `q4_0` (<8GB) |
| `cache_type_v` | V-cache quantization | Same as K-cache |
| `ctx_size` | Context window size | Min(model_ctx, available_memory * 0.6 * 1024) |

### Batch Processing
| Setting | Description | Current Logic |
|---------|-------------|---------------|
| `batch_size` | Prompt processing batch size | 2048 (GPU), 512 (CPU) |
| `ubatch_size` | Micro-batch size | 512 (GPU), 256 (CPU) |
| `parallel` | Parallel sequences | 1 (ctx>32K), min(4, cpu_physical/2) |

### NUMA/CPU Affinity
| Setting | Description | Current Logic |
|---------|-------------|---------------|
| `numa` | Enable NUMA awareness | `True` |
| `numa_value` | NUMA mode | `"distribute"` |
| `cpu_affinity` | CPU pinning | `"auto"` |
| `cpu_strict` | Strict CPU mode | `False` |

### Speculative Decoding
| Setting | Description | Current Logic |
|---------|-------------|---------------|
| `spec_type` | Speculation type | `"ngram-simple"` if GPU + model≥6GB, else `"none"` |
| `spec_draft_n_max` | Max draft tokens | 3 |
| `spec_draft_p_split` | Split probability | 0.1 |
| `spec_ngram_size_n` | N-gram size N | 12 |
| `spec_ngram_size_m` | N-gram size M | 48 |

### YaRN/RoPE Scaling (for long context)
| Setting | Description | Current Logic |
|---------|-------------|---------------|
| `rope_scaling_type` | Scaling method | `"yarn"` if ctx≥32K, else `"none"` |
| `rope_scale` | Scale factor | `max(1.0, model_ctx / 32768)` |
| `yarn_orig_ctx` | Original context | 32768 |
| `yarn_ext_factor` | Extrapolation factor | 1.0 |
| `yarn_attn_factor` | Attention factor | 1.0 |
| `yarn_beta_slow` | Beta slow | 1.0 |
| `yarn_beta_fast` | Beta fast | 32.0 |

### Other
| Setting | Description | Current Logic |
|---------|-------------|---------------|
| `timeout` | Request timeout | 3600 |
| `jinja` | Jinja tool parsing | `True` |
| `reasoning_preserve` | Preserve reasoning | `True` |

---

## 2. llama-bench CLI Options & Output Format

### Key Parameters (from llama.cpp/tools/llama-bench/README.md)

```
-m, --model <filename>              Model file (required)
-hf, --hf-repo <user>/<model>[:quant] Hugging Face repo
-p, --n-prompt <n>                  Prompt tokens (default: 512)
-n, --n-gen <n>                     Generation tokens (default: 128)
-pg <pp,tg>                         Combined prompt+gen test
-d, --n-depth <n>                   Context depth for KV cache (default: 0)
-b, --batch-size <n>                Batch size (default: 2048)
-ub, --ubatch-size <n>              Micro-batch size (default: 512)
-ctk, --cache-type-k <t>            K-cache type (default: f16)
-ctv, --cache-type-v <t>            V-cache type (default: f16)
-t, --threads <n>                   Threads (default: system)
-ngl, --n-gpu-layers <n>            GPU layers (default: 99)
-fa, --flash-attn <on|off|auto>     Flash attention (default: auto)
--numa <distribute|isolate|numactl> NUMA mode (default: disabled)
-r, --repetitions <n>               Repetitions per test (default: 5)
-o, --output <csv|json|jsonl|md|sql> Output format (default: md)
--no-warmup                         Skip warmup runs
```

### Test Types
1. **Prompt Processing (pp)**: `-p N -n 0` - measures prefill throughput
2. **Text Generation (tg)**: `-p 0 -n N` - measures decode throughput  
3. **Combined (pg)**: `-pg <pp,tg>` - both phases

### Output Formats
- **JSON** (`-o json`): Structured, includes individual repetition results
- **JSONL** (`-o jsonl`): One JSON object per line, streaming-friendly
- **CSV** (`-o csv`): Tabular, good for spreadsheets
- **Markdown** (`-o md`): Human-readable (default)
- **SQL** (`-o sql`): INSERT statements for database

### JSON Output Structure (example)
```json
{
  "cpu_name": "Intel Core i9-13900K",
  "gpu_name": "NVIDIA RTX 3090",
  "model": "models/7B/ggml-model-q4_0.gguf",
  "model_desc": "llama 7B mostly Q4_0",
  "n_prompt": 512,
  "n_gen": 128,
  "n_batch": 2048,
  "n_threads": 16,
  "n_gpu_layers": 99,
  "flash_attn": 1,
  "cache_type_k": "f16",
  "cache_type_v": "f16",
  "numa": "distribute",
  "test_time": 1.234,
  "avg_ts": 45.67,
  "stddev_ts": 0.89,
  "repetitions": [
    {"t": 1.21, "ts": 46.2},
    {"t": 1.25, "ts": 44.8},
    ...
  ]
}
```

---

## 3. llama-batched-bench CLI Options & Output Format

### Key Parameters (from llama.cpp/tools/batched-bench/README.md)

```
-m, --model <filename>              Model file (required)
-c, --ctx-size <n>                  Context size / KV cache max (default: 2048)
-b, --batch-size <n>                Batch size (default: 2048)
-ub, --ubatch-size <n>              Micro-batch size (default: 512)
-ngl, --n-gpu-layers <n>            GPU layers (default: 99)
-npp, --n-prompt-per-batch <n>      Prompt tokens per batch (comma-separated)
-ntg, --n-gen-per-batch <n>         Gen tokens per batch (comma-separated)
-npl, --n-parallel <n>              Parallel sequences (comma-separated)
-pps, --prompt-shared               Shared prompt mode (vs per-batch)
-t, --threads <n>                   Threads
-tb, --threads-batch <n>            Threads for batch
-fa, --flash-attn <0|1>             Flash attention
--numa <distribute|isolate|numactl> NUMA mode
--output-format <jsonl|md>          Output format (default: md)
```

### Two Modes
1. **Prompt Not Shared** (`-pps` not set): Each batch has separate prompt, N_KV = B × (PP + TG)
2. **Prompt Shared** (`-pps` set): Common prompt for all batches, N_KV = PP + B × TG

### JSONL Output Structure (example)
```jsonl
{"n_kv_max": 2048, "n_batch": 2048, "n_ubatch": 512, "flash_attn": 0, "is_pp_shared": 0, "n_gpu_layers": 99, "n_threads": 8, "n_threads_batch": 8, "pp": 128, "tg": 128, "pl": 1, "n_kv": 256, "t_pp": 0.234, "speed_pp": 547.45, "t_tg": 3.504, "speed_tg": 36.53, "t": 3.737, "speed": 68.50}
{"n_kv_max": 2048, "n_batch": 2048, "n_ubatch": 512, "flash_attn": 0, "is_pp_shared": 0, "n_gpu_layers": 99, "n_threads": 8, "n_threads_batch": 8, "pp": 128, "tg": 128, "pl": 2, "n_kv": 512, "t_pp": 0.423, "speed_pp": 605.77, "t_tg": 11.106, "speed_tg": 23.05, "t": 11.529, "speed": 44.41}
...
```

Key metrics:
- `speed_pp`: Prompt processing tokens/sec
- `speed_tg`: Token generation tokens/sec  
- `speed`: Overall tokens/sec (weighted)
- `pl`: Parallel sequences (batch count)

---

## 4. Parameter Space Definition

### Primary Variables (to sweep)

| Parameter | Values | Rationale |
|-----------|--------|-----------|
| `gpu_layers` | `[0, 20, 50, 99]` | Key variable: 0=CPU, 20=minimal, 50=partial, 99=full offload |
| `batch_size` | `[256, 512, 1024, 2048, 4096]` | Prompt processing throughput |
| `ubatch_size` | `[128, 256, 512, 1024]` | Physical batch size (must ≤ batch_size) |
| `threads` | `[physical_cores, logical_cores, physical_cores//2]` | CPU parallelism |
| `threads_batch` | `[threads, physical_cores]` | Batch thread count |
| `cache_type_k/v` | `[q4_0, q8_0, fp16]` | KV cache quantization tradeoff |
| `flash_attn` | `[on, off, auto]` | Flash attention mode |
| `numa_value` | `[distribute, isolate, off]` | NUMA strategy |

### Conditional Variables (model/hardware dependent)

| Parameter | Condition | Values |
|-----------|-----------|--------|
| `spec_type` | GPU + model_size ≥ 6GB | `[none, ngram-simple]` |
| `spec_draft_n_max` | spec_type=ngram-simple | `[3, 5, 8]` |
| `spec_ngram_size_n` | spec_type=ngram-simple | `[12, 16]` |
| `spec_ngram_size_m` | spec_type=ngram-simple | `[48, 64]` |

### Fixed Parameters (per benchmark run)
| Parameter | Value | Reason |
|-----------|-------|--------|
| `n_prompt` | 512 | Standard prompt size |
| `n_gen` | 128 | Standard generation size |
| `repetitions` | 3 | Balance noise reduction vs time |
| `ctx_size` | model default (or 4096) | Model's trained context |

### Parameter Space Size Estimation

**Full Cartesian Product** (bounded):
- gpu_layers: 4
- batch_size: 5 (but ubatch_size ≤ batch_size, so ~10 valid pairs)
- threads: 3
- threads_batch: 2 (per threads choice)
- cache_type: 3
- flash_attn: 3
- numa: 3
- spec_type: 2 (conditional)

≈ **4 × 10 × 3 × 2 × 3 × 3 × 3 × 2 = 1,296 combinations** (too many)

**Practical Subset** (prioritized, ~24 configs):
1. **GPU Layer Sweep** (4 configs): Fix other params at defaults, vary gpu_layers
2. **Batch Size Sweep** (4 configs): Fix gpu_layers=optimal, vary batch/ubatch pairs
3. **Thread Sweep** (3 configs): Fix gpu_layers=optimal, vary threads
4. **Cache Type Sweep** (3 configs): Fix gpu_layers=optimal, vary cache_type
5. **Flash Attention** (3 configs): Fix gpu_layers=optimal, vary flash_attn
6. **NUMA** (3 configs): Fix gpu_layers=optimal, vary numa
7. **Speculative Decoding** (4 configs): If applicable, vary spec params

**Total: ~24 configurations** → At 3 reps × ~2-3 sec/test = **~3-4 minutes** (well under 120s target)

---

## 5. Benchmark Methodology

### Phase 1: llama-bench (Single-Sequence Performance)
**Purpose**: Measure baseline prefill (prompt processing) and decode (token generation) throughput

```bash
# For each config combination:
llama-bench \
  -m <model_path> \
  -p 512 -n 128 \          # Fixed prompt/gen sizes
  -b <batch_size> \
  -ub <ubatch_size> \
  -t <threads> \
  -tb <threads_batch> \
  -ngl <gpu_layers> \
  -ctk <cache_type_k> \
  -ctv <cache_type_v> \
  -fa <flash_attn> \
  --numa <numa_value> \
  -r 3 \                   # 3 repetitions
  -o json                  # Machine-readable output
```

**Metrics Collected**:
- `pp_avg_ts`: Prompt processing tokens/sec (prefill)
- `tg_avg_ts`: Token generation tokens/sec (decode)
- `pp_stddev`, `tg_stddev`: Variability

### Phase 2: llama-batched-bench (Multi-Sequence/Batched Performance)
**Purpose**: Measure throughput under concurrent load (multiple parallel sequences)

```bash
# For each config (using optimal from Phase 1):
llama-batched-bench \
  -m <model_path> \
  -c <ctx_size> \
  -b <batch_size> \
  -ub <ubatch_size> \
  -ngl <gpu_layers> \
  -t <threads> \
  -tb <threads_batch> \
  -fa <flash_attn> \
  --numa <numa_value> \
  -npp 128,256,512 \
  -ntg 128,256 \
  -npl 1,2,4,8 \
  --output-format jsonl
```

**Metrics Collected**:
- `speed_pp` per parallel level
- `speed_tg` per parallel level
- `speed` (overall) per parallel level
- Optimal parallel sequence count for max throughput

### Scoring Function
For each configuration, compute a composite score:

```
score = w1 * pp_ts + w2 * tg_ts + w3 * batch_throughput - w4 * latency_p99
```

Where weights (tunable):
- `w1 = 0.3` (prompt processing important for interactive)
- `w2 = 0.4` (token generation critical for streaming)
- `w3 = 0.2` (batched throughput for concurrent users)
- `w4 = 0.1` (penalize high variance)

**Select configuration with highest median score across 3 runs**.

---

## 6. API Contract: POST /api/benchmark-optimize

### Request
```json
{
  "model": "model-name",
  "max_duration_sec": 120,
  "configs_to_test": null,
  "force_rerun": false,
  "benchmark_mode": "full"
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `model` | string | Yes | - | Model name from registry |
| `max_duration_sec` | int | No | 120 | Max wall time for benchmark |
| `configs_to_test` | array | No | auto | Specific configs to test (override auto) |
| `force_rerun` | bool | No | false | Ignore cache, re-run benchmarks |
| `benchmark_mode` | string | No | "full" | "full", "quick", "gpu-layers-only" |

### Response
```json
{
  "optimal_settings": {
    "threads": 16,
    "threads_batch": 16,
    "gpu_layers": 50,
    "batch_size": 2048,
    "ubatch_size": 512,
    "cache_type_k": "q8_0",
    "cache_type_v": "q8_0",
    "flash_attn": "auto",
    "numa": true,
    "numa_value": "distribute",
    "cpu_affinity": "auto",
    "cpu_strict": false,
    "ctx_size": 8192,
    "spec_type": "ngram-simple",
    "spec_draft_n_max": 3,
    "spec_draft_p_split": 0.1,
    "spec_ngram_size_n": 12,
    "spec_ngram_size_m": 48,
    "parallel": 4,
    "timeout": 3600,
    "mlock": true,
    "no_mmap": false,
    "jinja": true,
    "reasoning_preserve": true
  },
  "benchmark_results": [
    {
      "config": { ... },
      "llama_bench": {
        "pp_ts": 2407.24,
        "pp_stddev": 8.41,
        "tg_ts": 130.89,
        "tg_stddev": 0.54,
        "repetitions": 3
      },
      "batched_bench": {
        "max_throughput_ts": 670.96,
        "optimal_parallel": 32,
        "results_per_parallel": [...]
      },
      "score": 945.3,
      "duration_sec": 12.3
    },
    ...
  ],
  "hardware_info": {
    "cpu_count_logical": 32,
    "cpu_count_physical": 16,
    "memory_total_gb": 64,
    "has_nvidia_gpu": true,
    "total_gpu_vram_gb": 24,
    "gpu_info": [...],
    "cuda_version": "12.4"
  },
  "model_info": {
    "name": "model-name",
    "file_size_gb": 12.5,
    "ctx_size": 32768
  },
  "cache_key": "model-name_rtx3090_64gb_cpu16c",
  "cached": false,
  "total_duration_sec": 87.4,
  "timestamp": "2026-10-05T12:34:56Z"
}
```

### Error Responses
| Code | Condition |
|------|-----------|
| 400 | Missing model parameter |
| 404 | Model not found |
| 409 | Benchmark already running for this model |
| 500 | Benchmark execution failed |
| 503 | llama-bench/llama-batched-bench not available |

---

## 7. Caching Strategy

### Cache Key Design
```
cache_key = f"{model_name}_{hardware_fingerprint}"
```

### Hardware Fingerprint Components
```python
def generate_hardware_fingerprint(hardware_info):
    parts = []
    
    # GPU identifier (most impactful)
    gpus = hardware_info.get("gpu_info", [])
    for gpu in sorted(gpus, key=lambda x: x.get("memory_mb", 0), reverse=True):
        name = gpu.get("name", "").replace(" ", "_").lower()
        vram = gpu.get("memory_mb", 0)
        parts.append(f"{name}_{vram}mb")
    
    # CPU
    cpu_physical = hardware_info.get("cpu_count_physical", 0)
    parts.append(f"cpu{cpu_physical}c")
    
    # RAM
    ram = hardware_info.get("memory_total_gb", 0)
    parts.append(f"ram{ram}gb")
    
    # Platform
    platform = hardware_info.get("platform", "").lower()
    parts.append(platform)
    
    return "_".join(parts)[:100]  # Limit length
```

**Example**: `rtx_3090_24576mb_cpu16c_ram64gb_linux`

### Cache Storage
- **Location**: `{slot_save_path}/benchmarks/` or `~/.cache/llamaweb/benchmarks/`
- **Format**: JSON file named `{cache_key}.json`
- **TTL**: 30 days (hardware changes invalidate)
- **Invalidation**: 
  - Model file modified (mtime/size change)
  - Hardware fingerprint mismatch
  - llama.cpp version change (track via `llama-server --version`)

### Cache Entry Structure
```json
{
  "cache_key": "...",
  "model_name": "...",
  "model_hash": "sha256_of_model_file",
  "hardware_fingerprint": "...",
  "llama_cpp_version": "b1234",
  "optimal_settings": { ... },
  "benchmark_results": [ ... ],
  "created_at": "2026-10-05T12:34:56Z",
  "expires_at": "2026-11-04T12:34:56Z"
}
```

---

## 8. Implementation Plan

### Step 1: Create Benchmark Runner Module (`routes/benchmark.py`)
- `run_llama_bench(model_path, config) -> dict`
- `run_batched_bench(model_path, config) -> dict`
- `generate_config_combinations(hardware_info, model_info) -> list[dict]`
- `score_results(results) -> dict` (returns best config)

### Step 2: Add Cache Layer
- `get_cached_benchmark(cache_key) -> dict | None`
- `save_benchmark_cache(cache_key, data) -> None`
- `invalidate_cache(model_name) -> None`

### Step 3: Add API Endpoint (`routes/system.py`)
- `POST /api/benchmark-optimize`
- Integrate with existing `get_hardware_info()` and model registry

### Step 4: Integration with Model Startup
- Optionally auto-run benchmark on first model load
- Use benchmark results to populate model settings
- Fall back to heuristic if benchmark fails/times out

---

## 9. Acceptance Criteria

- [ ] Document covers all 35+ settings from `compute_optimal_settings()`
- [ ] Parameter space defined with ~24 practical configurations
- [ ] Benchmark duration estimate < 120 seconds for full sweep
- [ ] API contract specifies request/response schemas
- [ ] Cache key includes model hash + hardware fingerprint
- [ ] Both llama-bench and llama-batched-bench integrated
- [ ] JSON/JSONL output parsing implemented
- [ ] Scoring function balances pp/tg/batched throughput

---

## 10. Edge Cases & Considerations

| Scenario | Handling |
|----------|----------|
| llama-bench not installed | Return 503 with install instructions |
| Model too large for GPU | Skip GPU layer configs > VRAM capacity |
| Benchmark OOM | Catch, reduce batch_size, retry |
| WSL + Windows binary | Use Windows paths for bench tools |
| No GPU detected | Only test CPU configs (gpu_layers=0) |
| Speculative decoding unsupported | Skip spec configs gracefully |
| Cache corrupted | Delete and re-run |
| Concurrent benchmark requests | Lock per-model, queue or reject |

---

## 11. Future Extensions

1. **Multi-objective optimization**: Pareto frontier (speed vs memory vs quality)
2. **Continuous benchmarking**: Re-run on hardware/driver updates
3. **A/B testing**: Compare heuristic vs empirical settings in production
4. **Distributed benchmarking**: Run across multiple machines
5. **Model quantization sweep**: Test different quantizations of same model
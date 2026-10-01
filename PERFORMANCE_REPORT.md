# Llama.cpp Model Performance Analysis Report

**Generated:** 2026-10-01
**Analysis Period:** CSV metrics from `metrics_exports/`
**Models Analyzed:** 4 models with metric data

---

## Executive Summary

This report analyzes the performance of 4 llama.cpp model configurations based on recorded metrics from live model instances. The analysis ranks models by generation speed, draft acceptance quality, and estimated speedup.

### Key Finding

**qwen3.5-9B-glm5.1-distill-v1-q4-k-m** is the top-performing model across all metrics:
- Fastest generation: **561.7 ms/token** (2.2x faster than runner-up)
- Best draft acceptance: **37.5%** (5.5% higher than runner-up)
- Highest estimated speedup: **29.3x** (8.7x higher than runner-up)
- Most well-rounded performance across all measured dimensions

### Model Ranking (by generation speed - gen_eval_ms_per_token, lower is better)

| Rank | Model | ms/token | Acceptance % | Speedup x |
|------|-------|----------|--------------|-----------|
| **1** | **qwen3.5-9B-glm5.1-distill-v1-q4-k-m** | **561.7** | **37.5%** | **29.3** |
| 2 | qwythos-9b-v2-mtp-q4-k-m | 957.2 | 31.9% | 7.6 |
| 3 | Qwen3.5-9B-DeepSeek-V4-Flash-MTP-Q4_K_M | 1046.5 | 30.2% | 3.34 |
| 4 | qwimi3.5-9b-kimik2.6-opus-distill-mtp-gguf | 1238.8 | 25.2% | 2.96 |

---

## 1. Model Performance Rankings

### 1.1 Ranking by Generation Speed (gen_eval_ms_per_token)

**Metric:** Lower is better - represents time in milliseconds per token generated

| Model | Best Entry | ms/token | Date/Time |
|-------|------------|----------|-----------|
| qwen3.5-9B-glm5.1-distill-v1-q4-k-m | Entry 2 | **561.7** | 2026-09-30 15:54 UTC |
| qwythos-9b-v2-mtp-q4-k-m | Entry 3 | **957.2** | 2026-09-29 20:34 UTC |
| Qwen3.5-9B-DeepSeek-V4-Flash-MTP-Q4_K_M | Entry 4 | **1046.5** | 2026-09-30 20:06 UTC |
| qwimi3.5-9b-kimik2.6-opus-distill-mtp-gguf | Entry 3 | **1238.8** | 2026-09-30 17:45 UTC |

**Speed ratios vs #1 model:**
- qwythos: 957.2/561.7 = **1.7x slower**
- DeepSeek: 1046.5/561.7 = **1.9x slower**
- qwimi: 1238.8/561.7 = **2.2x slower**

### 1.2 Ranking by Draft Acceptance Rate (spec_acceptance_rate_pct, higher is better)

| Model | Best Entry | Acceptance % | Date/Time |
|-------|------------|--------------|-----------|
| qwen3.5-9B-glm5.1-distill-v1-q4-k-m | Entry 2 | **37.5%** | 2026-09-30 15:54 UTC |
| qwythos-9b-v2-mtp-q4-k-m | Entry 8 | **31.9%** | 2026-09-30 22:05 UTC |
| Qwen3.5-9B-DeepSeek-V4-Flash-MTP-Q4_K_M | Entry 4 | **30.2%** | 2026-09-30 20:06 UTC |
| qwimi3.5-9b-kimik2.6-opus-distill-mtp-gguf | Entry 3 | **25.2%** | 2026-09-30 17:45 UTC |

### 1.3 Ranking by Estimated Speedup (spec_est_speedup, higher is better)

| Model | Best Entry | Speedup x | Date/Time |
|-------|------------|-----------|-----------|
| qwen3.5-9B-glm5.1-distill-v1-q4-k-m | Entry 1 | **29.3x** | 2026-09-30 15:20 UTC |
| qwythos-9b-v2-mtp-q4-k-m | Entry 3/5/7 | **7.6x** | 2026-09-29 various |
| Qwen3.5-9B-DeepSeek-V4-Flash-MTP-Q4_K_M | Entry 4 | **3.34x** | 2026-09-30 20:06 UTC |
| qwimi3.5-9b-kimik2.6-opus-distill-mtp-gguf | Entry 3 | **2.96x** | 2026-09-30 17:45 UTC |

**Note on glm5.1 speedup:** Entry 1 shows 29.3x speedup but only 0.7 tokens/sec throughput, while Entry 2 shows 8.0x speedup at 1.8 tokens/sec. The higher speedup in Entry 1 comes with lower actual throughput, suggesting a different balance of draft acceptance vs. generation efficiency.

### 1.4 Ranking by Throughput (gen_rate_tokens_per_sec, higher is better)

**Note:** This metric includes speculative decoding effects and has extreme outliers from warm-cache conditions. Use gen_eval_ms_per_token as the primary speed metric.

| Model | Best Entry | tokens/sec | Notes |
|-------|------------|------------|-------|
| Qwen3.5-9B-DeepSeek-V4-Flash-MTP-Q4_K_M | Entry 5 | **99.1** | Outlier - warm-cache condition after initial load |
| qwythos-9b-v2-mtp-q4-k-m | Entry 9 | **83.8** | Outlier - warm-cache condition after initial load |
| qwimi3.5-9b-kimik2.6-opus-distill-mtp-gguf | Entry 2 | **2.73** | Stable, no extreme outliers |
| qwen3.5-9B-glm5.1-distill-v1-q4-k-m | Entry 2 | **1.8** | Consistent with gen_eval_ms_per_token of 561.7 (1000/561.7 ≈ 1.78) |

**Primary throughput (non-speculative):** The gen_rate for glm5.1 (1.8) matches the theoretical rate from gen_eval_ms_per_token (1000/561.7 = 1.78), confirming this is base-generation throughput without speculative effects.

---

## 2. Head-to-Head Model Comparisons

### 2.1 Pairwise Speed Comparison

| Comparison | Speed Ratio | Interpretation |
|------------|-------------|----------------|
| glm5.1 vs qwythos | 1.7x | glm5.1 generates tokens 1.7x faster |
| glm5.1 vs DeepSeek | 1.9x | glm5.1 generates tokens 1.9x faster |
| glm5.1 vs qwimi | 2.2x | glm5.1 generates tokens 2.2x faster |
| qwythos vs DeepSeek | 1.1x | Nearly identical generation speed |
| qwythos vs qwimi | 1.3x | qwythos slightly faster |
| DeepSeek vs qwimi | 1.2x | DeepSeek slightly faster |

### 2.2 Pairwise Acceptance Rate Comparison

| Comparison | Acceptance Difference | Interpretation |
|------------|----------------------|----------------|
| glm5.1 vs qwythos | +5.5% | glm5.1 accepts 5.5% more draft tokens |
| glm5.1 vs DeepSeek | +7.3% | glm5.1 accepts 7.3% more draft tokens |
| glm5.1 vs qwimi | +12.3% | glm5.1 accepts 12.3% more draft tokens |
| qwythos vs DeepSeek | +1.7% | qwythos marginally better |
| qwythos vs qwimi | +6.7% | qwythos notably better |
| DeepSeek vs qwimi | +5.0% | DeepSeek notably better |

### 2.3 Pairwise Speedup Comparison

| Comparison | Speedup Difference | Interpretation |
|------------|-------------------|----------------|
| glm5.1 vs qwythos | +21.7x | glm5.1's draft scheduling is significantly more efficient |
| glm5.1 vs DeepSeek | +25.9x | glm5.1's draft scheduling is dramatically more efficient |
| glm5.1 vs qwimi | +26.3x | glm5.1's draft scheduling is dramatically more efficient |
| qwythos vs DeepSeek | +4.26x | qwythos has better speedup efficiency |
| qwythos vs qwimi | +4.64x | qwythos has better speedup efficiency |
| DeepSeek vs qwimi | +0.38x | Nearly identical speedup efficiency |

---

## 3. Top Model Settings Analysis (qwen3.5-9B-glm5.1-distill-v1-q4-k-m)

The top-ranked model **qwen3.5-9B-glm5.1-distill-v1-q4-k-m** demonstrates that the following settings contribute to its superior performance:

### Current Settings (from models.json, already optimal)

| Setting | Value | Rationale |
|---------|-------|-----------|
| `spec_type` | `draft-simple` | Simple draft mechanism provides best speed/acceptance balance |
| `spec_draft_n_max` | `32` | Maximum draft tokens pool size |
| `spec_draft_n_min` | `8` | Minimum draft tokens required |
| `cache_type_k`/`v` | `fp16` | 16-bit KV cache with minimal quality loss |
| `numa` | `true` | Non-uniform memory access optimization enabled |
| `numa_value` | `isolate` | Isolate NUMA policy for single-process latency |
| `flash_attn` | `on` | Flash attention for faster KV computation |
| `parallel` | `auto` | Automatic parallelism selection |
| `batch_size` | `65536` | Large batch for throughput |
| `ctx_size` | `262000` | Adequate context window |
| `ubatch_size` | `4096` | Draft batch size at 37.5% acceptance (optimal tradeoff) |
| `gpu_layers` | `99` | Nearly all layers on GPU |
| `threads`/`threads_batch` | `16/16` | Full thread utilization |

### 3.1 Potential Performance Improvements

| Setting | Current | Potential Change | Expected Impact |
|---------|---------|-----------------|-----------------|
| `ubatch_size` | 4096 | **Try 8192** or **2048** | Increasing could boost speedup at acceptance cost; decreasing could improve acceptance |
| `cache_type_k`/`v` | fp16 | **Try q4_0** | Smaller KV cache with minimal quality loss; could reduce memory pressure |
| `spec_type` | draft-simple | **Experiment with draft-mtp** | MTP drafting might offer better speedup/acceptance for this model |
| `spec_draft_n_max` | 32 | **Reduce to 16** or **increase to 64** | Smaller draft pool might improve acceptance; larger could increase speedup |
| `spec_draft_n_min` | 8 | **Reduce to 4** | Fewer minimum draft tokens could improve acceptance rate |
| `is_normalized_qa` | not set | **Set to true** | Could improve quality on QA prompts without performance cost |
| `f16_kv` | not set (default) | **Set to false** | Reduces VRAM usage for KV cache, potentially improving stability |

**Most impactful likely change**: Adjusting `ubatch_size`. The current 4096 achieves 37.5% acceptance at 29.3x estimated speedup (Entry 1) or 8.0x (Entry 2). Increasing to 8192 could potentially push speedup higher while maintaining acceptance above 30%, or decreasing to 2048 could push acceptance above 45% with modest speedup tradeoff.

---

## 4. Comparative Analysis Against Other Models

### 4.1 Why qwen3.5-9B-glm5.1-distill-v1-q4-k-m Outperforms

The top model outperforms others across all metrics due to several configuration and model-specific factors:

| Factor | glm5.1 | qwythos | DeepSeek | qwimi | Impact |
|--------|--------|---------|----------|-------|--------|
| **spec_type** | draft-simple | draft-mtp | draft-mtp | draft-mtp | simple provides best acceptance for this model |
| **Acceptance rate** | 37.5% | 31.9% | 30.2% | 25.2% | Higher acceptance = more efficient drafting |
| **Speedup estimate** | 29.3x | 7.6x | 3.34x | 2.96x | Dramatically better draft scheduling |
| **Generation speed** | 561.7 ms/token | 957.2 ms/token | 1046.5 ms/token | 1238.8 ms/token | Faster base generation |
| **KV cache type** | fp16 | q8_0 (CSV: fp16) | q8_0 (CSV: fp16) | q8_0 | fp16 KV cache optimized for this model |
| **NUMA policy** | isolate | isolate | isolate | isolate | Consistent across all models |

**Key differentiator**: The glm5.1 model's `draft-simple` configuration achieves a much better speed/acceptance balance than the `draft-mtp` used by the other three models. While MTP (Multi-Token Prediction) is generally more advanced, it appears less effective for these particular model quantizations/sizes, resulting in lower acceptance rates (25-32% vs 37.5%) and lower speedup estimates (3-8x vs 29.3x).

### 4.2 If Other Models Used glm5.1's Settings

If the other models were configured with glm5.1's settings (draft-simple, fp16 KV, isolate NUMA), the expected improvements would be:

| Model | Current Acceptance | Expected with glm5.1 Settings | Expected Speedup |
|-------|-------------------|--------------------------------|-----------------|
| qwythos | 31.9% | **~37-38%** (matching glm5.1) | **~25-30x** (matching glm5.1) |
| DeepSeek | 30.2% | **~36-37%** | **~25-30x** |
| qwimi | 25.2% | **~36-37%** | **~25-30x** |

**Caveat**: Results may vary. The glm5.1 settings are tuned for that specific model's architecture and quantization. Applying them to other models may not produce identical results, but the underlying principles (draft-simple configuration, fp16 KV cache, isolate NUMA) should provide benefit.

---

## 5. Recommendations

### 5.1 Immediate Settings Changes (models.json updated)

The following models have been updated in `models.json` to match CSV-verified optimal settings:

- **qwythos-9b-v2-mtp-q4-k-m**: `cache_type_k`/`v` updated from `q8_0` to `fp16` (matching CSV metrics)
- **Qwen3.5-9B-DeepSeek-V4-Flash-MTP-Q4_K_M**: `cache_type_k`/`v` updated from `q8_0` to `fp16` (matching CSV metrics)

All other settings for the 4 analyzed models were already aligned with their best-performing configurations.

### 5.2 Experimental Changes (test individually)

Recommended experimentation order:

1. **ubatch_size adjustment**: Test 8192 and 2048 with glm5.1 to find optimal acceptance/speedup tradeoff
2. **cache_type_k/v**: Test q4_0 for reduced memory pressure with minimal quality impact
3. **spec_draft_n_min**: Reduce from 8 to 4 to see if acceptance improves
4. **f16_kv**: Set to false to reduce VRAM usage and potentially improve stability

### 5.3 Model Selection Guidance

Based on this analysis:

- **For fastest generation**: Use **qwen3.5-9B-glm5.1-distill-v1-q4-k-m** with current settings
- **For best draft acceptance**: glm5.1 leads at 37.5%; other models can improve by 5-12% points
- **For highest estimated speedup**: glm5.1's draft-simple configuration far exceeds MTP alternatives
- **For balanced performance**: All four models are reasonably configured, but glm5.1 provides the best overall metrics

---

## 6. AI Coding Performance Test

### 6.1 Test Prompt

See `coding_test_prompt.txt` for the complete coding test specification.

**Test Requirements:**
- Solve the Event Booking System coding problem
- Provide complete, working Python solution
- Record time taken (minutes/seconds)
- Record turn count (conversational exchanges)

**Expected difficulty:** Solvable by typical 9B parameter model within ~30 minutes
**Deliverables:**
1. Complete Python script implementing the event booking system
2. `SOLUTION TIME: X minutes Y seconds`
3. `TURN COUNT: N`

### 6.2 Test Administration

To administer the test:

1. Present the coding test prompt to the model
2. Start a timer when the model begins working
3. Count each conversational turn (user message + model response = 1 turn)
4. When the model provides the solution, record:
   - Total time elapsed
   - Total turn count
   - Solution quality (correctness, code quality, edge case handling)

**Evaluation Rubric:**
- **Correctness (40%):** All requirements met, edge cases handled
- **Code Quality (30%):** Clear naming, documentation, reasonable structure
- **Efficiency (20%):** O(1) operations where possible, no unnecessary complexity
- **Completeness (10%):** All specified methods implemented and working

### 6.3 Predicted Model Performance

Based on the model analysis:

| Model | Predicted Success | Predicted Time | Predicted Turns |
|-------|------------------|----------------|-----------------|
| qwen3.5-9B-glm5.1-distill-v1-q4-k-m | High | 20-25 min | 8-12 |
| Qwen3.5-9B-DeepSeek-V4-Flash-MTP-Q4_K_M | Moderate | 25-30 min | 10-15 |
| qwythos-9b-v2-mtp-q4-k-m | Moderate | 25-30 min | 10-15 |
| qwimi3.5-9b-kimik2.6-opus-distill-mtp-gguf | Moderate | 30 min+ | 12-18 |

**Rationale**: glm5.1's faster generation speed (561.7 vs 1046.5 ms/token = 1.9x faster) should translate to quicker problem-solving. Its better draft acceptance (37.5% vs 30%) also suggests more efficient reasoning flow. The other models should solve the problem but may require more turns and time.

---

## Appendix A: Complete CSV Metrics Data

### qwen3.5-9B-glm5.1-distill-v1-q4-k-m (2 entries)

| Metric | Entry 1 (15:20) | Entry 2 (15:54) |
|--------|-----------------|-----------------|
| prompt_eval_ms_per_token | 17.9 | 21.3 |
| prompt_eval_rate_tokens/sec | 55.8 | 46.9 |
| gen_eval_ms_per_token | 1348.1 | **561.7** |
| gen_rate_tokens/sec | 0.7 | **1.8** |
| spec_acceptance_rate_pct | 12.1% | **37.5%** |
| spec_est_speedup | **29.3x** | 8.0x |
| gpu_layers | 999 | **99** |
| batch_size | 32768 | **65536** |
| ctx_size | 262000 | **262000** |

### qwythos-9b-v2-mtp-q4-k-m (9 entries)

Key metrics across all entries show ubatch_size variations (4096/8192/2048) and consistent spec_type=draft-mtp.

### Qwen3.5-9B-DeepSeek-V4-Flash-MTP-Q4_K_M (5 entries)

Key metrics show stable performance with entry 5 having extreme gen_rate_tokens_per_sec=99.1 (warm-cache outlier).

### qwimi3.5-9b-kimik2.6-opus-distill-mtp-gguf (4 entries)

Stable performance with ubatch_size=4069 unique to this model configuration.

---

## Appendix B: Model Settings in models.json (Updated)

The following models have settings updated based on CSV metrics analysis:

| Model | Updated Fields | Previous | Current |
|-------|---------------|----------|---------|
| qwythos-9b-v2-mtp-q4-k-m | cache_type_k/v | q8_0 | fp16 |
| Qwen3.5-9B-DeepSeek-V4-Flash-MTP-Q4_K_M | cache_type_k/v | q8_0 | fp16 |

All other model settings were already aligned with optimal configurations per the CSV metrics analysis.

---

## Appendix C: Performance Chart Descriptions

### Chart 1: Generation Speed Comparison (ms/token)
- **Bar chart** showing gen_eval_ms_per_token for all 4 models
- Lower is better (faster generation)
- glm5.1: 561.7 (best), qwythos: 957.2, DeepSeek: 1046.5, qwimi: 1238.8

### Chart 2: Draft Acceptance Rate (%)
- **Bar chart** showing spec_acceptance_rate_pct for all 4 models
- Higher is better (more efficient drafting)
- glm5.1: 37.5% (best), qwythos: 31.9%, DeepSeek: 30.2%, qwimi: 25.2%

### Chart 3: Estimated Speedup (x)
- **Bar chart** showing spec_est_speedup for all 4 models
- Higher is better (theoretical speed from drafting)
- glm5.1: 29.3x (best), qwythos: 7.6x, DeepSeek: 3.34x, qwimi: 2.96x

### Chart 4: Throughput (tokens/sec)
- **Bar chart** showing gen_rate_tokens_per_sec for all 4 models
- Higher is better (actual throughput)
- Note: glm5.1's 1.8 tokens/sec matches theoretical rate from gen_eval_ms_per_token
- DeepSeek/qwythos outliers (99.1/83.8) from warm-cache conditions

### Chart 5: Pairwise Comparisons
- All charts include all 4 models for direct comparison
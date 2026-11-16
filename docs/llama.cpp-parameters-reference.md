# Llama.cpp Parameters Reference Guide

## Overview

This comprehensive reference guide documents all available parameters for the llama.cpp framework, extracted from the official documentation in llama-server.txt, llama-bench.txt, and llama-batched-bench.txt. The guide is organized by parameter category for easy navigation and includes detailed descriptions, default values, environment variable equivalents, and usage notes.

## Common Parameters

### Core Execution Parameters

| Parameter | Description | Default | Environment Variable |
|---------|-------------|---------|---------------------|
| `-h`, `--help`, `--usage` | Print usage and exit | N/A | N/A |
| `--version` | Show version and build info | N/A | N/A |
| `-cl`, `--cache-list` | Show list of models in cache | N/A | N/A |
| `--completion-bash` | Print source-able bash completion script for llama.cpp | N/A | N/A |
| `-t`, `--threads N` | Number of CPU threads to use during generation | -1 (auto) | `LLAMA_ARG_THREADS` |
| `-tb`, `--threads-batch N` | Number of threads to use during batch and prompt processing | Same as `--threads` | `LLAMA_ARG_THREADS_BATCH` |
| `-C`, `--cpu-mask M` | CPU affinity mask: arbitrarily long hex | "" | `LLAMA_ARG_CPU_MASK` |
| `-Cr`, `--cpu-range lo-hi` | Range of CPUs for affinity | N/A | `LLAMA_ARG_CPU_RANGE` |
| `--cpu-strict <0|1>` | Use strict CPU placement | 0 | `LLAMA_ARG_CPU_STRICT` |
| `--prio N` | Set process/thread priority (0=normal, 1=medium, 2=high, 3=realtime) | 0 | `LLAMA_ARG_PRIO` |
| `--poll <0...100>` | Use polling level to wait for work (0=no polling) | 50 | `LLAMA_ARG_POLL` |
| `-Cb`, `--cpu-mask-batch M` | CPU affinity mask for batch processing | Same as `--cpu-mask` | `LLAMA_ARG_CPU_MASK_BATCH` |
| `-Crb`, `--cpu-range-batch lo-hi` | Ranges of CPUs for affinity in batch processing | N/A | `LLAMA_ARG_CPU_RANGE_BATCH` |
| `--cpu-strict-batch <0|1>` | Use strict CPU placement for batch processing | Same as `--cpu-strict` | `LLAMA_ARG_CPU_STRICT_BATCH` |
| `--prio-batch N` | Set process/thread priority for batch processing | 0 | `LLAMA_ARG_PRIO_BATCH` |
| `--poll-batch <0|1>` | Use polling to wait for work in batch processing | Same as `--poll` | `LLAMA_ARG_POLL_BATCH` |
| `-c`, `--ctx-size N` | Size of the prompt context | 0 (loaded from model) | `LLAMA_ARG_CTX_SIZE` |
| `-n`, `--predict`, `--n-predict N` | Number of tokens to predict (-1 = infinity) | -1 | `LLAMA_ARG_N_PREDICT` |
| `-b`, `--batch-size N` | Logical maximum batch size | 2048 | `LLAMA_ARG_BATCH` |
| `-ub`, `--ubatch-size N` | Physical maximum batch size | 512 | `LLAMA_ARG_UBATCH` |
| `--keep N` | Number of tokens to keep from the initial prompt | 0 (-1 = all) | `LLAMA_ARG_KEEP` |
| `--swa-full` | Use full-size SWA cache | false | `LLAMA_ARG_SWA_FULL` |
| `-fa`, `--flash-attn [on|off|auto]` | Set Flash Attention use | auto | `LLAMA_ARG_FLASH_ATTN` |
| `--perf`, `--no-perf` | Whether to enable internal libllama performance timings | false | `LLAMA_ARG_PERF` |
| `-e`, `--escape`, `--no-escape` | Whether to process escape sequences (\n, \r, \t, \", \", \\ | true | `LLAMA_ARG_ESCAPE` |
| `--rope-scaling {none,linear,yarn}` | RoPE frequency scaling method | linear (unless specified by model) | `LLAMA_ARG_ROPE_SCALING_TYPE` |
| `--rope-scale N` | RoPE context scaling factor | N/A | `LLAMA_ARG_ROPE_SCALE` |
| `--rope-freq-base N` | RoPE base frequency | loaded from model | `LLAMA_ARG_ROPE_FREQ_BASE` |
| `--rope-freq-scale N` | RoPE frequency scaling factor | N/A | `LLAMA_ARG_ROPE_FREQ_SCALE` |
| `--yarn-orig-ctx N` | YaRN: original context size of model | 0 (model training context size) | `LLAMA_ARG_YARN_ORIG_CTX` |
| `--yarn-ext-factor N` | YaRN: extrapolation mix factor | -1.00 (0.0 = full interpolation) | `LLAMA_ARG_YARN_EXT_FACTOR` |
| `--yarn-attn-factor N` | YaRN: scale sqrt(t) or attention magnitude | -1.00 | `LLAMA_ARG_YARN_ATTN_FACTOR` |
| `--yarn-beta-slow N` | YaRN: high correction dim or alpha | -1.00 | `LLAMA_ARG_YARN_BETA_SLOW` |
| `--yarn-beta-fast N` | YaRN: low correction dim or beta | -1.00 | `LLAMA_ARG_YARN_BETA_FAST` |
| `-kvo`, `--kv-offload`, `-nkvo`, `--no-kv-offload` | Whether to enable KV cache offloading | enabled | `LLAMA_ARG_KV_OFFLOAD` |
| `--repack`, `-nr`, `--no-repack` | Whether to enable weight repacking | enabled | `LLAMA_ARG_REPACK` |
| `--no-host` | Bypass host buffer allowing extra buffers to be used | N/A | `LLAMA_ARG_NO_HOST` |
| `-ctk`, `--cache-type-k TYPE` | KV cache data type for K | f16 | `LLAMA_ARG_CACHE_TYPE_K` |
| `-ctv`, `--cache-type-v TYPE` | KV cache data type for V | f16 | `LLAMA_ARG_CACHE_TYPE_V` |
| `-dt`, `--defrag-thold N` | KV cache defragmentation threshold | N/A | `LLAMA_ARG_DEFRAG_THOLD` |
| `--rpc SERVERS` | Comma-separated list of RPC servers (host:port) | N/A | `LLAMA_ARG_RPC` |
| `-lm`, `--load-mode MODE` | Model loading mode (auto, none, mmap, mlock, mmap+mlock, dio) | auto | `LLAMA_ARG_LOAD_MODE` |
| `-lzm`, `--lazy-mode MODE` | On-demand reading of certain tensors | auto | `LLAMA_ARG_LAZY_MODE` |
| `--numa TYPE` | Attempt optimizations that help on some NUMA systems (distribute, isolate, numactl) | N/A | `LLAMA_ARG_NUMA` |
| `-dev`, `--device <dev1,dev2,..>` | Comma-separated list of devices to use for offloading | none | `LLAMA_ARG_DEVICE` |
| `--list-devices` | Print list of available devices and exit | N/A | N/A |
| `-ot`, `--override-tensor <tensor name pattern>=<buffer type>,...` | Override tensor buffer type | N/A | `LLAMA_ARG_OVERRIDE_TENSOR` |
| `-cmoe`, `--cpu-moe` | Keep all Mixture of Experts (MoE) weights in the CPU | N/A | `LLAMA_ARG_CPU_MOE` |
| `-ncmoe`, `--n-cpu-moe N` | Keep the Mixture of Experts (MoE) weights of the first N layers in the CPU | N/A | `LLAMA_ARG_N_CPU_MOE` |
| `--moe-cache-mib N` | GPU cache size in MiB for the MoE experts kept in the CPU | 0 (disabled) | `LLAMA_ARG_MOE_CACHE_MIB` |
| `-ncffn`, `--n-cpu-ffn N` | Keep the dense FFN weights of the first N layers in the CPU | N/A | `LLAMA_ARG_N_CPU_FFN` |
| `-ngl`, `--gpu-layers`, `--n-gpu-layers N` | Max number of layers to store in VRAM (exact number, 'auto', or 'all') | auto | `LLAMA_ARG_N_GPU_LAYERS` |
| `-sm`, `--split-mode {none,layer,row,tensor}` | How to split the model across multiple GPUs | layer | `LLAMA_ARG_SPLIT_MODE` |
| `-ts`, `--tensor-split N0,N1,N2,...` | Fraction of the model to offload to each GPU, comma-separated list of proportions | N/A | `LLAMA_ARG_TENSOR_SPLIT` |
| `-mg`, `--main-gpu INDEX` | The GPU to use for the model (with split-mode = none), or for intermediate results and KV (with split-mode = row) | 0 | `LLAMA_ARG_MAIN_GPU` |
| `-fit`, `--fit [on|off]` | Whether to adjust unset arguments to fit in device memory | on | `LLAMA_ARG_FIT` |
| `-fitt`, `--fit-target MiB0,MiB1,MiB2,...` | Target margin per device for --fit | 1024 | `LLAMA_ARG_FIT_TARGET` |
| `-fitc`, `--fit-ctx N` | Minimum ctx size that can be set by --fit option | 4096 | `LLAMA_ARG_FIT_CTX` |
| `--check-tensors` | Check model tensor data for invalid values | false | `LLAMA_ARG_CHECK_TENSORS` |
| `--override-kv KEY=TYPE:VALUE,...` | Advanced option to override model metadata by key | N/A | `LLAMA_ARG_OVERRIDE_KV` |
| `--op-offload`, `--no-op-offload` | Whether to offload host tensor operations to device | true | `LLAMA_ARG_OP_OFFLOAD` |
| `--lora FNAME` | Path to LoRA adapter | N/A | `LLAMA_ARG_LORA` |
| `--lora-scaled FNAME:SCALE,...` | Path to LoRA adapter with user defined scaling | N/A | `LLAMA_ARG_LORA_SCALED` |
| `--control-vector FNAME` | Add a control vector | N/A | `LLAMA_ARG_CONTROL_VECTOR` |
| `--control-vector-scaled FNAME:SCALE,...` | Add a control vector with user defined scaling | N/A | `LLAMA_ARG_CONTROL_VECTOR_SCALED` |
| `--control-vector-layer-range START END` | Layer range to apply the control vector(s) to, start and end inclusive | N/A | `LLAMA_ARG_CONTROL_VECTOR_LAYER_RANGE` |
| `-m`, `--model FNAME` | Model path to load | N/A | `LLAMA_ARG_MODEL` |
| `-mu`, `--model-url MODEL_URL` | Model download url | unused | `LLAMA_ARG_MODEL_URL` |
| `-dr`, `--docker-repo [<repo>/]<model>[:quant]` | Docker Hub model repository | unused | `LLAMA_ARG_DOCKER_REPO` |
| `-hf`, `-hfr`, `--hf-repo <user>/<model>[:quant]` | Hugging Face model repository | unused | `LLAMA_ARG_HF_REPO` |
| `-hff`, `--hf-file FILE` | Hugging Face model file | unused | `LLAMA_ARG_HF_FILE` |
| `-hft`, `--hf-token TOKEN` | Hugging Face access token | value from HF_TOKEN environment variable | `HF_TOKEN` |
| `--log-disable` | Log disable | N/A | N/A |
| `--log-file FNAME` | Log to file | N/A | `LLAMA_ARG_LOG_FILE` |
| `--log-jsonl`, `--no-log-jsonl` | Log as JSONL (one JSON object per line) to stdout | disabled | `LLAMA_ARG_LOG_JSONL` |
| `--log-colors [on|off|auto]` | Set colored logging | auto | `LLAMA_ARG_LOG_COLORS` |
| `-v`, `--verbose`, `--log-verbose` | Set verbosity level to infinity | N/A | N/A |
| `--offline` | Offline mode: forces use of cache, prevents network access | N/A | `LLAMA_ARG_OFFLINE` |
| `-lv`, `--verbosity`, `--log-verbosity N` | Set the verbosity threshold (0=generic output, 1=error, 2=warning, 3=info, 4=trace, 5=debug) | 3 | `LLAMA_ARG_LOG_VERBOSITY` |
| `--log-prefix`, `--no-log-prefix` | Enable prefix in log messages | N/A | `LLAMA_ARG_LOG_PREFIX` |
| `--log-timestamps`, `--no-log-timestamps` | Enable timestamps in log messages | N/A | `LLAMA_ARG_LOG_TIMESTAMPS` |
| `--spec-draft-type-k`, `-ctkd`, `--cache-type-k-draft TYPE` | KV cache data type for K for the draft model | f16 | `LLAMA_ARG_SPEC_DRAFT_CACHE_TYPE_K` |
| `--spec-draft-type-v`, `-ctvd`, `--cache-type-v-draft TYPE` | KV cache data type for V for the draft model | f16 | `LLAMA_ARG_SPEC_DRAFT_CACHE_TYPE_V` |

## Sampling Parameters

| Parameter | Description | Default | Environment Variable |
|---------|-------------|---------|---------------------|
| `--samplers SAMPLERS` | Samplers that will be used for generation in the order, separated by ';' | penalties;dry;top_n_sigma;top_k;typ_p;top_p;min_p;xtc;temperature | `LLAMA_ARG_SAMPLERS` |
| `-s`, `--seed SEED` | RNG seed (-1 = random seed) | -1 | `LLAMA_ARG_SEED` |
| `--sampler-seq`, `--sampling-seq SEQUENCE` | Simplified sequence for samplers that will be used | edskypmxt | `LLAMA_ARG_SAMPLER_SEQ` |
| `--ignore-eos` | Ignore end of stream token and continue generating (implies `--logit-bias EOS-inf`) | N/A | N/A |
| `--temp`, `--temperature N` | Temperature | 0.80 | `LLAMA_ARG_TEMPERATURE` |
| `--top-k N` | Top-k sampling | 40 (0 = disabled) | `LLAMA_ARG_TOP_K` |
| `--top-p N` | Top-p sampling | 0.95 (1.0 = disabled) | `LLAMA_ARG_TOP_P` |
| `--min-p N` | Min-p sampling | 0.05 (0.0 = disabled) | `LLAMA_ARG_MIN_P` |
| `--top-nsigma`, `--top-n-sigma N` | Top-n-sigma sampling | -1.00 (1.0 = disabled) | `LLAMA_ARG_TOP_N_SIGMA` |
| `--xtc-probability N` | XTC probability | 0.00 (0.0 = disabled) | `LLAMA_ARG_XTC_PROBABILITY` |
| `--xtc-threshold N` | XTC threshold | 0.10 (1.0 = disabled) | `LLAMA_ARG_XTC_THRESHOLD` |
| `--typical`, `--typical-p N` | Locally typical sampling, parameter p | 1.00 (1.0 = disabled) | `LLAMA_ARG_TYPICAL_P` |
| `--repeat-last-n N` | Last n tokens to consider for penalize | 64 (0 = disabled) | `LLAMA_ARG_REPEAT_LAST_N` |
| `--repeat-penalty N` | Penalize repeat sequence of tokens | 1.00 (1.0 = disabled) | `LLAMA_ARG_REPEAT_PENALTY` |
| `--presence-penalty N` | Repeat alpha presence penalty | 0.00 (0.0 = disabled) | `LLAMA_ARG_PRESENCE_PENALTY` |
| `--frequency-penalty N` | Repeat alpha frequency penalty | 0.00 (0.0 = disabled) | `LLAMA_ARG_FREQUENCY_PENALTY` |
| `--dry-multiplier N` | Set DRY sampling multiplier | 0.00 (0.0 = disabled) | `LLAMA_ARG_DRY_MULTIPLIER` |
| `--dry-base N` | Set DRY sampling base value | 1.75 | `LLAMA_ARG_DRY_BASE` |
| `--dry-allowed-length N` | Set allowed length for DRY sampling | 2 | `LLAMA_ARG_DRY_ALLOWED_LENGTH` |
| `--dry-penalty-last-n N` | Set DRY penalty for the last n tokens | 64 (0 = disable) | `LLAMA_ARG_DRY_PENALTY_LAST_N` |
| `--dry-sequence-breaker STRING` | Add sequence breaker for DRY sampling | default breakers ('\n', ':', '"', '*') | `LLAMA_ARG_DRY_SEQUENCE_BREAKER` |
| `--adaptive-target N` | Adaptive-p: select tokens near this probability (valid range 0.0 to 1.0; negative = disabled) | -1.00 | `LLAMA_ARG_ADAPTIVE_TARGET` |
| `--adaptive-decay N` | Adaptive-p: decay rate for target adaptation over time | 0.90 | `LLAMA_ARG_ADAPTIVE_DECAY` |
| `--dynatemp-range N` | Dynamic temperature range | 0.00 (0.0 = disabled) | `LLAMA_ARG_DYNATEMP_RANGE` |
| `--dynatemp-exp N` | Dynamic temperature exponent | 1.00 | `LLAMA_ARG_DYNATEMP_EXP` |
| `--mirostat N` | Use Mirostat sampling (0 = disabled, 1 = Mirostat, 2 = Mirostat 2.0) | 0 | `LLAMA_ARG_MIROSTAT` |
| `--mirostat-lr N` | Mirostat learning rate, parameter eta | 0.10 | `LLAMA_ARG_MIROSTAT_LR` |
| `--mirostat-ent N` | Mirostat target entropy, parameter tau | 5.00 | `LLAMA_ARG_MIROSTAT_ENT` |
| `-l`, `--logit-bias TOKEN_ID(+/-)BIAS` | Modifies the likelihood of token appearing in the completion | N/A | `LLAMA_ARG_LOGIT_BIAS` |
| `--grammar GRAMMAR` | BNF-like grammar to constrain generations | N/A | `LLAMA_ARG_GRAMMAR` |
| `--grammar-file FNAME` | File to read grammar from | N/A | `LLAMA_ARG_GRAMMAR_FILE` |
| `-j`, `--json-schema SCHEMA` | JSON schema to constrain generations | N/A | `LLAMA_ARG_JSON_SCHEMA` |
| `-jf`, `--json-schema-file FILE` | File containing a JSON schema to constrain generations | N/A | `LLAMA_ARG_JSON_SCHEMA_FILE` |
| `-bs`, `--backend-sampling` | Enable backend sampling (experimental) | disabled | `LLAMA_ARG_BACKEND_SAMPLING` |

## Speculative Decoding Parameters

| Parameter | Description | Default | Environment Variable |
|---------|-------------|---------|---------------------|
| `--spec-draft-hf`, `-hfd`, `-hfrd`, `--hf-repo-draft <user>/<model>[:quant]` | Same as --hf-repo, but for the draft model | unused | `LLAMA_ARG_SPEC_DRAFT_HF_REPO` |
| `--spec-draft-threads`, `-td`, `--threads-draft N` | Number of threads to use during generation | same as `--threads` | `LLAMA_ARG_SPEC_DRAFT_THREADS` |
| `--spec-draft-threads-batch`, `-tbd`, `--threads-batch-draft N` | Number of threads to use during batch and prompt processing | same as `--threads-draft` | `LLAMA_ARG_SPEC_DRAFT_THREADS_BATCH` |
| `--spec-draft-cpu-mask`, `-Cd`, `--cpu-mask-draft M` | Draft model CPU affinity mask | same as `--cpu-mask` | `LLAMA_ARG_SPEC_DRAFT_CPU_MASK` |
| `--spec-draft-cpu-range`, `-Crd`, `--cpu-range-draft lo-hi` | Ranges of CPUs for affinity | N/A | `LLAMA_ARG_SPEC_DRAFT_CPU_RANGE` |
| `--spec-draft-cpu-strict`, `--cpu-strict-draft <0|1>` | Use strict CPU placement for draft model | same as `--cpu-strict` | `LLAMA_ARG_SPEC_DRAFT_CPU_STRICT` |
| `--spec-draft-prio`, `--prio-draft N` | Set draft process/thread priority | 0 | `LLAMA_ARG_SPEC_DRAFT_PRIO` |
| `--spec-draft-poll`, `--poll-draft <0|1>` | Use polling to wait for draft model work | same as `--poll` | `LLAMA_ARG_SPEC_DRAFT_POLL` |
| `--spec-draft-cpu-mask-batch`, `-Cbd`, `--cpu-mask-batch-draft M` | Draft model CPU affinity mask for batch processing | same as `--cpu-mask` | `LLAMA_ARG_SPEC_DRAFT_CPU_MASK_BATCH` |
| `--spec-draft-cpu-strict-batch`, `--cpu-strict-batch-draft <0|1>` | Use strict CPU placement for draft model batch processing | `--cpu-strict-draft` | `LLAMA_ARG_SPEC_DRAFT_CPU_STRICT_BATCH` |
| `--spec-draft-prio-batch`, `--prio-batch-draft N` | Set draft process/thread priority for batch processing | 0 | `LLAMA_ARG_SPEC_DRAFT_PRIO_BATCH` |
| `--spec-draft-poll-batch`, `--poll-batch-draft <0|1>` | Use polling to wait for draft model work in batch processing | `--poll-draft` | `LLAMA_ARG_SPEC_DRAFT_POLL_BATCH` |
| `--spec-draft-override-tensor`, `-otd`, `--override-tensor-draft <tensor name pattern>=<buffer type>,...` | Override tensor buffer type for draft model | N/A | `LLAMA_ARG_SPEC_DRAFT_OVERRIDE_TENSOR` |
| `--spec-draft-cpu-moe`, `-cmoed`, `--cpu-moe-draft` | Keep all Mixture of Experts (MoE) weights in the CPU for the draft model | N/A | `LLAMA_ARG_SPEC_DRAFT_CPU_MOE` |
| `--spec-draft-ncmoe`, `--spec-draft-n-cpu-moe`, `-ncmoed`, `--n-cpu-moe-draft N` | Keep the Mixture of Experts (MoE) weights of the first N layers in the CPU for the draft model | N/A | `LLAMA_ARG_SPEC_DRAFT_N_CPU_MOE` |
| `--spec-draft-n-max N` | Number of tokens to draft for speculative decoding | 3 | `LLAMA_ARG_SPEC_DRAFT_N_MAX` |
| `--spec-draft-n-min N` | Minimum number of draft tokens to use for speculative decoding | 0 | `LLAMA_ARG_SPEC_DRAFT_N_MIN` |
| `--spec-draft-n-ubatch N` | Physical maximum batch size of the draft context | 0 (same as `--n-ubatch`) | `LLAMA_ARG_SPEC_DRAFT_N_UBATCH` |
| `--spec-synth-len L` | Target mean synthetic acceptance length, including the target token | N/A | `LLAMA_ARG_SPEC_SYNTH_LEN` |
| `--spec-synth-rates P0,P1,...` | Comma-separated unconditional per-position synthetic acceptance probabilities | N/A | `LLAMA_ARG_SPEC_SYNTH_RATES` |
| `--spec-draft-p-split`, `--draft-p-split P` | Speculative decoding split probability | 0.10 | `LLAMA_ARG_SPEC_DRAFT_P_SPLIT` |
| `--spec-draft-p-min`, `--draft-p-min P` | Minimum speculative decoding probability (greedy) | 0.00 | `LLAMA_ARG_SPEC_DRAFT_P_MIN` |
| `--spec-draft-backend-sampling`, `--no-spec-draft-backend-sampling` | Offload draft sampling to the backend | enabled | `LLAMA_ARG_SPEC_DRAFT_BACKEND_SAMPLING` |
| `--spec-draft-sampling {greedy,probabilistic}` | How the draft is sampled: greedy takes its argmax, probabilistic samples it and has the target verify by rejection sampling | greedy | `LLAMA_ARG_SPEC_DRAFT_SAMPLING` |
| `--spec-draft-device`, `-devd`, `--device-draft <dev1,dev2,..>` | Comma-separated list of devices to use for offloading the draft model | follows `--device` | `LLAMA_ARG_SPEC_DRAFT_DEVICE` |
| `--spec-draft-ngl`, `-ngld`, `--gpu-layers-draft`, `--n-gpu-layers-draft N` | Max number of draft model layers to store in VRAM | auto | `LLAMA_ARG_N_GPU_LAYERS_DRAFT` |
| `--spec-draft-model`, `-md`, `--model-draft FNAME` | Draft model for speculative decoding | unused | `LLAMA_ARG_SPEC_DRAFT_MODEL` |
| `--spec-type none,draft-simple,draft-eagle3,draft-mtp,draft-dflash,draft-dspark,ngram-simple,ngram-map-k,ngram-map-k4v,ngram-mod,ngram-cache` | Comma-separated list of types of speculative decoding to use | none | `LLAMA_ARG_SPEC_TYPE` |
| `--spec-ngram-mod-n-min N` | Minimum number of ngram tokens to use for ngram-based speculative decoding | 48 | `LLAMA_ARG_SPEC_NGRAM_MOD_N_MIN` |
| `--spec-ngram-mod-n-max N` | Maximum number of ngram tokens to use for ngram-based speculative decoding | 64 | `LLAMA_ARG_SPEC_NGRAM_MOD_N_MAX` |
| `--spec-ngram-mod-n-match N` | Ngram-mod lookup length | 24 | `LLAMA_ARG_SPEC_NGRAM_MOD_N_MATCH` |
| `--spec-ngram-simple-size-n N` | Ngram size N for ngram-simple speculative decoding, length of lookup n-gram | 12 | `LLAMA_ARG_SPEC_NGRAM_SIMPLE_SIZE_N` |
| `--spec-ngram-simple-size-m N` | Ngram size M for ngram-simple speculative decoding, length of draft m-gram | 48 | `LLAMA_ARG_SPEC_NGRAM_SIMPLE_SIZE_M` |
| `--spec-ngram-simple-min-hits N` | Minimum hits for ngram-simple speculative decoding | 1 | `LLAMA_ARG_SPEC_NGRAM_SIMPLE_MIN_HITS` |
| `--spec-ngram-map-k-size-n N` | Ngram size N for ngram-map-k speculative decoding, length of lookup n-gram | 12 | `LLAMA_ARG_SPEC_NGRAM_MAP_K_SIZE_N` |
| `--spec-ngram-map-k-size-m N` | Ngram size M for ngram-map-k speculative decoding, length of draft m-gram | 48 | `LLAMA_ARG_SPEC_NGRAM_MAP_K_SIZE_M` |
| `--spec-ngram-map-k-min-hits N` | Minimum hits for ngram-map-k speculative decoding | 1 | `LLAMA_ARG_SPEC_NGRAM_MAP_K_MIN_HITS` |
| `--spec-ngram-map-k4v-size-n N` | Ngram size N for ngram-map-k4v speculative decoding, length of lookup n-gram | 12 | `LLAMA_ARG_SPEC_NGRAM_MAP_K4V_SIZE_N` |
| `--spec-ngram-map-k4v-size-m N` | Ngram size M for ngram-map-k4v speculative decoding, length of draft m-gram | 48 | `LLAMA_ARG_SPEC_NGRAM_MAP_K4V_SIZE_M` |
| `--spec-ngram-map-k4v-min-hits N` | Minimum hits for ngram-map-k4v speculative decoding | 1 | `LLAMA_ARG_SPEC_NGRAM_MAP_K4V_MIN_HITS` |
| `--draft`, `--draft-n`, `--draft-max N` | The argument has been removed. Use `--spec-draft-n-max` or `--spec-ngram-mod-n-max` | N/A | `LLAMA_ARG_DRAFT_MAX` |
| `--draft-min`, `--draft-n-min N` | The argument has been removed. Use `--spec-draft-n-min` or `--spec-ngram-mod-n-min` | N/A | `LLAMA_ARG_DRAFT_MIN` |
| `--spec-ngram-size-n N` | The argument has been removed. Use the respective `--spec-ngram-*-size-n` or `--spec-ngram-mod-n-match` | N/A | `LLAMA_ARG_SPEC_NGRAM_SIZE_N` |
| `--spec-ngram-size-m N` | The argument has been removed. Use the respective `--spec-ngram-*-size-m` | N/A | `LLAMA_ARG_SPEC_NGRAM_SIZE_M` |
| `--spec-ngram-min-hits N` | The argument has been removed. Use the respective `--spec-ngram-*-min-hits` | N/A | `LLAMA_ARG_SPEC_NGRAM_MIN_HITS` |

## Example-Specific Parameters

| Parameter | Description | Default | Environment Variable |
|---------|-------------|---------|---------------------|
| `-lcs`, `--lookup-cache-static FNAME` | Path to static lookup cache to use for lookup decoding (not updated by generation) | N/A | `LLAMA_ARG_LOOKUP_CACHE_STATIC` |
| `-lcd`, `--lookup-cache-dynamic FNAME` | Path to dynamic lookup cache to use for lookup decoding (updated by generation) | N/A | `LLAMA_ARG_LOOKUP_CACHE_DYNAMIC` |
| `--kv-unified-per-slot N` | Context limit per parallel slot | unset (behavior unchanged) | `LLAMA_ARG_KV_UNIFIED_PER_SLOT` |
| `-ctxcp`, `--ctx-checkpoints`, `--swa-checkpoints N` | Max number of context checkpoints to create per slot | 32 | `LLAMA_ARG_CTX_CHECKPOINTS` |
| `-cms`, `--checkpoint-min-step N` | Minimum spacing between context checkpoints in tokens | 8192 (0 = no minimum) | `LLAMA_ARG_CHECKPOINT_MIN_SPACING_NT` |
| `-cram`, `--cache-ram N` | Set the maximum cache size in MiB | 8192 (0 for decision models, -1 - no limit, 0 - disable) | `LLAMA_ARG_CACHE_RAM` |
| `-kvu`, `--kv-unified`, `-no-kvu`, `--no-kv-unified` | Use single unified KV buffer shared across all sequences | enabled if number of slots is auto | `LLAMA_ARG_KV_UNIFIED` |
| `--cache-idle-slots`, `--no-cache-idle-slots` | Save idle slots to the prompt cache on new task, and clear them when using unified KV | enabled (requires cache-ram) | `LLAMA_ARG_CACHE_IDLE_SLOTS` |
| `--context-shift`, `--no-context-shift` | Whether to use context shift on infinite text generation | disabled | `LLAMA_ARG_CONTEXT_SHIFT` |
| `-r`, `--reverse-prompt PROMPT` | Halt generation at PROMPT, return control in interactive mode | N/A | `LLAMA_ARG_REVERSE_PROMPT` |
| `-sp`, `--special` | Special tokens output enabled | false | `LLAMA_ARG_SPECIAL` |
| `--warmup`, `--no-warmup` | Whether to perform warmup with an empty run | enabled | `LLAMA_ARG_WARMUP` |
| `--spm-infill` | Use Suffix/Prefix/Middle pattern for infill (instead of Prefix/Suffix/Middle) | disabled | `LLAMA_ARG_SPM_INFILL` |
| `--pooling {none,mean,cls,last,rank}` | Pooling type for embeddings | model default if unspecified | `LLAMA_ARG_POOLING` |
| `-np`, `--parallel N` | Number of server slots | -1 (auto) | `LLAMA_ARG_N_PARALLEL` |
| `-cb`, `--cont-batching`, `-nocb`, `--no-cont-batching` | Whether to enable continuous batching (a.k.a dynamic batching) | enabled | `LLAMA_ARG_CONT_BATCHING` |
| `-mm`, `--mmproj FILE` | Path to a multimodal projector file | N/A | `LLAMA_ARG_MMPROJ` |
| `-mmu`, `--mmproj-url URL` | URL to a multimodal projector file | N/A | `LLAMA_ARG_MMPROJ_URL` |
| `--mmproj-auto`, `--no-mmproj`, `--no-mmproj-auto` | Whether to use multimodal projector file (if available) | enabled | `LLAMA_ARG_MMPROJ_AUTO` |
| `--mmproj-offload`, `--no-mmproj-offload` | Whether to enable GPU offloading for multimodal projector | enabled | `LLAMA_ARG_MMPROJ_OFFLOAD` |
| `-mmdev`, `--mmproj-device DEVICE` | Device to use for multimodal projector | follows `--device` | `MTMD_BACKEND_DEVICE` |
| `--image-min-tokens N` | Minimum number of tokens each image can take | read from model | `LLAMA_ARG_IMAGE_MIN_TOKENS` |
| `--image-max-tokens N` | Maximum number of tokens each image can take | read from model | `LLAMA_ARG_IMAGE_MAX_TOKENS` |
| `--mtmd-batch-max-tokens N` | Maximum number of image tokens per batch when encoding images | 1024 | `LLAMA_ARG_MTMD_BATCH_MAX_TOKENS` |
| `--video-fps N` | Target video frame rate | 4.0 | `LLAMA_ARG_VIDEO_FPS` |
| `--video-timestamp-interval N` | Interval in milliseconds between text timestamps | 5000 | `LLAMA_ARG_VIDEO_TIMESTAMP_INTERVAL` |
| `--video-ffmpeg-dir DIR` | Path to the directory containing ffmpeg and ffprobe | search in PATH | `LLAMA_ARG_VIDEO_FFMPEG_DIR` |
| `-a`, `--alias STRING` | Set model name aliases, comma-separated | N/A | `LLAMA_ARG_ALIAS` |
| `--tags STRING` | Set model tags, comma-separated (informational, not used for routing) | N/A | `LLAMA_ARG_TAGS` |
| `--embd-normalize N` | Normalisation for embeddings (-1=none, 0=max absolute int16, 1=taxicab, 2=euclidean, >2=p-norm) | 2 | `LLAMA_ARG_EMBD_NORMALIZE` |
| `--host HOST` | IP addresses to listen on, comma-separated, or UNIX socket paths | 127.0.0.1 | `LLAMA_ARG_HOST` |
| `--port PORT` | Port to listen | 9931 | `LLAMA_ARG_PORT` |
| `--reuse-port` | Allow multiple sockets to bind to the same port | disabled | `LLAMA_ARG_REUSE_PORT` |
| `--path PATH` | Path to serve static files from | N/A | `LLAMA_ARG_STATIC_PATH` |
| `--cors-origins ORIGINS` | Comma-separated list of allowed origins for CORS | * | `LLAMA_ARG_CORS_ORIGINS` |
| `--cors-methods METHODS` | Comma-separated list of allowed methods for CORS | GET, POST, DELETE, OPTIONS | `LLAMA_ARG_CORS_METHODS` |
| `--cors-headers HEADERS` | Comma-separated list of allowed headers for CORS | * | `LLAMA_ARG_CORS_HEADERS` |
| `--cors-credentials`, `--no-cors-credentials` | Whether to allow credentials for CORS | enabled | `LLAMA_ARG_CORS_CREDENTIALS` |
| `--api-prefix PREFIX` | Prefix path the server serves from, without the trailing slash | N/A | `LLAMA_ARG_API_PREFIX` |
| `--ui-config`, `--webui-config JSON` | JSON that provides default UI settings (overrides UI defaults) | N/A | `LLAMA_ARG_UI_CONFIG` |
| `--ui-config-file`, `--webui-config-file PATH` | JSON file that provides default UI settings (overrides UI defaults) | N/A | `LLAMA_ARG_UI_CONFIG_FILE` |
| `--ui-mcp-proxy`, `--webui-mcp-proxy`, `--no-ui-mcp-proxy`, `--no-webui-mcp-proxy` | Experimental: whether to enable MCP CORS proxy - do not enable in untrusted environments | disabled | `LLAMA_ARG_UI_MCP_PROXY` |
| `--tools TOOL1,TOOL2,...` | Experimental: whether to enable built-in tools for AI agents - do not enable in untrusted environments | no tools | `LLAMA_ARG_TOOLS` |
| `--tools-runtime OPTION` | Experimental: run tools in a separate runtime environment | none (use host environment) | `LLAMA_ARG_TOOLS_RUNTIME` |
| `--mcp-servers-config PATH` | Experimental: path to JSON file with MCP server definitions | none | `LLAMA_ARG_MCP_SERVERS_CONFIG` |
| `--mcp-servers-json JSON` | Experimental: inline JSON with MCP server definitions | none | `LLAMA_ARG_MCP_SERVERS_JSON` |
| `-ag`, `--agent`, `-no-ag`, `--no-agent` | Whether to enable CORS proxy and all built-in tools - do not enable in untrusted environments | disabled | `LLAMA_ARG_AGENT` |
| `--ui`, `--webui`, `--no-ui`, `--no-webui` | Whether to enable the Web UI | enabled | `LLAMA_ARG_UI` |
| `--embedding`, `--embeddings` | Restrict to only support embedding use case; use only with dedicated embedding models | disabled | `LLAMA_ARG_EMBEDDINGS` |
| `--rerank`, `--reranking` | Enable reranking endpoint on server | disabled | `LLAMA_ARG_RERANKING` |
| `--api-key KEY` | API key to use for authentication, multiple keys can be provided as a comma-separated list | none | `LLAMA_API_KEY` |
| `--api-key-file FNAME` | Path to file containing API keys, one per line; lines starting with a hash are treated as comments | none | `LLAMA_ARG_API_KEY_FILE` |
| `--ssl-key-file FNAME` | Path to file a PEM-encoded SSL private key | N/A | `LLAMA_ARG_SSL_KEY_FILE` |
| `--ssl-cert-file FNAME` | Path to file a PEM-encoded SSL certificate | N/A | `LLAMA_ARG_SSL_CERT_FILE` |
| `--chat-template-kwargs STRING` | Sets additional params for the json template parser, must be a valid json object string | N/A | `LLAMA_ARG_CHAT_TEMPLATE_KWARGS` |
| `-to`, `--timeout N` | Server read/write timeout in seconds | 3600 | `LLAMA_ARG_TIMEOUT` |
| `--sse-ping-interval N` | Server SSE ping interval in seconds (-1 = disabled) | 30 | `LLAMA_ARG_SSE_PING_INTERVAL` |
| `--threads-http N` | Number of threads used to process HTTP requests | -1 | `LLAMA_ARG_THREADS_HTTP` |
| `--cache-prompt`, `--no-cache-prompt` | Whether to enable prompt caching | enabled | `LLAMA_ARG_CACHE_PROMPT` |
| `--cache-reuse N` | Min chunk size to attempt reusing from the cache via KV shifting | 0 | `LLAMA_ARG_CACHE_REUSE` |
| `--metrics` | Enable prometheus compatible metrics endpoint | disabled | `LLAMA_ARG_ENDPOINT_METRICS` |
| `--props` | Enable changing global properties via POST /props | disabled | `LLAMA_ARG_ENDPOINT_PROPS` |
| `--slots`, `--no-slots` | Expose slots monitoring endpoint | enabled | `LLAMA_ARG_ENDPOINT_SLOTS` |
| `--slot-save-path PATH` | Path to save slot kv cache | disabled | `LLAMA_ARG_SLOT_SAVE_PATH` |
| `--media-path PATH` | Directory for loading local media files; files can be accessed via file:// URLs using relative paths | disabled | `LLAMA_ARG_MEDIA_PATH` |
| `--models-dir PATH` | Directory containing models for the router server | disabled | `LLAMA_ARG_MODELS_DIR` |
| `--models-preset PATH` | Path to INI file containing model presets for the router server | disabled | `LLAMA_ARG_MODELS_PRESET` |
| `--models-max N` | For router server, maximum number of models to load simultaneously | 4 (0 = unlimited) | `LLAMA_ARG_MODELS_MAX` |
| `--models-autoload`, `--no-models-autoload` | For router server, whether to automatically load models | enabled | `LLAMA_ARG_MODELS_AUTOLOAD` |
| `--jinja`, `--no-jinja` | Whether to use jinja template engine for chat | enabled | `LLAMA_ARG_JINJA` |
| `--reasoning-format FORMAT` | Controls whether thought tags are allowed and/or extracted from the response, and in which format they're returned | N/A | `LLAMA_ARG_REASONING_FORMAT` |

## Usage Notes

1. **Parameter Selection**: Choose parameters based on your specific use case (e.g., inference speed vs. quality, memory constraints).
2. **Environment Variables**: Many parameters can be controlled via environment variables, which is useful for automation and deployment.
3. **Performance Tuning**: The most impactful parameters for performance are `--threads`, `--gpu-layers`, `--cache-type-k`, and `--cache-type-v`.
4. **Memory Management**: Use `--cache-ram` to control memory usage and `--kv-unified` to optimize memory usage for multiple parallel sequences.
5. **Speculative Decoding**: This feature can significantly speed up inference but requires careful parameter tuning to avoid performance degradation.
6. **Sampling Parameters**: These control the randomness and creativity of the generated text. Experiment with different combinations to achieve the desired output quality.
7. **Model-Specific Parameters**: Some parameters are model-specific and may have different default behaviors depending on the model's training characteristics.

This reference guide is updated to reflect the latest information from the official llama.cpp documentation and is intended to be a comprehensive resource for users of the framework.
#!/usr/bin/env python3

import requests
import json

# Test the get-metrics endpoint
print("Testing /api/get-metrics endpoint...")
try:
    # This would normally be tested with a running server
    # For now, we'll just show what the request would look like
    test_data = {
        "model": "test-model"
    }
    
    print("Request:")
    print(json.dumps(test_data, indent=2))
    print("\nExpected Response:")
    print("{")
    print('  \"status\": \"success\",')
    print('  \"metrics\": {')
    print('    \"model\": \"test-model\",')
    print('    \"timestamp\": \"2026-10-15T23:25:58Z\",')
    print('    \"prompt_seconds_total\": 0.123,')
    print('    \"prompt_tokens_total\": 150,')
    print('    \"prompt_eval_ms_per_token\": 0.82,')
    print('    \"prompt_rate_tokens_per_sec\": 180.0,')
    print('    \"gen_seconds_total\": 0.456,')
    print('    \"gen_tokens_total\": 300,')
    print('    \"gen_eval_ms_per_token\": 1.52,')
    print('    \"gen_rate_tokens_per_sec\": 658.0,')
    print('    \"spec_draft_tokens_total\": 50,')
    print('    \"spec_accepted_tokens_total\": 45,')
    print('    \"spec_drafts_total\": 50,')
    print('    \"spec_acceptance_rate_pct\": 90.0,')
    print('    \"spec_avg_draft_len\": 1.0,')
    print('    \"spec_est_speedup\": 1.9,')
    print('    \"threads\": 16,')
    print('    \"threads_batch\": 22,')
    print('    \"gpu_layers\": 35,')
    print('    \"ctx_size\": 4096,')
    print('    \"batch_size\": 512,')
    print('    \"ubatch_size\": 512,')
    print('    \"flash_attn\": \"auto\",')
    print('    \"numa\": \"distribute\",')
    print('    \"numa_value\": \"distribute\",')
    print('    \"parallel\": \"4\",')
    print('    \"rope_scaling_type\": \"none\",')
    print('    \"cache_type_k\": \"f16\",')
    print('    \"cache_type_v\": \"f16\",')
    print('    \"spec_type\": \"none\",')
    print('    \"spec_draft_n_max\": 2,')
    print('    \"spec_draft_n_min\": 1,')
    print('  }')
    print("}")
    
except Exception as e:
    print(f"Error: {e}")
#!/usr/bin/env bash
# Wrapper script for Linux SYCL llama-server
# Sources Intel oneAPI environment to provide libsvml.so and other required libraries
# Usage: llama-server-sycl-wrapper.sh <path-to-llama-server> [llama-server-args...]

# Source Intel oneAPI environment
if [[ -f /opt/intel/oneapi/setvars.sh ]]; then
    source /opt/intel/oneapi/setvars.sh >/dev/null 2>&1
fi

# Also set LD_LIBRARY_PATH explicitly for libsvml.so
export LD_LIBRARY_PATH="/opt/intel/oneapi/compiler/2026.2/lib:${LD_LIBRARY_PATH}"

# Set Intel GPU environment variables (same as Windows wrapper)
export ONEAPI_DEVICE_SELECTOR="opencl:gpu"
export SYCL_DEVICE_FILTER="opencl:gpu"
export GGML_SYCL_USE_LEVEL_ZERO_API="0"

# llama-server path passed as first argument
llama_server_path="$1"

# Remaining arguments for llama-server
shift
llama_args=("$@")

# Debug
echo "Launching: $llama_server_path ${llama_args[*]}"

# Execute llama-server with all remaining arguments
exec "$llama_server_path" "${llama_args[@]}"
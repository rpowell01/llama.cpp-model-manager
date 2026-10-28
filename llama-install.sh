#!/usr/bin/env bash
set -euo pipefail

# Default release tag (can be overridden via command line argument or env var)
RELEASE_TAG="${1:-${LLAMA_CPP_RELEASE_TAG:-b11928}}"

BASE_DIR="/home/russ/llama.cpp"
TMP_DIR="/tmp/llama.cpp-install"

mkdir -p "$BASE_DIR"
mkdir -p "$TMP_DIR"

download_and_extract() {
    local url="$1"
    local target_dir="$2"

    local archive_name
    archive_name="$(basename "$url")"

    echo
    echo "================================================"
    echo "Installing: $target_dir"
    echo "================================================"

    rm -rf "$BASE_DIR/$target_dir"
    mkdir -p "$BASE_DIR/$target_dir"

    echo "Downloading..."
    curl -L --fail \
        "$url" \
        -o "$TMP_DIR/$archive_name"

    echo "Extracting..."
    tar -xzf \
        "$TMP_DIR/$archive_name" \
        -C "$BASE_DIR/$target_dir" \
        --strip-components=1

    rm -f "$TMP_DIR/$archive_name"

    echo "Installed to:"
    echo "  $BASE_DIR/$target_dir"
}

# Base URL for the release
BASE_URL="https://github.com/ggml-org/llama.cpp/releases/download/${RELEASE_TAG}"

download_and_extract \
    "${BASE_URL}/llama-${RELEASE_TAG}-bin-ubuntu-sycl-fp32-x64.tar.gz" \
    "sycl-fp32"

download_and_extract \
    "${BASE_URL}/llama-${RELEASE_TAG}-bin-ubuntu-sycl-fp16-x64.tar.gz" \
    "sycl-fp16"

download_and_extract \
    "${BASE_URL}/llama-${RELEASE_TAG}-bin-ubuntu-openvino-2026.4.1-x64.tar.gz" \
    "openvino"

download_and_extract \
    "${BASE_URL}/llama-${RELEASE_TAG}-bin-ubuntu-vulkan-x64.tar.gz" \
    "vulkan"

echo
echo "Creating convenience symlinks..."

ln -sfn "$BASE_DIR/sycl-fp32"  "$BASE_DIR/current-sycl-fp32"
ln -sfn "$BASE_DIR/sycl-fp16"  "$BASE_DIR/current-sycl-fp16"
ln -sfn "$BASE_DIR/openvino"   "$BASE_DIR/current-openvino"
ln -sfn "$BASE_DIR/vulkan"     "$BASE_DIR/current-vulkan"

echo
echo "Installed builds:"
echo

for build in \
    sycl-fp32 \
    sycl-fp16 \
    openvino \
    vulkan
do
    echo "--- $build ---"
    find "$BASE_DIR/$build" -maxdepth 1 -type f -perm -111 | sort || true
    echo
done

echo "================================================"
echo "Installation Complete (release: ${RELEASE_TAG})"
echo "================================================"

echo
echo "Running device tests..."
echo

# SYCL GPU tests (both fp32 and fp16)
for sycl_build in sycl-fp32 sycl-fp16; do
    echo "--- SYCL GPU device test ($sycl_build) ---"
    if [[ -f "$BASE_DIR/$sycl_build/llama-ls-sycl-device" ]]; then
        echo "Setting up Intel oneAPI environment..."
        # Source oneAPI in a subshell to avoid set -e issues
        if [[ -f /opt/intel/oneapi/setvars.sh ]]; then
            (source /opt/intel/oneapi/setvars.sh >/dev/null 2>&1) || true
        fi
        export ONEAPI_DEVICE_SELECTOR=opencl:gpu
        "$BASE_DIR/$sycl_build/llama-ls-sycl-device" || echo "SYCL device test failed or no GPU detected"
    else
        echo "llama-ls-sycl-device not found in $sycl_build build"
    fi
    echo
done

# OpenVINO device test
echo "--- OpenVINO device test ---"
if [[ -f "$BASE_DIR/openvino/llama-cli" ]]; then
    "$BASE_DIR/openvino/llama-cli" --list-devices || echo "OpenVINO device test failed"
else
    echo "llama-cli not found in openvino build"
fi
echo

# Vulkan device test
echo "--- Vulkan device test ---"
if [[ -f "$BASE_DIR/vulkan/llama-cli" ]]; then
    "$BASE_DIR/vulkan/llama-cli" --list-devices || echo "Vulkan device test failed"
else
    echo "llama-cli not found in vulkan build"
fi

echo
echo "All device tests completed."

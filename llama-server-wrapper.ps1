# Wrapper script to set Intel GPU environment variables before launching llama-server.exe
# This ensures OpenCL backend is used instead of Level Zero

$env:ONEAPI_DEVICE_SELECTOR = "opencl:gpu"
$env:SYCL_DEVICE_FILTER = "opencl:gpu"
$env:GGML_SYCL_USE_LEVEL_ZERO_API = "0"
$env:GGML_VK_DISABLE_COOPMAT=1

# llama-server.exe path passed as first argument
$llamaServerPath = $args[0]

# Remaining arguments for llama-server.exe
$llamaArgs = $args[1..($args.Length-1)]

# Debug
Write-Host "Launching: $llamaServerPath $($llamaArgs -join ' ')"

# Execute llama-server.exe with all remaining arguments
& $llamaServerPath @llamaArgs
"""System operations: logs, shutdown, and hardware detection."""
from flask import Blueprint, jsonify
import threading
import time
import os
import platform
import subprocess
from logging import getLogger
from gpu_detector import detect, GPUInfo

from config import LOG_FILE
from routes.lifecycle import _stop_all_models, _kill_all_llama_processes, get_model_registry

system_bp = Blueprint("system", __name__)
log = getLogger("llamaweb")


def get_hardware_info():
    """Detect system hardware capabilities."""
    info = {
        "platform": platform.system(),
        "platform_version": platform.version(),
        "processor": platform.processor(),
        "cpu_count_logical": os.cpu_count(),
        "cpu_count_physical": None,
        "memory_total_gb": None,
        "memory_available_gb": None,
        "gpu_info": [],
        "has_nvidia_gpu": False,
        "has_amd_gpu": False,
        "has_intel_gpu": False,
        "cuda_version": None,
        "rocm_version": None,
        "opencl_version": None,
    }
    
    # Try to get physical CPU count
    try:
        import psutil
        info["cpu_count_physical"] = psutil.cpu_count(logical=False)
        mem = psutil.virtual_memory()
        info["memory_total_gb"] = round(mem.total / (1024**3), 1)
        info["memory_available_gb"] = round(mem.available / (1024**3), 1)
    except Exception:
        pass
    
    # Detect NVIDIA GPU
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            info["has_nvidia_gpu"] = True
            for line in result.stdout.strip().split('\n'):
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 3:
                    info["gpu_info"].append({
                        "name": parts[0],
                        "memory_mb": int(parts[1]),
                        "driver_version": parts[2],
                        "vendor": "NVIDIA"
                    })
    except Exception:
        pass
    
    # Detect AMD GPU (rocm-smi)
    try:
        result = subprocess.run(
            ["rocm-smi", "--showproductname", "--showmeminfo", "vram"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and "GPU" in result.stdout:
            info["has_amd_gpu"] = True
            # Parse rocm-smi output for GPU details
            for line in result.stdout.strip().split('\n'):
                if "GPU" in line and ":" in line:
                    info["gpu_info"].append({
                        "name": line.split(":")[-1].strip(),
                        "vendor": "AMD"
                    })
    except Exception:
        pass
    
    # Detect Intel Arc GPU using gpu_detector
    try:
        gpus = detect()
        for gpu in gpus:
            if gpu.vendor == "Intel" and gpu.memory_mb > 0:
                info["has_intel_gpu"] = True
                info["gpu_info"].append({
                    "name": gpu.name,
                    "memory_mb": gpu.memory_mb,
                    "vendor": "Intel",
                    "device_id": gpu.device_id,
                    "driver_version": gpu.driver_version,
                    "source": "gpu_detector"
                })
    except Exception as e:
        log.debug(f"gpu_detector failed: {e}")
    
    # Get CUDA version
    try:
        result = subprocess.run(["nvcc", "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if "release" in line:
                    info["cuda_version"] = line.split("release")[-1].split(",")[0].strip()
                    break
    except Exception:
        pass
    
    # Get OpenCL version (for Intel)
    try:
        result = subprocess.run(
            ["clinfo", "--version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if "OpenCL version" in line:
                    info["opencl_version"] = line.split("OpenCL version")[-1].strip()
                    break
    except Exception:
        pass
    
    # Determine total GPU VRAM
    total_vram_gb = sum(g.get("memory_mb", 0) for g in info["gpu_info"]) / 1024
    info["total_gpu_vram_gb"] = round(total_vram_gb, 1) if total_vram_gb > 0 else 0
    
    return info


@system_bp.route("/api/hardware-info")
def api_hardware_info():
    """Get detected hardware information."""
    try:
        info = get_hardware_info()
        return jsonify(info)
    except Exception as e:
        log.exception("Failed to get hardware info")
        return jsonify({"error": str(e)}), 500


def compute_optimal_settings(hardware_info, model_info=None):
    """Compute optimal model settings based on hardware capabilities."""
    settings = {}
    
    # CPU settings
    cpu_logical = hardware_info.get("cpu_count_logical", 16)
    cpu_physical = hardware_info.get("cpu_count_physical", cpu_logical // 2)
    settings["threads"] = cpu_physical
    settings["threads_batch"] = cpu_physical
    
    # Memory settings
    mem_total = hardware_info.get("memory_total_gb", 32)
    mem_available = hardware_info.get("memory_available_gb", mem_total * 0.7)
    
    # GPU settings
    has_nvidia = hardware_info.get("has_nvidia_gpu", False)
    has_amd = hardware_info.get("has_amd_gpu", False)
    has_intel = hardware_info.get("has_intel_gpu", False)
    total_vram = hardware_info.get("total_gpu_vram_gb", 0)
    gpu_info = hardware_info.get("gpu_info", [])
    
    # Model-specific overrides
    model_size_gb = 0
    model_ctx = 4096
    if model_info:
        model_size_gb = model_info.get("file_size_gb", 0)
        model_ctx = model_info.get("ctx_size", 4096)
    
    if has_nvidia or has_amd or has_intel:
        # GPU mode - determine how many layers to offload
        # Rule of thumb: ~1.5GB VRAM per 1B params at Q4_K_M
        if model_size_gb > 0 and total_vram > 0:
            # Estimate layers that fit in VRAM (leave 1-2GB for context/KV cache)
            usable_vram = max(0, total_vram - 2)
            # Rough estimate: each layer ~ model_size_gb / num_layers
            # For typical 30B model: ~32 layers, ~0.6GB per layer at Q4
            # We'll use a simpler heuristic
            if model_size_gb <= total_vram * 0.7:
                settings["gpu_layers"] = 99  # Full offload
            elif model_size_gb <= total_vram * 1.2:
                settings["gpu_layers"] = 50  # Partial offload
            else:
                settings["gpu_layers"] = 20  # Minimal offload
        elif total_vram >= 24:
            settings["gpu_layers"] = 99
        elif total_vram >= 16:
            settings["gpu_layers"] = 50
        elif total_vram >= 8:
            settings["gpu_layers"] = 20
        else:
            settings["gpu_layers"] = 0
        
        settings["flash_attn"] = "auto"
        settings["no_mmap"] = False
        settings["mlock"] = True
    else:
        # CPU-only mode
        settings["gpu_layers"] = 0
        settings["flash_attn"] = "off"
        settings["no_mmap"] = True
        settings["mlock"] = True
    
    # Parallel processing
    if model_ctx > 32768:
        settings["parallel"] = 1  # Single sequence for large context
    else:
        settings["parallel"] = min(4, cpu_physical // 2)
    
    # Batch sizes
    if has_nvidia or has_amd or has_intel:
        settings["batch_size"] = 2048
        settings["ubatch_size"] = 512
    else:
        settings["batch_size"] = 512
        settings["ubatch_size"] = 256
    
    # Context size - keep model default but cap at available memory
    # Each 1K context ~ 0.5-1GB depending on model
    max_ctx_by_mem = int((mem_available * 0.6) * 1024)  # rough estimate
    settings["ctx_size"] = min(model_ctx, max(4096, max_ctx_by_mem))
    
    # KV Cache types - use fp16 for quality on GPU, q4_0 for memory savings
    if total_vram >= 16:
        settings["cache_type_k"] = "fp16"
        settings["cache_type_v"] = "fp16"
    elif total_vram >= 8:
        settings["cache_type_k"] = "q8_0"
        settings["cache_type_v"] = "q8_0"
    else:
        settings["cache_type_k"] = "q4_0"
        settings["cache_type_v"] = "q4_0"
    
    # NUMA
    settings["numa"] = True
    settings["numa_value"] = "distribute"
    settings["cpu_affinity"] = "auto"
    settings["cpu_strict"] = False
    
    # Timeouts
    settings["timeout"] = 3600
    
    # Speculative decoding - enable ngram-simple for larger models on GPU
    if (has_nvidia or has_amd or has_intel) and model_size_gb >= 6:
        settings["spec_type"] = "ngram-simple"
        settings["spec_draft_n_max"] = 3
        settings["spec_draft_p_split"] = 0.1
        settings["spec_ngram_size_n"] = 12
        settings["spec_ngram_size_m"] = 48
    else:
        settings["spec_type"] = "none"
    
    # YaRN settings for long context models
    if model_ctx >= 32768:
        settings["rope_scaling_type"] = "yarn"
        settings["rope_scale"] = max(1.0, model_ctx / 32768)
        settings["yarn_orig_ctx"] = 32768
        settings["yarn_ext_factor"] = 1.0
        settings["yarn_attn_factor"] = 1.0
        settings["yarn_beta_slow"] = 1.0
        settings["yarn_beta_fast"] = 32.0
        settings["rope_freq_base"] = 10000.0
        settings["rope_freq_scale"] = 1.0 / settings["rope_scale"]
    else:
        settings["rope_scaling_type"] = "none"
        settings["rope_scale"] = 1.0
        settings["yarn_orig_ctx"] = 0
    
    # Agent/Tool parameters
    settings["jinja"] = True
    settings["reasoning_preserve"] = True
    
    return settings


@system_bp.route("/api/optimize-settings", methods=["POST"])
def api_optimize_settings():
    """Get optimized settings for a model based on hardware."""
    from flask import request
    from models import get_models
    
    data = request.get_json(force=True) or {}
    model_name = data.get("model", "").strip()
    
    if not model_name:
        return jsonify({"error": "Missing model name"}), 400
    
    models = get_models()
    model_info = models.get(model_name)
    
    if not model_info:
        return jsonify({"error": f"Unknown model '{model_name}'"}), 404
    
    try:
        hardware_info = get_hardware_info()
        optimal = compute_optimal_settings(hardware_info, model_info)
        
        return jsonify({
            "hardware": hardware_info,
            "optimal_settings": optimal,
            "model": model_name
        })
    except Exception as e:
        log.exception("Failed to compute optimal settings")
        return jsonify({"error": str(e)}), 500


@system_bp.route("/api/logs")
def api_logs():
    if not LOG_FILE.exists():
        return jsonify([])
    lines = LOG_FILE.read_text("utf-8", errors="replace").strip().splitlines()
    return jsonify(lines[-100:])


@system_bp.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    log.info("Shutdown requested via API")
    
    # First, stop all running models gracefully
    try:
        registry = get_model_registry()
        results = _stop_all_models(registry)
        log.info(f"Shutdown: stopped models: {results}")
    except Exception as e:
        log.exception("Error stopping models during shutdown")
    
    # Force kill any remaining llama-server processes
    try:
        killed = _kill_all_llama_processes()
        if killed:
            log.info(f"Shutdown: force killed {killed} remaining llama-server processes")
    except Exception as e:
        log.exception("Error force killing llama-server processes")
    
    def shutdown():
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=shutdown, daemon=True).start()
    return jsonify({"status": "shutdown", "models_stopped": results if 'results' in locals() else {}})

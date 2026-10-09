"""System operations: logs, shutdown, and hardware detection."""
from flask import Blueprint, jsonify, render_template, current_app
import threading
import time
import os
import platform
import subprocess
import re
from logging import getLogger

# Optional gpu_detector import (for Intel GPU detection on Linux)
try:
    from gpu_detector import detect, GPUInfo
    GPU_DETECTOR_AVAILABLE = True
except ImportError:
    GPU_DETECTOR_AVAILABLE = False
    detect = None
    GPUInfo = None

from config import LOG_FILE, FLASK_PORT
from routes.lifecycle import _stop_all_models, _kill_all_llama_processes, get_model_registry
from state import register_sse_client, unregister_sse_client, get_recent_logs, add_log_entry
from models import get_models as get_models_local

# Use the imported function directly (not re-imported inside functions)
get_models = get_models_local

system_bp = Blueprint("system", __name__)
log = getLogger("llamaweb")


@system_bp.route("/static/js/state.js")
def serve_state_js():
    """Serve state.js as a template to inject configuration variables."""
    return render_template(
        "state.js",
        host=current_app.config.get("SERVER_HOST", "127.0.0.1"),
        flask_port=current_app.config.get("FLASK_PORT", FLASK_PORT),
        cache_version=current_app.config.get("CACHE_VERSION", "1"),
    ), 200, {"Content-Type": "application/javascript"}


def _detect_windows_gpu() -> list[dict]:
    """Detect GPUs from Windows host when running in WSL.
    
    Uses PowerShell WMI queries to get GPU information from the Windows host.
    This is useful when running in WSL where Linux GPU detection tools
    cannot see the Windows-hosted GPUs.
    
    Returns:
        List of GPU info dictionaries with keys: name, vendor, driver_version,
        device_id, source, memory_mb (if available)
    """
    gpus = []
    try:
        # Check if we're in WSL
        with open("/proc/version", "r") as f:
            if "microsoft" not in f.read().lower() and "wsl" not in f.read().lower():
                return gpus
        
        # Query Windows for video controllers
        # Use cmd.exe /c to avoid Python subprocess timeout issues in WSL
        cmd = [
            "cmd.exe", "/c", "powershell.exe", "-Command",
            "Get-CimInstance Win32_VideoController | "
            "Select-Object Name, AdapterRAM, DriverVersion, PNPDeviceID | "
            "ConvertTo-Json -Compress"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return gpus
        
        import json
        data = json.loads(result.stdout)
        # Handle both single object and array
        if isinstance(data, dict):
            data = [data]
        
        for gpu in data:
            name = gpu.get("Name", "")
            if not name:
                continue
            
            # Determine vendor from name
            name_lower = name.lower()
            if "nvidia" in name_lower or "geforce" in name_lower or "quadro" in name_lower or "tesla" in name_lower:
                vendor = "NVIDIA"
            elif "amd" in name_lower or "radeon" in name_lower:
                vendor = "AMD"
            elif "intel" in name_lower or "arc" in name_lower or "uhd" in name_lower or "iris" in name_lower:
                vendor = "Intel"
            else:
                vendor = "Unknown"
            
            # Parse AdapterRAM (comes in bytes, may be null or weird format)
            # Windows WMI sometimes returns values near max int32 (2147483647) as a placeholder
            # for unknown VRAM, which equals ~2047 MB - we should ignore these
            memory_mb = None
            adapter_ram = gpu.get("AdapterRAM")
            # Check for bogus max int32 values that indicate unknown VRAM
            # Use a threshold since the exact value may vary (e.g., 2147479552 = 0x7ffff000)
            MAX_INT32 = 2147483647
            BOGUS_THRESHOLD = MAX_INT32 - 10000  # Values within 10KB of max int32 are likely bogus
            is_bogus = adapter_ram and isinstance(adapter_ram, (int, float)) and adapter_ram >= BOGUS_THRESHOLD
            if adapter_ram and isinstance(adapter_ram, (int, float)) and adapter_ram > 0 and adapter_ram < BOGUS_THRESHOLD:
                memory_mb = int(adapter_ram / (1024 * 1024))
            
            # Store whether this is a shared memory GPU with bogus VRAM for later estimation
            is_shared_memory = vendor == "Intel" and (memory_mb is None or is_bogus)
            
            # Extract device ID from PNPDeviceID
            device_id = None
            pnp_id = gpu.get("PNPDeviceID", "")
            if pnp_id:
                # Format: PCI\VEN_8086&DEV_7D55&SUBSYS_3F9617AA&REV_08\3&11583659&0&10
                ven_match = re.search(r"VEN_([0-9A-Fa-f]{4})", pnp_id)
                dev_match = re.search(r"DEV_([0-9A-Fa-f]{4})", pnp_id)
                if ven_match and dev_match:
                    device_id = f"{ven_match.group(1)}:{dev_match.group(1)}"
            
            gpus.append({
                "name": name,
                "vendor": vendor,
                "driver_version": gpu.get("DriverVersion"),
                "device_id": device_id,
                "memory_mb": memory_mb,
                "source": "windows_wmi",
                "is_shared_memory": is_shared_memory
            })
    except Exception as e:
        log.debug(f"Windows GPU detection failed: {e}")
    
    return gpus


def _detect_windows_cpu_memory() -> dict:
    """Detect CPU and memory from Windows host when running in WSL.
    
    Uses PowerShell WMI queries to get CPU and memory information from the Windows host.
    This is useful when running in WSL where Linux tools cannot see the Windows host resources.
    
    Returns:
        Dictionary with cpu_count_logical, cpu_count_physical, memory_total_gb, memory_available_gb
    """
    info = {}
    try:
        # Check if we're in WSL
        with open("/proc/version", "r") as f:
            if "microsoft" not in f.read().lower() and "wsl" not in f.read().lower():
                return info
    except Exception:
        return info
    
    try:
        # Query Windows for CPU and memory info
        cmd = [
            "cmd.exe", "/c", "powershell.exe", "-Command",
            "Get-CimInstance Win32_Processor | "
            "Select-Object NumberOfLogicalProcessors, NumberOfCores | "
            "ConvertTo-Json -Compress"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            if isinstance(data, dict):
                data = [data]
            total_logical = sum(int(p.get("NumberOfLogicalProcessors", 0)) for p in data)
            total_cores = sum(int(p.get("NumberOfCores", 0)) for p in data)
            if total_logical > 0:
                info["cpu_count_logical"] = total_logical
            if total_cores > 0:
                info["cpu_count_physical"] = total_cores
    except Exception:
        pass
    
    try:
        # Query Windows for memory
        cmd = [
            "cmd.exe", "/c", "powershell.exe", "-Command",
            "Get-CimInstance Win32_OperatingSystem | "
            "Select-Object TotalVisibleMemorySize, FreePhysicalMemory | "
            "ConvertTo-Json -Compress"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            # Memory in KB, convert to GB
            total_kb = int(data.get("TotalVisibleMemorySize", 0))
            free_kb = int(data.get("FreePhysicalMemory", 0))
            if total_kb > 0:
                info["memory_total_gb"] = round(total_kb / (1024 * 1024), 1)
            if free_kb > 0:
                info["memory_available_gb"] = round(free_kb / (1024 * 1024), 1)
    except Exception:
        pass
    
    return info
    # Use cmd.exe /c to avoid Python subprocess timeout issues in WSL
    try:
        cmd = [
            "cmd.exe", "/c", "powershell.exe", "-Command",
            "Get-CimInstance Win32_VideoController | "
            "Select-Object Name, AdapterRAM, DriverVersion, PNPDeviceID | "
            "ConvertTo-Json -Compress"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return gpus
        
        import json
        data = json.loads(result.stdout)
        # Handle both single object and array
        if isinstance(data, dict):
            data = [data]
        
        for gpu in data:
            name = gpu.get("Name", "")
            if not name:
                continue
            
            # Determine vendor from name
            name_lower = name.lower()
            if "nvidia" in name_lower or "geforce" in name_lower or "quadro" in name_lower or "tesla" in name_lower:
                vendor = "NVIDIA"
            elif "amd" in name_lower or "radeon" in name_lower:
                vendor = "AMD"
            elif "intel" in name_lower or "arc" in name_lower or "uhd" in name_lower or "iris" in name_lower:
                vendor = "Intel"
            else:
                vendor = "Unknown"
            
            # Parse AdapterRAM (comes in bytes, may be null or weird format)
            # Windows WMI sometimes returns values near max int32 (2147483647) as a placeholder
            # for unknown VRAM, which equals ~2047 MB - we should ignore these
            memory_mb = None
            adapter_ram = gpu.get("AdapterRAM")
            # Check for bogus max int32 values that indicate unknown VRAM
            # Use a threshold since the exact value may vary (e.g., 2147479552 = 0x7ffff000)
            MAX_INT32 = 2147483647
            BOGUS_THRESHOLD = MAX_INT32 - 10000  # Values within 10KB of max int32 are likely bogus
            is_bogus = adapter_ram and isinstance(adapter_ram, (int, float)) and adapter_ram >= BOGUS_THRESHOLD
            if adapter_ram and isinstance(adapter_ram, (int, float)) and adapter_ram > 0 and adapter_ram < BOGUS_THRESHOLD:
                memory_mb = int(adapter_ram / (1024 * 1024))
            
            # Store whether this is a shared memory GPU with bogus VRAM for later estimation
            is_shared_memory = vendor == "Intel" and (memory_mb is None or is_bogus)
            
            # Extract device ID from PNPDeviceID
            device_id = None
            pnp_id = gpu.get("PNPDeviceID", "")
            if pnp_id:
                # Format: PCI\VEN_8086&DEV_7D55&SUBSYS_3F9617AA&REV_08\3&11583659&0&10
                ven_match = re.search(r"VEN_([0-9A-Fa-f]{4})", pnp_id)
                dev_match = re.search(r"DEV_([0-9A-Fa-f]{4})", pnp_id)
                if ven_match and dev_match:
                    device_id = f"{ven_match.group(1)}:{dev_match.group(1)}"
            
            gpus.append({
                "name": name,
                "vendor": vendor,
                "driver_version": gpu.get("DriverVersion"),
                "device_id": device_id,
                "memory_mb": memory_mb,
                "source": "windows_wmi",
                "is_shared_memory": is_shared_memory
            })
    except Exception as e:
        log.debug(f"Windows GPU detection failed: {e}")
    
    return gpus


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
    
    # If running in WSL, try to get Windows host hardware info first
    try:
        with open("/proc/version", "r") as f:
            if "microsoft" in f.read().lower() or "wsl" in f.read().lower():
                # We're in WSL - get Windows host hardware info
                win_info = _detect_windows_cpu_memory()
                info.update(win_info)
    except Exception:
        pass
    
    # Fallback to Linux/psutil detection (for non-WSL or if WSL detection failed)
    if info["cpu_count_logical"] is None or info["memory_total_gb"] is None:
        try:
            import psutil
            if info["cpu_count_physical"] is None:
                info["cpu_count_physical"] = psutil.cpu_count(logical=False)
            if info["cpu_count_logical"] is None:
                info["cpu_count_logical"] = psutil.cpu_count()
            mem = psutil.virtual_memory()
            if info["memory_total_gb"] is None:
                info["memory_total_gb"] = round(mem.total / (1024**3), 1)
            if info["memory_available_gb"] is None:
                info["memory_available_gb"] = round(mem.available / (1024**3), 1)
        except Exception:
            pass
    
    # Detect NVIDIA GPU
    nvidia_detected = False
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            info["has_nvidia_gpu"] = True
            nvidia_detected = True
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
    amd_detected = False
    try:
        result = subprocess.run(
            ["rocm-smi", "--showproductname", "--showmeminfo", "vram"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and "GPU" in result.stdout:
            info["has_amd_gpu"] = True
            amd_detected = True
            # Parse rocm-smi output for GPU details
            for line in result.stdout.strip().split('\n'):
                if "GPU" in line and ":" in line:
                    info["gpu_info"].append({
                        "name": line.split(":")[-1].strip(),
                        "vendor": "AMD"
                    })
    except Exception:
        pass
    
    # Detect Intel Arc GPU using gpu_detector (Linux native)
    intel_detected = False
    if GPU_DETECTOR_AVAILABLE:
        try:
            gpus = detect()
            for gpu in gpus:
                if gpu.vendor == "Intel" and gpu.memory_mb > 0:
                    info["has_intel_gpu"] = True
                    intel_detected = True
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
    
    # Fallback: Detect GPUs from Windows host (for WSL environments)
    # Only run Windows detection if we're in WSL and at least one GPU type wasn't detected
    if not (nvidia_detected and amd_detected and intel_detected):
        windows_gpus = _detect_windows_gpu()
        for gpu in windows_gpus:
            if gpu["vendor"] == "NVIDIA" and not nvidia_detected:
                info["has_nvidia_gpu"] = True
                info["gpu_info"].append({
                    "name": gpu["name"],
                    "memory_mb": gpu["memory_mb"],
                    "vendor": "NVIDIA",
                    "device_id": gpu["device_id"],
                    "driver_version": gpu["driver_version"],
                    "source": "windows_wmi"
                })
                log.info(f"Detected NVIDIA GPU via Windows WMI: {gpu['name']}")
            elif gpu["vendor"] == "AMD" and not amd_detected:
                info["has_amd_gpu"] = True
                info["gpu_info"].append({
                    "name": gpu["name"],
                    "memory_mb": gpu["memory_mb"],
                    "vendor": "AMD",
                    "device_id": gpu["device_id"],
                    "driver_version": gpu["driver_version"],
                    "source": "windows_wmi"
                })
                log.info(f"Detected AMD GPU via Windows WMI: {gpu['name']}")
            elif gpu["vendor"] == "Intel" and not intel_detected:
                info["has_intel_gpu"] = True
                info["gpu_info"].append({
                    "name": gpu["name"],
                    "memory_mb": gpu["memory_mb"],
                    "vendor": "Intel",
                    "device_id": gpu["device_id"],
                    "driver_version": gpu["driver_version"],
                    "source": "windows_wmi",
                    "is_shared_memory": gpu.get("is_shared_memory", False)
                })
                log.info(f"Detected Intel GPU via Windows WMI: {gpu['name']}")
    
    # Estimate shared memory for Intel integrated/Arc GPUs with unknown VRAM
    # Intel Arc and integrated GPUs use shared system memory
    system_ram_gb = info.get("memory_total_gb")
    if system_ram_gb:
        for gpu in info["gpu_info"]:
            if gpu.get("vendor") == "Intel" and gpu.get("is_shared_memory"):
                # Intel Arc typically reserves up to 50% of system RAM for graphics
                # Use a conservative estimate of 50% for Arc, 25% for integrated
                is_arc = "arc" in gpu.get("name", "").lower()
                reserve_pct = 0.5 if is_arc else 0.25
                estimated_mb = int(system_ram_gb * 1024 * reserve_pct)
                gpu["memory_mb"] = estimated_mb
                gpu["memory_estimated"] = True
                log.info(f"Estimated shared memory for {gpu['name']}: {estimated_mb} MB ({reserve_pct*100}% of {system_ram_gb} GB system RAM)")
    
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
    total_vram_gb = sum((g.get("memory_mb") or 0) for g in info["gpu_info"]) / 1024
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
    """
    Compute optimal model settings based on hardware capabilities (HEURISTIC MODE).
    
    This function uses rule-based formulas to estimate good settings.
    For more accurate, hardware-specific optimization, use the empirical
    benchmark-based optimization via /api/benchmark-optimize or BenchmarkOptimizer.
    
    This is the LEGACY/HEURISTIC optimizer. It provides fast (~ms) estimates
    but may not find the true optimum for your specific hardware/model combination.
    
    Args:
        hardware_info: Dictionary from get_hardware_info()
        model_info: Optional dictionary with model metadata (file_size_gb, ctx_size)
        
    Returns:
        Dictionary of recommended llama.cpp server settings
    """
    settings = {}
    
    # Coerce None hardware values to safe defaults
    # .get() default only applies when key is missing, not when value is None
    cpu_logical = hardware_info.get("cpu_count_logical") or 16
    cpu_physical = hardware_info.get("cpu_count_physical") or (cpu_logical // 2)
    mem_total = hardware_info.get("memory_total_gb") or 32
    mem_available = hardware_info.get("memory_available_gb") or (mem_total * 0.7)
    total_vram = hardware_info.get("total_gpu_vram_gb") or 0
    
    # CPU settings
    settings["threads"] = cpu_physical
    settings["threads_batch"] = cpu_physical
    
    # Memory settings
    # mem_total and mem_available already coerced above
    
    # GPU settings
    has_nvidia = hardware_info.get("has_nvidia_gpu", False)
    has_amd = hardware_info.get("has_amd_gpu", False)
    has_intel = hardware_info.get("has_intel_gpu", False)
    # total_vram already coerced above
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
    """Get optimized settings for a model based on hardware (HEURISTIC MODE).
    
    This endpoint uses rule-based formulas for instant (~ms) optimization.
    For empirical benchmark-based optimization, use /api/benchmark-optimize.
    
    Request body:
        {"model": "string"}  # Required: model name from registry
        
    Response:
        {
            "hardware": {...},
            "optimal_settings": {...},  # Heuristic estimates
            "model": "string"
        }
    """
    from flask import request
    
    data = request.get_json(force=True) or {}
    model_name_raw = data.get("model")
    if model_name_raw is None:
        return jsonify({"error": "Missing model name"}), 400
    try:
        model_name = str(model_name_raw).strip()
    except Exception:
        return jsonify({"error": "Model name must be a string"}), 400
    
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


@system_bp.route("/api/benchmark-optimize", methods=["POST"])
def api_benchmark_optimize():
    """
    Run empirical benchmark optimization for a model.
    
    Request body:
        {
            "model": "string",                    # Required: model name from registry
            "max_duration_sec": int,              # Optional: max benchmark time (default: BENCHMARK_MAX_DURATION)
            "target": "throughput"|"latency"|"balanced"  # Optional: optimization target (default: "balanced")
        }
    
    Response:
        {
            "optimal_settings": {...},            # Same format as /api/optimize-settings
            "benchmark_results": [...],           # Raw benchmark results for transparency
            "hardware_info": {...},               # Hardware info used for optimization
            "cached": bool,                       # Whether result came from cache
            "duration_sec": float,                # Total optimization time
            "optimization_target": "string",      # Target used for optimization
            "fallback_reason": "string"           # Present if fallback to heuristic was used
        }
    """
    from flask import request
    import time as time_module
    from optimizer import BenchmarkOptimizer
    import config
    
    # Check if benchmark optimization is enabled
    if not config.BENCHMARK_ENABLED:
        return jsonify({"error": "Benchmark optimization is disabled. Set BENCHMARK_ENABLED=true to enable."}), 403
    
    data = request.get_json(force=True) or {}
    
    # Validate model name - coerce to string and strip
    model_name_raw = data.get("model")
    if model_name_raw is None:
        return jsonify({"error": "Missing model name"}), 400
    try:
        model_name = str(model_name_raw).strip()
    except Exception:
        return jsonify({"error": "Model name must be a string"}), 400
    
    if not model_name:
        return jsonify({"error": "Missing model name"}), 400
    
    # Validate optimization target - coerce to string and lower
    target_raw = data.get("target", "balanced")
    try:
        target = str(target_raw).lower()
    except Exception:
        return jsonify({"error": "Target must be a string"}), 400
    
    if target not in ("throughput", "latency", "balanced"):
        return jsonify({"error": f"Invalid target '{target}'. Must be 'throughput', 'latency', or 'balanced'"}), 400
    
    # Validate max_duration_sec - reject bool (since bool is subclass of int)
    max_duration_sec = data.get("max_duration_sec", config.BENCHMARK_MAX_DURATION)
    if type(max_duration_sec) is not int or max_duration_sec < 10 or max_duration_sec > 3600:
        return jsonify({"error": "max_duration_sec must be an integer between 10 and 3600"}), 400
    
    # Check if we're testing a specific current config
    test_current_config = data.get("test_current_config", False)
    current_config = data.get("current_config", {}) if test_current_config else {}
    
    models = get_models()
    model_info = models.get(model_name)
    
    if not model_info:
        return jsonify({"error": f"Unknown model '{model_name}'"}), 404
    
    # Get model path
    model_path_str = model_info.get("model_path", "")
    if not model_path_str:
        return jsonify({"error": f"Model '{model_name}' has no associated file path"}), 400
    
    # Resolve model path
    try:
        from config import resolve_model_path
        model_path = resolve_model_path(model_path_str)
        if not model_path.exists():
            return jsonify({"error": f"Model file not found: {model_path}"}), 404
    except Exception as e:
        log.exception(f"Failed to resolve model path for '{model_name}'")
        return jsonify({"error": f"Failed to resolve model path: {e}"}), 500
    
    try:
        start_time = time_module.time()
        
        # Get hardware info
        hardware_info = get_hardware_info()
        log.info(f"Starting benchmark optimization for model '{model_name}' (target: {target}, max_duration: {max_duration_sec}s, test_current_config: {test_current_config})")
        
        # Create optimizer with configured cache directory
        optimizer = BenchmarkOptimizer(config.BENCHMARK_CACHE_DIR)
        
        # Generate cache key (used in both branches)
        cache_key = optimizer._generate_cache_key(str(model_path), hardware_info)
        
        if test_current_config and current_config:
            # Run single benchmark with current config
            result = optimizer.run_single_benchmark(
                str(model_path),
                current_config,
                max_duration_sec=max_duration_sec
            )
            
            # Also load cached optimization results for comparison
            cache_key = optimizer._generate_cache_key(str(model_path), hardware_info)
            cached_result = optimizer._load_from_cache(cache_key, hardware_info)
            cached_benchmarks = []
            if cached_result and cached_result.get("benchmark_results"):
                # Only include successful cached benchmarks
                cached_benchmarks = [r for r in cached_result["benchmark_results"] if r.get("success", False)]
                log.info(f"Found {len(cached_benchmarks)} successful cached benchmarks for comparison")
            else:
                log.info(f"No cached benchmarks found (cached_result: {cached_result is not None}, has_benchmarks: {bool(cached_result and cached_result.get('benchmark_results'))})")
            
            # Combine current benchmark result with cached benchmarks for comparison
            # Deduplicate by config to avoid showing same benchmark twice
            combined_results = result.get("benchmark_results", []).copy()
            for cached_bm in cached_benchmarks:
                cached_config_str = str(sorted(cached_bm.get("config", {}).items()))
                is_duplicate = any(
                    str(sorted(r.get("config", {}).items())) == cached_config_str 
                    for r in combined_results
                )
                if not is_duplicate:
                    combined_results.append(cached_bm)
            
            duration_sec = time_module.time() - start_time
            
            # Convert optimal_settings from BenchmarkConfig format back to compute_optimal_settings format
            optimal_settings = result.get("optimal_settings", {}).copy()
            if "n_gpu_layers" in optimal_settings:
                optimal_settings["gpu_layers"] = optimal_settings.pop("n_gpu_layers")
            
            response = {
                "optimal_settings": optimal_settings,
                "benchmark_results": combined_results,
                "hardware_info": result.get("hardware_info", hardware_info),
                "cached": False,
                "duration_sec": round(duration_sec, 2),
                "optimization_target": target,
                "model": model_name,
                "test_current_config": True,
                "cached_benchmarks_count": len(cached_benchmarks),
                "cache_key": cache_key,
            }
            
            # Include fallback reason if present
            if "fallback_reason" in result:
                response["fallback_reason"] = result["fallback_reason"]
            
            log.info(f"Benchmark optimization completed for '{model_name}' in {duration_sec:.2f}s (cached: False, test_current_config: True, cached_benchmarks: {len(cached_benchmarks)})")
            
            return jsonify(response)
        else:
            # Always run optimizer.optimize() which handles incremental caching internally
            # It will load cached results, find missing configs, and only run those
            result = optimizer.optimize(
                str(model_path),
                max_duration_sec=max_duration_sec,
                optimization_target=target
            )
        
        duration_sec = time_module.time() - start_time
        
        # Convert optimal_settings from BenchmarkConfig format back to compute_optimal_settings format
        # (replace n_gpu_layers with gpu_layers for compatibility)
        optimal_settings = result.get("optimal_settings", {}).copy()
        if "n_gpu_layers" in optimal_settings:
            optimal_settings["gpu_layers"] = optimal_settings.pop("n_gpu_layers")
        
        # Determine if this was a cache hit (no new benchmarks run)
        was_cached = not result.get("new_benchmarks_run", False)
        
        # Build response
        response = {
            "optimal_settings": optimal_settings,
            "benchmark_results": result.get("benchmark_results", []),
            "hardware_info": result.get("hardware_info", hardware_info),
            "cached": was_cached,
            "duration_sec": round(duration_sec, 2),
            "optimization_target": target,
            "model": model_name,
            "cache_key": result.get("cache_key", cache_key),
        }
        
        # Include fallback reason if present
        if "fallback_reason" in result:
            response["fallback_reason"] = result["fallback_reason"]
        
        log.info(f"Benchmark optimization completed for '{model_name}' in {duration_sec:.2f}s (cached: {was_cached}, test_current_config: {test_current_config})")
        
        return jsonify(response)
        
    except FileNotFoundError as e:
        log.exception(f"Model file not found for '{model_name}'")
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        log.exception(f"Benchmark optimization failed for '{model_name}'")
        return jsonify({"error": f"Optimization failed: {str(e)}"}), 500


@system_bp.route("/api/benchmark-history", methods=["POST"])
def api_benchmark_history():
    """
    Get historical benchmark results for a model without running new benchmarks.
    
    Request body:
        {
            "model": "string"  # Required: model name from registry
        }
    
    Response:
        {
            "optimal_settings": {...},      # Best settings from cache
            "benchmark_results": [...],     # All historical benchmark results
            "hardware_info": {...},         # Hardware info used
            "cached": true,                 # Always true for history endpoint
            "duration_sec": 0,              # No new benchmark run
            "optimization_target": "string", # Target from cached result
            "model": "string",
        }
    """
    from flask import request
    from optimizer import BenchmarkOptimizer
    import config
    
    data = request.get_json(force=True) or {}
    
    model_name_raw = data.get("model")
    if model_name_raw is None:
        return jsonify({"error": "Missing model name"}), 400
    try:
        model_name = str(model_name_raw).strip()
    except Exception:
        return jsonify({"error": "Model name must be a string"}), 400
    
    if not model_name:
        return jsonify({"error": "Missing model name"}), 400
    
    models = get_models()
    model_info = models.get(model_name)
    
    if not model_info:
        return jsonify({"error": f"Unknown model '{model_name}'"}), 404
    
    model_path_str = model_info.get("model_path", "")
    if not model_path_str:
        return jsonify({"error": f"Model '{model_name}' has no associated file path"}), 400
    
    try:
        from config import resolve_model_path
        model_path = resolve_model_path(model_path_str)
        if not model_path.exists():
            return jsonify({"error": f"Model file not found: {model_path}"}), 404
    except Exception as e:
        log.exception(f"Failed to resolve model path for '{model_name}'")
        return jsonify({"error": f"Failed to resolve model path: {e}"}), 500
    
    try:
        hardware_info = get_hardware_info()
        
        optimizer = BenchmarkOptimizer(config.BENCHMARK_CACHE_DIR)
        cache_key = optimizer._generate_cache_key(str(model_path), hardware_info)
        cached_result = optimizer._load_from_cache(cache_key, hardware_info)
        
        if cached_result is None:
            return jsonify({
                "error": "No benchmark history found for this model on current hardware",
                "model": model_name,
            }), 404
        
        # Convert optimal_settings format
        optimal_settings = cached_result.get("optimal_settings", {}).copy()
        if "n_gpu_layers" in optimal_settings:
            optimal_settings["gpu_layers"] = optimal_settings.pop("n_gpu_layers")
        
        response = {
            "optimal_settings": optimal_settings,
            "benchmark_results": cached_result.get("benchmark_results", []),
            "hardware_info": cached_result.get("hardware_info", hardware_info),
            "cached": True,
            "duration_sec": 0,
            "optimization_target": cached_result.get("optimization_target", "balanced"),
            "model": model_name,
        }
        
        # Include fallback reason if present
        if "fallback_reason" in cached_result:
            response["fallback_reason"] = cached_result["fallback_reason"]
        
        log.info(f"Benchmark history retrieved for '{model_name}' (cached results: {len(cached_result.get('benchmark_results', []))})")
        
        return jsonify(response)
        
    except FileNotFoundError as e:
        log.exception(f"Model file not found for '{model_name}'")
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        log.exception(f"Benchmark history retrieval failed for '{model_name}'")
        return jsonify({"error": f"History retrieval failed: {str(e)}"}), 500


@system_bp.route("/api/logs")
def api_logs():
    if not LOG_FILE.exists():
        return jsonify([])
    lines = LOG_FILE.read_text("utf-8", errors="replace").strip().splitlines()
    return jsonify(lines[-100:])



@system_bp.route("/api/logs/stream")
def api_logs_stream():
    """Server-Sent Events endpoint for real-time log streaming."""
    from flask import Response
    import queue
    client_queue = register_sse_client()
    
    def generate():
        try:
            # Send recent logs first
            recent = get_recent_logs(100)
            for entry in recent:
                import json
                yield f"data: {json.dumps(entry)}\n\n"
            
            # Then stream new entries
            while True:
                try:
                    data = client_queue.get(timeout=30)
                    yield data
                except queue.Empty:
                    # Send keepalive comment
                    yield ": keepalive\n\n"
                except GeneratorExit:
                    break
        finally:
            unregister_sse_client(client_queue)
    
    return Response(generate(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })

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

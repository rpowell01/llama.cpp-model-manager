def estimate_gguf_size(model_id: str, filename: str) -> int:
    """
    Estimate GGUF file size based on model parameters and quantization.
    Returns size in bytes, or 0 if estimation fails.
    """
    # Quantization bytes per parameter (approximate)
    quant_patterns = {
        'Q2_K': 0.35, 'Q2_K_S': 0.30, 'Q2_K_M': 0.33,
        'Q3_K_S': 0.42, 'Q3_K_M': 0.47, 'Q3_K_L': 0.50,
        'Q4_0': 0.50, 'Q4_K_S': 0.50, 'Q4_K_M': 0.55, 'Q4_K_L': 0.60,
        'Q5_0': 0.65, 'Q5_K_S': 0.65, 'Q5_K_M': 0.70, 'Q5_K_L': 0.75,
        'Q6_K': 0.80, 'Q8_0': 1.0, 'Q8_K': 1.0,
        'F16': 2.0, 'BF16': 2.0, 'F32': 4.0,
    }

    # Known model family sizes (in billions of parameters)
    known_models = {
        'phi-3-mini': 3.8, 'phi-3.5-mini': 3.8, 'phi-4': 14,  # phi-4 ~14B
        'llama-2-7b': 7, 'llama-2-13b': 13, 'llama-2-70b': 70,
        'llama-3-8b': 8, 'llama-3-70b': 70,
        'llama-3.1-8b': 8, 'llama-3.1-70b': 70, 'llama-3.1-405b': 405,
        'mistral-7b': 7, 'mixtral-8x7b': 47,  # Mixtral effective ~47B
        'gemma-2b': 2, 'gemma-7b': 7,
    }

    # Determine bytes per parameter from quantization
    bpc = 2.0  # Default F16
    filename_lower = filename.lower()
    for quant, bytes_per_param in quant_patterns.items():
        if quant.lower() in filename_lower:
            bpc = bytes_per_param
            break

    # Try to extract parameter count from model_id or filename
    import re
    param_count = None

    # Look for explicit number followed by B (7B, 13B, 70B, etc.)
    match = re.search(r'(\d+(?:\.\d+)?)\s*[bB]', model_id + ' ' + filename)
    if match:
        try:
            param_count = float(match.group(1))
        except:
            pass

    # If not found, check known model families
    if param_count is None:
        model_id_lower = model_id.lower()
        for family, params in known_models.items():
            if family in model_id_lower:
                param_count = params
                break

    if param_count is None:
        return 0

    total_params = param_count * 1_000_000_000
    estimated_size = int(total_params * bpc)
    return estimated_size


def purge_windows_standby_list() -> tuple[bool, str]:
    """
    Purge the Windows standby list (cached memory) to free up physical RAM.
    
    This uses the undocumented NtSetSystemInformation API which requires
    Administrator privileges and the SeProfileSingleProcessPrivilege.
    
    Returns:
        (success, message): success is True if purge succeeded, message contains details
    """
    import sys
    if sys.platform != "win32":
        return False, "Not running on Windows"
    
    try:
        import ctypes
        from ctypes import wintypes
        
        ntdll = ctypes.WinDLL("ntdll")
        
        # SystemMemoryListInformation = 80 (undocumented)
        SystemMemoryListInformation = 80
        # MemoryPurgeStandbyList = 4
        MemoryPurgeStandbyList = 4
        
        class SYSTEM_MEMORY_LIST_COMMAND(ctypes.Structure):
            _fields_ = [("command", ctypes.c_int)]
        
        cmd = SYSTEM_MEMORY_LIST_COMMAND(MemoryPurgeStandbyList)
        
        # NtSetSystemInformation prototype:
        # NTSTATUS NtSetSystemInformation(
        #   SYSTEM_INFORMATION_CLASS SystemInformationClass,
        #   PVOID SystemInformation,
        #   ULONG SystemInformationLength
        # )
        NtSetSystemInformation = ntdll.NtSetSystemInformation
        NtSetSystemInformation.argtypes = [
            wintypes.ULONG,  # SystemInformationClass
            ctypes.c_void_p,  # SystemInformation
            wintypes.ULONG    # SystemInformationLength
        ]
        NtSetSystemInformation.restype = wintypes.LONG  # NTSTATUS
        
        status = NtSetSystemInformation(
            SystemMemoryListInformation,
            ctypes.byref(cmd),
            ctypes.sizeof(cmd)
        )
        
        # NTSTATUS success codes are >= 0
        if status >= 0:
            return True, f"Standby list purged successfully (status=0x{status:X})"
        else:
            # Common error codes:
            # 0xC0000022 = STATUS_ACCESS_DENIED (needs admin)
            # 0xC000000D = STATUS_INVALID_PARAMETER
            return False, f"NtSetSystemInformation failed with status 0x{status:X}"
            
    except PermissionError:
        return False, "Permission denied - requires Administrator privileges"
    except AttributeError as e:
        return False, f"API not available: {e}"
    except Exception as e:
        return False, f"Error purging standby list: {e}"


def set_process_working_set_size() -> tuple[bool, str]:
    """
    Alternative: Trim the current process working set.
    This is a documented API that doesn't require admin but only affects
    the current process, not system-wide memory.
    """
    import sys
    if sys.platform != "win32":
        return False, "Not running on Windows"
    
    try:
        import ctypes
        from ctypes import wintypes
        
        kernel32 = ctypes.WinDLL("kernel32")
        
        # GetCurrentProcess pseudo-handle
        GetCurrentProcess = kernel32.GetCurrentProcess
        GetCurrentProcess.restype = wintypes.HANDLE
        
        # SetProcessWorkingSetSizeEx (Windows 8.1+) or SetProcessWorkingSetSize
        # Flags for SetProcessWorkingSetSizeEx:
        # QUOTA_LIMITS_HARDWS_MIN_ENABLE = 0x00000001
        # QUOTA_LIMITS_HARDWS_MAX_ENABLE = 0x00000004
        
        if hasattr(kernel32, 'SetProcessWorkingSetSizeEx'):
            SetProcessWorkingSetSizeEx = kernel32.SetProcessWorkingSetSizeEx
            SetProcessWorkingSetSizeEx.argtypes = [
                wintypes.HANDLE,
                ctypes.c_size_t,
                ctypes.c_size_t,
                wintypes.DWORD
            ]
            SetProcessWorkingSetSizeEx.restype = wintypes.BOOL
            
            # Use -1 for both min/max to trim to minimum
            result = SetProcessWorkingSetSizeEx(
                GetCurrentProcess(),
                ctypes.c_size_t(-1),
                ctypes.c_size_t(-1),
                0
            )
        else:
            # Older API
            SetProcessWorkingSetSize = kernel32.SetProcessWorkingSetSize
            SetProcessWorkingSetSize.argtypes = [
                wintypes.HANDLE,
                ctypes.c_size_t,
                ctypes.c_size_t
            ]
            SetProcessWorkingSetSize.restype = wintypes.BOOL
            
            result = SetProcessWorkingSetSize(
                GetCurrentProcess(),
                ctypes.c_size_t(-1),
                ctypes.c_size_t(-1)
            )
        
        if result:
            return True, "Process working set trimmed"
        else:
            error = ctypes.GetLastError()
            return False, f"SetProcessWorkingSetSize failed with error {error}"
            
    except Exception as e:
        return False, f"Error trimming working set: {e}"

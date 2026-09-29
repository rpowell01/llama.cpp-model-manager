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
    
    On native Windows: calls NtSetSystemInformation directly via ctypes
    On WSL: executes a PowerShell command that calls the Windows API
    
    Returns:
        (success, message): success is True if purge succeeded, message contains details
    """
    import sys
    import platform
    import subprocess
    
    is_windows = platform.system() == "Windows"
    is_wsl = False
    if not is_windows:
        try:
            with open("/proc/version", "r") as f:
                if "microsoft" in f.read().lower():
                    is_wsl = True
        except Exception:
            pass
    
    if not is_windows and not is_wsl:
        return False, "Not running on Windows or WSL"
    
    if is_windows:
        # Native Windows: direct ctypes call
        return _purge_standby_native()
    else:
        # WSL: execute PowerShell command
        return _purge_standby_wsl()


def _purge_standby_native() -> tuple[bool, str]:
    """Native Windows implementation using ctypes."""
    try:
        import ctypes
        from ctypes import wintypes
        
        ntdll = ctypes.WinDLL("ntdll")
        
        SystemMemoryListInformation = 80
        MemoryPurgeStandbyList = 4
        
        class SYSTEM_MEMORY_LIST_COMMAND(ctypes.Structure):
            _fields_ = [("command", ctypes.c_int)]
        
        cmd = SYSTEM_MEMORY_LIST_COMMAND(MemoryPurgeStandbyList)
        
        NtSetSystemInformation = ntdll.NtSetSystemInformation
        NtSetSystemInformation.argtypes = [
            wintypes.ULONG,
            ctypes.c_void_p,
            wintypes.ULONG
        ]
        NtSetSystemInformation.restype = wintypes.LONG
        
        status = NtSetSystemInformation(
            SystemMemoryListInformation,
            ctypes.byref(cmd),
            ctypes.sizeof(cmd)
        )
        
        if status >= 0:
            return True, f"Standby list purged successfully (status=0x{status:X})"
        else:
            return False, f"NtSetSystemInformation failed with status 0x{status:X}"
            
    except PermissionError:
        return False, "Permission denied - requires Administrator privileges"
    except AttributeError as e:
        return False, f"API not available: {e}"
    except Exception as e:
        return False, f"Error purging standby list: {e}"


def _purge_standby_wsl() -> tuple[bool, str]:
    """WSL implementation using PowerShell to call Windows API."""
    try:
        # PowerShell script to call NtSetSystemInformation via P/Invoke
        ps_script = r'''
        Add-Type -TypeDefinition @"
        using System;
        using System.Runtime.InteropServices;
        public class MemoryPurge {
            [DllImport("ntdll.dll")]
            public static extern int NtSetSystemInformation(
                int SystemInformationClass,
                ref SYSTEM_MEMORY_LIST_COMMAND SystemInformation,
                int SystemInformationLength
            );
            
            [StructLayout(LayoutKind.Sequential)]
            public struct SYSTEM_MEMORY_LIST_COMMAND {
                public int Command;
            }
            
            public const int SystemMemoryListInformation = 80;
            public const int MemoryPurgeStandbyList = 4;
            
            public static int PurgeStandbyList() {
                SYSTEM_MEMORY_LIST_COMMAND cmd = new SYSTEM_MEMORY_LIST_COMMAND();
                cmd.Command = MemoryPurgeStandbyList;
                return NtSetSystemInformation(SystemMemoryListInformation, ref cmd, Marshal.SizeOf(cmd));
            }
        }
"@
        $result = [MemoryPurge]::PurgeStandbyList()
        Write-Host "STATUS:$result"
        '''
        
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        # Parse output for STATUS
        for line in result.stdout.splitlines():
            if line.startswith("STATUS:"):
                status = int(line.split(":")[1])
                if status >= 0:
                    return True, f"Standby list purged successfully (status=0x{status:X})"
                else:
                    return False, f"NtSetSystemInformation failed with status 0x{status:X}"
        
        if result.returncode != 0:
            return False, f"PowerShell failed: {result.stderr}"
        
        return False, "Could not parse PowerShell output"
        
    except subprocess.TimeoutExpired:
        return False, "PowerShell command timed out"
    except Exception as e:
        return False, f"Error running PowerShell: {e}"


def set_process_working_set_size() -> tuple[bool, str]:
    """
    Alternative: Trim the current process working set.
    This is a documented API that doesn't require admin but only affects
    the current process, not system-wide memory.
    
    On native Windows: calls SetProcessWorkingSetSizeEx directly via ctypes
    On WSL: executes a PowerShell command that calls the Windows API
    """
    import sys
    import platform
    import subprocess
    
    is_windows = platform.system() == "Windows"
    is_wsl = False
    if not is_windows:
        try:
            with open("/proc/version", "r") as f:
                if "microsoft" in f.read().lower():
                    is_wsl = True
        except Exception:
            pass
    
    if not is_windows and not is_wsl:
        return False, "Not running on Windows or WSL"
    
    if is_windows:
        return _trim_working_set_native()
    else:
        return _trim_working_set_wsl()


def _trim_working_set_native() -> tuple[bool, str]:
    """Native Windows implementation using ctypes."""
    try:
        import ctypes
        from ctypes import wintypes
        
        kernel32 = ctypes.WinDLL("kernel32")
        
        GetCurrentProcess = kernel32.GetCurrentProcess
        GetCurrentProcess.restype = wintypes.HANDLE
        
        if hasattr(kernel32, 'SetProcessWorkingSetSizeEx'):
            SetProcessWorkingSetSizeEx = kernel32.SetProcessWorkingSetSizeEx
            SetProcessWorkingSetSizeEx.argtypes = [
                wintypes.HANDLE,
                ctypes.c_size_t,
                ctypes.c_size_t,
                wintypes.DWORD
            ]
            SetProcessWorkingSetSizeEx.restype = wintypes.BOOL
            
            result = SetProcessWorkingSetSizeEx(
                GetCurrentProcess(),
                ctypes.c_size_t(-1),
                ctypes.c_size_t(-1),
                0
            )
        else:
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


def _trim_working_set_wsl() -> tuple[bool, str]:
    """WSL implementation using PowerShell to call Windows API."""
    try:
        ps_script = r'''
        Add-Type -TypeDefinition @"
        using System;
        using System.Runtime.InteropServices;
        public class WorkingSetTrim {
            [DllImport("kernel32.dll")]
            public static extern IntPtr GetCurrentProcess();
            
            [DllImport("kernel32.dll")]
            public static extern bool SetProcessWorkingSetSizeEx(
                IntPtr hProcess,
                IntPtr dwMinimumWorkingSetSize,
                IntPtr dwMaximumWorkingSetSize,
                uint dwFlags
            );
            
            [DllImport("kernel32.dll")]
            public static extern bool SetProcessWorkingSetSize(
                IntPtr hProcess,
                IntPtr dwMinimumWorkingSetSize,
                IntPtr dwMaximumWorkingSetSize
            );
            
            public static bool Trim() {
                IntPtr hProcess = GetCurrentProcess();
                IntPtr minMax = (IntPtr)(-1);
                
                // Try SetProcessWorkingSetSizeEx first (Windows 8.1+)
                try {
                    return SetProcessWorkingSetSizeEx(hProcess, minMax, minMax, 0);
                } catch {
                    // Fallback to older API
                    return SetProcessWorkingSetSize(hProcess, minMax, minMax);
                }
            }
        }
"@
        $result = [WorkingSetTrim]::Trim()
        Write-Host "RESULT:$result"
        if (-not $result) {
            $err = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
            Write-Host "ERROR:$err"
        }
        '''
        
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        for line in result.stdout.splitlines():
            if line.startswith("RESULT:"):
                success = line.split(":")[1].strip().lower() == "true"
                if success:
                    return True, "Process working set trimmed"
            elif line.startswith("ERROR:"):
                error = line.split(":")[1].strip()
                return False, f"SetProcessWorkingSetSize failed with error {error}"
        
        if result.returncode != 0:
            return False, f"PowerShell failed: {result.stderr}"
        
        return False, "Could not parse PowerShell output"
        
    except subprocess.TimeoutExpired:
        return False, "PowerShell command timed out"
    except Exception as e:
        return False, f"Error running PowerShell: {e}"

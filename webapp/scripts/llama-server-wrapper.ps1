# Wrapper script to initialize Intel oneAPI environment and launch llama-server.exe
# Supports Intel Arc GPU via SYCL/OpenCL backend

param(
    # First positional argument: path to llama-server.exe (called by Flask app)
    [Parameter(Position=0, Mandatory=$true)]
    [string]$LlamaServerPath,

    # Catch-all for remaining arguments (passed to llama-server.exe)
    # This prevents PowerShell from trying to parse -m, --host, etc. as script parameters
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$LlamaArgs = @(),

    # Named parameters for Intel oneAPI configuration (must be passed by name, e.g. -OneApiPath)
    [Parameter(Mandatory=$false)]
    [string]$OneApiPath = "",

    [string]$Version = "2026.2",
    [string]$Arch = "intel64",  # Options: intel64, ia32
    [string]$VsVersion = ""     # Optional: e.g., "vs2022" or "vs2026"
)

# Enable/disable Intel oneAPI configuration
$enableIntelOneAPI = $true

if ($enableIntelOneAPI) {
    # Determine OneAPI root path: parameter > env var > common install locations
    if (-Not $OneApiPath) {
        $OneApiPath = $env:ONEAPI_ROOT
    }
    if (-Not $OneApiPath) {
        $CommonPaths = @(
            "C:\Program Files (x86)\Intel\oneAPI",
            "C:\Program Files\Intel\oneAPI",
            "C:\Intel\oneAPI",
            (Join-Path $env:ProgramFiles "Intel\oneAPI"),
            (Join-Path ${env:ProgramFiles(x86)} "Intel\oneAPI")
        )
        $OneApiPath = $CommonPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
    }

    if (-Not $OneApiPath) {
        Write-Error "Could not locate Intel oneAPI installation. Set ONEAPI_ROOT env var or pass -OneApiPath parameter."
        Write-Error "Searched: $($CommonPaths -join ', ')"
        return
    }

    Write-Host "Using Intel oneAPI at: $OneApiPath" -ForegroundColor Cyan

    # Locate the initialization script (checks versioned, then root setvars.bat)
    $PossiblePaths = @(
        (Join-Path $OneApiPath "$Version\oneapi-vars.bat"),
        (Join-Path $OneApiPath "2026.2.0\oneapi-vars.bat"),
        (Join-Path $OneApiPath "setvars.bat")
    )

    $BatScript = $PossiblePaths | Where-Object { Test-Path $_ } | Select-Object -First 1

    if (-Not $BatScript) {
        Write-Error "Could not find oneapi-vars.bat or setvars.bat under: $OneApiPath"
        Write-Error "Searched: $($PossiblePaths -join ', ')"
        return
    }

    # Build arguments
    $ArgsList = @($Arch)
    if ($VsVersion -ne "") { $ArgsList += $VsVersion }
    $ArgString = $ArgsList -join " "

    Write-Host "Initializing Intel oneAPI $Version from: $BatScript" -ForegroundColor Cyan

    # Execute the batch file in cmd.exe and capture the resulting environment variables
    $CmdCommand = "`"$BatScript`" $ArgString > nul && set"
    $EnvOutput = cmd.exe /c $CmdCommand

    foreach ($Line in $EnvOutput) {
        if ($Line -match "^([^=]+)=(.*)$") {
            [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process")
        }
    }

    if ($env:SETVARS_COMPLETED -eq "1") {
        Write-Host "Intel oneAPI $Version environment successfully enabled." -ForegroundColor Green
    } else {
        Write-Warning "Initialization finished, but SETVARS_COMPLETED was not detected."
    }

    # Additional llama.cpp-specific environment variables for Intel GPU
    $env:ONEAPI_DEVICE_SELECTOR = "opencl:gpu"
    $env:SYCL_DEVICE_FILTER = "opencl:gpu"
    $env:GGML_SYCL_USE_LEVEL_ZERO_API = "1"
    $env:GGML_VK_DISABLE_COOPMAT = "1"
    $env:GGML_OPENVINO_DEVICE="NPU"
} else {
		$env:ONEAPI_DEVICE_SELECTOR = "opencl:gpu"
        $env:SYCL_DEVICE_FILTER = "opencl:gpu"
	    $env:GGML_SYCL_USE_LEVEL_ZERO_API = "0"
		$env:GGML_VK_DISABLE_COOPMAT = "1"
        $env:GGML_OPENVINO_DEVICE="NPU"
    Write-Host "Intel oneAPI configuration disabled" -ForegroundColor Yellow
}

# Remaining arguments for llama-server.exe (captured via ValueFromRemainingArguments)
$llamaArgs = $LlamaArgs

# Debug
Write-Host "Launching: $LlamaServerPath $($llamaArgs -join ' ')"

# Execute llama-server.exe with all remaining arguments
& $LlamaServerPath @llamaArgs
<#
.SYNOPSIS
    Llama.cpp Web App Startup Script (PowerShell)
.DESCRIPTION
    This script manages the virtual environment and starts the web application.
    It will:
    1. Check for and create a Python virtual environment if it doesn't exist
    2. Activate the virtual environment
    3. Check for required Python modules and install missing ones
    4. Start the web application
.EXAMPLE
    .\start.ps1
    .\start.ps1 --port 5000 --host 0.0.0.0
#>

[CmdletBinding()]
param(
    # Additional arguments passed to the Flask app
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AppArgs
)

$ErrorActionPreference = "Stop"

# Configuration
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$VenvName = "venv"
$VenvPath = Join-Path $ScriptDir $VenvName
$RequirementsFile = Join-Path $ScriptDir "requirements.txt"
$PythonExec = "python"

# Intel GPU acceleration - use OpenCL backend instead of Level Zero
$env:ONEAPI_DEVICE_SELECTOR = "opencl:gpu"
$env:SYCL_DEVICE_FILTER = "opencl:gpu"
$env:GGML_SYCL_USE_LEVEL_ZERO_API = "0"

# Colors for output
$Red = [ConsoleColor]::Red
$Green = [ConsoleColor]::Green
$Yellow = [ConsoleColor]::Yellow
$Cyan = [ConsoleColor]::Cyan
$Gray = [ConsoleColor]::Gray

function Write-LogInfo {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor $Cyan
}

function Write-LogSuccess {
    param([string]$Message)
    Write-Host "[SUCCESS] $Message" -ForegroundColor $Green
}

function Write-LogWarning {
    param([string]$Message)
    Write-Host "[WARNING] $Message" -ForegroundColor $Yellow
}

function Write-LogError {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor $Red
}

function Test-Command {
    param([string]$Name)
    $null = Get-Command $Name -ErrorAction SilentlyContinue
    return $?
}

function Get-ModuleImportName {
    param([string]$PackageName)
    
    # Map package names that differ from their import names
    $importMap = @{
        "python-dotenv" = "dotenv"
        "pyyaml" = "yaml"
        "python-dateutil" = "dateutil"
        "beautifulsoup4" = "bs4"
        "python-magic" = "magic"
        "opencv-python" = "cv2"
        "pillow" = "PIL"
    }
    
    if ($importMap.ContainsKey($PackageName)) {
        return $importMap[$PackageName]
    }
    return $PackageName
}

function Check-Python {
    if (-not (Test-Command $PythonExec)) {
        Write-LogError "Python 3 is not installed. Please install Python 3 before running this script."
        exit 1
    }
    $version = & $PythonExec --version 2>&1
    Write-LogInfo "Found Python: $version"
}

function Create-Venv {
    if (-not (Test-Path $VenvPath)) {
        Write-LogInfo "Creating virtual environment at ${VenvPath}..."
        try {
            & $PythonExec -m venv $VenvPath
            Write-LogSuccess "Virtual environment created successfully."
        } catch {
            Write-LogError "Failed to create virtual environment. Check Python venv module is available."
            exit 1
        }
    } else {
        Write-LogInfo "Virtual environment already exists at ${VenvPath}."
    }
}

function Activate-Venv {
    Write-LogInfo "Activating virtual environment..."
    $activateScript = Join-Path $VenvPath "Scripts\Activate.ps1"
    if (Test-Path $activateScript) {
        . $activateScript
        Write-LogSuccess "Virtual environment activated."
    } else {
        Write-LogError "Activation script not found at ${activateScript}"
        exit 1
    }
}

function Check-Pip {
    $venvPip = Join-Path $VenvPath "Scripts\pip.exe"
    if (-not (Test-Path $venvPip)) {
        Write-LogError "pip not found in virtual environment at ${venvPip}"
        exit 1
    }
    $version = & $venvPip --version 2>&1
    Write-LogInfo "Found pip: $version"
}

function Install-Requirements {
    if (-not (Test-Path $RequirementsFile)) {
        Write-LogError "Requirements file not found at ${RequirementsFile}"
        exit 1
    }

    Write-LogInfo "Checking required Python modules..."

    $requirementsContent = Get-Content $RequirementsFile -Raw
    if ([string]::IsNullOrWhiteSpace($requirementsContent)) {
        Write-LogError "Requirements file is empty."
        exit 1
    }

    $venvPython = Join-Path $VenvPath "Scripts\python.exe"
    $missingModules = @()
    $lines = Get-Content $RequirementsFile

    foreach ($line in $lines) {
        # Skip empty lines and comments
        $line = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#")) {
            continue
        }

        # Extract package name (before any version specifier)
        $packageName = $line -replace '(>=|==|<|>|~=|!=).*', '' -replace '^\s+|\s+$', ''
        if ([string]::IsNullOrWhiteSpace($packageName)) {
            continue
        }

        # Get import name (may differ from package name)
        $importName = Get-ModuleImportName $packageName

        # Check if module is installed using venv python
        try {
            $null = & $venvPython -c "import $importName" 2>$null
            Write-LogInfo "Module ${packageName} (import: ${importName}) is already installed."
        } catch {
            $missingModules += $line
            Write-LogWarning "Module ${packageName} (import: ${importName}) is not installed."
        }
    }

    # Install missing modules
    if ($missingModules.Count -gt 0) {
        Write-LogInfo "Installing missing modules: $($missingModules -join ', ')"
        try {
            & $venvPython -m pip install --upgrade $missingModules
            Write-LogSuccess "All required modules installed successfully."
        } catch {
            Write-LogError "Failed to install required modules."
            exit 1
        }
    } else {
        Write-LogSuccess "All required modules are already installed."
    }
}

function Verify-Modules {
    Write-LogInfo "Verifying all required modules are available..."
    $allOk = $true
    $venvPython = Join-Path $VenvPath "Scripts\python.exe"
    $lines = Get-Content $RequirementsFile

    foreach ($line in $lines) {
        $line = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#")) {
            continue
        }

        $packageName = $line -replace '(>=|==|<|>|~=|!=).*', '' -replace '^\s+|\s+$', ''
        if ([string]::IsNullOrWhiteSpace($packageName)) {
            continue
        }

        $importName = Get-ModuleImportName $packageName

        try {
            $null = & $venvPython -c "import $importName" 2>$null
        } catch {
            Write-LogError "Module ${packageName} (import: ${importName}) is still not available after installation."
            $allOk = $false
        }
    }

    if ($allOk) {
        Write-LogSuccess "All modules verified successfully."
    } else {
        exit 1
    }
}

function Start-App {
    Write-LogInfo "Starting the web application..."
    Write-LogInfo "Working directory: ${ScriptDir}"
    $venvPython = Join-Path $VenvPath "Scripts\python.exe"
    Write-LogInfo "Python interpreter: ${venvPython}"

    Set-Location $ScriptDir

    # Start the Flask application using venv python
    & $venvPython webapp/main.py @AppArgs
}

# Main execution
function Main {
    Write-LogInfo "Starting Llama.cpp Web App..."
    Write-LogInfo "Script directory: ${ScriptDir}"

    # Step 1: Check Python
    Check-Python

    # Step 2: Create virtual environment if needed
    Create-Venv

    # Step 3: Activate virtual environment
    Activate-Venv

    # Step 4: Check pip
    Check-Pip

    # Step 5: Install requirements
    Install-Requirements

    # Step 6: Verify modules
    Verify-Modules

    # Step 7: Start the application
    Start-App
}

# Run main function
Main
<#
.SYNOPSIS
    Llama.cpp Model Manager - Docker Setup for Windows
.DESCRIPTION
    This script downloads the latest release from GitHub and deploys the webapp
    as a Docker container on Windows. It handles:
    - Prerequisites check (Docker, Git)
    - Downloading latest release from GitHub
    - Setting up required directories and volumes
    - Configuring llama-server binary (required for model serving)
    - Starting the Docker container
.NOTES
    Requires: Docker Desktop for Windows, Git for Windows
    GitHub: https://github.com/rpowell01/llama.cpp-model-manager
#>

[CmdletBinding()]
param(
    # GitHub repository
    [string]$RepoOwner = "rpowell01",
    [string]$RepoName = "llama.cpp-model-manager",

    # Installation directory
    [string]$InstallDir = "$env:USERPROFILE\llama.cpp-model-manager",

    # llama-server binary directory (optional - will prompt if not provided)
    [string]$LlamaServerDir = "",

    # Docker Compose project name
    [string]$ProjectName = "llamaweb",

    # Skip prompts (use defaults)
    [switch]$AutoConfirm,

    # Show debug output
    [switch]$DebugOutput
)

# ──────────────────────────────────────────────────────────────────────────────
# Configuration & Setup
# ──────────────────────────────────────────────────────────────────────────────

$ErrorActionPreference = "Stop"

# Colors
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

function Write-LogVerbose {
    param([string]$Message)
    if ($DebugOutput) {
        Write-Host "[VERBOSE] $Message" -ForegroundColor $Gray
    }
}

# ──────────────────────────────────────────────────────────────────────────────
# Prerequisites Check
# ──────────────────────────────────────────────────────────────────────────────

function Check-Prerequisites {
    Write-LogInfo "Checking prerequisites..."

    # Check Docker
    try {
        $dockerVersion = docker --version 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "Docker not found"
        }
        Write-LogSuccess "Docker found: $dockerVersion"
    } catch {
        Write-LogError "Docker is not installed or not in PATH."
        Write-Host "Please install Docker Desktop for Windows:" -ForegroundColor $Yellow
        Write-Host "  https://docs.docker.com/desktop/install/windows-install/" -ForegroundColor $Cyan
        exit 1
    }

    # Check Docker Compose
    try {
        $composeVersion = docker compose version 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "Docker Compose not found"
        }
        Write-LogSuccess "Docker Compose found: $composeVersion"
    } catch {
        Write-LogError "Docker Compose v2 is not available."
        Write-Host "Please update Docker Desktop to a version with Compose v2." -ForegroundColor $Yellow
        exit 1
    }

    # Check Git
    try {
        $gitVersion = git --version 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "Git not found"
        }
        Write-LogSuccess "Git found: $gitVersion"
    } catch {
        Write-LogError "Git is not installed or not in PATH."
        Write-Host "Please install Git for Windows:" -ForegroundColor $Yellow
        Write-Host "  https://git-scm.com/download/win" -ForegroundColor $Cyan
        exit 1
    }

    # Check if Docker daemon is running
    try {
        $null = docker info 2>&1 | Out-Null
        Write-LogSuccess "Docker daemon is running"
    } catch {
        Write-LogError "Docker daemon is not running. Please start Docker Desktop."
        exit 1
    }
}

# ──────────────────────────────────────────────────────────────────────────────
# GitHub Source Download (Release or Main Branch)
# ─────────────────────────────────────────────────────────────────────────��────

function Get-SourceInfo {
    Write-LogInfo "Fetching latest release from GitHub..."

    $apiUrl = "https://api.github.com/repos/$RepoOwner/$RepoName/releases/latest"

    try {
        $response = Invoke-RestMethod -Uri $apiUrl -Headers @{ "Accept" = "application/vnd.github.v3+json" } -UseBasicParsing
        $response.SourceType = "release"
        return $response
    } catch {
        # No releases found - fall back to main branch
        if ($_.Exception.Response.StatusCode -eq 404) {
            Write-LogInfo "No releases found, falling back to main branch..."
            $apiUrl = "https://api.github.com/repos/$RepoOwner/$RepoName"
            $repoInfo = Invoke-RestMethod -Uri $apiUrl -Headers @{ "Accept" = "application/vnd.github.v3+json" } -UseBasicParsing
            $repoInfo.SourceType = "branch"
            $repoInfo.tag_name = $repoInfo.default_branch
            $repoInfo.name = "main branch"
            $repoInfo.zipball_url = "https://github.com/$RepoOwner/$RepoName/archive/refs/heads/$($repoInfo.default_branch).zip"
            return $repoInfo
        } else {
            Write-LogError "Failed to fetch repository information: $($_.Exception.Message)"
            Write-Host "Check your internet connection and GitHub API availability." -ForegroundColor $Yellow
            exit 1
        }
    }
}

function Download-Source {
    param(
        [object]$SourceInfo,
        [string]$Destination
    )

    $downloadUrl = $SourceInfo.zipball_url
    $fileName = "$($SourceInfo.tag_name).zip"

    $zipPath = Join-Path $Destination $fileName

    Write-LogInfo "Downloading $fileName ($($SourceInfo.SourceType))..."
    Write-LogVerbose "URL: $downloadUrl"

    try {
        $progressPreference = 'silentlyContinue'
        Invoke-WebRequest -Uri $downloadUrl -OutFile $zipPath -UseBasicParsing
        $progressPreference = 'Continue'
        Write-LogSuccess "Downloaded to $zipPath"
        return $zipPath
    } catch {
        Write-LogError "Download failed: $($_.Exception.Message)"
        exit 1
    }
}

function Extract-Release {
    param(
        [string]$ZipPath,
        [string]$ExtractDir
    )

    Write-LogInfo "Extracting release..."

    try {
        # Remove existing directory if it exists
        if (Test-Path $ExtractDir) {
            Write-LogVerbose "Removing existing directory: $ExtractDir"
            Remove-Item -Recurse -Force $ExtractDir -ErrorAction SilentlyContinue
        }

        # Extract
        Expand-Archive -Path $ZipPath -DestinationPath $ExtractDir -Force

        # GitHub zipballs often have a single top-level folder with the tag name
        # Find the actual content directory
        $contents = Get-ChildItem $ExtractDir -Directory
        if ($contents.Count -eq 1) {
            $innerDir = $contents[0].FullName
            if ($innerDir -ne $ExtractDir) {
                Write-LogVerbose "Moving contents from $innerDir to $ExtractDir"
                Move-Item -Path "$innerDir\*" -Destination $ExtractDir -Force
                Move-Item -Path "$innerDir\.*" -Destination $ExtractDir -Force -ErrorAction SilentlyContinue
                Remove-Item -Recurse -Force $innerDir -ErrorAction SilentlyContinue
            }
        }

        Write-LogSuccess "Extracted to $ExtractDir"
        return $ExtractDir
    } catch {
        Write-LogError "Extraction failed: $($_.Exception.Message)"
        exit 1
    }
}

# ──────────────────────────────────────────────────────────────────────────────
# llama-server Binary Setup
# ──────────────────────────────────────────────────────────────────────────────

function Find-LlamaServerOnHost {
    Write-LogInfo "Searching for llama-server binary on host..."

    $searchPaths = @(
        "$env:USERPROFILE\llama.cpp\bin",
        "$env:USERPROFILE\llama.cpp",
        "$env:LOCALAPPDATA\llama.cpp\bin",
        "$env:LOCALAPPDATA\llama.cpp",
        "$env:PROGRAMFILES\llama.cpp\bin",
        "$env:PROGRAMFILES\llama.cpp",
        "C:\llama.cpp\bin",
        "C:\llama.cpp"
    )

    foreach ($path in $searchPaths) {
        if (Test-Path $path) {
            $binaries = Get-ChildItem $path -Filter "llama-server*" -File -ErrorAction SilentlyContinue
            if ($binaries.Count -gt 0) {
                Write-LogSuccess "Found llama-server at: $path"
                foreach ($bin in $binaries) {
                    Write-Host "  - $($bin.Name) ($($bin.FullName))" -ForegroundColor $Gray
                }
                return $path
            }
        }
    }

    return $null
}

function Prompt-LlamaServerDir {
    param(
        [string]$FoundPath
    )

    Write-Host ""
    Write-Host "================================================================" -ForegroundColor $Cyan
    Write-Host "LLAMA-SERVER BINARY REQUIRED" -ForegroundColor $Yellow
    Write-Host "================================================================" -ForegroundColor $Cyan
    Write-Host ""
    Write-Host "The web UI manages llama-server processes but does NOT include the binary."
    Write-Host "You must provide a llama-server binary built with KV cache slot save support."
    Write-Host ""

    if ($FoundPath) {
        Write-Host "Found llama-server at: $FoundPath" -ForegroundColor $Green
        $useFound = Read-Host "Use this directory? (Y/n)"
        if ($useFound -eq '' -or $useFound -like 'y*') {
            return $FoundPath
        }
    }

    Write-Host ""
    Write-Host "Common locations:" -ForegroundColor $Cyan
    Write-Host "  - $env:USERPROFILE\llama.cpp\bin" -ForegroundColor $Gray
    Write-Host "  - $env:LOCALAPPDATA\llama.cpp\bin" -ForegroundColor $Gray
    Write-Host "  - C:\llama.cpp\bin" -ForegroundColor $Gray
    Write-Host ""
    Write-Host "If you haven't built llama.cpp yet, see:" -ForegroundColor $Cyan
    Write-Host "  https://github.com/ggerganov/llama.cpp/blob/master/docs/build.md" -ForegroundColor $Cyan
    Write-Host "  Build with: cmake -DGGML_NATIVE=OFF -DLLAMA_CURL=ON -DGGML_CUDA=ON .." -ForegroundColor $Gray
    Write-Host ""

    while ($true) {
        $path = Read-Host "Enter path to llama-server directory (or 'skip' to configure later)"
        if ($path -eq 'skip') {
            Write-LogWarning "Skipping llama-server configuration. You must mount it later in docker-compose.yml"
            return ""
        }
        if (Test-Path $path) {
            $binaries = Get-ChildItem $path -Filter "llama-server*" -File -ErrorAction SilentlyContinue
            if ($binaries.Count -gt 0) {
                return $path
            } else {
                Write-LogWarning "No llama-server binary found in $path"
            }
        } else {
            Write-LogWarning "Directory not found: $path"
        }
    }
}

function Update-DockerCompose {
    param(
        [string]$ComposeFile,
        [string]$LlamaServerDir,
        [string]$InstallDir
    )

    Write-LogInfo "Updating docker-compose.yml..."

    $content = Get-Content $ComposeFile -Raw

    # Update the volumes section to include llama-server mount
    if ($LlamaServerDir) {
        # Convert Windows path to Docker-compatible path
        $dockerPath = $LlamaServerDir.Replace('\', '/')
        if ($dockerPath -match '^[A-Za-z]:/') {
            # Convert C:/path to /c/path for Docker
            $drive = $dockerPath[0].ToLower()
            $dockerPath = "/$drive" + $dockerPath.Substring(2)
        }

        $volumeLine = "      - ${dockerPath}:/app/llama-server:ro"

        # Check if volume already exists
        if ($content -notmatch [regex]::Escape($volumeLine)) {
            # Insert after the logs volume line
            $content = $content -replace '(      - \.\/logs:\/app\/logs)', "`$1`n$volumeLine"
            Write-LogSuccess "Added llama-server volume mount: $volumeLine"
        } else {
            Write-LogInfo "llama-server volume mount already present"
        }
    }

    # Update MODEL_DIR to use the host models directory
    $hostModelsDir = Join-Path $InstallDir "models"
    $dockerModelsPath = $hostModelsDir.Replace('\', '/')
    if ($dockerModelsPath -match '^[A-Za-z]:/') {
        $drive = $dockerModelsPath[0].ToLower()
        $dockerModelsPath = "/$drive" + $dockerModelsPath.Substring(2)
    }

    $content = $content -replace 'MODEL_DIR=\/app\/models', "MODEL_DIR=$dockerModelsPath"

    # Write back
    Set-Content -Path $ComposeFile -Value $content -Encoding UTF8
    Write-LogSuccess "Updated docker-compose.yml"
}

# ──────────────────────────────────────────────────────────────────────────────
# Docker Deployment
# ──────────────────────────────────────────────────────────────────────────────

function Deploy-Docker {
    param(
        [string]$InstallDir,
        [string]$ProjectName
    )

    Write-LogInfo "Deploying Docker container..."

    Set-Location $InstallDir

    # Build image
    Write-LogInfo "Building Docker image..."
    try {
        docker compose -p $ProjectName build
        Write-LogSuccess "Docker image built successfully"
    } catch {
        Write-LogError "Docker build failed: $($_.Exception.Message)"
        exit 1
    }

    # Start container
    Write-LogInfo "Starting container..."
    try {
        docker compose -p $ProjectName up -d
        Write-LogSuccess "Container started successfully"
    } catch {
        Write-LogError "Container start failed: $($_.Exception.Message)"
        exit 1
    }

    # Show status
    Write-LogInfo "Container status:"
    docker compose -p $ProjectName ps
}

function Show-PostInstallInfo {
    param(
        [string]$InstallDir,
        [string]$ProjectName
    )

    Write-Host ""
    Write-Host "================================================================" -ForegroundColor $Cyan
    Write-Host "DEPLOYMENT COMPLETE" -ForegroundColor $Green
    Write-Host "================================================================" -ForegroundColor $Cyan
    Write-Host ""
    Write-Host "Web UI: http://localhost:5000" -ForegroundColor $Cyan
    Write-Host ""
    Write-Host "Installation directory: $InstallDir" -ForegroundColor $Gray
    Write-Host ""
    Write-Host "Useful commands:" -ForegroundColor $Cyan
    Write-Host "  View logs:     docker compose -p $ProjectName logs -f web" -ForegroundColor $Gray
    Write-Host "  Stop:          docker compose -p $ProjectName down" -ForegroundColor $Gray
    Write-Host "  Restart:       docker compose -p $ProjectName restart" -ForegroundColor $Gray
    Write-Host "  Update:        Re-run this script" -ForegroundColor $Gray
    Write-Host ""
    Write-Host "Model configuration:" -ForegroundColor $Cyan
    Write-Host "  Edit $InstallDir\webapp\data\models.json" -ForegroundColor $Gray
    Write-Host "  Or use the web UI to add models" -ForegroundColor $Gray
    Write-Host ""
    Write-Host "llama-server binary:" -ForegroundColor $Cyan
    Write-Host "  Mounted at: /app/llama-server (inside container)" -ForegroundColor $Gray
    Write-Host "  To change: Edit docker-compose.yml volumes section" -ForegroundColor $Gray
    Write-Host ""
}

# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

function Main {
    Write-Host ""
    Write-Host "==================================================================" -ForegroundColor $Cyan
    Write-Host "  Llama.cpp Model Manager - Docker Setup for Windows" -ForegroundColor $Cyan
    Write-Host "==================================================================" -ForegroundColor $Cyan
    Write-Host ""

    # Check prerequisites
    Check-Prerequisites

    # Get source info (release or main branch)
    $sourceInfo = Get-SourceInfo
    Write-LogInfo "Latest source: $($sourceInfo.tag_name) - $($sourceInfo.name)"
    if ($sourceInfo.published_at) {
        Write-LogVerbose "Published: $($sourceInfo.published_at)"
    }

    # Prepare install directory
    if (-not (Test-Path $InstallDir)) {
        Write-LogInfo "Creating installation directory: $InstallDir"
        New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    }

    # Download and extract
    $tempDir = [System.IO.Path]::GetTempPath()
    $zipPath = Download-Source -SourceInfo $sourceInfo -Destination $tempDir
    $extractDir = Extract-Release -ZipPath $zipPath -ExtractDir $InstallDir

    # Find or prompt for llama-server directory
    if (-not $LlamaServerDir) {
        $foundPath = Find-LlamaServerOnHost
        $LlamaServerDir = Prompt-LlamaServerDir -FoundPath $foundPath
    }

    # Update docker-compose.yml
    $composeFile = Join-Path $extractDir "docker-compose.yml"
    if (Test-Path $composeFile) {
        Update-DockerCompose -ComposeFile $composeFile -LlamaServerDir $LlamaServerDir -InstallDir $extractDir
    } else {
        Write-LogError "docker-compose.yml not found in extracted release"
        exit 1
    }

    # Create models directory
    $modelsDir = Join-Path $extractDir "models"
    if (-not (Test-Path $modelsDir)) {
        New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null
        Write-LogInfo "Created models directory: $modelsDir"
    }

    # Create logs directory
    $logsDir = Join-Path $extractDir "logs"
    if (-not (Test-Path $logsDir)) {
        New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
        Write-LogInfo "Created logs directory: $logsDir"
    }

    # Deploy
    Deploy-Docker -InstallDir $extractDir -ProjectName $ProjectName

    # Show post-install info
    Show-PostInstallInfo -InstallDir $extractDir -ProjectName $ProjectName

    # Cleanup temp zip
    if (Test-Path $zipPath) {
        Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
    }
}

# ──────────────────────────────────────────────────────────────────────────────
# Run Main
# ──────────────────────────────────────────────────────────────────────────────

try {
    Main
} catch {
    Write-LogError "Script failed: $($_.Exception.Message)"
    if ($DebugOutput) {
        Write-Host $_.ScriptStackTrace -ForegroundColor $Red
    }
    exit 1
}
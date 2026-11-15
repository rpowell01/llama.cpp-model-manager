#!/bin/bash

# Llama.cpp Web App Startup Script
# ================================
# This script manages the virtual environment and starts the web application.
# It will:
# 1. Check for and create a Python virtual environment if it doesn't exist
# 2. Activate the virtual environment
# 3. Check for required Python modules and install missing ones
# 4. Start the web application

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_NAME="venv"
VENV_PATH="${SCRIPT_DIR}/${VENV_NAME}"
REQUIREMENTS_FILE="${SCRIPT_DIR}/requirements.txt"
PYTHON_EXEC="python3"

# Initialize Intel oneAPI environment (workaround for OCL_ICD_FILENAMES unbound variable bug)
#  if [ -f /opt/intel/oneapi/setvars.sh ]; then
#      export OCL_ICD_FILENAMES=""  # Prevent unbound variable error in setvars.sh
#      source /opt/intel/oneapi/setvars.sh --force 2>/dev/null || true
#  fi

# Intel GPU acceleration - use OpenCL backend instead of Level Zero
export ONEAPI_DEVICE_SELECTOR="opencl:gpu"
export SYCL_DEVICE_FILTER="opencl:gpu"
export GGML_SYCL_USE_LEVEL_ZERO_API="0"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

# Check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check if Python is available
check_python() {
    if ! command_exists "$PYTHON_EXEC"; then
        log_error "Python 3 is not installed. Please install Python 3 before running this script."
        exit 1
    fi
    log_info "Found Python: $($PYTHON_EXEC --version 2>&1)"
}

# Create virtual environment if it doesn't exist
create_venv() {
    if [ ! -d "$VENV_PATH" ]; then
        log_info "Creating virtual environment at ${VENV_PATH}..."
        if ! "$PYTHON_EXEC" -m venv "$VENV_PATH"; then
            log_error "Failed to create virtual environment. Check Python venv module is available."
            exit 1
        fi
        log_success "Virtual environment created successfully."
    else
        log_info "Virtual environment already exists at ${VENV_PATH}."
    fi
}

# Activate virtual environment
activate_venv() {
    log_info "Activating virtual environment..."
    # Handle both Unix (bin/) and Windows (Scripts/) venv layouts
    if [ -f "${VENV_PATH}/bin/activate" ]; then
        source "${VENV_PATH}/bin/activate"
    elif [ -f "${VENV_PATH}/Scripts/activate" ]; then
        source "${VENV_PATH}/Scripts/activate"
    else
        log_error "Virtual environment activation script not found at ${VENV_PATH}/bin/activate or ${VENV_PATH}/Scripts/activate"
        exit 1
    fi
    log_success "Virtual environment activated."
}

# Check if pip is available and working in the current environment, install if missing
check_pip() {
    # Check if pip command works (not just if it exists)
    if ! pip --version >/dev/null 2>&1; then
        log_warning "pip is not available or broken in the current Python environment. Installing pip..."
        if ! python -m ensurepip --upgrade 2>/dev/null; then
            log_warning "ensurepip failed, trying alternative method..."
            if ! python -m pip install --upgrade pip 2>/dev/null; then
                log_error "Failed to install pip."
                exit 1
            fi
        fi
        log_success "pip installed successfully."
    fi
    log_info "Found pip: $(pip --version)"
}

# Check and install required modules
install_requirements() {
    if [ ! -f "$REQUIREMENTS_FILE" ]; then
        log_error "Requirements file not found at ${REQUIREMENTS_FILE}"
        exit 1
    fi

    log_info "Checking required Python modules..."
    
    # Read requirements file
    local missing_modules=()
    local requirements_content
    requirements_content=$(cat "$REQUIREMENTS_FILE" 2>/dev/null || echo "")
    
    if [ -z "$requirements_content" ]; then
        log_error "Requirements file is empty."
        exit 1
    fi

    # Check each requirement using the activated venv python
    while IFS= read -r line || [ -n "$line" ]; do
        # Skip empty lines and comments
        line=$(echo "$line" | sed 's/#.*//' | xargs)
        [ -z "$line" ] && continue
        
        # Extract module name (before any version specifier) and convert to lowercase
        local module_name
        module_name=$(echo "$line" | sed 's/>=.*//;s/==.*//;s/<.*//;s/>.*//;s/~=.*//;s/!=.*//' | xargs | tr '[:upper:]' '[:lower:]')
        
        if [ -z "$module_name" ]; then
            continue
        fi
        
        # Map PyPI package names to Python import names
        local import_name="$module_name"
        case "$module_name" in
            "python-dotenv") import_name="dotenv" ;;
            "gpu-detector") import_name="gpu_detector" ;;
            "pyyaml") import_name="yaml" ;;
            "pillow") import_name="PIL" ;;
            "opencv-python") import_name="cv2" ;;
            "scikit-learn") import_name="sklearn" ;;
            "beautifulsoup4") import_name="bs4" ;;
        esac
        
        # Check if module is installed using the activated python
        if ! python -c "import ${import_name}" 2>/dev/null; then
            missing_modules+=("$line")
            log_warning "Module ${module_name} (import: ${import_name}) is not installed."
        else
            log_info "Module ${module_name} is already installed."
        fi
    done < "$REQUIREMENTS_FILE"

    # Install missing modules
    if [ ${#missing_modules[@]} -gt 0 ]; then
        log_info "Installing missing modules: ${missing_modules[*]}"
        if ! pip install --upgrade "${missing_modules[@]}"; then
            log_error "Failed to install required modules."
            exit 1
        fi
        log_success "All required modules installed successfully."
    else
        log_success "All required modules are already installed."
    fi
}

# Verify all modules are available
verify_modules() {
    log_info "Verifying all required modules are available..."
    
    local all_ok=true
    while IFS= read -r line || [ -n "$line" ]; do
        line=$(echo "$line" | sed 's/#.*//' | xargs)
        [ -z "$line" ] && continue
        
        local module_name
        module_name=$(echo "$line" | sed 's/>=.*//;s/==.*//;s/<.*//;s/>.*//;s/~=.*//;s/!=.*//' | xargs)
        
        if [ -z "$module_name" ]; then
            continue
        fi
        
        # Map PyPI package names to Python import names
        local import_name="$module_name"
        case "$module_name" in
            "python-dotenv") import_name="dotenv" ;;
            "gpu-detector") import_name="gpu_detector" ;;
            "pyyaml") import_name="yaml" ;;
            "pillow") import_name="PIL" ;;
            "opencv-python") import_name="cv2" ;;
            "scikit-learn") import_name="sklearn" ;;
            "beautifulsoup4") import_name="bs4" ;;
        esac
        
        # Use the activated python to check
        if ! python -c "import ${import_name}" 2>/dev/null; then
            log_error "Module ${module_name} (import: ${import_name}) is still not available after installation."
            all_ok=false
        fi
    done < "$REQUIREMENTS_FILE"

    if [ "$all_ok" = true ]; then
        log_success "All modules verified successfully."
    else
        exit 1
    fi
}

# Start the web application
start_app() {
    log_info "Starting the web application..."
    log_info "Working directory: ${SCRIPT_DIR}"
    log_info "Python interpreter: $(which python)"
    
    cd "$SCRIPT_DIR"
    
    # Start the Flask application
    exec python webapp/main.py "$@"
}

# Cleanup on exit
cleanup() {
    log_info "Cleaning up..."
    # Any cleanup code can go here
}

# Trap signals for cleanup
trap cleanup EXIT INT TERM

# Main execution
main() {
    log_info "Starting Llama.cpp Web App..."
    log_info "Script directory: ${SCRIPT_DIR}"
    
    # Step 1: Check Python
    check_python
    
    # Step 2: Create virtual environment if needed
    create_venv
    
    # Step 3: Activate virtual environment
    activate_venv
    
    # Step 4: Check pip
    check_pip
    
    # Step 5: Install requirements
    install_requirements
    
    # Step 6: Verify modules
    verify_modules
    
    # Step 7: Start the application
    start_app "$@"
}

# Run main function
main "$@"

#!/bin/bash

# PostgreSQL Backup Setup and Launcher Script
# Checks Python3, creates virtual environment, installs dependencies, and runs backup script

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to display error and exit
error_exit() {
    echo -e "${RED}ERROR: $1${NC}" >&2
    exit 1
}

# Function to display info message
info() {
    echo -e "${GREEN}INFO: $1${NC}"
}

# Check Python3 installation
check_python() {
    if ! command -v python3 &> /dev/null; then
        error_exit "Python3 is not installed. Please install Python 3.7 or higher."
    fi

    # Get Python version using Python's built-in version info
    PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)

    if [[ -z "$PYTHON_VERSION" ]]; then
        error_exit "Unable to determine Python version"
    fi

    # Split version into major and minor
    IFS='.' read -r MAJOR MINOR <<< "$PYTHON_VERSION"

    # Check if version is at least 3.7
    if [[ "$MAJOR" -lt 3 ]] || [[ "$MAJOR" -eq 3 && "$MINOR" -lt 7 ]]; then
        error_exit "Python 3.7+ is required. Current version: $PYTHON_VERSION"
    fi

    info "Python $PYTHON_VERSION detected ✓"
}

# Setup virtual environment
setup_venv() {
    local VENV_PATH="./pg_backup_venv"

    # Check if venv is already installed
    if ! python3 -m venv --help &> /dev/null; then
        error_exit "Python venv module not found. Install python3-venv package."
    fi

    # Create virtual environment
    if [[ ! -d "$VENV_PATH" ]]; then
        info "Creating virtual environment..."
        python3 -m venv "$VENV_PATH" || error_exit "Failed to create virtual environment"
    fi

    # Activate virtual environment
    source "$VENV_PATH/bin/activate" || error_exit "Failed to activate virtual environment"

    info "Virtual environment activated ✓"
}

# Install required dependencies
install_dependencies() {
    info "Installing required dependencies..."

    pip install --upgrade pip || error_exit "Failed to upgrade pip"

    # List of required Python packages
    DEPENDENCIES=(
        "psycopg2-binary"
        "boto3"
        "minio"
        "google-cloud-storage"
    )

    for dep in "${DEPENDENCIES[@]}"; do
        pip install "$dep" || error_exit "Failed to install $dep"
    done

    info "All dependencies installed successfully ✓"
}

# Main script execution
main() {

    # Get the directory where the script is located
    SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
    cd "$SCRIPT_DIR" || error_exit "Failed to change to script directory"
    # Perform checks and setup
    check_python
    setup_venv
    install_dependencies

    # Path to the backup script (adjust if needed)
    BACKUP_SCRIPT="./backup_script.py"

    # Check if backup script exists
    if [[ ! -f "$BACKUP_SCRIPT" ]]; then
        error_exit "Backup script not found at $BACKUP_SCRIPT"
    fi

    info "Launching PostgreSQL Backup Script..."

    # Pass all arguments to the Python script
    python3 "$BACKUP_SCRIPT" "$@"

    # Deactivate virtual environment
    deactivate
}

# Run main function with all script arguments
main "$@"
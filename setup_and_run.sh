#!/bin/bash

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to display usage
show_usage() {
    echo "Usage: curl https://raw.githubusercontent.com/a-mourad/postgres-backup/main/setup_and_run.sh | bash [command] [options]"
    echo ""
    echo "Commands:"
    echo "  backup              Run backup script"
    echo "  restore DB_NAME     Restore specific database"
    echo "  clean               Remove virtual environment and cache"
    echo "  reset               Reset virtual environment and reinstall requirements"
    echo "  help                Show this help message"
    echo ""
    echo "Options:"
    echo "  --host HOST         Database host"
    echo "  --port PORT         Database port"
    echo "  --user USER         Database user"
    echo "  --password PASS     Database password"
    echo "  --dir DIR           Backup directory"
    echo ""
    echo "Examples:"
    echo "  curl ... | bash backup"
    echo "  curl ... | bash backup --host localhost --port 5432 --user db_user --password secret"
    echo "  curl ... | bash restore mydb --host localhost --user root"
    echo "  curl ... | bash clean"
    echo "  curl ... | bash reset"
    echo "  curl ... | bash help"
}

# Check if Python 3 is installed
if ! command_exists python3; then
    echo "Error: Python 3 is required but not installed."
    echo "Please install Python 3 with: sudo apt install python3.12 python3.12-venv"
    exit 1
fi

# Clone the repository if it doesn't exist
REPO_DIR="postgres-backup"
if [ ! -d "$REPO_DIR" ]; then
    echo "Cloning repository..."
    git clone https://github.com/a-mourad/postgres-backup.git "$REPO_DIR" || { echo "Failed to clone repository"; exit 1; }
fi

# Move into the repository directory
cd "$REPO_DIR" || { echo "Failed to change directory to $REPO_DIR"; exit 1; }

# Pull latest changes
echo "Pulling latest changes..."
git pull origin main || { echo "Failed to pull latest changes"; }

# Copy .env if exists
if [ -f "../.env" ]; then
    cp ../.env . || { echo "Failed to copy .env file"; }
fi

# Setup virtual environment
echo "Setting up virtual environment..."
python3 -m venv venv || { echo "Failed to create virtual environment"; exit 1; }
source venv/bin/activate || { echo "Failed to activate virtual environment"; exit 1; }
pip install -r requirements.txt || { echo "Failed to install requirements"; exit 1; }

# Function to extract database parameters
get_db_params() {
    DB_PARAMS=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --host|--port|--user|--password|--dir)
                if [ -n "$2" ]; then
                    DB_PARAMS="$DB_PARAMS $1 $2"
                    shift 2
                else
                    echo "Error: Missing value for parameter $1"
                    exit 1
                fi
                ;;
            *)
                shift
                ;;
        esac
    done
    echo "$DB_PARAMS"
}

# Main execution flow
if [ $# -eq 0 ]; then
    show_usage
    exit 0
fi

COMMAND="$1"
shift

case "$COMMAND" in
    backup)
        DB_PARAMS=$(get_db_params "$@")
        python3 run.py backup $DB_PARAMS
        ;;
    restore)
        if [ $# -lt 1 ]; then
            echo "Error: Database name is required for restore command"
            show_usage
            exit 1
        fi
        DB_NAME="$1"
        shift
        DB_PARAMS=$(get_db_params "$@")
        python3 run.py restore "$DB_NAME" $DB_PARAMS
        ;;
    clean)
        python3 run.py clean
        ;;
    reset)
        python3 run.py reset
        ;;
    help)
        show_usage
        exit 0
        ;;
    *)
        echo "Error: Unknown command '$COMMAND'"
        show_usage
        exit 1
        ;;
esac

# Check if the command was successful
if [ $? -eq 0 ]; then
    echo "Command completed successfully"
else
    echo "Command failed with error code $?"
    exit 1
fi
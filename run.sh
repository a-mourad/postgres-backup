#!/bin/bash
# run.sh

# Make sure Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "Python 3 is required but not installed. Please install Python 3 first."
    echo "sudo apt install python3.12 python3.12-venv"
    exit 1
fi

# Function to display usage
show_usage() {
    echo "Usage: ./run.sh [command] [options]"
    echo ""
    echo "Commands:"
    echo "  backup              Run backup script"
    echo "  restore DB_NAME     Restore specific database"
    echo "  clean              Remove virtual environment and cache"
    echo "  reset              Reset virtual environment and reinstall requirements"
    echo ""
    echo "Options:"
    echo "  --host HOST        Database host"
    echo "  --port PORT        Database port"
    echo "  --user USER        Database user"
    echo "  --password PASS    Database password"
    echo ""
    echo "Examples:"
    echo "  ./run.sh backup"
    echo "  ./run.sh backup --host localhost --port 5432 --user root --password secret"
    echo "  ./run.sh restore mydb --host localhost --user root"
    echo "  ./run.sh clean"
    echo "  ./run.sh reset"
}
# Check if any command is provided
if [ $# -eq 0 ]; then
    show_usage
    exit 1
fi


# Determine the script's directory
script_dir="$(dirname "$(readlink -f "$0")")"

# Change to the script's directory
cd "$script_dir"
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
# Check if the command is backup, clean, reset, restore, or help
# Get the command
COMMAND="$1"
shift

# Process commands with their parameters
case "$COMMAND" in
    backup)
        # Extract database parameters and pass them to the Python script
        DB_PARAMS=$(get_db_params "$@")
        python3 run.py backup $DB_PARAMS
        ;;
    restore)
        if [ $# -lt 1 ]; then
            echo "Error: Database name is required for restore command"
            echo "Usage: ./run.sh restore <database_name> [options]"
            exit 1
        fi
        DB_NAME="$1"
        shift
        # Extract database parameters and pass them along with the database name
        DB_PARAMS=$(get_db_params "$@")
        python3 run.py restore "$DB_NAME" $DB_PARAMS
        ;;
    clean)
        python3 run.py clean
        ;;
    reset)
        python3 run.py reset
        ;;
    --help|-h)
        show_usage
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
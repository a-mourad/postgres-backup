#!/bin/bash
# ============================================================================
# Run Database Constraint Fix Script
# ============================================================================
#
# This script runs the SQL fix script to clean up duplicate and orphaned
# constraints in the PostgreSQL database.
#
# Usage:
#   ./run_fix_constraints.sh [host] [port] [username] [database] [password]
#
# Example:
#   ./run_fix_constraints.sh host.docker.internal 2320 odoo ruetech
#
# ============================================================================

set -e

# Default values
HOST="${1:-host.docker.internal}"
PORT="${2:-2320}"
USERNAME="${3:-odoo}"
DATABASE="${4:-ruetech}"
PASSWORD="${5:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_SCRIPT="${SCRIPT_DIR}/fix_database_constraints.sql"

if [ ! -f "$SQL_SCRIPT" ]; then
    echo "Error: SQL script not found at $SQL_SCRIPT"
    exit 1
fi

echo "============================================================================"
echo "Database Constraint Fix Script"
echo "============================================================================"
echo "Host:     $HOST"
echo "Port:     $PORT"
echo "Username: $USERNAME"
echo "Database: $DATABASE"
echo "============================================================================"
echo ""
echo "WARNING: This script will modify system catalogs!"
echo "Press Ctrl+C to cancel, or Enter to continue..."
read

if [ -n "$PASSWORD" ]; then
    export PGPASSWORD="$PASSWORD"
    psql -h "$HOST" -p "$PORT" -U "$USERNAME" -d "$DATABASE" -f "$SQL_SCRIPT"
    unset PGPASSWORD
else
    psql -h "$HOST" -p "$PORT" -U "$USERNAME" -d "$DATABASE" -f "$SQL_SCRIPT"
fi

echo ""
echo "============================================================================"
echo "Fix script completed!"
echo "============================================================================"
echo ""
echo "Next steps:"
echo "1. Run: VACUUM FULL ANALYZE; (optional but recommended)"
echo "2. Create a new backup to verify the issues are resolved"
echo ""



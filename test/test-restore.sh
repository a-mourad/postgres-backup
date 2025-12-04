#!/bin/bash
# End-to-End Test Script for PostgreSQL Backup Manager
# This script tests backup and restore functionality

set -e

echo "========================================="
echo "PostgreSQL Backup Manager - E2E Test"
echo "========================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test database connection details
DB_HOST="${DB_HOST:-test-postgres}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-testuser}"
DB_PASS="${DB_PASS:-testpass}"
DB_NAME="${DB_NAME:-testdb}"
BACKUP_DIR="/host/tmp/test-backups"

echo -e "${YELLOW}Test Configuration:${NC}"
echo "  Host: $DB_HOST"
echo "  Port: $DB_PORT"
echo "  User: $DB_USER"
echo "  Database: $DB_NAME"
echo "  Backup Directory: $BACKUP_DIR"
echo ""

# Function to check if database is accessible
check_db_connection() {
    echo -e "${YELLOW}Checking database connection...${NC}"
    docker exec test-postgres psql -U "$DB_USER" -d "$DB_NAME" -c "SELECT version();" > /dev/null 2>&1
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ Database connection successful${NC}"
        return 0
    else
        echo -e "${RED}✗ Database connection failed${NC}"
        return 1
    fi
}

# Function to create backup
test_backup() {
    echo ""
    echo -e "${YELLOW}=== Testing Backup ===${NC}"
    
    docker exec pg-backup-manager python /app/backup_script.py \
        --action backup \
        --host "$DB_HOST" \
        --port "$DB_PORT" \
        --username "$DB_USER" \
        --password "$DB_PASS" \
        --database "$DB_NAME" \
        --backup-dir "$BACKUP_DIR" \
        --storage-type local
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ Backup completed successfully${NC}"
        
        # Check if backup file exists
        BACKUP_FILE=$(find /tmp/test-backups -name "*${DB_NAME}*.sql" -type f | head -1)
        if [ -n "$BACKUP_FILE" ]; then
            echo -e "${GREEN}✓ Backup file found: $BACKUP_FILE${NC}"
            echo "$BACKUP_FILE" > /tmp/test_backup_file.txt
            return 0
        else
            echo -e "${RED}✗ Backup file not found${NC}"
            return 1
        fi
    else
        echo -e "${RED}✗ Backup failed${NC}"
        return 1
    fi
}

# Function to test restore
test_restore() {
    echo ""
    echo -e "${YELLOW}=== Testing Restore ===${NC}"
    
    RESTORE_DB_NAME="${DB_NAME}_restored"
    BACKUP_FILE=$(cat /tmp/test_backup_file.txt 2>/dev/null || echo "")
    
    if [ -z "$BACKUP_FILE" ]; then
        echo -e "${RED}✗ No backup file found for restore${NC}"
        return 1
    fi
    
    echo "Restoring to database: $RESTORE_DB_NAME"
    echo "Using backup file: $BACKUP_FILE"
    
    # Convert host path to container path
    # /tmp/test-backups -> /host/tmp/test-backups
    if [[ "$BACKUP_FILE" == /tmp/* ]]; then
        CONTAINER_BACKUP_FILE="/host$BACKUP_FILE"
    else
        CONTAINER_BACKUP_FILE="$BACKUP_FILE"
    fi
    
    docker exec pg-backup-manager python /app/backup_script.py \
        --action restore \
        --host "$DB_HOST" \
        --port "$DB_PORT" \
        --username "$DB_USER" \
        --password "$DB_PASS" \
        --database "$RESTORE_DB_NAME" \
        --backup-file "$CONTAINER_BACKUP_FILE" \
        --restore-type database \
        --drop-existing
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ Restore completed successfully${NC}"
        
        # Verify restore by checking if database exists and has data
        TABLE_COUNT=$(docker exec test-postgres psql -U "$DB_USER" -d "$RESTORE_DB_NAME" -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE';" 2>/dev/null | tr -d ' ')
        if [ -n "$TABLE_COUNT" ] && [ "$TABLE_COUNT" -gt 0 ]; then
            echo -e "${GREEN}✓ Restore verification: Database has $TABLE_COUNT table(s)${NC}"
            
            # Check for data
            USER_COUNT=$(docker exec test-postgres psql -U "$DB_USER" -d "$RESTORE_DB_NAME" -t -c "SELECT COUNT(*) FROM users;" 2>/dev/null | tr -d ' ')
            if [ -n "$USER_COUNT" ] && [ "$USER_COUNT" -gt 0 ]; then
                echo -e "${GREEN}✓ Restore verification: Found $USER_COUNT user(s) in restored database${NC}"
                return 0
            else
                echo -e "${YELLOW}⚠ Restore verification: No data found in users table${NC}"
                return 1
            fi
        else
            echo -e "${RED}✗ Restore verification failed: Database has no tables${NC}"
            return 1
        fi
    else
        echo -e "${RED}✗ Restore failed${NC}"
        return 1
    fi
}

# Main test flow
main() {
    # Check prerequisites
    if ! docker ps | grep -q "test-postgres"; then
        echo -e "${RED}✗ Test PostgreSQL container is not running${NC}"
        echo "Please start it with: docker-compose -f docker-compose.yml -f docker-compose.test.yml up -d test-postgres"
        exit 1
    fi
    
    if ! docker ps | grep -q "pg-backup-manager"; then
        echo -e "${RED}✗ Backup manager container is not running${NC}"
        echo "Please start it with: docker-compose up -d"
        exit 1
    fi
    
    # Run tests
    check_db_connection || exit 1
    test_backup || exit 1
    test_restore || exit 1
    
    echo ""
    echo -e "${GREEN}=========================================${NC}"
    echo -e "${GREEN}All tests passed! ✓${NC}"
    echo -e "${GREEN}=========================================${NC}"
}

# Run main function
main


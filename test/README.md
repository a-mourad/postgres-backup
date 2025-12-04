# End-to-End Testing Setup

This directory contains test configuration for end-to-end testing of the PostgreSQL Backup Manager.

## Quick Start

1. **Start the test environment:**
   ```bash
   docker-compose -f docker-compose.yml -f docker-compose.test.yml up -d
   ```

2. **Wait for services to be ready:**
   ```bash
   docker-compose -f docker-compose.yml -f docker-compose.test.yml ps
   ```

3. **Access the web interface:**
   - Open http://localhost:6536
   - Use the following connection details:
     - **Host:** `test-postgres` (when connecting from within Docker network)
     - **Host:** `localhost` (when connecting from host machine)
     - **Port:** `5433` (mapped from container's 5432)
     - **Username:** `testuser`
     - **Password:** `testpass`
     - **Database:** `testdb`

## Test Scenarios

### 1. Test Database Connection
- Go to the web interface
- Enter test database credentials
- Click "Test Connection"
- Should show success message

### 2. Test Backup
- Select "Backup" tab
- Enter database name: `testdb`
- Set backup directory: `/host/tmp/test-backups`
- Click "Start Backup"
- Verify backup is created

### 3. Test Restore from SQL File
- After creating a backup, go to "Restore" tab
- Select "Database Only" restore type
- Enter database name: `testdb_restored`
- Select SQL dump file from backup directory
- Check "Drop existing database"
- Click "Start Restore"
- Verify restore completes and verification passes

### 4. Test Restore from Backup Session
- Go to "Restore" tab
- Select "Database Only" restore type
- Enter database name: `testdb_restored`
- Set backup directory to where backup was created
- Leave backup file empty (uses latest)
- Check "Drop existing database"
- Click "Start Restore"
- Verify restore completes

## Clean Up

```bash
docker-compose -f docker-compose.yml -f docker-compose.test.yml down -v
```

This will remove all containers and volumes (including test database data).

## Manual Database Access

Connect to the test database directly:

```bash
# From host machine
docker exec -it test-postgres psql -U testuser -d testdb

# Or using psql from host (if installed)
psql -h localhost -p 5433 -U testuser -d testdb
```

## Test Data

The test database includes:
- `users` table with 3 sample users
- `products` table with 3 sample products
- `user_count` view
- All tables have sample data for testing restore verification


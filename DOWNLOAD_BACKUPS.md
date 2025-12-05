# How to Download Backups

This guide explains how to download backups from the PG Backup Manager tool.

## Method 1: Web Interface (Recommended)

The easiest way to download backups is through the web interface:

### Steps:

1. **Access the Web Interface**
   - Open your browser and navigate to: `http://localhost:6536` (or your configured port)
   - Log in with your credentials

2. **Navigate to the Files Tab**
   - Click on the **"Files"** tab in the left panel
   - Scroll down to the **"Available Backups"** section

3. **View Available Backups**
   - You'll see a list of backup sessions organized by timestamp
   - Each session shows:
     - Session ID (timestamp folder name)
     - Creation date
     - Available databases
     - Project files (if any)

4. **Download Options**

   **Download Individual Database:**
   - Click the download icon (⬇️) next to any database name
   - This downloads the SQL dump file for that specific database
   - File format: `{database_name}_{session_id}.sql`

   **Download Project Files:**
   - If a backup includes project files, click the download icon next to "Project Files"
   - This downloads the `files.tar.gz` archive
   - File format: `files_{session_id}.tar.gz`

   **Download Complete Backup Session:**
   - Click the **"Download All"** button at the bottom of each backup session
   - This creates a tar.gz archive containing the entire backup session
   - Includes all databases and files
   - File format: `backup_{session_id}.tar.gz`

## Method 2: Direct File Access

Backups are stored locally on your filesystem. You can access them directly:

### Default Backup Location:
```
/tmp/db-backups/
```

### Backup Structure:
```
/tmp/db-backups/
├── 2024-01-15_10-30-00/          # Session folder (timestamp)
│   ├── databases/                # Database dumps
│   │   ├── database1.sql
│   │   └── database2.sql
│   └── files.tar.gz              # Project files archive (if included)
├── 2024-01-16_14-20-00/
│   └── ...
```

### Access Files Directly:
```bash
# List all backup sessions
ls -la /tmp/db-backups/

# View databases in a specific session
ls -la /tmp/db-backups/2024-01-15_10-30-00/databases/

# Copy a database dump
cp /tmp/db-backups/2024-01-15_10-30-00/databases/mydb.sql ~/Downloads/

# Copy files archive
cp /tmp/db-backups/2024-01-15_10-30-00/files.tar.gz ~/Downloads/
```

## Method 3: API Endpoint (Programmatic Access)

You can download backups programmatically using the API endpoint:

### Endpoint:
```
GET /api/download-backup
```

### Parameters:
- `session_id` (required): The backup session ID (timestamp folder name)
- `file_type` (required): One of:
  - `"database"` - Download a specific database SQL dump
  - `"files"` - Download the files archive
  - `"all"` - Download the complete backup session
- `database_name` (required if `file_type="database"`): Name of the database
- `backup_dir` (optional): Backup directory path (default: `/tmp/db-backups`)

### Examples:

**Download a specific database:**
```bash
curl -b cookies.txt "http://localhost:6536/api/download-backup?session_id=2024-01-15_10-30-00&file_type=database&database_name=mydb&backup_dir=/tmp/db-backups" \
  -o mydb_backup.sql
```

**Download files archive:**
```bash
curl -b cookies.txt "http://localhost:6536/api/download-backup?session_id=2024-01-15_10-30-00&file_type=files&backup_dir=/tmp/db-backups" \
  -o files_backup.tar.gz
```

**Download complete backup session:**
```bash
curl -b cookies.txt "http://localhost:6536/api/download-backup?session_id=2024-01-15_10-30-00&file_type=all&backup_dir=/tmp/db-backups" \
  -o complete_backup.tar.gz
```

**Note:** You'll need to authenticate first. The API requires a valid session cookie. You can:
1. Log in through the web interface first
2. Extract the session cookie from your browser
3. Use it in the `curl` command with `-b cookies.txt`

## Method 4: Docker Container Access

If you're running the tool in Docker, you can access backups from the host:

### Backup Location in Container:
```
/host/tmp/db-backups/
```

Since the host filesystem is mounted at `/host`, backups are accessible on both:
- **Host machine**: `/tmp/db-backups/`
- **Container**: `/host/tmp/db-backups/`

### Copy from Container:
```bash
# Copy a backup from container to host
docker cp pg-backup-manager:/host/tmp/db-backups/2024-01-15_10-30-00 ~/Downloads/
```

## File Formats

### Database Dumps
- **Format**: SQL text files
- **Extension**: `.sql`
- **Content**: PostgreSQL dump in SQL format
- **Usage**: Can be restored using `psql` or the restore feature

### Files Archive
- **Format**: Compressed tar archive
- **Extension**: `.tar.gz`
- **Content**: All project files and folders
- **Usage**: Extract with `tar -xzf files.tar.gz`

### Complete Backup
- **Format**: Compressed tar archive
- **Extension**: `.tar.gz`
- **Content**: Entire backup session (databases + files)
- **Usage**: Extract with `tar -xzf backup_{session_id}.tar.gz`

## Tips

1. **Large Backups**: For large backups, the web interface will stream the download. Be patient and don't close the browser tab.

2. **Backup Directory**: You can change the backup directory in the backup configuration. The default is `/tmp/db-backups`.

3. **Session IDs**: Session IDs are timestamps in the format `YYYY-MM-DD_HH-MM-SS`, making it easy to identify when backups were created.

4. **Cloud Storage**: If backups are uploaded to cloud storage (S3, MinIO, Google Drive), you'll need to download them from the cloud storage interface separately.

5. **File Permissions**: Ensure you have read permissions on the backup directory and files.

## Troubleshooting

### "Backup session not found"
- Verify the session ID is correct
- Check that the backup directory path is correct
- Ensure the backup session exists in the specified directory

### "Permission denied"
- Check file permissions on the backup directory
- If using Docker, ensure the container has proper volume mount permissions

### Download fails or is incomplete
- Check available disk space
- Verify network connectivity (for web interface downloads)
- Try downloading individual components instead of the complete backup


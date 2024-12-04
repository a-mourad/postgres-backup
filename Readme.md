Usage Instructions


 

sudo ./run.sh --action backup \
--include-folders /path/to/folders/to/backup \
--backup-dir=/path/to/floder/to/store/backups/locally \ 
--host localhost \
--port 5432 \
--username db_username \
--password db_password \
--storage-type minio \
--storage-endpoint minio-ip:port \
--storage-access-key access_key \
--storage-secret-key secret \
--storage-bucket bucket 

Save the script as setup.sh
Make it executable:

bashCopychmod +x setup.sh
Example Usages

Backup all databases (local):

bashCopy./setup.sh --action backup

Backup specific database:

bashCopy./setup.sh --action backup --database myapp_db

Backup with S3:

bashCopy./setup.sh --action backup \
    --database myapp_db \
    --storage-type s3 \
    --storage-access-key YOUR_ACCESS_KEY \
    --storage-secret-key YOUR_SECRET_KEY \
    --storage-bucket MY_BUCKET
Features

✅ Checks Python 3 installation
✅ Verifies Python version (3.7+)
✅ Creates virtual environment
✅ Installs required dependencies
✅ Passes all arguments to backup script
✅ Colorful, informative output
✅ Comprehensive error handling

Prerequisites

Python 3.7+
python3-venv package
Sudo access for package installation (if needed)

Additional Notes

Requires the bc command for version comparison
Installs dependencies in a virtual environment
Provides detailed error messages
Supports all original backup script parameters


Usage Examples
bashCopy# Local backup (default)
python backup_script.py --action backup --database myapp_db

# S3 Backup
python backup_script.py --action backup \
    --database myapp_db \
    --storage-type s3 \
    --storage-access-key YOUR_ACCESS_KEY \
    --storage-secret-key YOUR_SECRET_KEY \
    --storage-bucket MY_BUCKET \
    --storage-region us-east-1

# MinIO Backup
python backup_script.py --action backup \
    --database myapp_db \
    --storage-type minio \
    --storage-endpoint minio.example.com:9000 \
    --storage-access-key YOUR_ACCESS_KEY \
    --storage-secret-key YOUR_SECRET_KEY \
    --storage-bucket MY_BUCKET

# Restore from backup
python backup_script.py --action restore \
    --database myapp_db
Dependencies
bashCopypip install psycopg2 boto3 minio google-cloud-storage
Security Considerations

Credentials passed directly via CLI
Optional password parameter
Comprehensive logging
Flexible storage configuration

Improvements Made

Direct credential input
Removed .env dependency
More flexible cloud storage configuration
Enhanced error handling

PostgresQl Database Backup and Restore Script
=========================

This repository provides a script for managing postgresql database backups and restores, along with other utility commands to manage the environment.

 

Environment Configuration
-------------------------

The script relies on a `.env` file to configure the database connection, backup settings, and storage providers. This file must be located in the same directory as the script.

Here is a sample `.env` file:
 
```
DB_HOST=localhost
DB_PORT=5432
DB_USER=username
DB_PASSWORD=password
BACKUP_DIR=/var/backups
KEEP_BACKUP_COUNT=5

# Minio Configuration
MINIO_ENABLED=false
MINIO_ENDPOINT=play.min.io:9000
MINIO_ACCESS_KEY=your_access_key
MINIO_SECRET_KEY=your_secret_key
MINIO_BUCKET_NAME=db-backups
MINIO_SECURE=true

# Google Drive Configuration
GDRIVE_ENABLED=false
GDRIVE_CREDENTIALS_PATH=/path/to/credentials.json
GDRIVE_FOLDER_ID=your_folder_id

# AWS S3 Configuration
S3_ENABLED=false
AWS_ACCESS_KEY_ID=your_access_key_id
AWS_SECRET_ACCESS_KEY=your_secret_access_key
S3_BUCKET_NAME=db-backups
AWS_REGION=us-east-1
 
```
### Explanation of Environment Variables

-   `DB_HOST`: Hostname or IP address of the database server (e.g., `localhost`).

-   `DB_PORT`: Port number for the database (e.g., `5432` for PostgreSQL).

-   `DB_USER`: Username to connect to the database.

-   `DB_PASSWORD`: Password for the database user.

-   `BACKUP_DIR`: Directory where backups will be stored.

-   `KEEP_BACKUP_COUNT`: Number of backup files to retain in the backup directory.

#### Minio Configuration

-   `MINIO_ENABLED`: Set to `true` to enable Minio storage.

-   `MINIO_ENDPOINT`: Minio server endpoint (e.g., `play.min.io:9000`).

-   `MINIO_ACCESS_KEY`: Minio access key.

-   `MINIO_SECRET_KEY`: Minio secret key.

-   `MINIO_BUCKET_NAME`: Name of the bucket to store backups.

-   `MINIO_SECURE`: Set to `true` to use HTTPS; otherwise, `false`.

#### Google Drive Configuration

-   `GDRIVE_ENABLED`: Set to `true` to enable Google Drive storage.

-   `GDRIVE_CREDENTIALS_PATH`: Path to the Google Drive credentials JSON file.

-   `GDRIVE_FOLDER_ID`: ID of the Google Drive folder to store backups.

#### AWS S3 Configuration

-   `S3_ENABLED`: Set to `true` to enable AWS S3 storage.

-   `AWS_ACCESS_KEY_ID`: AWS access key ID.

-   `AWS_SECRET_ACCESS_KEY`: AWS secret access key.

-   `S3_BUCKET_NAME`: Name of the S3 bucket to store backups.

-   `AWS_REGION`: AWS region (e.g., `us-east-1`).

 
* * * * *

Setup
-----

1.  Install Python and create a virtual environment:

    sudo apt install python3.12 python3.12-venv
 

    chmod +x run.py run.sh

4.  Configure the `.env` file with your database connection details and storage provider settings.

* * * * *

Usage
-----

The script can be executed using the `run.sh` file. Below are the available commands:
 
Usage: sudo ./run.sh [command] [options]
```
Commands:

  backup              Run backup script
  restore DB_NAME     Restore specific database
  clean               Remove virtual environment and cache
  reset               Reset virtual environment and reinstall requirements
  help                Show this help message

Options:
  --host HOST        Database host
  --port PORT        Database port
  --user USER        Database user
  --password PASS    Database password
  --dir              Backup Directory

Examples:
  ./run.sh backup
  ./run.sh backup --dir /backups --host localhost --port 5432 --user db_user --password secret
  ./run.sh restore mydb --dir /backups/project --host localhost --user db_user
  ./run.sh clean
  ./run.sh reset
  ./run.sh help
```
### Backup

To create a backup of the database and upload it to the configured storage providers, run:
 

./run.sh backup

### Restore

To restore a database from a backup stored in the configured storage providers, run:
 

./run.sh restore <database_name>

Replace `<database_name>` with the name of the database you want to restore.

### Clean Up

To clean up the virtual environment and cache files, run:
 

./run.sh clean

### Reset Environment

To reset everything and create a fresh environment, run:
 

./run.sh reset

### Help

To see a list of available commands and their descriptions, run:
 
./run.sh help

* * * * *

Storage Providers
-----------------

The script supports multiple storage providers for backup and restore operations. The enabled storage providers are configured in the `.env` file.

-   **Local Storage**: Backups are stored in the directory specified by `BACKUP_DIR`.

-   **Minio**: Backups are uploaded to the specified Minio bucket.

-   **Google Drive**: Backups are uploaded to the specified Google Drive folder.

-   **AWS S3**: Backups are uploaded to the specified S3 bucket.

The script automatically handles uploading backups to all enabled storage providers and can restore from any of them based on availability.

*Note*: Ensure that the correct credentials and configurations are set for each enabled storage provider.
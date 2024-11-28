```
# PostgresQl Database Backup and Restore Script

This repository provides a script for managing PostgreSQL database backups and restores, along with other utility commands to manage the environment.

## Repository Files

- `README.md`: Documentation for the repository.
- `setup_and_run.sh`: Script to set up and run the backup and restore operations.
- `run.sh`: Script to run the backup and restore operations after cloning the repository.
- `run.py`: Python script that handles the backup and restore operations.
- `requirements.txt`: List of Python dependencies.
- `.env.example`: Example configuration file for environment variables.
- `backup.py`: Python script for backup operations.
- `restore.py`: Python script for restore operations.
- `storage.py`: Python script for storage operations.

## Environment Configuration

The script can work only with a local storage driver if a `.env` file is not present. The following environment variables are mandatory in this case:

- `DB_HOST`: Hostname or IP address of the database server (e.g., `localhost`).
- `DB_PORT`: Port number for the database (e.g., `5432` for PostgreSQL).
- `DB_USER`: Username to connect to the database.
- `DB_PASSWORD`: Password for the database user.
- `BACKUP_DIR`: Directory where backups will be stored.
- `KEEP_BACKUP_COUNT`: Number of backup files to retain in the backup directory.

To configure the database connection, backup settings, and storage providers, create a `.env` file in the same directory as the scripts with the following format:

```env
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

Setup and Usage
---------------

### Running via `curl`

You can run the setup and run the script directly using `curl`:

```
sudo curl -sSL https://raw.githubusercontent.com/a-mourad/postgres-backup/main/setup_and_run.sh | bash [command] [options]

```

#### Commands:

-   `backup`: Run the backup script.
-   `restore DB_NAME`: Restore a specific database.
-   `clean`: Remove the virtual environment and cache.
-   `reset`: Reset the virtual environment and reinstall requirements.
-   `--help` or `-h`: Show this help message.

#### Examples:

```
curl https://raw.githubusercontent.com/a-mourad/postgres-backup/main/setup_and_run.sh | bash backup
curl https://raw.githubusercontent.com/a-mourad/postgres-backup/main/setup_and_run.sh | bash backup --host localhost --port 5432 --user db_user --password secret
curl https://raw.githubusercontent.com/a-mourad/postgres-backup/main/setup_and_run.sh | bash restore mydb --host localhost --user root
curl https://raw.githubusercontent.com/a-mourad/postgres-backup/main/setup_and_run.sh | bash clean
curl https://raw.githubusercontent.com/a-mourad/postgres-backup/main/setup_and_run.sh | bash reset

```

### Cloning the Repository and Using `run.sh`

Alternatively, you can clone the repository and use the `run.sh` script:

```
git clone https://github.com/a-mourad/postgres-backup.git
cd postgres-backup
chmod +x run.sh
./run.sh [command] [options]

```

#### Commands:

-   `backup`: Run the backup script.
-   `restore DB_NAME`: Restore a specific database.
-   `clean`: Remove the virtual environment and cache.
-   `reset`: Reset the virtual environment and reinstall requirements.
-   `--help` or `-h`: Show this help message.

#### Examples:

```
./run.sh backup
./run.sh backup --host localhost --port 5432 --user db_user --password secret
./run.sh restore mydb --host localhost --user root
./run.sh clean
./run.sh reset
```
import os
import subprocess
import psycopg2
import logging
import gzip
from dotenv import load_dotenv
import sys
import argparse
from datetime import datetime
from storage import StorageProvider, LocalStorage, MinioStorage, GoogleDriveStorage, S3Storage

class Restore:
    def __init__(self, args=None):
        self._load_env()
        self._parse_args(args)
        self.backup_dir = self._get_param('dir', 'BACKUP_DIR', '/var/backups')
        self.setup_logging()
        self.storage_providers = self.load_storage_providers()

    def _load_env(self):
        # Load environment variables from .env file
        env_path = os.path.join(os.path.dirname(__file__), '.env')
        load_dotenv(env_path)

    def _parse_args(self, args):
        parser = argparse.ArgumentParser(description='Postgres Database Restore Tool')
        parser.add_argument('database', help='Database name to restore')
        parser.add_argument('--host', help='Database host')
        parser.add_argument('--port', help='Database port')
        parser.add_argument('--user', help='Database user')
        parser.add_argument('--password', help='Database password')
        parser.add_argument('--backup-file', help='Specific backup file to restore')
        parser.add_argument('--dir', help='Specific backup directory')

        self.args = parser.parse_args(args)

    def _get_param(self, param_name, env_var_name, default=None):
        """Get parameter value from command line args or environment variable"""
        arg_value = getattr(self.args, param_name)
        return arg_value if arg_value is not None else os.getenv(env_var_name, default)

    def setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler('restore.log')
            ]
        )
        self.logger = logging.getLogger(__name__)

    def load_storage_providers(self):
        providers = []

        # Always include local storage
        providers.append(LocalStorage(self.backup_dir))
        # Minio
        if os.getenv('MINIO_ENABLED', 'false').lower() == 'true':
            try:
                minio_endpoint = os.getenv('MINIO_ENDPOINT')
                minio_access_key = os.getenv('MINIO_ACCESS_KEY')
                minio_secret_key = os.getenv('MINIO_SECRET_KEY')
                minio_bucket_name = os.getenv('MINIO_BUCKET_NAME')
                minio_secure = os.getenv('MINIO_SECURE', 'true').lower() == 'true'
                providers.append(MinioStorage(minio_endpoint, minio_access_key, minio_secret_key, minio_bucket_name, minio_secure))
                self.logger.info("Minio storage initialized.")
            except Exception as e:
                self.logger.error(f"Failed to initialize Minio storage: {e}")

        # Google Drive
        if os.getenv('GDRIVE_ENABLED', 'false').lower() == 'true':
            try:
                gdrive_credentials_path = os.getenv('GDRIVE_CREDENTIALS_PATH')
                gdrive_folder_id = os.getenv('GDRIVE_FOLDER_ID')
                providers.append(GoogleDriveStorage(gdrive_credentials_path, gdrive_folder_id))
                self.logger.info("Google Drive storage initialized.")
            except Exception as e:
                self.logger.error(f"Failed to initialize Google Drive storage: {e}")

        # AWS S3
        if os.getenv('S3_ENABLED', 'false').lower() == 'true':
            try:
                aws_access_key_id = os.getenv('AWS_ACCESS_KEY_ID')
                aws_secret_access_key = os.getenv('AWS_SECRET_ACCESS_KEY')
                s3_bucket_name = os.getenv('S3_BUCKET_NAME')
                aws_region = os.getenv('AWS_REGION', 'us-east-1')
                providers.append(S3Storage(aws_access_key_id, aws_secret_access_key, s3_bucket_name, aws_region))
                self.logger.info("AWS S3 storage initialized.")
            except Exception as e:
                self.logger.error(f"Failed to initialize AWS S3 storage: {e}")

        return providers

    def get_latest_backup_from_storage(self, db_name):
        latest_backup = None
        latest_timestamp = None

        for provider in self.storage_providers:
            try:
                # List backup files for the database in this provider
                # The prefix is the database name directory
                prefix = os.path.join(db_name, '')
                files = provider.list_files(prefix)
                self.logger.info(f"Files in {db_name}: {files}")

                # files is a list of (file_name, identifier) tuples
                for file_name, identifier in files:
                    # Check if the file name matches the backup file pattern
                    if file_name.endswith('.sql.gz') and file_name.startswith(db_name + '_'):
                        timestamp_str = file_name[len(db_name) + 1:-len('.sql.gz')]
                        try:
                            timestamp = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
                            self.logger.info(f"Found backup: {file_name} with timestamp {timestamp}")
                            if latest_timestamp is None or timestamp > latest_timestamp:
                                latest_timestamp = timestamp
                                latest_backup = (file_name, provider, identifier)
                        except ValueError:
                            self.logger.warning(f"Invalid timestamp in file name: {file_name}")
                    else:
                        self.logger.debug(f"Ignoring file: {file_name}")
            except Exception as e:
                self.logger.error(f"Error listing files from {provider.__class__.__name__}: {e}")

        if latest_backup:
            return latest_backup
        else:
            self.logger.error(f"No backup files found for database {db_name}")
            return None

    def download_backup(self, file_name, provider, identifier, destination_path):
        try:
            provider.download_file(identifier, destination_path)
            self.logger.info(f"Downloaded backup file {file_name} from {provider.__class__.__name__}")
            return True
        except Exception as e:
            self.logger.error(f"Error downloading backup file {file_name} from {provider.__class__.__name__}: {e}")
            return False

    def drop_database(self, db_name):
        try:
            conn = psycopg2.connect(
                host=self._get_param('host', 'DB_HOST'),
                port=self._get_param('port', 'DB_PORT'),
                user=self._get_param('user', 'DB_USER'),
                password=self._get_param('password', 'DB_PASSWORD'),
                database='postgres'
            )
            conn.autocommit = True
            cur = conn.cursor()

            # Terminate existing connections
            cur.execute(f"""
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s AND pid <> pg_backend_pid()
            """, (db_name,))

            # Drop the database
            cur.execute(f"DROP DATABASE IF EXISTS {db_name}")

            cur.close()
            conn.close()
            self.logger.info(f"Successfully dropped database {db_name}")
            return True
        except Exception as e:
            self.logger.error(f"Error dropping database {db_name}: {str(e)}")
            return False

    def create_database(self, db_name):
        try:
            conn = psycopg2.connect(
                host=self._get_param('host', 'DB_HOST'),
                port=self._get_param('port', 'DB_PORT'),
                user=self._get_param('user', 'DB_USER'),
                password=self._get_param('password', 'DB_PASSWORD'),
                database='postgres'
            )
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute(f"CREATE DATABASE {db_name}")
            cur.close()
            conn.close()
            self.logger.info(f"Successfully created database {db_name}")
            return True
        except Exception as e:
            self.logger.error(f"Error creating database {db_name}: {str(e)}")
            return False

    def restore_database(self, db_name, backup_file=None):
        if backup_file:
            # If a specific backup file is provided, use that
            if os.path.exists(backup_file):
                # Local file, use it directly
                restore_file = backup_file
            else:
                # Not found locally, try to download from storage providers
                for provider in self.storage_providers:
                    try:
                        # Download the specified backup file from the provider
                        # Assuming the backup file is in the provider's backup directory
                        provider.download_file(os.path.join(db_name, os.path.basename(backup_file)), backup_file)
                        restore_file = backup_file
                        break
                    except Exception as e:
                        self.logger.warning(f"Failed to download {backup_file} from {provider.__class__.__name__}: {e}")
                else:
                    self.logger.error(f"Backup file {backup_file} not found in any storage provider")
                    return False
        else:
            # Get the latest backup from storage providers
            latest_backup = self.get_latest_backup_from_storage(db_name)
            if not latest_backup:
                return False
            file_name, provider, identifier = latest_backup

            # Download the latest backup to a temporary location
            temp_backup_path = os.path.join(self.backup_dir, file_name)
            if not self.download_backup(file_name, provider, identifier, temp_backup_path):
                return False
            restore_file = temp_backup_path

        # Proceed with restoring the database from restore_file
        # Drop and recreate the database
        if not self.drop_database(db_name):
            return False
        if not self.create_database(db_name):
            return False

        # Restore from backup
        try:
            # Execute psql command to restore the database
            env = os.environ.copy()
            env['PGPASSWORD'] = self._get_param('password', 'DB_PASSWORD')

            with gzip.open(restore_file, 'rb') as f:
                restore_cmd = [
                    'psql',
                    '-h', self._get_param('host', 'DB_HOST'),
                    '-p', self._get_param('port', 'DB_PORT'),
                    '-U', self._get_param('user', 'DB_USER'),
                    '-d', db_name
                ]

                process = subprocess.Popen(
                    restore_cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env
                )

                stdout, stderr = process.communicate(f.read())

                if process.returncode != 0:
                    self.logger.error(f"Error restoring database: {stderr.decode()}")
                    return False

            self.logger.info(f"Successfully restored database {db_name} from {restore_file}")
            # Clean up the temporary backup file if it's not the original file
            if not backup_file:
                os.remove(restore_file)
            return True
        except Exception as e:
            self.logger.error(f"Error during database restore: {str(e)}")
            return False

def main():
    restore_tool = Restore(sys.argv[1:])

    db_name = restore_tool.args.database

    if not db_name:
        restore_tool.logger.error("Database name is required")
        sys.exit(1)

    if restore_tool.args.backup_file:
        # Restore from a specific backup file
        if not restore_tool.restore_database(db_name, restore_tool.args.backup_file):
            sys.exit(1)
    else:
        # Restore from the latest backup
        if not restore_tool.restore_database(db_name):
            sys.exit(1)

    sys.exit(0)

if __name__ == "__main__":
    main()
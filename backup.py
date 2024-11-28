import os
import subprocess
import psycopg2
from datetime import datetime
import logging
import gzip
from dotenv import load_dotenv
import sys
import argparse
from storage import StorageProvider, LocalStorage, MinioStorage, GoogleDriveStorage, S3Storage

class Backup:
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
        parser = argparse.ArgumentParser(description='Postgres Database Backup Tool')
        parser.add_argument('--host', help='Database host')
        parser.add_argument('--port', help='Database port')
        parser.add_argument('--user', help='Database user')
        parser.add_argument('--password', help='Database password')
        parser.add_argument('--dir', help='backup directory')

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
                logging.FileHandler('backup.log')
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

    def get_database_list(self):
        try:
            conn = psycopg2.connect(
                host=self._get_param('host', 'DB_HOST'),
                port=self._get_param('port', 'DB_PORT'),
                user=self._get_param('user', 'DB_USER'),
                password=self._get_param('password', 'DB_PASSWORD'),
                database='postgres'
            )
            cur = conn.cursor()
            cur.execute("SELECT datname FROM pg_database WHERE datistemplate = false;")
            databases = [db[0] for db in cur.fetchall()]
            cur.close()
            conn.close()
            return databases
        except Exception as e:
            self.logger.error(f"Error getting database list: {str(e)}")
            return []

    def backup_database(self, db_name):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_folder = os.path.join(self.backup_dir, db_name)

        if not os.path.exists(backup_folder):
            os.makedirs(backup_folder)

        backup_file = os.path.join(backup_folder, f"{db_name}_{timestamp}.sql.gz")

        try:
            # Create pg_dump command
            pg_dump_cmd = [
                'pg_dump',
                '-h', self._get_param('host', 'DB_HOST'),
                '-p', self._get_param('port', 'DB_PORT'),
                '-U', self._get_param('user', 'DB_USER'),
                '-d', db_name
            ]

            # Execute pg_dump and compress output
            env = os.environ.copy()
            env['PGPASSWORD'] = self._get_param('password', 'DB_PASSWORD')

            with gzip.open(backup_file, 'wb') as f:
                process = subprocess.Popen(
                    pg_dump_cmd,
                    stdout=subprocess.PIPE,
                    env=env
                )
                for line in process.stdout:
                    f.write(line)
                process.wait()

            if process.returncode == 0:
                self.logger.info(f"Successfully created local backup: {backup_file}")
            else:
                self.logger.error(f"Failed to create local backup for database {db_name}")
                return False

            # Upload backup to each storage provider
            for provider in self.storage_providers:
                try:
                    provider_name = provider.__class__.__name__
                    provider_destination = os.path.join(db_name, os.path.basename(backup_file))
                    provider.upload_file(backup_file, provider_destination)
                    self.logger.info(f"Uploaded backup to {provider_name}: {provider_destination}")
                except Exception as e:
                    self.logger.error(f"Error uploading backup to {provider_name}: {e}")

            return True

        except Exception as e:
            self.logger.error(f"Error backing up database {db_name}: {str(e)}")
            return False

    def cleanup_old_backups(self, db_name):
        """Keep only the specified number of most recent backups"""
        backup_folder = os.path.join(self.backup_dir, db_name)
        keep_count = int(os.getenv('KEEP_BACKUP_COUNT', 5))

        if not os.path.exists(backup_folder):
            return

        # Get list of backup files
        backups = []
        for f in os.listdir(backup_folder):
            if f.endswith('.sql.gz'):
                backup_path = os.path.join(backup_folder, f)
                backups.append((backup_path, os.path.getmtime(backup_path)))

        # Sort backups by modification time (newest first)
        backups.sort(key=lambda x: x[1], reverse=True)

        # Remove old backups
        for backup_path, _ in backups[keep_count:]:
            try:
                os.remove(backup_path)
                self.logger.info(f"Removed old backup: {backup_path}")
            except Exception as e:
                self.logger.error(f"Error removing old backup {backup_path}: {str(e)}")

def main():
    backup_tool = Backup(sys.argv[1:])  # Pass command line arguments
    databases = backup_tool.get_database_list()

    if not databases:
        backup_tool.logger.error("No databases found to backup")
        sys.exit(1)

    success = True
    for db in databases:
        if db != 'postgres':  # Skip postgres system database
            if backup_tool.backup_database(db):
                backup_tool.cleanup_old_backups(db)
            else:
                success = False

    if not success:
        sys.exit(1)

if __name__ == "__main__":
    main()
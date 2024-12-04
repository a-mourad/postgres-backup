#!/usr/bin/env python3
"""
PostgreSQL Database and File Backup/Restore Utility

Features:
- Full PostgreSQL database backup
- File and folder backup
- Cloud storage support (Local, MinIO, S3, Google Drive)
- Flexible backup and restore options
- Comprehensive logging and error handling

Dependencies:
- psycopg2
- boto3
- minio
- google-cloud-storage
"""

import os
import sys
import argparse
import logging
import subprocess
import shutil
import tarfile
from datetime import datetime
from typing import List, Optional, Dict, Union

# Cloud Storage Imports
import boto3
from minio import Minio
from google.cloud import storage


class DatabaseBackupManager:
    def __init__(
            self,
            host: str = 'localhost',
            port: int = 5432,
            username: str = 'postgres',
            password: Optional[str] = None,
            backup_dir: str = './backups',
            storage_type: str = 'local',
            storage_config: Optional[Dict[str, str]] = None
    ):
        """
        Initialize backup manager with database and storage configurations.

        Args:
            host (str): PostgreSQL server host
            port (int): PostgreSQL server port
            username (str): Database username
            password (str, optional): Database password
            backup_dir (str): Directory to store backups
            storage_type (str): Storage backend (local/s3/minio/gdrive)
            storage_config (dict): Configuration for cloud storage
        """
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.backup_dir = os.path.abspath(backup_dir)
        self.storage_type = storage_type
        self.storage_config = storage_config or {}

        # Configure logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s: %(message)s',
            handlers=[
                logging.FileHandler('backup.log'),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)

        # Initialize storage client based on type
        self.storage_client = self._initialize_storage_client()

        # Create backup directory if not exists
        os.makedirs(self.backup_dir, exist_ok=True)

    def _initialize_storage_client(self):
        """Initialize cloud storage client based on configuration."""
        try:
            if self.storage_type == 's3':
                return boto3.client('s3',
                                    aws_access_key_id=self.storage_config.get('access_key'),
                                    aws_secret_access_key=self.storage_config.get('secret_key'),
                                    region_name=self.storage_config.get('region', 'us-east-1')
                                    )
            elif self.storage_type == 'minio':
                return Minio(
                    endpoint=self.storage_config.get('endpoint', 'localhost:9000'),
                    access_key=self.storage_config.get('access_key'),
                    secret_key=self.storage_config.get('secret_key'),
                    secure=self.storage_config.get('secure', False)
                )
            elif self.storage_type == 'gdrive':
                # Note: Google Drive requires additional setup with service account
                return storage.Client.from_service_account_json(
                    self.storage_config.get('credentials_path')
                )
        except Exception as e:
            self.logger.error(f"Storage client initialization failed: {e}")
            return None

    def list_databases(self) -> List[str]:
        """
        List user-created PostgreSQL databases, excluding system databases.
        """
        try:
            cmd = [
                'psql',
                f'-h{self.host}',
                f'-p{self.port}',
                f'-U{self.username}',
                '-d', 'postgres',
                '-t',  # Tuple-only mode
                '-A',  # Unaligned output mode
                '-c',
                "SELECT datname FROM pg_database WHERE datistemplate = false AND datname NOT IN ('postgres', 'template0', 'template1');"
            ]

            self.logger.info(f"Executing command: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env={**os.environ, 'PGPASSWORD': self.password}
            )

            if result.returncode != 0:
                self.logger.error(f"Error executing psql: {result.stderr}")
                return []

            databases = [db.strip() for db in result.stdout.splitlines() if db.strip()]
            self.logger.info(f"Found databases: {databases}")
            return databases

        except Exception as e:
            self.logger.error(f"Database listing failed: {e}")
            self.logger.error(f"Command output: {getattr(e, 'output', 'No output')}")
            return []
    def backup_database(
            self,
            database: str,
            include_folders: Optional[List[str]] = None
    ) -> Dict[str, str]:
        """
        Backup a specific database and optional folders.
        """
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        db_backup_dir = os.path.join(self.backup_dir, database, timestamp)
        os.makedirs(db_backup_dir, exist_ok=True)

        try:
            # PostgreSQL Database Dump
            pg_dump_path = os.path.join(db_backup_dir, f'{database}_dump.sql')

            # Comprehensive pg_dump command
            pg_dump_cmd = [
                'pg_dump',
                f'-h{self.host}',
                f'-p{self.port}',
                f'-U{self.username}',
                '-f', pg_dump_path,
                '-v',  # Verbose output
                '--no-owner',  # Exclude owner information
                '--no-acl',  # Exclude ACL information
                database
            ]

            # Run pg_dump with password in environment
            subprocess.run(
                pg_dump_cmd,
                env={**os.environ, 'PGPASSWORD': self.password},
                check=True,
                capture_output=True,
                text=True
            )

            # Folder Backups
            if include_folders:
                for folder in include_folders:
                    if os.path.exists(folder):
                        folder_name = os.path.basename(folder)
                        folder_backup_path = os.path.join(db_backup_dir, f'{folder_name}_backup.tar.gz')
                        with tarfile.open(folder_backup_path, 'w:gz') as tar:
                            tar.add(folder, arcname=folder_name)

            # Compress entire backup
            archive_path = os.path.join(self.backup_dir, database, f'{database}_{timestamp}_backup.tar.gz')
            with tarfile.open(archive_path, 'w:gz') as tar:
                tar.add(db_backup_dir, arcname=os.path.basename(db_backup_dir))

            if self.storage_client and self.storage_type != 'local':
                self._upload_to_cloud_storage(archive_path, database)

            self.logger.info(f"Successfully backed up database {database}")
            return {
                'database_dump': pg_dump_path,
                'backup_archive': archive_path
            }

        except subprocess.CalledProcessError as e:
            self.logger.error(f"Backup for {database} failed: {e}")
            self.logger.error(f"STDOUT: {e.stdout}")
            self.logger.error(f"STDERR: {e.stderr}")
            return {}
        except Exception as e:
            self.logger.error(f"Unexpected error backing up {database}: {e}")
            return {}

    def _upload_to_cloud_storage(self, file_path: str, database: str):
        """
        Upload backup to cloud storage.

        Args:
            file_path (str): Path to the backup file
            database (str): Database name for organizing in cloud storage
        """
        try:
            if self.storage_type == 's3':
                bucket = self.storage_config.get('bucket')
                key = f'backups/{database}/{os.path.basename(file_path)}'
                self.storage_client.upload_file(file_path, bucket, key)

            elif self.storage_type == 'minio':
                bucket = self.storage_config.get('bucket')
                key = f'backups/{database}/{os.path.basename(file_path)}'
                self.storage_client.fput_object(bucket, key, file_path)

            elif self.storage_type == 'gdrive':
                # Placeholder for Google Drive upload logic
                # Requires more complex implementation
                pass

            self.logger.info(f"Uploaded {file_path} to {self.storage_type}")

        except Exception as e:
            self.logger.error(f"Cloud storage upload failed: {e}")

    def restore_database(
            self,
            database: str,
            backup_file: Optional[str] = None,
            drop_existing: bool = True
    ) -> bool:
        """
        Restore a database from a specific backup or latest backup.

        Args:
            database (str): Target database name
            backup_file (str, optional): Specific backup file to restore
            drop_existing (bool): Whether to drop existing database before restore
        """
        try:
            if not backup_file:
                # Find latest backup
                backup_dir = os.path.join(self.backup_dir, database)
                print("dir", backup_dir)
                backups = sorted([
                    os.path.join(backup_dir, d)
                    for d in os.listdir(backup_dir)
                    if os.path.isdir(os.path.join(backup_dir, d))
                ], reverse=True)
                print("backups",backups)
                if not backups:
                    raise FileNotFoundError(f"No backups found in directory {backup_dir}")
                backup_file = os.path.join(backups[0], f'{database}_dump.sql')

            if drop_existing:
                # Drop existing database
                self.logger.info(f"Dropping existing database {database}")
                drop_cmd = [
                    'psql',
                    f'-h{self.host}',
                    f'-p{self.port}',
                    f'-U{self.username}',
                    'postgres',  # Connect to postgres database to drop the target
                    '-c', f'DROP DATABASE IF EXISTS "{database}"'
                ]
                subprocess.run(
                    drop_cmd,
                    env={**os.environ, 'PGPASSWORD': self.password},
                    check=True,
                    capture_output=True
                )

                # Create fresh database
                create_cmd = [
                    'psql',
                    f'-h{self.host}',
                    f'-p{self.port}',
                    f'-U{self.username}',
                    'postgres',
                    '-c', f'CREATE DATABASE "{database}"'
                ]
                subprocess.run(
                    create_cmd,
                    env={**os.environ, 'PGPASSWORD': self.password},
                    check=True,
                    capture_output=True
                )

            # Restore database
            restore_cmd = [
                'psql',
                f'-h{self.host}',
                f'-p{self.port}',
                f'-U{self.username}',
                '-d', database,
                '-f', backup_file
            ]

            result = subprocess.run(
                restore_cmd,
                env={**os.environ, 'PGPASSWORD': self.password},
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                self.logger.error(f"Restore failed: {result.stderr}")
                return False

            self.logger.info(f"Successfully restored database {database}")
            return True

        except Exception as e:
            self.logger.error(f"Database restoration failed: {e}")
            return False


def main():
    parser = argparse.ArgumentParser(description='PostgreSQL Backup and Restore Utility')

    # Common arguments
    parser.add_argument('--action', choices=['backup', 'restore'], required=True)
    parser.add_argument('--database', help='Specific database name')
    parser.add_argument('--backup-file', help='Specific backup file for restoration')
    parser.add_argument('--include-folders', nargs='+', help='Folders to include in backup')
    parser.add_argument('--backup-dir', default='/tmp/postgres-backups', help='Folder where backups are going to be stored')

    # Database connection arguments
    parser.add_argument('--host', default='localhost', help='PostgreSQL server host')
    parser.add_argument('--port', type=int, default=5432, help='PostgreSQL server port')
    parser.add_argument('--username', default='postgres', help='Database username')
    parser.add_argument('--password', help='Database password')
    parser.add_argument('--drop-existing', action='store_true', help='Drop existing database before restore')

    # Storage arguments
    parser.add_argument('--storage-type',
                        choices=['local', 's3', 'minio', 'gdrive'],
                        default='local',
                        help='Cloud storage type'
                        )
    parser.add_argument('--storage-endpoint', help='Cloud storage endpoint (for MinIO)')
    parser.add_argument('--storage-access-key', help='Cloud storage access key')
    parser.add_argument('--storage-secret-key', help='Cloud storage secret key')
    parser.add_argument('--storage-bucket', help='Cloud storage bucket name')
    parser.add_argument('--storage-region', help='Cloud storage region (for S3)')
    parser.add_argument('--storage-credentials', help='Path to credentials file (for Google Drive)')

    args = parser.parse_args()

    # Prepare storage configuration
    storage_config = {}
    if args.storage_type != 'local':
        storage_config = {
            'endpoint': args.storage_endpoint,
            'access_key': args.storage_access_key,
            'secret_key': args.storage_secret_key,
            'bucket': args.storage_bucket,
            'region': args.storage_region,
            'credentials_path': args.storage_credentials
        }
        # Remove None values
        storage_config = {k: v for k, v in storage_config.items() if v is not None}

    backup_manager = DatabaseBackupManager(
        host=args.host,
        port=args.port,
        username=args.username,
        password=args.password,
        backup_dir=args.backup_dir,
        storage_type=args.storage_type,
        storage_config=storage_config
    )

    if args.action == 'backup':
        if args.database:
            backup_manager.backup_database(args.database, args.include_folders)
        else:
            # Backup all databases
            databases = backup_manager.list_databases()
            for db in databases:
                backup_manager.backup_database(db, args.include_folders)

    elif args.action == 'restore':
        if not args.database:
            print("Error: Database name is required for restoration.")
            sys.exit(1)

        success = backup_manager.restore_database(args.database, args.backup_file,drop_existing=args.drop_existing)
        sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()

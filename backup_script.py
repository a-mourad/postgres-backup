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
        
        # Test database connection
        self._test_database_connection()

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

    def _test_database_connection(self):
        """Test database connection before proceeding with operations."""
        try:
            test_cmd = [
                'psql',
                f'-h{self.host}',
                f'-p{self.port}',
                f'-U{self.username}',
                'postgres',
                '-c', 'SELECT version();'
            ]
            
            result = subprocess.run(
                test_cmd,
                env={**os.environ, 'PGPASSWORD': self.password},
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                self.logger.info("Database connection test successful")
            else:
                self.logger.error(f"Database connection test failed: {result.stderr}")
                raise Exception(f"Database connection failed: {result.stderr}")
                
        except subprocess.TimeoutExpired:
            self.logger.error("Database connection test timed out")
            raise Exception("Database connection timed out")
        except Exception as e:
            self.logger.error(f"Database connection test error: {e}")
            raise

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

    def restore_filestore(
            self,
            backup_file: str,
            target_filestore_path: str,
            remove_existing: bool = True
    ) -> bool:
        """
        Restore filestore from backup archive.

        Args:
            backup_file (str): Path to the backup archive
            target_filestore_path (str): Target path for filestore restoration
            remove_existing (bool): Whether to remove existing filestore before restore
        """
        try:
            # Remove existing filestore if requested
            if remove_existing and os.path.exists(target_filestore_path):
                self.logger.info(f"Removing existing filestore at {target_filestore_path}")
                shutil.rmtree(target_filestore_path)

            # Create target directory
            os.makedirs(os.path.dirname(target_filestore_path), exist_ok=True)

            # Extract filestore from backup
            with tarfile.open(backup_file, 'r:gz') as tar:
                # Find filestore backup in the archive
                filestore_members = [member for member in tar.getmembers() 
                                   if 'filestore_backup.tar.gz' in member.name]
                
                if not filestore_members:
                    self.logger.warning("No filestore backup found in archive")
                    return False

                # Extract filestore backup
                filestore_backup = tar.extractfile(filestore_members[0])
                if filestore_backup:
                    # Extract filestore to target location
                    with tarfile.open(fileobj=filestore_backup, mode='r:gz') as filestore_tar:
                        filestore_tar.extractall(os.path.dirname(target_filestore_path))
                        
                        # Move extracted filestore to target path
                        extracted_path = os.path.join(os.path.dirname(target_filestore_path), 'filestore')
                        if os.path.exists(extracted_path):
                            if os.path.exists(target_filestore_path):
                                shutil.rmtree(target_filestore_path)
                            shutil.move(extracted_path, target_filestore_path)
                            self.logger.info(f"Successfully restored filestore to {target_filestore_path}")
                            return True

            return False

        except Exception as e:
            self.logger.error(f"Filestore restoration failed: {e}")
            return False

    def restore_database(
            self,
            database: str,
            backup_file: Optional[str] = None,
            drop_existing: bool = True,
            restore_filestore: bool = False,
            filestore_path: Optional[str] = None
    ) -> bool:
        """
        Restore a database from a specific backup or latest backup.

        Args:
            database (str): Target database name
            backup_file (str, optional): Specific backup file to restore
            drop_existing (bool): Whether to drop existing database before restore
            restore_filestore (bool): Whether to restore filestore along with database
            filestore_path (str, optional): Target path for filestore restoration
        """
        try:
            if not backup_file:
                # Find latest backup
                backup_dir = os.path.join(self.backup_dir, database)
                self.logger.info(f"Looking for backups in: {backup_dir}")
                
                if not os.path.exists(backup_dir):
                    raise FileNotFoundError(f"Backup directory does not exist: {backup_dir}")
                
                # First, try to find timestamp folders (newer backup format)
                timestamp_folders = []
                for item in os.listdir(backup_dir):
                    item_path = os.path.join(backup_dir, item)
                    if os.path.isdir(item_path) and item.count('_') == 2:  # Format: YYYY-MM-DD_HH-MM-SS
                        timestamp_folders.append(item_path)
                
                if timestamp_folders:
                    # Sort by timestamp (newest first)
                    timestamp_folders.sort(reverse=True)
                    latest_folder = timestamp_folders[0]
                    backup_file = os.path.join(latest_folder, f'{database}_dump.sql')
                    self.logger.info(f"Found timestamp folder backup: {backup_file}")
                    
                    if not os.path.exists(backup_file):
                        self.logger.warning(f"Database dump not found in timestamp folder: {backup_file}")
                        # Fall back to archive files
                        backup_file = None
                
                # If no timestamp folders or dump file not found, try archive files
                if not backup_file:
                    self.logger.info("Looking for archive files...")
                    archive_files = []
                    for item in os.listdir(backup_dir):
                        if item.endswith('_backup.tar.gz'):
                            archive_files.append(os.path.join(backup_dir, item))
                    
                    if archive_files:
                        # Sort by modification time (newest first)
                        archive_files.sort(key=os.path.getmtime, reverse=True)
                        latest_archive = archive_files[0]
                        self.logger.info(f"Found latest archive: {latest_archive}")
                        
                        # Extract the database dump from the archive
                        try:
                            with tarfile.open(latest_archive, 'r:gz') as tar:
                                # Find the database dump file in the archive
                                dump_members = [member for member in tar.getmembers() 
                                              if member.name.endswith(f'{database}_dump.sql')]
                                
                                if dump_members:
                                    # Extract to a temporary location
                                    temp_dir = os.path.join(self.backup_dir, 'temp_extract')
                                    os.makedirs(temp_dir, exist_ok=True)
                                    tar.extractall(temp_dir)
                                    
                                    # Find the extracted dump file
                                    for root, dirs, files in os.walk(temp_dir):
                                        for file in files:
                                            if file == f'{database}_dump.sql':
                                                backup_file = os.path.join(root, file)
                                                self.logger.info(f"Extracted database dump: {backup_file}")
                                                break
                                        if backup_file:
                                            break
                                else:
                                    self.logger.error(f"No database dump found in archive: {latest_archive}")
                                    raise FileNotFoundError(f"No database dump found in archive: {latest_archive}")
                        except Exception as e:
                            self.logger.error(f"Failed to extract database dump from archive: {e}")
                            raise
                    else:
                        raise FileNotFoundError(f"No backups found in directory {backup_dir}")
                
                if not backup_file or not os.path.exists(backup_file):
                    raise FileNotFoundError(f"Database dump file not found: {backup_file}")
                
                self.logger.info(f"Using backup file: {backup_file}")

            if drop_existing:
                # Drop existing database
                self.logger.info(f"Attempting to drop existing database {database}")
                drop_cmd = [
                    'psql',
                    f'-h{self.host}',
                    f'-p{self.port}',
                    f'-U{self.username}',
                    'postgres',  # Connect to postgres database to drop the target
                    '-c', f'DROP DATABASE IF EXISTS "{database}"'
                ]
                
                try:
                    result = subprocess.run(
                        drop_cmd,
                        env={**os.environ, 'PGPASSWORD': self.password},
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    
                    if result.returncode == 0:
                        self.logger.info(f"Successfully dropped database {database}")
                    else:
                        self.logger.warning(f"Database drop command returned non-zero exit code: {result.returncode}")
                        self.logger.warning(f"STDERR: {result.stderr}")
                        
                        # Check if database is being accessed by other users
                        if "is being accessed by other users" in result.stderr:
                            self.logger.warning("Database is being accessed by other users. Attempting to terminate connections...")
                            
                            # Terminate all connections to the database
                            terminate_cmd = [
                                'psql',
                                f'-h{self.host}',
                                f'-p{self.port}',
                                f'-U{self.username}',
                                'postgres',
                                '-c', f"""
                                SELECT pg_terminate_backend(pid) 
                                FROM pg_stat_activity 
                                WHERE datname = '{database}' AND pid <> pg_backend_pid();
                                """
                            ]
                            
                            try:
                                terminate_result = subprocess.run(
                                    terminate_cmd,
                                    env={**os.environ, 'PGPASSWORD': self.password},
                                    capture_output=True,
                                    text=True,
                                    timeout=30
                                )
                                
                                if terminate_result.returncode == 0:
                                    self.logger.info("Terminated existing connections to database")
                                    
                                    # Try to drop database again
                                    drop_result = subprocess.run(
                                        drop_cmd,
                                        env={**os.environ, 'PGPASSWORD': self.password},
                                        capture_output=True,
                                        text=True,
                                        timeout=30
                                    )
                                    
                                    if drop_result.returncode == 0:
                                        self.logger.info(f"Successfully dropped database {database} after terminating connections")
                                    else:
                                        self.logger.warning(f"Database drop still failed after terminating connections: {drop_result.stderr}")
                                        self.logger.info("Continuing with restore...")
                                else:
                                    self.logger.warning(f"Failed to terminate connections: {terminate_result.stderr}")
                                    self.logger.info("Continuing with restore...")
                                    
                            except Exception as e:
                                self.logger.warning(f"Error terminating connections: {e}")
                                self.logger.info("Continuing with restore...")
                        else:
                            # Continue with restore even if drop failed (database might not exist)
                            self.logger.info("Continuing with restore...")
                        
                except subprocess.TimeoutExpired:
                    self.logger.error("Database drop command timed out")
                    raise
                except Exception as e:
                    self.logger.error(f"Error dropping database: {e}")
                    # Continue with restore even if drop failed
                    self.logger.info("Continuing with restore...")

                # Check if database already exists
                check_cmd = [
                    'psql',
                    f'-h{self.host}',
                    f'-p{self.port}',
                    f'-U{self.username}',
                    'postgres',
                    '-t', '-c', f"SELECT 1 FROM pg_database WHERE datname = '{database}';"
                ]
                
                try:
                    check_result = subprocess.run(
                        check_cmd,
                        env={**os.environ, 'PGPASSWORD': self.password},
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    
                    database_exists = check_result.stdout.strip() == '1'
                    
                    if database_exists:
                        self.logger.info(f"Database {database} already exists, skipping creation")
                    else:
                        # Create fresh database
                        self.logger.info(f"Creating fresh database {database}")
                        create_cmd = [
                            'psql',
                            f'-h{self.host}',
                            f'-p{self.port}',
                            f'-U{self.username}',
                            'postgres',
                            '-c', f'CREATE DATABASE "{database}"'
                        ]
                        
                        try:
                            result = subprocess.run(
                                create_cmd,
                                env={**os.environ, 'PGPASSWORD': self.password},
                                capture_output=True,
                                text=True,
                                timeout=30
                            )
                            
                            if result.returncode == 0:
                                self.logger.info(f"Successfully created database {database}")
                            else:
                                self.logger.error(f"Database creation failed with exit code: {result.returncode}")
                                self.logger.error(f"STDERR: {result.stderr}")
                                raise Exception(f"Failed to create database: {result.stderr}")
                                
                        except subprocess.TimeoutExpired:
                            self.logger.error("Database creation command timed out")
                            raise
                        except Exception as e:
                            self.logger.error(f"Error creating database: {e}")
                            raise
                            
                except Exception as e:
                    self.logger.error(f"Error checking database existence: {e}")
                    raise

            # Restore database
            self.logger.info(f"Starting database restore from: {backup_file}")
            restore_cmd = [
                'psql',
                f'-h{self.host}',
                f'-p{self.port}',
                f'-U{self.username}',
                '-d', database,
                '-f', backup_file
            ]

            try:
                result = subprocess.run(
                    restore_cmd,
                    env={**os.environ, 'PGPASSWORD': self.password},
                    capture_output=True,
                    text=True,
                    timeout=3600  # 1 hour timeout for restore (increased for large databases)
                )

                if result.returncode == 0:
                    self.logger.info(f"Successfully restored database {database}")
                else:
                    self.logger.error(f"Database restore failed with exit code: {result.returncode}")
                    self.logger.error(f"STDERR: {result.stderr}")
                    self.logger.error(f"STDOUT: {result.stdout}")
                    return False
                    
            except subprocess.TimeoutExpired:
                self.logger.error("Database restore command timed out")
                return False
            except Exception as e:
                self.logger.error(f"Error during database restore: {e}")
                return False

            # Restore filestore if requested
            if restore_filestore:
                # Auto-detect filestore path if not provided
                if not filestore_path:
                    # Try to detect from backup archive
                    backup_archive = None
                    if backup_file:
                        backup_dir = os.path.dirname(backup_file)
                        for file in os.listdir(backup_dir):
                            if file.endswith('_backup.tar.gz'):
                                backup_archive = os.path.join(backup_dir, file)
                                break
                    else:
                        backup_dir = os.path.join(self.backup_dir, database)
                        if os.path.exists(backup_dir):
                            archives = [f for f in os.listdir(backup_dir) if f.endswith('_backup.tar.gz')]
                            if archives:
                                backup_archive = os.path.join(backup_dir, sorted(archives)[-1])
                    
                    if backup_archive:
                        # Try to extract and detect filestore path from backup
                        try:
                            with tarfile.open(backup_archive, 'r:gz') as tar:
                                # Look for filestore backup in the archive
                                filestore_members = [member for member in tar.getmembers() 
                                                   if 'filestore_backup.tar.gz' in member.name]
                                if filestore_members:
                                    # Use the provided filestore_path or default
                                    if not filestore_path:
                                        filestore_path = f"/home/ubuntu/projects/{database}/filestore"
                                    self.logger.info(f"Using filestore path: {filestore_path}")
                        except Exception as e:
                            self.logger.warning(f"Could not detect filestore in backup: {e}")
                
                if filestore_path:
                    # Find the backup archive for filestore restoration
                    backup_archive = None
                    if backup_file:
                        # If specific backup file provided, look for the archive
                        backup_dir = os.path.dirname(backup_file)
                        for file in os.listdir(backup_dir):
                            if file.endswith('_backup.tar.gz'):
                                backup_archive = os.path.join(backup_dir, file)
                                break
                    else:
                        # Find latest backup archive
                        backup_dir = os.path.join(self.backup_dir, database)
                        if os.path.exists(backup_dir):
                            archives = [f for f in os.listdir(backup_dir) if f.endswith('_backup.tar.gz')]
                            if archives:
                                backup_archive = os.path.join(backup_dir, sorted(archives)[-1])

                    if backup_archive and os.path.exists(backup_archive):
                        self.logger.info(f"Restoring filestore from {backup_archive}")
                        filestore_success = self.restore_filestore(backup_archive, filestore_path)
                        if not filestore_success:
                            self.logger.warning("Filestore restoration failed, but database was restored successfully")
                    else:
                        self.logger.warning("No backup archive found for filestore restoration")
                else:
                    self.logger.warning("No filestore path specified and could not auto-detect")

            return True

        except Exception as e:
            self.logger.error(f"Database restoration failed: {e}")
            return False
        finally:
            # Cleanup temporary extraction directory
            temp_dir = os.path.join(self.backup_dir, 'temp_extract')
            if os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                    self.logger.info("Cleaned up temporary extraction directory")
                except Exception as e:
                    self.logger.warning(f"Failed to cleanup temporary directory: {e}")


def main():
    parser = argparse.ArgumentParser(description='PostgreSQL Backup and Restore Utility')

    # Common arguments
    parser.add_argument('--action', choices=['backup', 'restore'], required=True)
    parser.add_argument('--database', help='Specific database name')
    parser.add_argument('--backup-file', help='Specific backup file for restoration')
    parser.add_argument('--include-folders', nargs='+', help='Folders to include in backup')
    parser.add_argument('--backup-dir', default='/tmp/postgres-backups', help='Folder where backups are going to be stored')
    parser.add_argument('--skip-filestore', action='store_true', help='Skip filestore restoration (restore database only)')
    parser.add_argument('--project-path', help='Project root path where filestore will be restored (defaults to /home/ubuntu/projects/{database})')
    parser.add_argument('--filestore-path', help='Custom filestore path (overrides project-path)')

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

        # For restore operations, we only need local storage
        # Cloud storage parameters are ignored during restore
        restore_manager = DatabaseBackupManager(
            host=args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            backup_dir=args.backup_dir,
            storage_type='local',  # Force local storage for restore
            storage_config={}
        )

        # Determine filestore path
        filestore_path = args.filestore_path
        if not filestore_path and not args.skip_filestore:
            # Use project-path if provided, otherwise default to /home/ubuntu/projects/{database}
            if args.project_path:
                filestore_path = os.path.join(args.project_path, 'filestore')
            else:
                filestore_path = f"/home/ubuntu/projects/{args.database}/filestore"

        success = restore_manager.restore_database(
            args.database, 
            args.backup_file,
            drop_existing=args.drop_existing,
            restore_filestore=not args.skip_filestore,  # Default to True unless --skip-filestore is passed
            filestore_path=filestore_path
        )
        sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()

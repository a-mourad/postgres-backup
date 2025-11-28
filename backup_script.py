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
import time
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
        
        # Test database connection and detect PostgreSQL tools
        # Only test connection if not in full project restore mode (where DB might be down)
        # We will check this later in the restore logic if needed
        self._detect_pg_tools()
    
    def _detect_pg_tools(self):
        """Detect and configure PostgreSQL client tools matching server version."""
        try:
            # First, find any available psql to query server version
            temp_psql = self._find_any_pg_tool('psql')
            
            # Get server version - try/except because server might be down during restore init
            try:
                version_cmd = [
                    temp_psql,
                    f'-h{self.host}',
                    f'-p{self.port}',
                    f'-U{self.username}',
                    '-t', '-A',
                    '-c', 'SHOW server_version;'
                ]
                
                result = subprocess.run(
                    version_cmd,
                    env={**os.environ, 'PGPASSWORD': self.password} if self.password else os.environ,
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                
                if result.returncode == 0:
                    version_str = result.stdout.strip()
                    # Handle versions like "18.0", "14.5", "16.1 (Debian...)"
                    self.server_version = int(version_str.split('.')[0].split()[0])
                    self.logger.info(f"PostgreSQL server major version: {self.server_version}")
                    
                    # Find tools that are >= server version
                    self.pg_dump_path = self._find_compatible_pg_tool('pg_dump', self.server_version)
                    self.psql_path = self._find_compatible_pg_tool('psql', self.server_version)
                    
                    self.logger.info(f"Using pg_dump: {self.pg_dump_path}")
                    self.logger.info(f"Using psql: {self.psql_path}")
                else:
                    # DB might be down, fallback to defaults
                    self.logger.warning(f"Could not get server version (DB might be down): {result.stderr}")
                    self.server_version = None
                    self.pg_dump_path = self._find_any_pg_tool('pg_dump')
                    self.psql_path = temp_psql
            except Exception as e:
                self.logger.warning(f"Connection failed during tool detection: {e}")
                self.server_version = None
                self.pg_dump_path = self._find_any_pg_tool('pg_dump')
                self.psql_path = temp_psql
                
        except Exception as e:
            self.logger.warning(f"Could not detect PostgreSQL tools: {e}")
            self.server_version = None
            self.pg_dump_path = 'pg_dump'
            self.psql_path = 'psql'
    
    def _get_installed_pg_versions(self) -> List[int]:
        """Discover all installed PostgreSQL versions on the system."""
        versions = set()
        
        # Check common PostgreSQL installation directories
        pg_dirs = [
            '/usr/lib/postgresql',      # Debian/Ubuntu
            '/usr/pgsql-',              # RHEL/CentOS (prefix)
            '/opt/postgresql',          # Custom installs
            '/usr/local/pgsql',         # Source installs
            '/Library/PostgreSQL',      # macOS
        ]
        
        # Check Debian/Ubuntu style: /usr/lib/postgresql/{version}/
        if os.path.isdir('/usr/lib/postgresql'):
            for item in os.listdir('/usr/lib/postgresql'):
                try:
                    versions.add(int(item))
                except ValueError:
                    pass
        
        # Check RHEL/CentOS style: /usr/pgsql-{version}/
        for item in os.listdir('/usr') if os.path.isdir('/usr') else []:
            if item.startswith('pgsql-'):
                try:
                    versions.add(int(item.split('-')[1]))
                except (ValueError, IndexError):
                    pass
        
        # Check macOS style: /Library/PostgreSQL/{version}/
        if os.path.isdir('/Library/PostgreSQL'):
            for item in os.listdir('/Library/PostgreSQL'):
                try:
                    versions.add(int(item))
                except ValueError:
                    pass
        
        # Also check what's in PATH
        for tool in ['pg_dump', 'psql']:
            path = shutil.which(tool)
            if path:
                try:
                    result = subprocess.run([path, '--version'], capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        # Parse version from output like "pg_dump (PostgreSQL) 14.5"
                        version_str = result.stdout.strip()
                        import re
                        match = re.search(r'(\d+)\.', version_str)
                        if match:
                            versions.add(int(match.group(1)))
                except:
                    pass
        
        return sorted(versions, reverse=True)  # Return newest first
    
    def _get_tool_version(self, tool_path: str) -> Optional[int]:
        """Get the major version of a PostgreSQL tool."""
        try:
            result = subprocess.run([tool_path, '--version'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                import re
                match = re.search(r'(\d+)\.', result.stdout)
                if match:
                    return int(match.group(1))
        except:
            pass
        return None
    
    def _find_compatible_pg_tool(self, tool_name: str, min_version: int) -> str:
        """Find PostgreSQL tool with version >= min_version."""
        candidates = []
        
        # Build list of potential paths
        installed_versions = self._get_installed_pg_versions()
        
        for version in installed_versions:
            if version >= min_version:
                # Add version-specific paths
                paths = [
                    f'/usr/lib/postgresql/{version}/bin/{tool_name}',
                    f'/usr/pgsql-{version}/bin/{tool_name}',
                    f'/opt/postgresql-{version}/bin/{tool_name}',
                    f'/usr/local/pgsql-{version}/bin/{tool_name}',
                    f'/Library/PostgreSQL/{version}/bin/{tool_name}',
                ]
                for path in paths:
                    if os.path.isfile(path) and os.access(path, os.X_OK):
                        tool_version = self._get_tool_version(path)
                        if tool_version and tool_version >= min_version:
                            candidates.append((tool_version, path))
        
        # Also check PATH
        path_tool = shutil.which(tool_name)
        if path_tool:
            tool_version = self._get_tool_version(path_tool)
            if tool_version and tool_version >= min_version:
                candidates.append((tool_version, path_tool))
        
        # Sort by version (prefer exact match, then newer versions)
        if candidates:
            # Prefer exact version match, then closest higher version
            candidates.sort(key=lambda x: (x[0] != min_version, x[0]))
            selected = candidates[0][1]
            self.logger.info(f"Found {tool_name} v{candidates[0][0]} at {selected}")
            return selected
        
        # No compatible version found - warn user
        self.logger.error(f"No {tool_name} version >= {min_version} found!")
        self.logger.error(f"Server is PostgreSQL {min_version}, but no compatible client tools installed.")
        self.logger.error(f"Install PostgreSQL {min_version} client: sudo apt install postgresql-client-{min_version}")
        
        # Return default anyway, it will fail with a clear error
        return tool_name
    
    def _find_any_pg_tool(self, tool_name: str) -> str:
        """Find any available PostgreSQL tool (for initial connection)."""
        # Check PATH first
        path_tool = shutil.which(tool_name)
        if path_tool:
            return path_tool
        
        # Check common installation directories
        installed_versions = self._get_installed_pg_versions()
        for version in installed_versions:
            paths = [
                f'/usr/lib/postgresql/{version}/bin/{tool_name}',
                f'/usr/pgsql-{version}/bin/{tool_name}',
                f'/Library/PostgreSQL/{version}/bin/{tool_name}',
            ]
            for path in paths:
                if os.path.isfile(path) and os.access(path, os.X_OK):
                    return path
        
        return tool_name

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
            # Use any available psql for initial connection test
            psql_tool = self._find_any_pg_tool('psql') if not hasattr(self, 'psql_path') else self.psql_path
            test_cmd = [
                psql_tool,
                f'-h{self.host}',
                f'-p{self.port}',
                f'-U{self.username}',
                'postgres',
                '-c', 'SELECT version();'
            ]
            
            result = subprocess.run(
                test_cmd,
                env={**os.environ, 'PGPASSWORD': self.password} if self.password else os.environ,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                self.logger.info("Database connection test successful")
            else:
                self.logger.error(f"Database connection test failed: {result.stderr}")
                # Don't raise exception here to allow full project restore where DB might be down
                
        except subprocess.TimeoutExpired:
            self.logger.error("Database connection test timed out")
        except Exception as e:
            self.logger.error(f"Database connection test error: {e}")

    def list_databases(self, include_system: bool = True) -> List[str]:
        """
        List PostgreSQL databases.
        
        Args:
            include_system: If True, include all databases. If False, exclude system databases.
        """
        try:
            if include_system:
                query = "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname;"
            else:
                query = "SELECT datname FROM pg_database WHERE datistemplate = false AND datname NOT IN ('postgres', 'template0', 'template1') ORDER BY datname;"
            
            cmd = [
                self.psql_path,
                f'-h{self.host}',
                f'-p{self.port}',
                f'-U{self.username}',
                '-d', 'postgres',
                '-t',  # Tuple-only mode
                '-A',  # Unaligned output mode
                '-c',
                query
            ]

            self.logger.info(f"Executing command: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env={**os.environ, 'PGPASSWORD': self.password} if self.password else os.environ
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
            include_folders: Optional[List[str]] = None,
            project_path: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Backup a specific database and optional folders or entire project.
        
        Args:
            database (str): Database name to backup
            include_folders (List[str], optional): Specific folders to backup (legacy mode)
            project_path (str, optional): Full project path to backup (all folders inside)
        """
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        db_backup_dir = os.path.join(self.backup_dir, database, timestamp)
        os.makedirs(db_backup_dir, exist_ok=True)
        
        # Set directory permissions to be accessible on host (if running in container)
        # Try to get the UID/GID from environment or use 1000:1000 (common default)
        try:
            import pwd
            host_uid = int(os.environ.get('HOST_UID', '1000'))
            host_gid = int(os.environ.get('HOST_GID', '1000'))
            os.chown(db_backup_dir, host_uid, host_gid)
            # Also fix parent directories
            parent_dir = os.path.dirname(db_backup_dir)
            if os.path.exists(parent_dir):
                os.chown(parent_dir, host_uid, host_gid)
        except (OSError, ValueError, ImportError):
            # If chown fails (not root or not available), continue anyway
            pass

        try:
            # PostgreSQL Database Dump
            pg_dump_file = os.path.join(db_backup_dir, f'{database}_dump.sql')

            # Comprehensive pg_dump command
            pg_dump_cmd = [
                self.pg_dump_path,
                f'-h{self.host}',
                f'-p{self.port}',
                f'-U{self.username}',
                '-f', pg_dump_file,
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

            # Project Backup (entire project folder)
            if project_path:
                if os.path.exists(project_path):
                    self.logger.info(f"Backing up entire project from: {project_path}")
                    project_name = os.path.basename(project_path.rstrip('/'))
                    project_backup_path = os.path.join(db_backup_dir, f'{project_name}_backup.tar.gz')
                    
                    # Create tar.gz of entire project
                    with tarfile.open(project_backup_path, 'w:gz') as tar:
                        tar.add(project_path, arcname=project_name, filter=lambda tarinfo: None if '__pycache__' in tarinfo.name or '.pyc' in tarinfo.name else tarinfo)
                    self.logger.info(f"Successfully backed up project to: {project_backup_path}")
                else:
                    self.logger.warning(f"Project path does not exist: {project_path}")
            # Legacy: Folder Backups (specific folders)
            elif include_folders:
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
            
            # Set file ownership to be accessible on host (if running in container)
            try:
                host_uid = int(os.environ.get('HOST_UID', '1000'))
                host_gid = int(os.environ.get('HOST_GID', '1000'))
                os.chown(archive_path, host_uid, host_gid)
            except (OSError, ValueError):
                # If chown fails, continue anyway
                pass

            if self.storage_client and self.storage_type != 'local':
                self._upload_to_cloud_storage(archive_path, database)

            self.logger.info(f"Successfully backed up database {database}")
            return {
                'database_dump': pg_dump_file,
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

            # Check if backup_file is an archive and extract it if needed
            if backup_file.endswith('.tar.gz'):
                self.logger.info(f"Backup file is an archive, extracting...")
                try:
                    with tarfile.open(backup_file, 'r:gz') as tar:
                        # Find the database dump file in the archive
                        dump_members = [member for member in tar.getmembers() 
                                      if member.name.endswith(f'{database}_dump.sql')]
                        
                        if dump_members:
                            # Extract to a temporary location
                            temp_dir = os.path.join(self.backup_dir, 'temp_extract')
                            os.makedirs(temp_dir, exist_ok=True)
                            tar.extractall(temp_dir)
                            
                            # Find the extracted dump file
                            extracted_file = None
                            for root, dirs, files in os.walk(temp_dir):
                                for file in files:
                                    if file == f'{database}_dump.sql':
                                        extracted_file = os.path.join(root, file)
                                        break
                                if extracted_file:
                                    break
                            
                            if extracted_file and os.path.exists(extracted_file):
                                backup_file = extracted_file
                                self.logger.info(f"Extracted database dump: {backup_file}")
                            else:
                                raise FileNotFoundError(f"Database dump not found in archive: {backup_file}")
                        else:
                            raise FileNotFoundError(f"No database dump found in archive: {backup_file}")
                except Exception as e:
                    self.logger.error(f"Failed to extract database dump from archive: {e}")
                    raise

            if drop_existing:
                # Drop existing database
                self.logger.info(f"Attempting to drop existing database {database}")
                drop_cmd = [
                    self.psql_path,
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
                                self.psql_path,
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
                    self.psql_path,
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
                            self.psql_path,
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
            
            # Filter out problematic Odoo-specific parameters that PostgreSQL doesn't recognize
            # Create a temporary filtered SQL file if needed
            filtered_backup_file = backup_file
            temp_filtered_file_path = None
            try:
                # Check if file contains transaction_timeout (common Odoo parameter)
                with open(backup_file, 'rb') as f:
                    first_chunk = f.read(8192)
                    if b'transaction_timeout' in first_chunk:
                        self.logger.info("Filtering out unrecognized PostgreSQL parameters from SQL dump...")
                        import tempfile
                        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.sql', delete=False)
                        temp_filtered_file_path = temp_file.name
                        temp_file.close()
                        
                        # Filter out lines with transaction_timeout
                        with open(backup_file, 'rb') as infile, open(temp_filtered_file_path, 'wb') as outfile:
                            for line in infile:
                                if b'transaction_timeout' not in line.lower():
                                    outfile.write(line)
                        
                        filtered_backup_file = temp_filtered_file_path
                        self.logger.info(f"Created filtered SQL file: {filtered_backup_file}")
            except Exception as e:
                self.logger.warning(f"Could not filter SQL file, using original: {e}")
                filtered_backup_file = backup_file
            
            restore_cmd = [
                self.psql_path,
                f'-h{self.host}',
                f'-p{self.port}',
                f'-U{self.username}',
                '-d', database,
                '-f', filtered_backup_file
            ]

            try:
                result = subprocess.run(
                    restore_cmd,
                    env={**os.environ, 'PGPASSWORD': self.password},
                    capture_output=True,
                    text=True,
                    errors='replace',  # Handle non-UTF-8 characters gracefully
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
            finally:
                # Clean up temporary filtered SQL file if created
                if temp_filtered_file_path and os.path.exists(temp_filtered_file_path):
                    try:
                        os.unlink(temp_filtered_file_path)
                        self.logger.debug(f"Cleaned up temporary filtered SQL file")
                    except Exception as e:
                        self.logger.warning(f"Could not clean up temporary file {temp_filtered_file_path}: {e}")

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

    def _run_host_command(self, cmd: Union[str, List[str]], cwd: Optional[str] = None) -> bool:
        """
        Execute command on the host via Docker.
        """
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        
        self.logger.info(f"Running command on host: {cmd}")
        
        try:
            # Note: We assume the container has docker CLI installed and socket mounted
            # If we need to change directory, we chain it: cd path && command
            if cwd:
                # Convert container path /host/... to host path /...
                # Assuming /host maps to / on the host
                if cwd.startswith('/host'):
                    host_cwd = cwd[5:]
                else:
                    host_cwd = cwd
                
                full_cmd = f"cd {host_cwd} && {cmd}"
            else:
                full_cmd = cmd
            
            # Use 'docker run' to execute? No, we want to execute on the HOST.
            # But from inside a container, we can't easily execute directly on the host shell 
            # unless we use something like 'nsenter' (privileged) or just manage docker resources via socket.
            # 
            # The requirement is "run docker commands... inside docker container will be responsible".
            # If we mount the socket, 'docker' command inside container talks to host daemon.
            # So 'docker compose up' works on the host daemon.
            # But the 'docker compose' command needs to know where the files are ON THE HOST.
            #
            # The container sees files at /host/path/to/project
            # The host sees files at /path/to/project
            
            # So when we run 'docker compose -f ...', we must provide the HOST path.
            
            process = subprocess.run(
                full_cmd,
                shell=True,
                capture_output=True,
                text=True
            )
            
            if process.returncode != 0:
                self.logger.error(f"Command failed: {process.stderr}")
                return False
            
            self.logger.info(f"Command output: {process.stdout}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error executing host command: {e}")
            return False

    def restore_project(
            self,
            database: str,
            target_project_path: str,
            backup_file: Optional[str] = None,
            drop_existing: bool = True,
            restore_database: bool = True,
            post_restore_commands: Optional[List[str]] = None
    ) -> bool:
        """
        Restore full project (database + all project files).
        """
        try:
            # 1. Locate backup file
            if not backup_file:
                backup_dir = os.path.join(self.backup_dir, database)
                if not os.path.exists(backup_dir):
                    raise FileNotFoundError(f"Backup directory not found: {backup_dir}")
                
                archives = [os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if f.endswith('_backup.tar.gz')]
                if not archives:
                    raise FileNotFoundError("No backup archives found")
                
                archives.sort(key=os.path.getmtime, reverse=True)
                backup_file = archives[0]
                self.logger.info(f"Using latest backup: {backup_file}")

            # 2. Clear target directory
            self.logger.info(f"Restoring project to: {target_project_path}")
            if os.path.exists(target_project_path):
                # Only clear if it's not empty? Or always clear? User said "removed and replaced"
                if os.listdir(target_project_path):
                    self.logger.info("Cleaning target directory...")
                    # Be careful with rm -rf
                    for item in os.listdir(target_project_path):
                        item_path = os.path.join(target_project_path, item)
                        if os.path.isfile(item_path) or os.path.islink(item_path):
                            os.unlink(item_path)
                        elif os.path.isdir(item_path):
                            shutil.rmtree(item_path)
            else:
                os.makedirs(target_project_path, exist_ok=True)

            # 3. Extract Backup
            # Extract to temp first to find project files and db dump
            temp_extract = os.path.join(self.backup_dir, 'temp_restore_extract')
            if os.path.exists(temp_extract):
                shutil.rmtree(temp_extract)
            os.makedirs(temp_extract, exist_ok=True)

            self.logger.info(f"Extracting archive: {backup_file}")
            with tarfile.open(backup_file, 'r:gz') as tar:
                tar.extractall(temp_extract)

            # Locate inner files
            project_tar = None
            db_dump = None
            
            # The structure is usually:
            #   temp_extract/
            #     timestamp_dir/
            #       {project}_backup.tar.gz
            #       {db}_dump.sql
            
            for root, dirs, files in os.walk(temp_extract):
                for f in files:
                    if f.endswith('_backup.tar.gz') and f != os.path.basename(backup_file):
                        project_tar = os.path.join(root, f)
                    elif f.endswith('_dump.sql'):
                        db_dump = os.path.join(root, f)

            if not project_tar:
                self.logger.error("Project backup file not found inside archive")
                return False

            # Extract project files to target
            self.logger.info("Extracting project files...")
            with tarfile.open(project_tar, 'r:gz') as tar:
                # The tar might contain a top-level folder (e.g. 'doob'). 
                # We want the contents of that folder to go into target_project_path?
                # Or does target_project_path represent the parent?
                # Based on standard behavior, tar usually includes the directory.
                # Let's extract to target_project_path.
                
                # Check first member to see if it's a directory
                first = tar.next()
                tar.seek(0) # Reset
                
                # We'll extract all to target path.
                # If the tar has a root folder 'doob/', and target is '/.../doob', 
                # we might get '/.../doob/doob'. 
                # Strategy: Extract to temp, then move contents to target.
                
                temp_proj = os.path.join(temp_extract, 'project_files')
                os.makedirs(temp_proj, exist_ok=True)
                tar.extractall(temp_proj)
                
                # Move contents
                # If temp_proj contains a single directory matching project name, move its contents
                items = os.listdir(temp_proj)
                if len(items) == 1 and os.path.isdir(os.path.join(temp_proj, items[0])):
                    src_dir = os.path.join(temp_proj, items[0])
                    for item in os.listdir(src_dir):
                        shutil.move(os.path.join(src_dir, item), target_project_path)
                else:
                    # Move all items directly
                    for item in items:
                        shutil.move(os.path.join(temp_proj, item), target_project_path)

            # 4. Docker Operations
            # Convert container path to host path for docker commands
            # Container: /host/home/user/doob -> Host: /home/user/doob
            if target_project_path.startswith('/host'):
                host_target_path = target_project_path[5:]
            else:
                host_target_path = target_project_path

            self.logger.info("Building Docker services...")
            if not self._run_host_command("docker compose build", cwd=target_project_path):
                return False

            self.logger.info("Starting database service...")
            if not self._run_host_command("docker compose up db -d", cwd=target_project_path):
                return False

            # 5. Read .env and Restore Database
            env_file = os.path.join(target_project_path, '.env')
            if not os.path.exists(env_file):
                self.logger.error(f".env file not found at {env_file}")
                return False

            self.logger.info("Reading database configuration from .env...")
            db_config = {}
            with open(env_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if '=' in line:
                        key, val = line.split('=', 1)
                        db_config[key.strip()] = val.strip()

            # Set connection details for restore
            self.host = 'host.docker.internal' # Always use this from inside container
            self.port = int(db_config.get('DB_PORT', 5432))
            self.username = db_config.get('DB_USER', 'postgres')
            self.password = db_config.get('DB_PASSWORD')
            
            # Wait for DB to be ready
            self.logger.info("Waiting for database to be ready...")
            for i in range(30):
                try:
                    self._test_database_connection()
                    break
                except:
                    time.sleep(2)
                    if i == 29:
                        self.logger.error("Database failed to start in time")
                        return False

            # Update pg tools paths since we might be talking to a different DB version now?
            # Usually the container has newer tools so it should be fine.
            
            if restore_database and db_dump:
                self.logger.info("Restoring database dump...")
                if not self.restore_database(
                    database=database,
                    backup_file=db_dump,
                    drop_existing=True,
                    restore_filestore=False # Filestore handled by project restore
                ):
                    return False

            # 6. Finalize
            self.logger.info("Starting remaining services...")
            if not self._run_host_command("docker compose up -d", cwd=target_project_path):
                return False

            return True

        except Exception as e:
            self.logger.error(f"Project restoration failed: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False
        finally:
            # Cleanup temp
            if 'temp_extract' in locals() and os.path.exists(temp_extract):
                shutil.rmtree(temp_extract)


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
    
    # New Restore Arguments
    parser.add_argument('--restore-type', choices=['database', 'project'], default='database', help='Type of restore operation')
    parser.add_argument('--target-project-path', help='Target path on host for full project restore')
    parser.add_argument('--skip-database', action='store_true', help='Skip database restore in project mode')
    parser.add_argument('--post-restore-commands', nargs='+', help='Commands to run after restore')

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
            backup_manager.backup_database(
                args.database, 
                include_folders=args.include_folders,
                project_path=args.project_path
            )
        else:
            # Backup all databases
            databases = backup_manager.list_databases()
            for db in databases:
                backup_manager.backup_database(
                    db, 
                    include_folders=args.include_folders,
                    project_path=args.project_path
                )

    elif args.action == 'restore':
        if not args.database:
            print("Error: Database name is required for restoration.")
            sys.exit(1)

        # Handle Full Project Restore
        if args.restore_type == 'project':
            if not args.target_project_path:
                print("Error: --target-project-path is required for full project restore")
                sys.exit(1)
            
            success = backup_manager.restore_project(
                database=args.database,
                target_project_path=args.target_project_path,
                backup_file=args.backup_file,
                drop_existing=args.drop_existing,
                restore_database=not args.skip_database,
                post_restore_commands=args.post_restore_commands
            )
            sys.exit(0 if success else 1)

        # Handle Standard Database Restore
        else:
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

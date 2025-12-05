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
import shlex
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
        
        # Initialize pg tool paths to defaults (will be updated by _detect_pg_tools)
        self.psql_path = 'psql'
        self.pg_dump_path = 'pg_dump'
        self.pg_dumpall_path = 'pg_dumpall'
        self.server_version = None
        
        # Try to detect PostgreSQL tools, but don't fail if DB is not available
        # For project restore, DB won't be available until Docker is set up
        # Tools will be re-detected after Docker is running
        try:
            self._detect_pg_tools()
        except Exception as e:
            # If detection fails (e.g., DB not available), just use defaults
            self.logger.warning(f"Could not detect PostgreSQL tools during initialization (this is OK for project restore): {e}")
            # Ensure paths are set to defaults
            if not self.psql_path:
                self.psql_path = 'psql'
            if not self.pg_dump_path:
                self.pg_dump_path = 'pg_dump'
            if not self.pg_dumpall_path:
                self.pg_dumpall_path = 'pg_dumpall'
    
    def _detect_pg_tools(self):
        """Detect and configure PostgreSQL client tools matching server version."""
        try:
            # First, find any available psql to query server version
            temp_psql = self._find_any_pg_tool('psql')
            # Ensure temp_psql is never None
            if not temp_psql:
                temp_psql = 'psql'
            
            # Get server version - try/except because server might be down during restore init
            try:
                version_cmd = [
                    temp_psql,
                    f'-h{self.host}',
                    f'-p{self.port}',
                    f'-U{self.username}',
                    'postgres',  # Connect to postgres database (always exists)
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
                    self.pg_dumpall_path = self._find_compatible_pg_tool('pg_dumpall', self.server_version)
                    
                    # Ensure paths are never None
                    if not self.pg_dump_path:
                        self.pg_dump_path = temp_psql.replace('psql', 'pg_dump') if 'psql' in temp_psql else 'pg_dump'
                    if not self.psql_path:
                        self.psql_path = temp_psql
                    if not self.pg_dumpall_path:
                        self.pg_dumpall_path = temp_psql.replace('psql', 'pg_dumpall') if 'psql' in temp_psql else 'pg_dumpall'
                    
                    self.logger.info(f"Using pg_dump: {self.pg_dump_path}")
                    self.logger.info(f"Using psql: {self.psql_path}")
                    self.logger.info(f"Using pg_dumpall: {self.pg_dumpall_path}")
                else:
                    # DB might be down, fallback to defaults
                    self.logger.warning(f"Could not get server version (DB might be down): {result.stderr}")
                    self.server_version = None
                    self.pg_dump_path = self._find_any_pg_tool('pg_dump') or 'pg_dump'
                    self.psql_path = temp_psql
                    self.pg_dumpall_path = self._find_any_pg_tool('pg_dumpall') or 'pg_dumpall'
            except Exception as e:
                self.logger.warning(f"Connection failed during tool detection: {e}")
                self.server_version = None
                self.pg_dump_path = self._find_any_pg_tool('pg_dump') or 'pg_dump'
                self.psql_path = temp_psql
                self.pg_dumpall_path = self._find_any_pg_tool('pg_dumpall') or 'pg_dumpall'
                
        except Exception as e:
            self.logger.warning(f"Could not detect PostgreSQL tools: {e}")
            self.server_version = None
            self.pg_dump_path = 'pg_dump'
            self.psql_path = 'psql'
            self.pg_dumpall_path = 'pg_dumpall'
        
        # Final safety check - ensure paths are never None
        if not self.psql_path:
            self.psql_path = 'psql'
        if not self.pg_dump_path:
            self.pg_dump_path = 'pg_dump'
        if not self.pg_dumpall_path:
            self.pg_dumpall_path = 'pg_dumpall'
    
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
        for tool in ['pg_dump', 'psql', 'pg_dumpall']:
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
            
            # Set connection timeout environment variable for faster failure
            env = {**os.environ, 'PGPASSWORD': self.password, 'PGCONNECT_TIMEOUT': '5'} if self.password else {**os.environ, 'PGCONNECT_TIMEOUT': '5'}
            
            result = subprocess.run(
                test_cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=8  # Reduced from 10 to 8 seconds
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

            # Set connection timeout for faster failure if DB is unreachable
            env = {**os.environ, 'PGPASSWORD': self.password, 'PGCONNECT_TIMEOUT': '5'} if self.password else {**os.environ, 'PGCONNECT_TIMEOUT': '5'}
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=env,
                timeout=8  # Add timeout to prevent hanging
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
    def backup_session(
            self,
            databases: Optional[List[str]] = None,
            include_folders: Optional[List[str]] = None,
            project_path: Optional[str] = None
    ) -> bool:
        """
        Backup databases and files into a single session folder.
        
        When databases is None, backs up the entire PostgreSQL cluster using pg_dumpall.
        When databases is specified, backs up only those specific databases.
        
        Structure:
        backup_dir/
          YYYY-MM-DD_HH-MM-SS/
            files.tar.gz (if project_path or include_folders)
            databases/
              cluster_dump.sql (if no databases specified - entire cluster)
              OR
              db1.sql, db2.sql (if specific databases specified)
        """
        try:
            timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            session_dir = os.path.join(self.backup_dir, timestamp)
            os.makedirs(session_dir, exist_ok=True)
            
            # Set permissions for session dir
            self._set_permissions(session_dir)
            
            # 1. Backup Files (Generic/Shared)
            if project_path or include_folders:
                files_archive = os.path.join(session_dir, 'files.tar.gz')
                self.logger.info(f"Backing up files to: {files_archive}")
                
                # Define exclusion patterns for common unnecessary files/directories
                # These significantly reduce backup size and time
                exclude_dirs = {
                    '__pycache__', '.git', '.svn', '.hg',  # Version control & Python cache
                    'node_modules', '.npm', '.yarn',  # Node.js
                    '.venv', 'venv', 'env', 'ENV', 'virtualenv',  # Virtual environments
                    '.cache', '.pytest_cache', '.mypy_cache',  # Cache directories
                    'dist', 'build', '.build',  # Build artifacts
                    '.idea', '.vscode', '.vs',  # IDE files
                    'htmlcov', '.tox', '.coverage',  # Test artifacts
                }
                
                exclude_extensions = {'.pyc', '.pyo', '.pyd', '.log', '.tmp', '.temp', '.swp', '.swo', '.DS_Store', '.Thumbs.db'}
                
                def should_exclude(tarinfo):
                    """Filter function to exclude unnecessary files from backup."""
                    name = tarinfo.name
                    path_parts = name.split('/')
                    
                    # Check directory names
                    for part in path_parts:
                        if part in exclude_dirs:
                            return None
                    
                    # Check file extensions
                    if any(name.endswith(ext) for ext in exclude_extensions):
                        return None
                    
                    # Check for patterns in filename
                    basename = os.path.basename(name)
                    if basename.startswith('.') and basename not in ['.env', '.gitignore', '.dockerignore']:
                        # Exclude hidden files except important config files
                        if basename not in ['.env', '.gitignore', '.dockerignore', '.gitkeep']:
                            return None
                    
                    return tarinfo
                
                with tarfile.open(files_archive, 'w:gz') as tar:
                    if project_path and os.path.exists(project_path):
                        project_name = os.path.basename(project_path.rstrip('/'))
                        self.logger.info(f"Archiving project: {project_path}")
                        self.logger.info("This may take a while for large projects...")
                        
                        # Use tar.add with filter - automatically recursive for directories
                        tar.add(project_path, arcname=project_name, filter=should_exclude)
                        self.logger.info("Project files archived")
                    
                    if include_folders:
                        for folder in include_folders:
                            if os.path.exists(folder):
                                folder_name = os.path.basename(folder)
                                self.logger.info(f"Archiving folder: {folder}")
                                tar.add(folder, arcname=folder_name, filter=should_exclude)
                
                archive_size = os.path.getsize(files_archive)
                self._set_permissions(files_archive)
                size_mb = archive_size / (1024*1024)
                self.logger.info(f"Files backup completed: {size_mb:.1f} MB compressed")
                
                # Upload files archive if cloud storage configured
                if self.storage_client and self.storage_type != 'local':
                    self._upload_to_cloud_storage(files_archive, f"{timestamp}/files")

            # 2. Backup Databases
            db_dir = os.path.join(session_dir, 'databases')
            os.makedirs(db_dir, exist_ok=True)
            self._set_permissions(db_dir)
            
            # Ensure pg_dumpall_path is set
            if not hasattr(self, 'pg_dumpall_path') or not self.pg_dumpall_path:
                self.pg_dumpall_path = self._find_any_pg_tool('pg_dumpall') or 'pg_dumpall'
            
            # If no databases specified, backup entire cluster using pg_dumpall
            if not databases or len(databases) == 0:
                self.logger.info("No specific databases provided - backing up entire PostgreSQL cluster")
                dump_file = os.path.join(db_dir, 'cluster_dump.sql')
                
                # Use pg_dumpall for cluster-wide backup (best practice)
                pg_dumpall_cmd = [
                    self.pg_dumpall_path,
                    f'-h{self.host}',
                    f'-p{self.port}',
                    f'-U{self.username}',
                    '-f', dump_file,
                    '-v',  # Verbose output
                    '--clean',  # Add DROP statements
                    '--if-exists',  # Use IF EXISTS for DROP statements (safer)
                    '--no-owner',  # Don't output commands to set ownership
                    '--no-acl',  # Don't output access privileges
                    '--no-tablespaces',  # Don't include tablespace assignments
                    '--no-privileges',  # Don't dump access privileges
                ]
                
                # Set connection timeout for faster failure if DB is unreachable
                env = {**os.environ, 'PGPASSWORD': self.password, 'PGCONNECT_TIMEOUT': '10'}
                
                self.logger.info(f"Executing pg_dumpall to backup entire cluster...")
                result = subprocess.run(
                    pg_dumpall_cmd,
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True
                )
                
                if result.returncode == 0:
                    self.logger.info(f"Cluster backup completed successfully: {dump_file}")
                else:
                    self.logger.error(f"pg_dumpall failed: {result.stderr}")
                    raise subprocess.CalledProcessError(result.returncode, pg_dumpall_cmd, result.stdout, result.stderr)
                
                self._set_permissions(dump_file)
                
                # Upload cluster dump if cloud storage configured
                if self.storage_client and self.storage_type != 'local':
                    self._upload_to_cloud_storage(dump_file, f"{timestamp}/databases")
            else:
                # Backup specific databases using individual pg_dump calls
                self.logger.info(f"Backing up specific databases: {databases}")
                for db in databases:
                    self.logger.info(f"Backing up database: {db}")
                    dump_file = os.path.join(db_dir, f"{db}.sql")
                    
                    pg_dump_cmd = [
                        self.pg_dump_path,
                        f'-h{self.host}',
                        f'-p{self.port}',
                        f'-U{self.username}',
                        '-f', dump_file,
                        '-v',
                        '--no-owner',
                        '--no-acl',
                        '--no-tablespaces',  # Don't include tablespace assignments
                        '--no-privileges',   # Don't dump access privileges (redundant with --no-acl but ensures)
                        db
                    ]
                    
                    # Set connection timeout for faster failure if DB is unreachable
                    env = {**os.environ, 'PGPASSWORD': self.password, 'PGCONNECT_TIMEOUT': '10'}
                    
                    subprocess.run(
                        pg_dump_cmd,
                        env=env,
                        check=True,
                        capture_output=True,
                        text=True
                    )
                    
                    self._set_permissions(dump_file)
                    
                    # Upload DB dump if cloud storage configured
                    if self.storage_client and self.storage_type != 'local':
                        self._upload_to_cloud_storage(dump_file, f"{timestamp}/databases")
                    
            self.logger.info(f"Session backup completed: {session_dir}")
            return True

        except Exception as e:
            self.logger.error(f"Session backup failed: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False

    def _set_permissions(self, path: str):
        """Set file ownership to be accessible on host."""
        try:
            import pwd
            host_uid = int(os.environ.get('HOST_UID', '1000'))
            host_gid = int(os.environ.get('HOST_GID', '1000'))
            os.chown(path, host_uid, host_gid)
        except (OSError, ValueError, ImportError):
            pass

    def restore_session(
            self,
            backup_path: str,
            restore_type: str = 'all',
            specific_db: Optional[str] = None,
            drop_existing: bool = True,
            sql_dump_file: Optional[str] = None,
            ignore_errors: bool = False
    ) -> bool:
        """
        Restore from a session backup folder.
        
        Args:
            backup_path: Path to the timestamp folder (session)
            restore_type: 'all' or 'specific'
            specific_db: Database name if restore_type is 'specific'
            drop_existing: Whether to drop existing database before restore
            sql_dump_file: Optional path to a specific SQL dump file to restore
            ignore_errors: If True, continue restore even if errors occur (useful for dumps with duplicate constraints)
        """
        try:
            # Validate backup_path is not None
            if backup_path is None:
                raise ValueError("backup_path cannot be None")
            
            # Ensure psql_path is set before proceeding
            if not hasattr(self, 'psql_path') or not self.psql_path or self.psql_path is None:
                self.logger.warning("psql_path not set in restore_session, attempting to detect...")
                try:
                    detected_psql = self._find_any_pg_tool('psql')
                    self.psql_path = detected_psql if detected_psql else 'psql'
                except Exception as e:
                    self.logger.warning(f"Could not detect psql tool: {e}, using default 'psql'")
                    self.psql_path = 'psql'
            
            # Final safety check
            if self.psql_path is None or not isinstance(self.psql_path, str) or not self.psql_path:
                self.logger.error("psql_path is invalid, using default 'psql'")
                self.psql_path = 'psql'
            
            if not hasattr(self, 'pg_dump_path') or not self.pg_dump_path or self.pg_dump_path is None:
                try:
                    detected_pg_dump = self._find_any_pg_tool('pg_dump')
                    self.pg_dump_path = detected_pg_dump if detected_pg_dump else 'pg_dump'
                except Exception as e:
                    self.logger.warning(f"Could not detect pg_dump tool: {e}, using default 'pg_dump'")
                    self.pg_dump_path = 'pg_dump'
            
            # If a specific SQL dump file is provided, use it directly
            if sql_dump_file:
                if not os.path.exists(sql_dump_file):
                    self.logger.error(f"SQL dump file not found: {sql_dump_file}")
                    self.logger.error(f"Current working directory: {os.getcwd()}")
                    self.logger.error(f"Absolute path: {os.path.abspath(sql_dump_file)}")
                    raise FileNotFoundError(f"SQL dump file not found: {sql_dump_file}")
                
                if not os.path.isfile(sql_dump_file):
                    raise ValueError(f"SQL dump path is not a file: {sql_dump_file}")
                
                # Extract database name from filename or use provided specific_db
                if specific_db:
                    db_name = specific_db
                else:
                    # Try to extract from filename (e.g., /path/to/database.sql -> database)
                    db_name = os.path.basename(sql_dump_file)
                    if db_name.endswith('.sql'):
                        db_name = db_name[:-4]
                    else:
                        raise ValueError(f"Cannot determine database name from SQL dump file: {sql_dump_file}. Please specify --database")
                
                self.logger.info(f"Restoring database '{db_name}' from SQL dump file: {sql_dump_file}")
                
                # Create/Drop DB logic
                if drop_existing:
                    self._drop_and_create_db(db_name)
                    # Verify database is clean before restore
                    self._verify_database_empty(db_name)
                
                # Filter out problematic parameters from SQL dump
                filtered_sql_file, temp_file_to_cleanup = self._filter_sql_dump(sql_dump_file)
                
                try:
                    # Restore dump
                    # Use ON_ERROR_STOP to make psql exit on errors (not just warnings) - unless ignore_errors is set
                    # Use --single-transaction for atomic restore (all or nothing) - only when not ignoring errors
                    restore_cmd = [
                        self.psql_path,
                        f'-h{self.host}',
                        f'-p{self.port}',
                        f'-U{self.username}',
                        '-d', db_name,
                    ]
                    
                    if ignore_errors:
                        # Don't use single-transaction when ignoring errors - allows partial restore
                        self.logger.info("Running restore with --ignore-errors: errors will be logged but won't stop the restore")
                    else:
                        restore_cmd.extend(['--single-transaction', '-v', 'ON_ERROR_STOP=1'])
                    
                    restore_cmd.extend(['-f', filtered_sql_file])
                    
                    # Log the command (hide password in connection string)
                    cmd_display = ' '.join(restore_cmd)
                    if ignore_errors:
                        self.logger.info(f"Executing restore command: {cmd_display} (errors will be ignored)")
                    else:
                        self.logger.info(f"Executing restore command: {cmd_display}")
                    result = subprocess.run(
                        restore_cmd,
                        env={**os.environ, 'PGPASSWORD': self.password},
                        check=False,  # Don't raise exception, we'll handle it
                        capture_output=True,
                        text=True
                    )
                    
                    # Parse stderr for errors vs warnings
                    error_count = 0
                    warning_count = 0
                    critical_errors = []
                    
                    if result.stderr:
                        stderr_lines = result.stderr.split('\n')
                        for line in stderr_lines:
                            if 'ERROR:' in line.upper():
                                error_count += 1
                                critical_errors.append(line.strip())
                            elif 'WARNING:' in line.upper() or 'error: invalid command' in line.lower():
                                warning_count += 1
                    
                    # Log detailed error information
                    if error_count > 0:
                        log_level = self.logger.warning if ignore_errors else self.logger.error
                        log_level(f"Restore completed with {error_count} ERROR(s) and {warning_count} warning(s)")
                        if ignore_errors:
                            self.logger.warning("Errors were ignored due to --ignore-errors flag")
                        log_level("Errors found:")
                        for i, error in enumerate(critical_errors[:20], 1):  # Show first 20 errors
                            log_level(f"  {i}. {error}")
                        if len(critical_errors) > 20:
                            log_level(f"  ... and {len(critical_errors) - 20} more errors")
                    
                    if result.returncode != 0:
                        if ignore_errors:
                            self.logger.warning(f"Restore command returned code {result.returncode} but continuing due to --ignore-errors")
                            if result.stderr:
                                self.logger.warning(f"STDERR (first 2000 chars): {result.stderr[:2000]}")
                        else:
                            self.logger.error(f"Restore command failed with return code {result.returncode}")
                            self.logger.error(f"STDERR: {result.stderr}")
                            self.logger.error(f"STDOUT: {result.stdout}")
                            raise subprocess.CalledProcessError(result.returncode, restore_cmd, result.stdout, result.stderr)
                    
                    # Even if return code is 0, check for critical errors
                    if error_count > 0 and not ignore_errors:
                        self.logger.warning(f"Restore completed with exit code 0 but {error_count} ERROR(s) detected")
                        self.logger.warning("This may indicate partial restore or data integrity issues")
                        # Log full stderr for debugging
                        if result.stderr:
                            self.logger.warning(f"Full error output: {result.stderr}")
                    elif result.stderr and warning_count > 0:
                        # Only warnings, no errors
                        self.logger.warning(f"Restore warnings ({warning_count}): {result.stderr[:2000]}")  # Limit output
                    
                    self.logger.info(f"Restored database: {db_name}")
                    
                    # Verify the restore was successful
                    self.logger.info(f"Verifying restore for database '{db_name}'...")
                    if self._verify_database_restore(db_name):
                        self.logger.info(f"✓ Restore verification successful for database '{db_name}'")
                        print(f"✓ Restore verification successful for database '{db_name}'")
                    else:
                        self.logger.error(f"✗ Restore verification failed for database '{db_name}'")
                        print(f"✗ WARNING: Restore verification failed for database '{db_name}'. Please check the database manually.")
                        # Don't return False here - the restore command succeeded, verification is just a check
                    
                    return True
                finally:
                    # Clean up temp filtered file if created
                    if temp_file_to_cleanup:
                        try:
                            os.remove(temp_file_to_cleanup)
                            self.logger.debug(f"Cleaned up temp filtered SQL file: {temp_file_to_cleanup}")
                        except Exception as e:
                            self.logger.warning(f"Could not clean up temp file {temp_file_to_cleanup}: {e}")
            
            # Only validate backup_path if sql_dump_file is not provided
            if not sql_dump_file and (not backup_path or not os.path.exists(backup_path)):
                raise FileNotFoundError(f"Backup path not found: {backup_path}")
                
            # 1. Restore Databases
            db_dir = os.path.join(backup_path, 'databases')
            self.logger.info(f"Looking for database dumps in: {db_dir}")
            self.logger.info(f"  Directory exists: {os.path.exists(db_dir)}")
            
            databases_restored = 0
            
            if os.path.exists(db_dir):
                dump_files = [f for f in os.listdir(db_dir) if f.endswith('.sql')]
                self.logger.info(f"  Found {len(dump_files)} SQL dump file(s): {dump_files}")
                
                if not dump_files:
                    self.logger.warning(f"No SQL dump files found in {db_dir}")
                    self.logger.warning(f"  Available files: {os.listdir(db_dir) if os.path.exists(db_dir) else 'N/A'}")
                    raise FileNotFoundError(f"No SQL dump files found in {db_dir}")
                
                # Check if this is a cluster dump (pg_dumpall output)
                cluster_dump_file = 'cluster_dump.sql' if 'cluster_dump.sql' in dump_files else None
                
                if cluster_dump_file:
                    # Handle cluster-wide restore (pg_dumpall output)
                    self.logger.info("Detected cluster dump - restoring entire PostgreSQL cluster")
                    
                    if restore_type == 'specific' and specific_db:
                        self.logger.warning("Cluster dump detected but specific database requested. Cluster dump restores all databases.")
                        self.logger.warning("Ignoring --database parameter and restoring entire cluster.")
                    
                    # If drop_existing is True, drop all existing databases (except system databases)
                    if drop_existing:
                        self.logger.info("Dropping existing databases before cluster restore...")
                        self._drop_all_databases()
                    
                    full_dump_path = os.path.join(db_dir, cluster_dump_file)
                    self.logger.info(f"  Using cluster dump file: {full_dump_path}")
                    self.logger.info(f"  Dump file exists: {os.path.exists(full_dump_path)}")
                    
                    # Filter out problematic parameters from SQL dump
                    filtered_dump_path, temp_file_to_cleanup = self._filter_sql_dump(full_dump_path)
                    
                    try:
                        # Restore cluster dump to postgres database (or without -d flag)
                        # pg_dumpall output includes CREATE DATABASE statements, so we connect to postgres
                        restore_cmd = [
                            self.psql_path,
                            f'-h{self.host}',
                            f'-p{self.port}',
                            f'-U{self.username}',
                            '-d', 'postgres',  # Connect to postgres database for cluster restore
                        ]
                        
                        if ignore_errors:
                            # Don't use single-transaction when ignoring errors - allows partial restore
                            self.logger.info("Running cluster restore with --ignore-errors: errors will be logged but won't stop the restore")
                        else:
                            # Note: --single-transaction may not work well with pg_dumpall output
                            # as it contains multiple CREATE DATABASE statements
                            # Use ON_ERROR_STOP instead
                            restore_cmd.extend(['-v', 'ON_ERROR_STOP=1'])
                        
                        restore_cmd.extend(['-f', filtered_dump_path])
                        
                        # Set connection timeout for faster failure if DB is unreachable
                        env = {**os.environ, 'PGPASSWORD': self.password, 'PGCONNECT_TIMEOUT': '10'} if self.password else {**os.environ, 'PGCONNECT_TIMEOUT': '10'}
                        
                        error_mode = "ignore-errors (continue on failure)" if ignore_errors else "ON_ERROR_STOP=1"
                        self.logger.info(f"Executing cluster restore command: {' '.join(restore_cmd[:4])} ... {error_mode} -f {full_dump_path}")
                        result = subprocess.run(
                            restore_cmd,
                            env=env,
                            check=False,  # Don't raise exception, we'll handle it
                            capture_output=True,
                            text=True
                        )
                        
                        # Parse stderr for errors vs warnings
                        error_count = 0
                        warning_count = 0
                        critical_errors = []
                        
                        if result.stderr:
                            stderr_lines = result.stderr.split('\n')
                            for line in stderr_lines:
                                if 'ERROR:' in line.upper():
                                    error_count += 1
                                    critical_errors.append(line.strip())
                                elif 'WARNING:' in line.upper() or 'error: invalid command' in line.lower():
                                    warning_count += 1
                        
                        # Log detailed error information
                        if error_count > 0:
                            log_level = self.logger.warning if ignore_errors else self.logger.error
                            log_level(f"Cluster restore completed with {error_count} ERROR(s) and {warning_count} warning(s)")
                            if ignore_errors:
                                self.logger.warning("Errors were ignored due to --ignore-errors flag")
                            log_level("Errors found:")
                            for i, error in enumerate(critical_errors[:20], 1):  # Show first 20 errors
                                log_level(f"  {i}. {error}")
                            if len(critical_errors) > 20:
                                log_level(f"  ... and {len(critical_errors) - 20} more errors")
                        
                        if result.returncode != 0:
                            if ignore_errors:
                                self.logger.warning(f"Cluster restore command returned code {result.returncode} but continuing due to --ignore-errors")
                                if result.stderr:
                                    self.logger.warning(f"STDERR (first 2000 chars): {result.stderr[:2000]}")
                            else:
                                self.logger.error(f"Cluster restore command failed with return code {result.returncode}")
                                self.logger.error(f"STDERR: {result.stderr}")
                                self.logger.error(f"STDOUT: {result.stdout}")
                                raise subprocess.CalledProcessError(result.returncode, restore_cmd, result.stdout, result.stderr)
                        
                        # Even if return code is 0, check for critical errors
                        if error_count > 0 and not ignore_errors:
                            self.logger.warning(f"Cluster restore completed with exit code 0 but {error_count} ERROR(s) detected")
                            self.logger.warning("This may indicate partial restore or data integrity issues")
                            # Log full stderr for debugging
                            if result.stderr:
                                self.logger.warning(f"Full error output: {result.stderr}")
                        elif result.stderr and warning_count > 0:
                            # Only warnings, no errors
                            self.logger.warning(f"Cluster restore warnings ({warning_count}): {result.stderr[:2000]}")  # Limit output
                        
                        self.logger.info("✓ Cluster restore completed successfully")
                        print("✓ Cluster restore completed successfully")
                        databases_restored = 1  # Mark as restored
                        
                    finally:
                        # Clean up temp filtered file if created
                        if temp_file_to_cleanup:
                            try:
                                os.remove(temp_file_to_cleanup)
                                self.logger.debug(f"Cleaned up temp filtered SQL file: {temp_file_to_cleanup}")
                            except Exception as e:
                                self.logger.warning(f"Could not clean up temp file {temp_file_to_cleanup}: {e}")
                else:
                    # Handle individual database dumps (pg_dump output)
                    for dump_file in dump_files:
                        db_name = dump_file[:-4] # Remove .sql
                        
                        if restore_type == 'specific' and specific_db and db_name != specific_db:
                            self.logger.info(f"Skipping database {db_name} (not matching specific_db: {specific_db})")
                            continue
                            
                        self.logger.info(f"Restoring database: {db_name}")
                        full_dump_path = os.path.join(db_dir, dump_file)
                        self.logger.info(f"  Using dump file: {full_dump_path}")
                        self.logger.info(f"  Dump file exists: {os.path.exists(full_dump_path)}")
                        
                        # Create/Drop DB logic similar to restore_database
                        if drop_existing:
                            self._drop_and_create_db(db_name)
                            # Verify database is clean before restore
                            self._verify_database_empty(db_name)
                        
                        # Filter out problematic parameters from SQL dump
                        filtered_dump_path, temp_file_to_cleanup = self._filter_sql_dump(full_dump_path)
                        
                        try:
                            # Restore dump
                            # Use ON_ERROR_STOP to make psql exit on errors (not just warnings) - unless ignore_errors is set
                            # Use --single-transaction for atomic restore (all or nothing) - only when not ignoring errors
                            restore_cmd = [
                                self.psql_path,
                                f'-h{self.host}',
                                f'-p{self.port}',
                                f'-U{self.username}',
                                '-d', db_name,
                            ]
                            
                            if ignore_errors:
                                # Don't use single-transaction when ignoring errors - allows partial restore
                                self.logger.info("Running restore with --ignore-errors: errors will be logged but won't stop the restore")
                            else:
                                restore_cmd.extend(['--single-transaction', '-v', 'ON_ERROR_STOP=1'])
                            
                            restore_cmd.extend(['-f', filtered_dump_path])
                            
                            # Set connection timeout for faster failure if DB is unreachable
                            env = {**os.environ, 'PGPASSWORD': self.password, 'PGCONNECT_TIMEOUT': '10'} if self.password else {**os.environ, 'PGCONNECT_TIMEOUT': '10'}
                            
                            error_mode = "ignore-errors (continue on failure)" if ignore_errors else "ON_ERROR_STOP=1"
                            self.logger.info(f"Executing restore command: {' '.join(restore_cmd[:4])} ... -d {db_name} {error_mode} -f {full_dump_path}")
                            result = subprocess.run(
                                restore_cmd,
                                env=env,
                                check=False,  # Don't raise exception, we'll handle it
                                capture_output=True,
                                text=True
                            )
                            
                            # Parse stderr for errors vs warnings
                            error_count = 0
                            warning_count = 0
                            critical_errors = []
                            
                            if result.stderr:
                                stderr_lines = result.stderr.split('\n')
                                for line in stderr_lines:
                                    if 'ERROR:' in line.upper():
                                        error_count += 1
                                        critical_errors.append(line.strip())
                                    elif 'WARNING:' in line.upper() or 'error: invalid command' in line.lower():
                                        warning_count += 1
                            
                            # Log detailed error information
                            if error_count > 0:
                                log_level = self.logger.warning if ignore_errors else self.logger.error
                                log_level(f"Restore completed with {error_count} ERROR(s) and {warning_count} warning(s)")
                                if ignore_errors:
                                    self.logger.warning("Errors were ignored due to --ignore-errors flag")
                                log_level("Errors found:")
                                for i, error in enumerate(critical_errors[:20], 1):  # Show first 20 errors
                                    log_level(f"  {i}. {error}")
                                if len(critical_errors) > 20:
                                    log_level(f"  ... and {len(critical_errors) - 20} more errors")
                            
                            if result.returncode != 0:
                                if ignore_errors:
                                    self.logger.warning(f"Restore command returned code {result.returncode} but continuing due to --ignore-errors")
                                    if result.stderr:
                                        self.logger.warning(f"STDERR (first 2000 chars): {result.stderr[:2000]}")
                                else:
                                    self.logger.error(f"Restore command failed with return code {result.returncode}")
                                    self.logger.error(f"STDERR: {result.stderr}")
                                    self.logger.error(f"STDOUT: {result.stdout}")
                                    raise subprocess.CalledProcessError(result.returncode, restore_cmd, result.stdout, result.stderr)
                            
                            # Even if return code is 0, check for critical errors
                            if error_count > 0 and not ignore_errors:
                                self.logger.warning(f"Restore completed with exit code 0 but {error_count} ERROR(s) detected")
                                self.logger.warning("This may indicate partial restore or data integrity issues")
                                # Log full stderr for debugging
                                if result.stderr:
                                    self.logger.warning(f"Full error output: {result.stderr}")
                            elif result.stderr and warning_count > 0:
                                # Only warnings, no errors
                                self.logger.warning(f"Restore warnings ({warning_count}): {result.stderr[:2000]}")  # Limit output
                            
                            self.logger.info(f"Restored database: {db_name}")
                            
                            # Verify the restore was successful
                            self.logger.info(f"Verifying restore for database '{db_name}'...")
                            if self._verify_database_restore(db_name):
                                self.logger.info(f"✓ Restore verification successful for database '{db_name}'")
                                print(f"✓ Restore verification successful for database '{db_name}'")
                            else:
                                self.logger.error(f"✗ Restore verification failed for database '{db_name}'")
                                print(f"✗ WARNING: Restore verification failed for database '{db_name}'. Please check the database manually.")
                            
                            databases_restored += 1
                        finally:
                            # Clean up temp filtered file if created
                            if temp_file_to_cleanup:
                                try:
                                    os.remove(temp_file_to_cleanup)
                                    self.logger.debug(f"Cleaned up temp filtered SQL file: {temp_file_to_cleanup}")
                                except Exception as e:
                                    self.logger.warning(f"Could not clean up temp file {temp_file_to_cleanup}: {e}")
            else:
                self.logger.error(f"Database directory not found: {db_dir}")
                raise FileNotFoundError(f"Database directory not found: {db_dir}")
            
            if databases_restored == 0:
                if restore_type == 'specific' and specific_db:
                    self.logger.error(f"No database matching '{specific_db}' was found in the backup")
                    raise ValueError(f"No database matching '{specific_db}' was found in the backup. Available databases: {dump_files}")
                else:
                    self.logger.error(f"No databases were restored")
                    raise ValueError(f"No databases were restored from backup path: {backup_path}")

            # 2. Restore Files (Only if 'all' or specifically requested?)
            # User requirement: "restore all databases or restore specific database"
            # "intelligently" implies if I restore a specific DB, I might not want to overwrite shared files.
            # But if I restore ALL, I definitely want files.
            # Let's restore files ONLY on 'restore all' for now, or if it's a project restore.
            
            files_archive = os.path.join(backup_path, 'files.tar.gz')
            if restore_type == 'all' and os.path.exists(files_archive):
                # Where to restore? We need a target path.
                # Since we don't have project_path arg passed here easily in this signature,
                # we might need to rely on args or a fixed path.
                # BUT, restore_session is called from main(), which has args.
                pass 
                # Actual file restoration logic needs target path. 
                # We'll handle this in the main block or update signature.
            
            return True
            
        except Exception as e:
            self.logger.error(f"Restore session failed: {e}")
            return False

    def _drop_and_create_db(self, db_name: str):
        """Helper to drop and recreate a database."""
        try:
            # CRITICAL: Initialize psql_path to default IMMEDIATELY - do this first, no exceptions
            if not hasattr(self, 'psql_path'):
                self.psql_path = 'psql'
            
            # Force set to default if None or empty
            if self.psql_path is None or (isinstance(self.psql_path, str) and not self.psql_path):
                self.psql_path = 'psql'
            
            # Ensure psql_path is set - multiple safety checks
            if not self.psql_path or self.psql_path is None:
                self.logger.warning("psql_path not set, attempting to detect...")
                try:
                    detected_path = self._find_any_pg_tool('psql')
                    self.psql_path = detected_path if detected_path and detected_path is not None else 'psql'
                except Exception as e:
                    self.logger.warning(f"Could not detect psql tool: {e}, using default 'psql'")
                    self.psql_path = 'psql'
            
            # Final safety check - ensure it's a string and not None
            if self.psql_path is None or not isinstance(self.psql_path, str) or not self.psql_path:
                self.logger.error("psql_path is invalid, using default 'psql'")
                self.psql_path = 'psql'
            
            # CRITICAL: One final check - if still None, force it
            if self.psql_path is None:
                self.logger.error("CRITICAL: psql_path is still None after all checks! Forcing to 'psql'")
                self.psql_path = 'psql'
            
            # Validate it's actually a string now
            if not isinstance(self.psql_path, str):
                self.logger.error(f"CRITICAL: psql_path is not a string: {type(self.psql_path)}. Forcing to 'psql'")
                self.psql_path = 'psql'
            
            self.logger.info(f"Using psql_path: {self.psql_path} (type: {type(self.psql_path)}) for database operations")
            
            # Final validation right before use - this should never fail, but just in case
            # CRITICAL: One more check right before building the command
            if self.psql_path is None or not isinstance(self.psql_path, str):
                self.logger.error(f"CRITICAL: psql_path is invalid right before subprocess call! Value: {self.psql_path}, Type: {type(self.psql_path)}")
                self.psql_path = 'psql'  # Force it to a valid value
            
            # Validate other required attributes
            if self.host is None:
                self.logger.error("CRITICAL: host is None!")
                self.host = 'localhost'
            if self.port is None:
                self.logger.error("CRITICAL: port is None!")
                self.port = 5432
            if self.username is None:
                self.logger.error("CRITICAL: username is None!")
                self.username = 'postgres'
            
            # Build command list - ensure all values are strings
            # First, terminate all connections to the database
            self.logger.info(f"Terminating all connections to database '{db_name}'...")
            term_cmd = [
                str(self.psql_path),  # Explicitly convert to string
                f'-h{self.host}',
                f'-p{self.port}',
                f'-U{self.username}',
                'postgres',
                '-c', f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{db_name}' AND pid <> pg_backend_pid();"
            ]
            
            # Final validation of command list
            if any(arg is None for arg in term_cmd):
                self.logger.error(f"CRITICAL: Command list contains None values! Command: {term_cmd}")
                raise ValueError("Command list cannot contain None values")
            
            term_result = subprocess.run(
                term_cmd, 
                env={**os.environ, 'PGPASSWORD': self.password} if self.password else os.environ, 
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if term_result.returncode != 0:
                self.logger.warning(f"Warning: Could not terminate all connections: {term_result.stderr}")
            else:
                self.logger.info(f"Connections terminated. Waiting 2 seconds for cleanup...")
                time.sleep(2)  # Wait for connections to fully close
            
            # Drop database with retry logic
            self.logger.info(f"Dropping database '{db_name}'...")
            drop_cmd = [
                str(self.psql_path),  # Explicitly convert to string
                f'-h{self.host}',
                f'-p{self.port}',
                f'-U{self.username}',
                'postgres',
                '-c', f'DROP DATABASE IF EXISTS "{db_name}";'
            ]
            
            # Validate command list
            if any(arg is None for arg in drop_cmd):
                self.logger.error(f"CRITICAL: Drop command list contains None values! Command: {drop_cmd}")
                raise ValueError("Drop command list cannot contain None values")
            
            # Retry drop up to 3 times
            max_retries = 3
            for attempt in range(max_retries):
                drop_result = subprocess.run(
                    drop_cmd, 
                    env={**os.environ, 'PGPASSWORD': self.password} if self.password else os.environ, 
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                
                if drop_result.returncode == 0:
                    self.logger.info(f"Database '{db_name}' dropped successfully")
                    break
                elif attempt < max_retries - 1:
                    self.logger.warning(f"Drop attempt {attempt + 1} failed, retrying... Error: {drop_result.stderr}")
                    time.sleep(2)
                else:
                    self.logger.error(f"Failed to drop database after {max_retries} attempts: {drop_result.stderr}")
                    raise subprocess.CalledProcessError(drop_result.returncode, drop_cmd, drop_result.stdout, drop_result.stderr)
            
            # Verify database is actually dropped
            self.logger.info(f"Verifying database '{db_name}' is dropped...")
            verify_cmd = [
                str(self.psql_path),
                f'-h{self.host}',
                f'-p{self.port}',
                f'-U{self.username}',
                'postgres',
                '-t', '-A',
                '-c', f"SELECT 1 FROM pg_database WHERE datname = '{db_name}';"
            ]
            
            verify_result = subprocess.run(
                verify_cmd,
                env={**os.environ, 'PGPASSWORD': self.password} if self.password else os.environ,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if verify_result.stdout.strip():
                self.logger.warning(f"Database '{db_name}' still exists after drop, waiting and retrying...")
                time.sleep(3)
                # Try one more aggressive drop
                drop_result = subprocess.run(
                    drop_cmd,
                    env={**os.environ, 'PGPASSWORD': self.password} if self.password else os.environ,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if drop_result.returncode != 0:
                    self.logger.error(f"Failed to drop database '{db_name}': {drop_result.stderr}")
                    raise subprocess.CalledProcessError(drop_result.returncode, drop_cmd, drop_result.stdout, drop_result.stderr)
            
            # Check if user has CREATEDB privilege before attempting to create database
            self.logger.info(f"Checking if user '{self.username}' has CREATEDB privilege...")
            check_createdb_cmd = [
                str(self.psql_path),
                f'-h{self.host}',
                f'-p{self.port}',
                f'-U{self.username}',
                'postgres',
                '-t', '-A',
                '-c', "SELECT usecreatedb FROM pg_user WHERE usename = current_user;"
            ]
            
            check_result = subprocess.run(
                check_createdb_cmd,
                env={**os.environ, 'PGPASSWORD': self.password} if self.password else os.environ,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if check_result.returncode == 0:
                has_createdb = check_result.stdout.strip() == 't'
                if not has_createdb:
                    error_msg = (
                        f"User '{self.username}' does not have CREATEDB privilege. "
                        f"Cannot create database '{db_name}'. "
                        f"Please grant CREATEDB privilege to user '{self.username}' or use a superuser account."
                    )
                    self.logger.error(error_msg)
                    raise PermissionError(error_msg)
                self.logger.info(f"User '{self.username}' has CREATEDB privilege")
            else:
                self.logger.warning(f"Could not verify CREATEDB privilege: {check_result.stderr}. Proceeding with database creation...")
            
            # Create fresh database
            self.logger.info(f"Creating fresh database '{db_name}'...")
            create_cmd = [
                str(self.psql_path),  # Explicitly convert to string
                f'-h{self.host}',
                f'-p{self.port}',
                f'-U{self.username}',
                'postgres',
                '-c', f'CREATE DATABASE "{db_name}";'
            ]
            
            # Validate command list
            if any(arg is None for arg in create_cmd):
                self.logger.error(f"CRITICAL: Create command list contains None values! Command: {create_cmd}")
                raise ValueError("Create command list cannot contain None values")
            
            # Try CREATE DATABASE with collation mismatch auto-fix
            max_create_attempts = 2
            for create_attempt in range(max_create_attempts):
                create_result = subprocess.run(
                    create_cmd, 
                    env={**os.environ, 'PGPASSWORD': self.password} if self.password else os.environ, 
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                
                if create_result.returncode == 0:
                    self.logger.info(f"Database '{db_name}' created successfully")
                    break
                
                # Check for collation version mismatch error
                stderr_lower = create_result.stderr.lower() if create_result.stderr else ''
                if 'collation version mismatch' in stderr_lower and create_attempt < max_create_attempts - 1:
                    self.logger.warning(
                        "Detected collation version mismatch on template database. "
                        "This typically happens after system updates. Attempting automatic fix..."
                    )
                    
                    # Try to fix the collation mismatch
                    if self._fix_collation_version_mismatch():
                        self.logger.info("Collation fix applied, retrying database creation...")
                        continue
                    else:
                        self.logger.error(
                            "Could not automatically fix collation version mismatch. "
                            "Please run the following commands manually as a PostgreSQL superuser:\n"
                            "  ALTER DATABASE template1 REFRESH COLLATION VERSION;\n"
                            "  ALTER DATABASE postgres REFRESH COLLATION VERSION;"
                        )
                
                # If we get here, creation failed
                raise subprocess.CalledProcessError(
                    create_result.returncode, 
                    create_cmd, 
                    create_result.stdout, 
                    create_result.stderr
                )
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Error recreating DB {db_name}: Command failed with exit code {e.returncode}")
            if 'create_cmd' in locals():
                self.logger.error(f"Command: {' '.join(create_cmd)}")
            if e.stdout:
                self.logger.error(f"stdout: {e.stdout}")
            if e.stderr:
                self.logger.error(f"stderr: {e.stderr}")
            raise
        except PermissionError:
            # Re-raise permission errors as-is (they already have clear messages)
            raise
        except Exception as e:
            self.logger.error(f"Error recreating DB {db_name}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            raise
    
    def _drop_all_databases(self):
        """
        Drop all non-system databases for cluster restore.
        System databases (postgres, template0, template1) are preserved.
        """
        try:
            # Ensure psql_path is set
            if not hasattr(self, 'psql_path') or not self.psql_path:
                self.psql_path = self._find_any_pg_tool('psql') or 'psql'
            
            # Get list of all databases (excluding system databases)
            databases = self.list_databases(include_system=False)
            
            if not databases:
                self.logger.info("No user databases found to drop")
                return
            
            self.logger.info(f"Dropping {len(databases)} user database(s): {databases}")
            
            for db_name in databases:
                try:
                    # Terminate all connections first
                    self.logger.info(f"Terminating connections to database '{db_name}'...")
                    term_cmd = [
                        str(self.psql_path),
                        f'-h{self.host}',
                        f'-p{self.port}',
                        f'-U{self.username}',
                        'postgres',
                        '-c', f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{db_name}' AND pid <> pg_backend_pid();"
                    ]
                    
                    subprocess.run(
                        term_cmd,
                        env={**os.environ, 'PGPASSWORD': self.password} if self.password else os.environ,
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    
                    # Drop the database
                    drop_cmd = [
                        str(self.psql_path),
                        f'-h{self.host}',
                        f'-p{self.port}',
                        f'-U{self.username}',
                        'postgres',
                        '-c', f'DROP DATABASE IF EXISTS "{db_name}";'
                    ]
                    
                    drop_result = subprocess.run(
                        drop_cmd,
                        env={**os.environ, 'PGPASSWORD': self.password} if self.password else os.environ,
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    
                    if drop_result.returncode == 0:
                        self.logger.info(f"✓ Dropped database: {db_name}")
                    else:
                        self.logger.warning(f"Failed to drop database '{db_name}': {drop_result.stderr}")
                        
                except Exception as e:
                    self.logger.warning(f"Error dropping database '{db_name}': {e}")
                    # Continue with other databases
            
            self.logger.info("Finished dropping user databases")
            
        except Exception as e:
            self.logger.error(f"Error in _drop_all_databases: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            # Don't raise - allow restore to continue even if some databases couldn't be dropped

    def _fix_collation_version_mismatch(self) -> bool:
        """
        Fix collation version mismatch on template databases.
        
        This error occurs when the system's glibc version has changed since the
        PostgreSQL template databases were created. The fix is to refresh the
        collation version on template1 and postgres databases.
        
        Returns:
            bool: True if fix was successful, False otherwise
        """
        self.logger.info("Attempting to fix collation version mismatch on template databases...")
        
        databases_to_fix = ['template1', 'postgres']
        fixed_any = False
        
        for db in databases_to_fix:
            try:
                refresh_cmd = [
                    str(self.psql_path),
                    f'-h{self.host}',
                    f'-p{self.port}',
                    f'-U{self.username}',
                    db,
                    '-c', f'ALTER DATABASE {db} REFRESH COLLATION VERSION;'
                ]
                
                self.logger.info(f"Refreshing collation version on '{db}'...")
                
                result = subprocess.run(
                    refresh_cmd,
                    env={**os.environ, 'PGPASSWORD': self.password} if self.password else os.environ,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                
                if result.returncode == 0:
                    self.logger.info(f"Successfully refreshed collation version on '{db}'")
                    fixed_any = True
                else:
                    # Check if it's a permission error
                    if 'permission denied' in result.stderr.lower() or 'must be owner' in result.stderr.lower():
                        self.logger.warning(
                            f"Cannot refresh collation on '{db}' - insufficient privileges. "
                            f"User '{self.username}' may need superuser privileges. "
                            f"You can manually fix this by running as superuser: "
                            f"ALTER DATABASE {db} REFRESH COLLATION VERSION;"
                        )
                    else:
                        self.logger.warning(f"Could not refresh collation on '{db}': {result.stderr}")
                    
            except subprocess.TimeoutExpired:
                self.logger.warning(f"Timeout refreshing collation on '{db}'")
            except Exception as e:
                self.logger.warning(f"Error refreshing collation on '{db}': {e}")
        
        return fixed_any

    def _filter_sql_dump(self, sql_file: str) -> tuple:
        """
        Filter out problematic parameters from SQL dump files.
        
        Some PostgreSQL parameters (like transaction_timeout) are version-specific
        or from custom builds (Odoo). This filters them out to allow restore on
        standard PostgreSQL installations.
        
        Args:
            sql_file: Path to the SQL dump file
            
        Returns:
            tuple: (filtered_file_path, temp_file_path_or_none)
                   If no filtering needed, returns (original_file, None)
                   If filtered, returns (temp_file, temp_file) - caller should clean up
        """
        # Parameters to filter out (case-insensitive)
        problematic_params = [
            b'transaction_timeout',
            b'idle_session_timeout',  # Another common Odoo/custom parameter
        ]
        
        try:
            # Quick check if file needs filtering
            with open(sql_file, 'rb') as f:
                # Read first 16KB to check for problematic parameters
                first_chunk = f.read(16384)
                needs_filtering = any(param in first_chunk.lower() for param in problematic_params)
                
                if not needs_filtering:
                    # Also check a bit further in case SET statements are after initial comments
                    f.seek(0)
                    for i, line in enumerate(f):
                        if i > 50:  # Check first 50 lines
                            break
                        line_lower = line.lower()
                        if any(param in line_lower for param in problematic_params):
                            needs_filtering = True
                            break
            
            if not needs_filtering:
                return (sql_file, None)
            
            self.logger.info("Filtering out unrecognized PostgreSQL parameters from SQL dump...")
            
            import tempfile
            temp_file = tempfile.NamedTemporaryFile(mode='wb', suffix='.sql', delete=False)
            temp_file_path = temp_file.name
            
            filtered_count = 0
            with open(sql_file, 'rb') as infile:
                for line in infile:
                    line_lower = line.lower()
                    # Check if line contains any problematic parameter
                    should_filter = any(param in line_lower for param in problematic_params)
                    
                    if should_filter:
                        filtered_count += 1
                        # Write as comment instead of removing completely
                        temp_file.write(b'-- FILTERED: ' + line)
                    else:
                        temp_file.write(line)
            
            temp_file.close()
            self.logger.info(f"Filtered {filtered_count} line(s) with problematic parameters")
            
            return (temp_file_path, temp_file_path)
            
        except Exception as e:
            self.logger.warning(f"Could not filter SQL file, using original: {e}")
            return (sql_file, None)

    def _verify_database_empty(self, db_name: str) -> bool:
        """
        Verify that a database is empty (no user tables or constraints).
        This ensures a clean state before restore.
        
        Returns:
            bool: True if database is empty, False otherwise
        """
        try:
            # Ensure psql_path is set
            if not hasattr(self, 'psql_path') or not self.psql_path:
                self.psql_path = 'psql'
            
            # Check if database has any user tables
            check_tables_cmd = [
                self.psql_path,
                f'-h{self.host}',
                f'-p{self.port}',
                f'-U{self.username}',
                db_name,
                '-t', '-A',
                '-c', "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE';"
            ]
            
            result = subprocess.run(
                check_tables_cmd,
                env={**os.environ, 'PGPASSWORD': self.password} if self.password else os.environ,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                self.logger.warning(f"Could not verify database '{db_name}' is empty: {result.stderr}")
                return True  # Assume it's okay if we can't check
            
            table_count = result.stdout.strip()
            if table_count and table_count != '0':
                self.logger.warning(f"Database '{db_name}' is not empty - found {table_count} table(s). This may cause restore issues.")
                return False
            
            # Check for constraints
            check_constraints_cmd = [
                self.psql_path,
                f'-h{self.host}',
                f'-p{self.port}',
                f'-U{self.username}',
                db_name,
                '-t', '-A',
                '-c', "SELECT COUNT(*) FROM pg_constraint WHERE conrelid != 0;"
            ]
            
            result = subprocess.run(
                check_constraints_cmd,
                env={**os.environ, 'PGPASSWORD': self.password} if self.password else os.environ,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                constraint_count = result.stdout.strip()
                if constraint_count and constraint_count != '0':
                    self.logger.warning(f"Database '{db_name}' has {constraint_count} constraint(s). This may cause restore issues.")
                    # Don't return False here - some constraints are system-level
            
            self.logger.info(f"✓ Database '{db_name}' is clean and ready for restore")
            return True
            
        except Exception as e:
            self.logger.warning(f"Error verifying database is empty: {e}")
            return True  # Assume it's okay if we can't verify
    
    def _verify_database_restore(self, db_name: str) -> bool:
        """
        Verify that a database restore was successful by checking:
        1. Database exists
        2. Database has tables
        3. Database has data (at least some rows)
        
        Returns:
            bool: True if verification passes, False otherwise
        """
        try:
            # Ensure psql_path is set
            if not hasattr(self, 'psql_path') or not self.psql_path:
                self.psql_path = 'psql'
            
            # 1. Check if database exists
            check_db_cmd = [
                self.psql_path,
                f'-h{self.host}',
                f'-p{self.port}',
                f'-U{self.username}',
                'postgres',
                '-t', '-A',
                '-c', f"SELECT 1 FROM pg_database WHERE datname = '{db_name}';"
            ]
            
            result = subprocess.run(
                check_db_cmd,
                env={**os.environ, 'PGPASSWORD': self.password} if self.password else os.environ,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0 or not result.stdout.strip():
                self.logger.error(f"Verification failed: Database '{db_name}' does not exist")
                return False
            
            self.logger.info(f"✓ Database '{db_name}' exists")
            
            # 2. Check if database has tables
            check_tables_cmd = [
                self.psql_path,
                f'-h{self.host}',
                f'-p{self.port}',
                f'-U{self.username}',
                db_name,
                '-t', '-A',
                '-c', "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE';"
            ]
            
            result = subprocess.run(
                check_tables_cmd,
                env={**os.environ, 'PGPASSWORD': self.password} if self.password else os.environ,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                self.logger.error(f"Verification failed: Could not check tables in database '{db_name}': {result.stderr}")
                return False
            
            table_count = result.stdout.strip()
            try:
                table_count_int = int(table_count) if table_count else 0
            except ValueError:
                self.logger.warning(f"Could not parse table count: {table_count}")
                table_count_int = 0
            
            if table_count_int == 0:
                self.logger.warning(f"⚠ Database '{db_name}' exists but has no tables. Restore may have failed or database is empty.")
                return False
            
            self.logger.info(f"✓ Database '{db_name}' has {table_count_int} table(s)")
            
            # 3. Check if database has data (check total row count across all tables)
            check_data_cmd = [
                self.psql_path,
                f'-h{self.host}',
                f'-p{self.port}',
                f'-U{self.username}',
                db_name,
                '-t', '-A',
                '-c', """
                SELECT COALESCE(SUM(n_tup_ins), 0) as total_rows
                FROM pg_stat_user_tables;
                """
            ]
            
            result = subprocess.run(
                check_data_cmd,
                env={**os.environ, 'PGPASSWORD': self.password} if self.password else os.environ,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                total_rows = result.stdout.strip()
                try:
                    total_rows_int = int(total_rows) if total_rows else 0
                    if total_rows_int > 0:
                        self.logger.info(f"✓ Database '{db_name}' contains data (estimated {total_rows_int} rows)")
                    else:
                        self.logger.warning(f"⚠ Database '{db_name}' has tables but appears to have no data")
                except ValueError:
                    pass
            
            # Alternative: Get a sample of table names
            list_tables_cmd = [
                self.psql_path,
                f'-h{self.host}',
                f'-p{self.port}',
                f'-U{self.username}',
                db_name,
                '-t', '-A',
                '-c', "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' LIMIT 10;"
            ]
            
            result = subprocess.run(
                list_tables_cmd,
                env={**os.environ, 'PGPASSWORD': self.password} if self.password else os.environ,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0 and result.stdout.strip():
                tables = [t.strip() for t in result.stdout.strip().split('\n') if t.strip()]
                if tables:
                    self.logger.info(f"✓ Sample tables found: {', '.join(tables[:5])}{'...' if len(tables) > 5 else ''}")
            
            self.logger.info(f"✓ Restore verification passed for database '{db_name}'")
            return True
            
        except Exception as e:
            self.logger.error(f"Verification error for database '{db_name}': {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False

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
            # Ensure psql_path is set before proceeding
            if not hasattr(self, 'psql_path') or not self.psql_path or self.psql_path is None:
                self.logger.warning("psql_path not set, attempting to detect...")
                try:
                    detected_psql = self._find_any_pg_tool('psql')
                    self.psql_path = detected_psql if detected_psql else 'psql'
                except Exception as e:
                    self.logger.warning(f"Could not detect psql tool: {e}, using default 'psql'")
                    self.psql_path = 'psql'
            
            # Final safety check
            if self.psql_path is None or not isinstance(self.psql_path, str) or not self.psql_path:
                self.logger.error("psql_path is invalid, using default 'psql'")
                self.psql_path = 'psql'
            
            if not hasattr(self, 'pg_dump_path') or not self.pg_dump_path or self.pg_dump_path is None:
                try:
                    detected_pg_dump = self._find_any_pg_tool('pg_dump')
                    self.pg_dump_path = detected_pg_dump if detected_pg_dump else 'pg_dump'
                except Exception as e:
                    self.logger.warning(f"Could not detect pg_dump tool: {e}, using default 'pg_dump'")
                    self.pg_dump_path = 'pg_dump'
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
                        # Create fresh database with collation mismatch auto-fix
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
                            max_create_attempts = 2
                            for create_attempt in range(max_create_attempts):
                                result = subprocess.run(
                                    create_cmd,
                                    env={**os.environ, 'PGPASSWORD': self.password},
                                    capture_output=True,
                                    text=True,
                                    timeout=30
                                )
                                
                                if result.returncode == 0:
                                    self.logger.info(f"Successfully created database {database}")
                                    # Verify database is clean before restore
                                    self._verify_database_empty(database)
                                    break
                                
                                # Check for collation version mismatch error
                                stderr_lower = result.stderr.lower() if result.stderr else ''
                                if 'collation version mismatch' in stderr_lower and create_attempt < max_create_attempts - 1:
                                    self.logger.warning(
                                        "Detected collation version mismatch on template database. "
                                        "Attempting automatic fix..."
                                    )
                                    
                                    # Try to fix the collation mismatch
                                    if self._fix_collation_version_mismatch():
                                        self.logger.info("Collation fix applied, retrying database creation...")
                                        continue
                                    else:
                                        self.logger.error(
                                            "Could not automatically fix collation version mismatch. "
                                            "Please run manually as superuser:\n"
                                            "  ALTER DATABASE template1 REFRESH COLLATION VERSION;\n"
                                            "  ALTER DATABASE postgres REFRESH COLLATION VERSION;"
                                        )
                                
                                # If we reach here on last attempt, raise error
                                if create_attempt == max_create_attempts - 1:
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
            
            # Filter out problematic parameters that PostgreSQL doesn't recognize
            filtered_backup_file, temp_filtered_file_path = self._filter_sql_dump(backup_file)
            
            restore_cmd = [
                self.psql_path,
                f'-h{self.host}',
                f'-p{self.port}',
                f'-U{self.username}',
                '-d', database,
                '--single-transaction',  # Atomic restore - all or nothing
                '-v', 'ON_ERROR_STOP=1',  # Stop on errors
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

                # Parse stderr for errors vs warnings
                error_count = 0
                warning_count = 0
                critical_errors = []
                
                if result.stderr:
                    stderr_lines = result.stderr.split('\n')
                    for line in stderr_lines:
                        if 'ERROR:' in line.upper():
                            error_count += 1
                            critical_errors.append(line.strip())
                        elif 'WARNING:' in line.upper() or 'error: invalid command' in line.lower():
                            warning_count += 1

                if result.returncode == 0:
                    if error_count > 0:
                        self.logger.warning(f"Restore completed with exit code 0 but {error_count} ERROR(s) detected")
                        self.logger.warning("This may indicate partial restore or data integrity issues")
                        self.logger.error("Critical errors found:")
                        for i, error in enumerate(critical_errors[:20], 1):  # Show first 20 errors
                            self.logger.error(f"  {i}. {error}")
                        if len(critical_errors) > 20:
                            self.logger.error(f"  ... and {len(critical_errors) - 20} more errors")
                        if result.stderr:
                            self.logger.warning(f"Full error output: {result.stderr[:5000]}")  # Limit output
                    elif warning_count > 0:
                        self.logger.warning(f"Restore completed with {warning_count} warning(s)")
                        if result.stderr:
                            self.logger.warning(f"Warnings: {result.stderr[:2000]}")  # Limit output
                    else:
                        self.logger.info(f"Successfully restored database {database}")
                else:
                    self.logger.error(f"Database restore failed with exit code: {result.returncode}")
                    if error_count > 0:
                        self.logger.error(f"Found {error_count} ERROR(s) and {warning_count} warning(s)")
                        self.logger.error("Critical errors:")
                        for i, error in enumerate(critical_errors[:20], 1):
                            self.logger.error(f"  {i}. {error}")
                        if len(critical_errors) > 20:
                            self.logger.error(f"  ... and {len(critical_errors) - 20} more errors")
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

            # Verify the restore was successful
            self.logger.info(f"Verifying restore for database '{database}'...")
            if self._verify_database_restore(database):
                self.logger.info(f"✓ Restore verification successful for database '{database}'")
                print(f"✓ Restore verification successful for database '{database}'")
            else:
                self.logger.error(f"✗ Restore verification failed for database '{database}'")
                print(f"✗ WARNING: Restore verification failed for database '{database}'. Please check the database manually.")
                # Don't return False here - the restore command succeeded, verification is just a check

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
            # For docker compose commands, we need to use --project-directory with the HOST path
            # because docker compose runs on the host and needs the host filesystem path
            if cwd:
                self.logger.info(f"Converting container path: {cwd}")
                # Convert container path to host path for docker compose
                # Container paths can be:
                #   /host/path/to/file -> /path/to/file (simple case)
                #   /host/host_mnt/path/to/file -> /path/to/file (nested mount case)
                host_cwd = cwd
                # Check for nested mount case first (longer prefix)
                if cwd.startswith('/host/host_mnt/'):
                    # Remove /host/host_mnt prefix to get host path
                    host_cwd = cwd[15:]  # len('/host/host_mnt/') = 15
                    self.logger.info(f"Matched /host/host_mnt/ pattern, converted to: {host_cwd}")
                elif cwd.startswith('/host/host_mnt'):
                    # Handle case without trailing slash
                    host_cwd = cwd[14:]  # len('/host/host_mnt') = 14
                    if not host_cwd.startswith('/'):
                        host_cwd = '/' + host_cwd
                    self.logger.info(f"Matched /host/host_mnt pattern, converted to: {host_cwd}")
                elif cwd.startswith('/host/'):
                    # Remove /host prefix to get host path
                    host_cwd = cwd[6:]  # len('/host/') = 6
                    self.logger.info(f"Matched /host/ pattern, converted to: {host_cwd}")
                elif cwd.startswith('/host'):
                    # Handle case without trailing slash
                    host_cwd = cwd[5:]  # len('/host') = 5
                    if not host_cwd.startswith('/'):
                        host_cwd = '/' + host_cwd
                    self.logger.info(f"Matched /host pattern, converted to: {host_cwd}")
                else:
                    self.logger.info(f"Path does not start with /host, using as-is: {host_cwd}")
                
                # Ensure we have an absolute path
                if not host_cwd.startswith('/'):
                    host_cwd = '/' + host_cwd
                
                # Validate the conversion - the host path should not contain /host or /host_mnt
                # This is a critical check to ensure we don't use container paths on the host
                original_host_cwd = host_cwd
                if '/host' in host_cwd or host_cwd.startswith('/host_mnt'):
                    self.logger.warning(f"Path conversion may have failed. Original: {cwd}, Converted: {host_cwd}")
                    # Try to fix: if it still starts with /host_mnt, remove that too
                    if host_cwd.startswith('/host_mnt/'):
                        host_cwd = host_cwd[10:]  # len('/host_mnt/') = 10
                        self.logger.info(f"Fixed path by removing /host_mnt/: {host_cwd}")
                    elif host_cwd.startswith('/host_mnt'):
                        host_cwd = host_cwd[9:]  # len('/host_mnt') = 9
                        if not host_cwd.startswith('/'):
                            host_cwd = '/' + host_cwd
                        self.logger.info(f"Fixed path by removing /host_mnt: {host_cwd}")
                    # Also check for /host/ prefix that might have been missed
                    if host_cwd.startswith('/host/'):
                        host_cwd = host_cwd[6:]  # len('/host/') = 6
                        self.logger.info(f"Fixed path by removing /host/: {host_cwd}")
                    elif host_cwd.startswith('/host'):
                        host_cwd = host_cwd[5:]  # len('/host') = 5
                        if not host_cwd.startswith('/'):
                            host_cwd = '/' + host_cwd
                        self.logger.info(f"Fixed path by removing /host: {host_cwd}")
                
                # Final validation - ensure the path is clean
                if '/host' in host_cwd or host_cwd.startswith('/host_mnt'):
                    self.logger.error(f"Path conversion failed completely! Original: {cwd}, Final: {host_cwd}")
                    # Last resort: try to extract just the path after the last /host or /host_mnt
                    # This is a fallback that should rarely be needed
                    parts = host_cwd.split('/')
                    # Find the index after /host or /host_mnt
                    start_idx = 0
                    for i, part in enumerate(parts):
                        if part == 'host_mnt' and i > 0 and parts[i-1] == 'host':
                            start_idx = i + 1
                            break
                        elif part == 'host' and i > 0:
                            start_idx = i + 1
                            break
                    if start_idx > 0:
                        host_cwd = '/' + '/'.join(parts[start_idx:])
                        self.logger.info(f"Emergency path fix: {host_cwd}")
                
                self.logger.info(f"Final converted path: {host_cwd} (was: {original_host_cwd})")
                
                # For docker compose commands, use --project-directory instead of cd
                # This works because docker compose runs on the host via the mounted socket
                is_docker_compose = 'docker compose' in cmd or 'docker-compose' in cmd
                self.logger.info(f"Command is docker compose: {is_docker_compose}, command: {cmd}")
                
                if is_docker_compose:
                    # Use --project-directory flag for docker compose
                    if '--project-directory' not in cmd:
                        # Insert --project-directory before the subcommand (build, up, etc.)
                        # Quote the path to handle spaces
                        quoted_path = shlex.quote(host_cwd)
                        parts = cmd.split()
                        self.logger.info(f"Command parts: {parts}")
                        if len(parts) >= 2 and parts[0] in ['docker', 'docker-compose']:
                            # Insert after 'compose' or after 'docker-compose'
                            insert_idx = 2 if parts[0] == 'docker' else 1
                            parts.insert(insert_idx, '--project-directory')
                            parts.insert(insert_idx + 1, quoted_path)
                            full_cmd = ' '.join(parts)
                            self.logger.info(f"Constructed docker compose command: {full_cmd}")
                        else:
                            # Fallback: use --project-directory with host path
                            # This should not happen for normal docker compose commands
                            self.logger.warning(f"Unexpected command format, using --project-directory fallback: {cmd}")
                            # Extract the subcommand (build, up, etc.)
                            subcmd = cmd.replace('docker compose', '').replace('docker-compose', '').strip()
                            full_cmd = f"docker compose --project-directory {quoted_path} {subcmd}"
                            self.logger.info(f"Fallback docker compose command: {full_cmd}")
                    else:
                        # --project-directory already present, just use the command as-is
                        full_cmd = cmd
                        self.logger.info(f"--project-directory already in command, using as-is: {full_cmd}")
                else:
                    # For non-docker commands, cd to container path (they run inside container)
                    full_cmd = f"cd {shlex.quote(cwd)} && {cmd}"
                    self.logger.info(f"Non-docker command, using cd: {full_cmd}")
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
            
            self.logger.info(f"Executing command: {full_cmd}")
            process = subprocess.run(
                full_cmd,
                shell=True,
                capture_output=True,
                text=True
            )
            
            if process.returncode != 0:
                self.logger.error(f"Command failed with exit code {process.returncode}")
                self.logger.error(f"Command stderr: {process.stderr}")
                self.logger.error(f"Command stdout: {process.stdout}")
                return False
            
            if process.stdout:
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
            # Container: /host/host_mnt/home/user/doob -> Host: /home/user/doob
            if target_project_path.startswith('/host/host_mnt/'):
                host_target_path = target_project_path[15:]  # len('/host/host_mnt/') = 15
            elif target_project_path.startswith('/host/host_mnt'):
                host_target_path = target_project_path[14:]  # len('/host/host_mnt') = 14
                if not host_target_path.startswith('/'):
                    host_target_path = '/' + host_target_path
            elif target_project_path.startswith('/host/'):
                host_target_path = target_project_path[6:]  # len('/host/') = 6
            elif target_project_path.startswith('/host'):
                host_target_path = target_project_path[5:]  # len('/host') = 5
                if not host_target_path.startswith('/'):
                    host_target_path = '/' + host_target_path
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
    parser.add_argument('--sql-dump-file', help='Specific SQL dump file to restore (for database-only restore)')
    parser.add_argument('--include-folders', nargs='+', help='Folders to include in backup')
    parser.add_argument('--backup-dir', default='/tmp/db-backups', help='Folder where backups are going to be stored')
    parser.add_argument('--skip-filestore', action='store_true', help='Skip filestore restoration (restore database only)')
    parser.add_argument('--project-path', help='Project root path where filestore will be restored (defaults to /home/ubuntu/projects/{database})')
    parser.add_argument('--filestore-path', help='Custom filestore path (overrides project-path)')
    
    # New Restore Arguments
    parser.add_argument('--restore-type', choices=['database', 'project'], default='database', help='Type of restore operation')
    parser.add_argument('--target-project-path', help='Target path on host for full project restore')
    parser.add_argument('--skip-database', action='store_true', help='Skip database restore in project mode')
    parser.add_argument('--preserve-existing', action='store_true', help='Preserve existing files/directories in target path (merge instead of replace)')
    parser.add_argument('--post-restore-commands', nargs='+', help='Commands to run after restore')

    # Database connection arguments
    parser.add_argument('--host', default='localhost', help='PostgreSQL server host')
    parser.add_argument('--port', type=int, default=5432, help='PostgreSQL server port')
    parser.add_argument('--username', default='postgres', help='Database username')
    parser.add_argument('--password', help='Database password')
    parser.add_argument('--drop-existing', action='store_true', help='Drop existing database before restore')
    parser.add_argument('--ignore-errors', action='store_true', 
                        help='Continue restore even if errors occur (useful for dumps with duplicate constraints)')

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
        target_databases = [args.database] if args.database else None
        
        backup_manager.backup_session(
            databases=target_databases,
            include_folders=args.include_folders,
            project_path=args.project_path
        )

    elif args.action == 'restore':
        # Handle project restore vs database restore
        if args.restore_type == 'project':
            # Project restore - restore full project (files + database)
            backup_manager.logger.info("Starting project restore...")
            
            # Ensure psql_path is initialized early for project restore
            if not hasattr(backup_manager, 'psql_path') or backup_manager.psql_path is None:
                backup_manager.logger.info("Initializing psql_path for project restore...")
                try:
                    detected_psql = backup_manager._find_any_pg_tool('psql')
                    backup_manager.psql_path = detected_psql if detected_psql else 'psql'
                except Exception as e:
                    backup_manager.logger.warning(f"Could not detect psql tool: {e}, using default 'psql'")
                    backup_manager.psql_path = 'psql'
            
            # Final safety check
            if backup_manager.psql_path is None or not isinstance(backup_manager.psql_path, str):
                backup_manager.logger.error("psql_path is invalid, using default 'psql'")
                backup_manager.psql_path = 'psql'
            
            if not args.target_project_path:
                backup_manager.logger.error("--target-project-path is required for project restore.")
                print("Error: --target-project-path is required for project restore.")
                sys.exit(1)
            
            # Find backup session if not specified
            if not args.backup_file:
                try:
                    # Ensure backup directory exists and is accessible
                    if not os.path.exists(args.backup_dir):
                        backup_manager.logger.error(f"Backup directory does not exist: {args.backup_dir}")
                        print(f"Error: Backup directory does not exist: {args.backup_dir}")
                        sys.exit(1)
                    
                    if not os.path.isdir(args.backup_dir):
                        backup_manager.logger.error(f"Backup path is not a directory: {args.backup_dir}")
                        print(f"Error: Backup path is not a directory: {args.backup_dir}")
                        sys.exit(1)
                    
                    # List all items in backup directory
                    all_items = os.listdir(args.backup_dir)
                    backup_manager.logger.info(f"Searching for backup sessions in: {args.backup_dir}")
                    backup_manager.logger.info(f"Found {len(all_items)} items in backup directory")
                    
                    # Filter for backup session directories (format: YYYY-MM-DD_HH-MM-SS)
                    # Exclude temporary directories
                    temp_prefixes = ['temp_', 'tmp_']
                    sessions = []
                    for d in all_items:
                        item_path = os.path.join(args.backup_dir, d)
                        # Skip if not a directory
                        if not os.path.isdir(item_path):
                            continue
                        # Skip temporary directories
                        if any(d.startswith(prefix) for prefix in temp_prefixes):
                            continue
                        # Validate timestamp format: YYYY-MM-DD_HH-MM-SS
                        parts = d.split('_')
                        if len(parts) >= 2:
                            # Check if first part looks like a date (YYYY-MM-DD)
                            date_part = parts[0]
                            if len(date_part.split('-')) == 3:
                                # Check if it's a valid backup session by looking for databases/ or files.tar.gz
                                has_databases = os.path.exists(os.path.join(item_path, 'databases'))
                                has_files = os.path.exists(os.path.join(item_path, 'files.tar.gz'))
                                if has_databases or has_files:
                                    sessions.append(d)
                    
                    if sessions:
                        sessions.sort(reverse=True)
                        args.backup_file = os.path.join(args.backup_dir, sessions[0])
                        backup_manager.logger.info(f"Auto-selected latest backup session: {args.backup_file}")
                    else:
                        backup_manager.logger.warning(f"No backup session directories found in {args.backup_dir}")
                        backup_manager.logger.warning(f"Available items: {all_items}")
                except FileNotFoundError as e:
                    backup_manager.logger.error(f"Backup directory not found: {args.backup_dir} - {e}")
                    print(f"Error: Backup directory not found: {args.backup_dir}")
                    sys.exit(1)
                except PermissionError as e:
                    backup_manager.logger.error(f"Permission denied accessing backup directory: {args.backup_dir} - {e}")
                    print(f"Error: Permission denied accessing backup directory: {args.backup_dir}")
                    sys.exit(1)
                except Exception as e:
                    backup_manager.logger.error(f"Error searching for backup sessions: {e}")
                    print(f"Error: Failed to search backup directory: {e}")
                    sys.exit(1)
            
            if not args.backup_file or not os.path.isdir(args.backup_file):
                backup_manager.logger.error(f"Could not find any backup session directory in {args.backup_dir}")
                print(f"Error: Could not find any backup session directory.")
                print(f"       Searched in: {args.backup_dir}")
                print(f"       Please ensure backups exist in the backup directory.")
                sys.exit(1)
            
            backup_manager.logger.info(f"Restoring from: {args.backup_file}")
            print(f"Restoring from: {args.backup_file}")
            
            # Find database name from backup
            db_dir = os.path.join(args.backup_file, 'databases')
            database_name = args.database
            if not database_name and os.path.exists(db_dir):
                dump_files = [f for f in os.listdir(db_dir) if f.endswith('.sql')]
                if dump_files:
                    database_name = dump_files[0][:-4]  # Remove .sql extension
                    backup_manager.logger.info(f"Auto-detected database name: {database_name}")
            
            if not database_name:
                backup_manager.logger.error("Could not determine database name.")
                print("Error: Could not determine database name. Please specify --database.")
                sys.exit(1)
            
            # Find database dump file
            db_dump_path = None
            if os.path.exists(db_dir):
                db_dump_file = os.path.join(db_dir, f"{database_name}.sql")
                if os.path.exists(db_dump_file):
                    db_dump_path = db_dump_file
                    backup_manager.logger.info(f"Found database dump: {db_dump_path}")
                else:
                    backup_manager.logger.warning(f"Database dump not found: {db_dump_file}")
            
            # Step 1: Extract project files FIRST (before Docker setup)
            files_archive = os.path.join(args.backup_file, 'files.tar.gz')
            if os.path.exists(files_archive):
                backup_manager.logger.info(f"Extracting project files to {args.target_project_path}...")
                print(f"Extracting project files to {args.target_project_path}...")
                
                # Clear target directory (unless --preserve-existing is set)
                if os.path.exists(args.target_project_path):
                    if os.listdir(args.target_project_path):
                        if args.preserve_existing:
                            backup_manager.logger.info("Preserving existing files/directories (--preserve-existing flag set)")
                            print("Preserving existing files/directories in target directory...")
                        else:
                            backup_manager.logger.info("Clearing target directory...")
                            failed_items = []
                            for item in os.listdir(args.target_project_path):
                                item_path = os.path.join(args.target_project_path, item)
                                try:
                                    # Try to fix permissions first (if we have access)
                                    try:
                                        if os.path.isdir(item_path):
                                            os.chmod(item_path, 0o755)
                                        else:
                                            os.chmod(item_path, 0o644)
                                    except (OSError, PermissionError):
                                        pass  # Ignore permission errors when trying to fix permissions
                                    
                                    if os.path.isfile(item_path) or os.path.islink(item_path):
                                        os.unlink(item_path)
                                    elif os.path.isdir(item_path):
                                        shutil.rmtree(item_path)
                                except PermissionError as e:
                                    failed_items.append(item_path)
                                    backup_manager.logger.warning(f"Permission denied removing {item_path}: {e}")
                                    backup_manager.logger.warning(f"  You may need to manually remove this item or fix permissions")
                                except Exception as e:
                                    failed_items.append(item_path)
                                    backup_manager.logger.warning(f"Could not remove {item_path}: {e}")
                            
                            if failed_items:
                                backup_manager.logger.warning(f"Could not remove {len(failed_items)} item(s) due to permission errors")
                                backup_manager.logger.warning(f"Restore will continue, but these items may conflict with extracted files")
                                print(f"Warning: Could not remove {len(failed_items)} item(s) from target directory due to permission errors")
                                print(f"         Restore will continue, but you may need to manually clean up these items later")
                                print(f"         Tip: Use --preserve-existing flag to keep existing files/directories")
                else:
                    os.makedirs(args.target_project_path, exist_ok=True)
                
                # Extract files
                try:
                    with tarfile.open(files_archive, 'r:gz') as tar:
                        # Extract to temp first to handle root directory in tar
                        temp_extract = os.path.join(args.backup_dir, 'temp_project_extract')
                        if os.path.exists(temp_extract):
                            shutil.rmtree(temp_extract)
                        os.makedirs(temp_extract, exist_ok=True)
                        tar.extractall(temp_extract)
                        
                        # Move contents to target (handle case where tar contains root folder)
                        items = os.listdir(temp_extract)
                        if len(items) == 1 and os.path.isdir(os.path.join(temp_extract, items[0])):
                            src_dir = os.path.join(temp_extract, items[0])
                            for item in os.listdir(src_dir):
                                src_path = os.path.join(src_dir, item)
                                dst_path = os.path.join(args.target_project_path, item)
                                try:
                                    # If destination exists, remove it first (might have been left due to permission issues)
                                    if os.path.exists(dst_path):
                                        try:
                                            if os.path.isdir(dst_path):
                                                shutil.rmtree(dst_path)
                                            else:
                                                os.unlink(dst_path)
                                        except (PermissionError, OSError) as e:
                                            backup_manager.logger.warning(f"Could not remove existing {dst_path}, will try to overwrite: {e}")
                                    shutil.move(src_path, args.target_project_path)
                                except Exception as e:
                                    backup_manager.logger.error(f"Failed to move {item} to target: {e}")
                                    raise
                        else:
                            for item in items:
                                src_path = os.path.join(temp_extract, item)
                                dst_path = os.path.join(args.target_project_path, item)
                                try:
                                    # If destination exists, remove it first
                                    if os.path.exists(dst_path):
                                        try:
                                            if os.path.isdir(dst_path):
                                                shutil.rmtree(dst_path)
                                            else:
                                                os.unlink(dst_path)
                                        except (PermissionError, OSError) as e:
                                            backup_manager.logger.warning(f"Could not remove existing {dst_path}, will try to overwrite: {e}")
                                    shutil.move(src_path, args.target_project_path)
                                except Exception as e:
                                    backup_manager.logger.error(f"Failed to move {item} to target: {e}")
                                    raise
                        
                        # Cleanup
                        if os.path.exists(temp_extract):
                            shutil.rmtree(temp_extract)
                    
                    backup_manager.logger.info("Project files extracted successfully")
                except Exception as e:
                    backup_manager.logger.error(f"Failed to extract project files: {e}")
                    print(f"Error: Failed to extract project files: {e}")
                    sys.exit(1)
            else:
                backup_manager.logger.warning(f"Files archive not found: {files_archive}")
                backup_manager.logger.info("Project files archive not found in backup. Assuming target directory already contains project files.")
                # Ensure target directory exists
                if not os.path.exists(args.target_project_path):
                    os.makedirs(args.target_project_path, exist_ok=True)
            
            # Step 2: Check for .env file (required for Docker setup)
            env_file = os.path.join(args.target_project_path, '.env')
            if not os.path.exists(env_file):
                backup_manager.logger.error(f".env file not found at {env_file}")
                print(f"Error: .env file not found at {env_file}")
                print(f"")
                print(f"This can happen if:")
                print(f"  1. The backup doesn't include project files (files.tar.gz is missing)")
                print(f"  2. The target directory doesn't contain the project files")
                print(f"")
                print(f"Solutions:")
                print(f"  - Create a backup that includes project files (use --project-path during backup)")
                print(f"  - Or manually place the project files (including .env) in: {args.target_project_path}")
                print(f"  - Or use database-only restore instead of project restore")
                sys.exit(1)
            
            # Step 3: Read .env and set up Docker
            backup_manager.logger.info("Reading database configuration from .env...")
            db_config = {}
            try:
                with open(env_file, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        if '=' in line:
                            key, val = line.split('=', 1)
                            db_config[key.strip()] = val.strip()
                
                # Update connection details
                backup_manager.host = 'host.docker.internal'
                backup_manager.port = int(db_config.get('DB_PORT', 5432))
                backup_manager.username = db_config.get('DB_USER', 'postgres')
                backup_manager.password = db_config.get('DB_PASSWORD')
                
                backup_manager.logger.info(f"Database config: host={backup_manager.host}, port={backup_manager.port}, user={backup_manager.username}")
            except Exception as e:
                backup_manager.logger.error(f"Failed to read .env file: {e}")
                print(f"Error: Failed to read .env file: {e}")
                sys.exit(1)
            
            # Step 4: Build and start Docker services
            success = True
            backup_manager.logger.info("Building Docker services...")
            print("Building Docker services...")
            if not backup_manager._run_host_command("docker compose build", cwd=args.target_project_path):
                backup_manager.logger.error("Docker compose build failed")
                print("Error: Docker compose build failed")
                success = False
            else:
                backup_manager.logger.info("Starting database service...")
                print("Starting database service...")
                if not backup_manager._run_host_command("docker compose up db -d", cwd=args.target_project_path):
                    backup_manager.logger.error("Failed to start database service")
                    print("Error: Failed to start database service")
                    success = False
                else:
                    # Wait for DB to be ready
                    backup_manager.logger.info("Waiting for database to be ready...")
                    print("Waiting for database to be ready...")
                    db_ready = False
                    for i in range(30):
                        try:
                            backup_manager._test_database_connection()
                            db_ready = True
                            backup_manager.logger.info("Database is ready")
                            break
                        except Exception as e:
                            if i < 29:
                                time.sleep(2)
                            else:
                                backup_manager.logger.error(f"Database failed to start in time: {e}")
                    
                    if not db_ready:
                        backup_manager.logger.error("Database failed to start in time")
                        print("Error: Database failed to start in time")
                        success = False
                    else:
                        # Re-detect tools with new connection
                        backup_manager.logger.info("Re-detecting PostgreSQL tools with new connection...")
                        backup_manager._detect_pg_tools()
            
            # Step 5: Restore database (after docker is up)
            if success and not args.skip_database and db_dump_path:
                backup_manager.logger.info(f"Restoring database: {database_name}")
                print(f"Restoring database: {database_name}")
                try:
                    success = backup_manager.restore_database(
                        database=database_name,
                        backup_file=db_dump_path,
                        drop_existing=args.drop_existing,
                        restore_filestore=False
                    )
                    if success:
                        backup_manager.logger.info("Database restored successfully")
                        print("Database restored successfully")
                    else:
                        backup_manager.logger.error("Database restore failed")
                        print("Error: Database restore failed")
                except Exception as e:
                    backup_manager.logger.error(f"Database restore error: {e}")
                    print(f"Error: Database restore failed: {e}")
                    success = False
            elif args.skip_database:
                backup_manager.logger.info("Skipping database restore (--skip-database flag set)")
            elif not db_dump_path:
                backup_manager.logger.warning("No database dump file found, skipping database restore")
            
            # Step 6: Start remaining services and run post-restore commands
            if success:
                backup_manager.logger.info("Starting remaining services...")
                print("Starting remaining services...")
                if not backup_manager._run_host_command("docker compose up -d", cwd=args.target_project_path):
                    backup_manager.logger.warning("Failed to start remaining services, but core restore succeeded")
                    print("Warning: Failed to start remaining services")
                
                # Run post-restore commands if specified
                if args.post_restore_commands:
                    backup_manager.logger.info("Running post-restore commands...")
                    for cmd in args.post_restore_commands:
                        if not backup_manager._run_host_command(cmd, cwd=args.target_project_path):
                            backup_manager.logger.warning(f"Post-restore command failed: {cmd}")
            
            if success:
                backup_manager.logger.info("Project restore completed successfully")
                print("Project restore completed successfully")
            else:
                backup_manager.logger.error("Project restore failed")
                print("Error: Project restore failed. Check logs for details.")
            
        else:
            # Database restore - restore databases from session backup
            # If sql_dump_file is provided, we can skip finding backup_file
            if args.sql_dump_file:
                # Use the SQL dump file directly
                if not args.database:
                    # Try to extract database name from filename
                    db_name = os.path.basename(args.sql_dump_file)
                    if db_name.endswith('.sql'):
                        db_name = db_name[:-4]
                    else:
                        backup_manager.logger.error("Cannot determine database name from SQL dump file. Please specify --database")
                        print("Error: Cannot determine database name from SQL dump file. Please specify --database")
                        sys.exit(1)
                    args.database = db_name
                
                backup_manager.logger.info(f"Restoring database '{args.database}' from SQL dump file: {args.sql_dump_file}")
                success = backup_manager.restore_session(
                    backup_path="",  # Not needed when sql_dump_file is provided
                    restore_type='specific',
                    specific_db=args.database,
                    drop_existing=args.drop_existing,
                    sql_dump_file=args.sql_dump_file,
                    ignore_errors=args.ignore_errors
                )
                # Exit early since we've already restored from the SQL dump file
                sys.exit(0 if success else 1)
            # Find backup session if not specified
            elif not args.backup_file:
                try:
                    # Ensure backup directory exists and is accessible
                    if not os.path.exists(args.backup_dir):
                        backup_manager.logger.error(f"Backup directory does not exist: {args.backup_dir}")
                        print(f"Error: Backup directory does not exist: {args.backup_dir}")
                        sys.exit(1)
                    
                    if not os.path.isdir(args.backup_dir):
                        backup_manager.logger.error(f"Backup path is not a directory: {args.backup_dir}")
                        print(f"Error: Backup path is not a directory: {args.backup_dir}")
                        sys.exit(1)
                    
                    # List all items in backup directory
                    all_items = os.listdir(args.backup_dir)
                    backup_manager.logger.info(f"Searching for backup sessions in: {args.backup_dir}")
                    backup_manager.logger.info(f"Found {len(all_items)} items in backup directory")
                    
                    # Filter for backup session directories (format: YYYY-MM-DD_HH-MM-SS)
                    # Exclude temporary directories
                    temp_prefixes = ['temp_', 'tmp_']
                    sessions = []
                    for d in all_items:
                        item_path = os.path.join(args.backup_dir, d)
                        # Skip if not a directory
                        if not os.path.isdir(item_path):
                            continue
                        # Skip temporary directories
                        if any(d.startswith(prefix) for prefix in temp_prefixes):
                            continue
                        # Validate timestamp format: YYYY-MM-DD_HH-MM-SS
                        parts = d.split('_')
                        if len(parts) >= 2:
                            # Check if first part looks like a date (YYYY-MM-DD)
                            date_part = parts[0]
                            if len(date_part.split('-')) == 3:
                                # Check if it's a valid backup session by looking for databases/ or files.tar.gz
                                has_databases = os.path.exists(os.path.join(item_path, 'databases'))
                                has_files = os.path.exists(os.path.join(item_path, 'files.tar.gz'))
                                if has_databases or has_files:
                                    sessions.append(d)
                    
                    if sessions:
                        sessions.sort(reverse=True)
                        args.backup_file = os.path.join(args.backup_dir, sessions[0])
                        backup_manager.logger.info(f"Auto-selected latest backup session: {args.backup_file}")
                    else:
                        backup_manager.logger.warning(f"No backup session directories found in {args.backup_dir}")
                        backup_manager.logger.warning(f"Available items: {all_items}")
                except FileNotFoundError as e:
                    backup_manager.logger.error(f"Backup directory not found: {args.backup_dir} - {e}")
                    print(f"Error: Backup directory not found: {args.backup_dir}")
                    sys.exit(1)
                except PermissionError as e:
                    backup_manager.logger.error(f"Permission denied accessing backup directory: {args.backup_dir} - {e}")
                    print(f"Error: Permission denied accessing backup directory: {args.backup_dir}")
                    sys.exit(1)
                except Exception as e:
                    backup_manager.logger.error(f"Error searching for backup sessions: {e}")
                    print(f"Error: Failed to search backup directory: {e}")
                    sys.exit(1)

            if not args.backup_file and not args.sql_dump_file:
                backup_manager.logger.error(f"Could not find any backup session in {args.backup_dir}")
                print("Error: Could not find any backup session.")
                print(f"       Searched in: {args.backup_dir}")
                print(f"       Please ensure backups exist in the backup directory or provide --sql-dump-file.")
                sys.exit(1)
            
            # Check if backup_file is a SQL file or a directory
            backup_path = None
            sql_dump_file_to_use = args.sql_dump_file
            
            if args.backup_file:
                backup_manager.logger.info(f"Checking --backup-file: {args.backup_file}")
                backup_manager.logger.info(f"  - exists: {os.path.exists(args.backup_file)}")
                backup_manager.logger.info(f"  - isfile: {os.path.isfile(args.backup_file) if os.path.exists(args.backup_file) else 'N/A'}")
                backup_manager.logger.info(f"  - isdir: {os.path.isdir(args.backup_file) if os.path.exists(args.backup_file) else 'N/A'}")
                
                if os.path.exists(args.backup_file) and os.path.isfile(args.backup_file) and (args.backup_file.endswith('.sql') or args.backup_file.endswith('.dump')):
                    # backup_file is a SQL file, use it as sql_dump_file
                    backup_manager.logger.info(f"Detected --backup-file as SQL file: {args.backup_file}")
                    sql_dump_file_to_use = args.backup_file
                    # Use the directory containing the file as backup_path (for potential files.tar.gz)
                    backup_path = os.path.dirname(args.backup_file)
                    print(f"Restoring from SQL file: {args.backup_file}")
                elif os.path.exists(args.backup_file) and os.path.isdir(args.backup_file):
                    # backup_file is a directory (backup session)
                    backup_path = args.backup_file
                    print(f"Restoring from backup session: {args.backup_file}")
                else:
                    backup_manager.logger.error(f"--backup-file must be either a directory or a SQL file: {args.backup_file}")
                    backup_manager.logger.error(f"  File exists: {os.path.exists(args.backup_file)}")
                    if os.path.exists(args.backup_file):
                        backup_manager.logger.error(f"  Is file: {os.path.isfile(args.backup_file)}")
                        backup_manager.logger.error(f"  Is dir: {os.path.isdir(args.backup_file)}")
                    print(f"Error: --backup-file must be either a directory or a SQL file: {args.backup_file}")
                    sys.exit(1)
            
            # Check if this is a cluster dump (when no database is specified)
            cluster_dump_detected = False
            if backup_path and not args.database:
                db_dir = os.path.join(backup_path, 'databases')
                if os.path.exists(db_dir):
                    dump_files = [f for f in os.listdir(db_dir) if f.endswith('.sql')]
                    if 'cluster_dump.sql' in dump_files:
                        cluster_dump_detected = True
                        backup_manager.logger.info("Cluster dump detected - will restore entire PostgreSQL cluster")
                        print("Cluster dump detected - will restore entire PostgreSQL cluster")
            
            # Detect restore scope: 'all' or 'specific'
            # If cluster dump is detected, always use 'all' to restore entire cluster
            restore_scope = 'specific' if args.database and not cluster_dump_detected else 'all'
            
            success = backup_manager.restore_session(
                backup_path=backup_path or "",
                restore_type=restore_scope,
                specific_db=args.database if not cluster_dump_detected else None,
                drop_existing=args.drop_existing,
                sql_dump_file=sql_dump_file_to_use,
                ignore_errors=args.ignore_errors
            )
            
            # Restore files if needed (for database restore, files are optional)
            if success and restore_scope == 'all' and not args.skip_filestore:
                files_archive = os.path.join(args.backup_file, 'files.tar.gz')
                if os.path.exists(files_archive):
                    target_path = args.target_project_path or args.project_path
                    if target_path:
                        print(f"Restoring files to {target_path}...")
                        with tarfile.open(files_archive, 'r:gz') as tar:
                            tar.extractall(os.path.dirname(target_path)) 
                    else:
                        print("Warning: Files found in backup but no --target-project-path provided. Skipping file restore.")

        sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()

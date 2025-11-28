#!/usr/bin/env python3
"""
PostgreSQL Backup Manager - Web Application
A modern web interface for PostgreSQL backup/restore operations with full host access.
"""

import os
import sys
import json
import asyncio
import subprocess
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ============================================================================
# Configuration
# ============================================================================

# Get the script directory
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_SCRIPT_PATH = os.environ.get("BACKUP_SCRIPT_PATH", "/app/backup_script.py")
HOST_ROOT = os.environ.get("HOST_ROOT", "/host")  # Root filesystem mount point
DEFAULT_BACKUP_DIR = os.environ.get("DEFAULT_BACKUP_DIR", "/host/tmp/postgres-backups")
# Use local data directory instead of /app
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
CONNECTIONS_FILE = os.environ.get("CONNECTIONS_FILE", "/app/data/connections.json")

# Path translation helpers
def host_path_to_container(path: str) -> str:
    """Convert a host filesystem path to container path."""
    # If path starts with /, assume it's relative to host root
    if path.startswith("/") and not path.startswith(HOST_ROOT):
        # Check if it's already a container path
        if os.path.exists(path):
            return path
        # Try prepending HOST_ROOT
        container_path = os.path.join(HOST_ROOT, path.lstrip("/"))
        if os.path.exists(container_path):
            return container_path
    return path

def container_path_to_host(path: str) -> str:
    """Convert a container path back to host-relative path."""
    if path.startswith(HOST_ROOT):
        return path[len(HOST_ROOT):] or "/"
    return path


# ============================================================================
# PostgreSQL Tools Detection
# ============================================================================

def get_installed_pg_versions() -> List[int]:
    """Discover all installed PostgreSQL versions on the system."""
    import re
    versions = set()
    
    # Check Debian/Ubuntu style: /usr/lib/postgresql/{version}/
    pg_lib_dir = "/usr/lib/postgresql"
    if os.path.isdir(pg_lib_dir):
        for item in os.listdir(pg_lib_dir):
            try:
                versions.add(int(item))
            except ValueError:
                pass
    
    # Check RHEL/CentOS style: /usr/pgsql-{version}/
    if os.path.isdir("/usr"):
        for item in os.listdir("/usr"):
            if item.startswith("pgsql-"):
                try:
                    versions.add(int(item.split("-")[1]))
                except (ValueError, IndexError):
                    pass
    
    # Check macOS style: /Library/PostgreSQL/{version}/
    mac_pg_dir = "/Library/PostgreSQL"
    if os.path.isdir(mac_pg_dir):
        for item in os.listdir(mac_pg_dir):
            try:
                versions.add(int(item))
            except ValueError:
                pass
    
    # Also check what's in PATH
    for tool in ["pg_dump", "psql"]:
        path = shutil.which(tool)
        if path:
            try:
                result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    match = re.search(r"(\d+)\.", result.stdout)
                    if match:
                        versions.add(int(match.group(1)))
            except:
                pass
    
    return sorted(versions, reverse=True)


def get_tool_version(tool_path: str) -> Optional[int]:
    """Get the major version of a PostgreSQL tool."""
    import re
    try:
        result = subprocess.run([tool_path, "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            match = re.search(r"(\d+)\.", result.stdout)
            if match:
                return int(match.group(1))
    except:
        pass
    return None


def find_compatible_pg_tool(tool_name: str, host: str, port: int, username: str, password: str = None) -> str:
    """Find PostgreSQL tool compatible with the server version."""
    import re
    
    # First, try to get server version using any available psql
    server_version = None
    temp_psql = shutil.which("psql")
    
    # Check common paths if not in PATH
    if not temp_psql:
        for v in get_installed_pg_versions():
            paths = [
                f"/usr/lib/postgresql/{v}/bin/psql",
                f"/usr/pgsql-{v}/bin/psql",
                f"/Library/PostgreSQL/{v}/bin/psql",
            ]
            for p in paths:
                if os.path.isfile(p) and os.access(p, os.X_OK):
                    temp_psql = p
                    break
            if temp_psql:
                break
    
    if not temp_psql:
        return tool_name  # Fallback
    
    # Get server version
    try:
        env = os.environ.copy()
        if password:
            env["PGPASSWORD"] = password
        
        result = subprocess.run(
            [temp_psql, f"-h{host}", f"-p{port}", f"-U{username}", "-t", "-A", "-c", "SHOW server_version;"],
            capture_output=True, text=True, env=env, timeout=10
        )
        
        if result.returncode == 0:
            version_str = result.stdout.strip()
            server_version = int(version_str.split(".")[0].split()[0])
    except:
        pass
    
    if not server_version:
        return shutil.which(tool_name) or tool_name
    
    # Find tool with version >= server_version
    candidates = []
    installed_versions = get_installed_pg_versions()
    
    for version in installed_versions:
        if version >= server_version:
            paths = [
                f"/usr/lib/postgresql/{version}/bin/{tool_name}",
                f"/usr/pgsql-{version}/bin/{tool_name}",
                f"/Library/PostgreSQL/{version}/bin/{tool_name}",
            ]
            for path in paths:
                if os.path.isfile(path) and os.access(path, os.X_OK):
                    tool_version = get_tool_version(path)
                    if tool_version and tool_version >= server_version:
                        candidates.append((tool_version, path))
    
    # Check PATH
    path_tool = shutil.which(tool_name)
    if path_tool:
        tool_version = get_tool_version(path_tool)
        if tool_version and tool_version >= server_version:
            candidates.append((tool_version, path_tool))
    
    if candidates:
        candidates.sort(key=lambda x: (x[0] != server_version, x[0]))
        return candidates[0][1]
    
    return shutil.which(tool_name) or tool_name


# ============================================================================
# Pydantic Models
# ============================================================================

class ConnectionConfig(BaseModel):
    host: str = "localhost"
    port: int = 5432
    username: str = "postgres"
    password: Optional[str] = None


class SavedConnection(BaseModel):
    id: str
    name: str
    host: str = "localhost"
    port: int = 5432
    username: str = "postgres"
    password: Optional[str] = None
    color: Optional[str] = None  # For UI identification


class SaveConnectionRequest(BaseModel):
    name: str
    host: str = "localhost"
    port: int = 5432
    username: str = "postgres"
    password: Optional[str] = None
    color: Optional[str] = None


class StorageConfig(BaseModel):
    storage_type: str = "local"
    endpoint: Optional[str] = None
    access_key: Optional[str] = None
    secret_key: Optional[str] = None
    bucket: Optional[str] = None
    region: Optional[str] = None
    credentials_path: Optional[str] = None


class BackupRequest(BaseModel):
    connection: ConnectionConfig
    storage: StorageConfig
    database: Optional[str] = None
    backup_dir: str = DEFAULT_BACKUP_DIR
    include_folders: Optional[List[str]] = None
    project_path: Optional[str] = None


class RestoreRequest(BaseModel):
    connection: ConnectionConfig
    database: str
    backup_dir: str = DEFAULT_BACKUP_DIR
    backup_file: Optional[str] = None
    drop_existing: bool = True
    skip_filestore: bool = False
    filestore_path: Optional[str] = None
    restore_type: str = "database"  # "database" or "project"
    target_project_path: Optional[str] = None
    skip_database: bool = False
    post_restore_commands: Optional[List[str]] = None


class TestConnectionRequest(BaseModel):
    host: str = "localhost"
    port: int = 5432
    username: str = "postgres"
    password: Optional[str] = None


# ============================================================================
# WebSocket Connection Manager
# ============================================================================

class ConnectionManager:
    """Manage WebSocket connections for real-time log streaming."""
    
    def __init__(self):
        self.active_connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
    
    async def broadcast(self, message: dict):
        """Send message to all connected clients."""
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass


manager = ConnectionManager()

# Current operation state
current_operation = {
    "running": False,
    "process": None,
    "type": None
}


# ============================================================================
# FastAPI Application
# ============================================================================

def load_saved_connections() -> List[SavedConnection]:
    """Load saved connections from file."""
    try:
        if os.path.exists(CONNECTIONS_FILE):
            with open(CONNECTIONS_FILE, 'r') as f:
                data = json.load(f)
                return [SavedConnection(**conn) for conn in data]
    except Exception as e:
        print(f"Error loading connections: {e}")
    return []


def save_connections_to_file(connections: List[SavedConnection]):
    """Save connections to file."""
    try:
        os.makedirs(os.path.dirname(CONNECTIONS_FILE), exist_ok=True)
        with open(CONNECTIONS_FILE, 'w') as f:
            json.dump([conn.model_dump() for conn in connections], f, indent=2)
    except Exception as e:
        print(f"Error saving connections: {e}")
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    os.makedirs(DEFAULT_BACKUP_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(CONNECTIONS_FILE), exist_ok=True)
    yield
    # Shutdown
    if current_operation["process"]:
        current_operation["process"].terminate()


app = FastAPI(
    title="PostgreSQL Backup Manager",
    description="Web-based PostgreSQL backup and restore utility",
    version="2.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ============================================================================
# Helper Functions
# ============================================================================

async def log_message(message: str, level: str = "info"):
    """Send log message to all connected WebSocket clients."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    await manager.broadcast({
        "type": "log",
        "timestamp": timestamp,
        "level": level,
        "message": message
    })


async def run_backup_command(cmd: List[str], operation_type: str):
    """Run backup/restore command and stream output via WebSocket."""
    global current_operation
    
    try:
        current_operation["running"] = True
        current_operation["type"] = operation_type
        
        await manager.broadcast({"type": "status", "status": "running", "operation": operation_type})
        
        # Log the command (hide password)
        display_cmd = " ".join(cmd)
        if "--password" in display_cmd:
            parts = display_cmd.split("--password")
            if len(parts) > 1:
                rest = parts[1].split("--", 1)
                hidden = "--password ****"
                if len(rest) > 1:
                    hidden += " --" + rest[1]
                display_cmd = parts[0] + hidden
        
        await log_message(f"Executing: {display_cmd}", "command")
        
        # Start process
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        current_operation["process"] = process
        
        # Stream output
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            
            line_text = line.decode('utf-8', errors='replace').strip()
            if not line_text:
                continue
            
            # Determine log level based on content
            level = "info"
            if "ERROR" in line_text or "error" in line_text.lower() or "failed" in line_text.lower():
                level = "error"
            elif "WARNING" in line_text or "warning" in line_text.lower():
                level = "warning"
            elif "SUCCESS" in line_text or "success" in line_text.lower() or "completed" in line_text.lower():
                level = "success"
            
            await log_message(line_text, level)
        
        # Wait for completion
        return_code = await process.wait()
        
        if return_code == 0:
            await log_message(f"{operation_type.title()} completed successfully!", "success")
            await manager.broadcast({"type": "status", "status": "completed", "success": True})
        else:
            await log_message(f"{operation_type.title()} failed with exit code: {return_code}", "error")
            await manager.broadcast({"type": "status", "status": "completed", "success": False})
        
        return return_code == 0
        
    except asyncio.CancelledError:
        await log_message("Operation cancelled", "warning")
        await manager.broadcast({"type": "status", "status": "cancelled"})
        return False
    except Exception as e:
        await log_message(f"Error: {str(e)}", "error")
        await manager.broadcast({"type": "status", "status": "error", "message": str(e)})
        return False
    finally:
        current_operation["running"] = False
        current_operation["process"] = None
        current_operation["type"] = None


def normalize_path(path: str) -> str:
    """Normalize a path to ensure it's a valid container path."""
    if not path:
        return path
    
    # If path starts with /host_mnt, prepend /host (must check before /host check!)
    if path.startswith("/host_mnt"):
        return "/host" + path
    
    # If path already starts with /host, return as-is
    if path.startswith("/host"):
        return path
    
    # If path is an absolute path (starts with /), prepend HOST_ROOT
    if path.startswith("/"):
        return os.path.join(HOST_ROOT, path.lstrip("/"))
    
    # Relative path, prepend HOST_ROOT
    return os.path.join(HOST_ROOT, path)


def build_backup_command(request: BackupRequest) -> List[str]:
    """Build backup command from request."""
    cmd = ["python", BACKUP_SCRIPT_PATH, "--action", "backup"]
    
    # Connection params
    cmd.extend(["--host", request.connection.host])
    cmd.extend(["--port", str(request.connection.port)])
    cmd.extend(["--username", request.connection.username])
    if request.connection.password:
        cmd.extend(["--password", request.connection.password])
    
    # Backup params
    if request.database:
        cmd.extend(["--database", request.database])
    
    # Normalize backup directory path
    backup_dir = normalize_path(request.backup_dir)
    cmd.extend(["--backup-dir", backup_dir])
    
    # Use project_path if provided, otherwise use include_folders (legacy)
    if request.project_path:
        cmd.extend(["--project-path", normalize_path(request.project_path)])
    elif request.include_folders:
        cmd.append("--include-folders")
        # Normalize each folder path
        for folder in request.include_folders:
            cmd.append(normalize_path(folder))
    
    # Storage params - validate and default to 'local' if invalid
    storage_type = request.storage.storage_type
    valid_storage_types = ['local', 's3', 'minio', 'gdrive']
    if storage_type not in valid_storage_types:
        storage_type = 'local'
    
    cmd.extend(["--storage-type", storage_type])
    
    if storage_type != "local":
        if request.storage.endpoint:
            cmd.extend(["--storage-endpoint", request.storage.endpoint])
        if request.storage.access_key:
            cmd.extend(["--storage-access-key", request.storage.access_key])
        if request.storage.secret_key:
            cmd.extend(["--storage-secret-key", request.storage.secret_key])
        if request.storage.bucket:
            cmd.extend(["--storage-bucket", request.storage.bucket])
        if request.storage.region:
            cmd.extend(["--storage-region", request.storage.region])
        if request.storage.credentials_path:
            cmd.extend(["--storage-credentials", normalize_path(request.storage.credentials_path)])
    
    return cmd


def build_restore_command(request: RestoreRequest) -> List[str]:
    """Build restore command from request."""
    cmd = ["python", BACKUP_SCRIPT_PATH, "--action", "restore"]
    
    # Connection params - only added if restore_type is 'database' or as fallback
    # For project restore, these might be overridden by .env file parsing in the script,
    # but we pass them anyway as defaults/initial connection
    cmd.extend(["--host", request.connection.host])
    cmd.extend(["--port", str(request.connection.port)])
    cmd.extend(["--username", request.connection.username])
    if request.connection.password:
        cmd.extend(["--password", request.connection.password])
    
    # Restore params
    cmd.extend(["--database", request.database])
    
    # Normalize backup directory path
    backup_dir = normalize_path(request.backup_dir)
    cmd.extend(["--backup-dir", backup_dir])
    
    # Restore type
    cmd.extend(["--restore-type", request.restore_type])
    
    if request.backup_file:
        cmd.extend(["--backup-file", normalize_path(request.backup_file)])
    
    if request.drop_existing:
        cmd.append("--drop-existing")
    
    if request.restore_type == "project":
        # Project restore
        if request.target_project_path:
            cmd.extend(["--target-project-path", normalize_path(request.target_project_path)])
        if request.skip_database:
            cmd.append("--skip-database")
        if request.post_restore_commands:
            cmd.append("--post-restore-commands")
            cmd.extend(request.post_restore_commands)
    else:
        # Database-only restore (legacy)
        if request.skip_filestore:
            cmd.append("--skip-filestore")
        if request.filestore_path:
            cmd.extend(["--filestore-path", normalize_path(request.filestore_path)])
    
    return cmd


# ============================================================================
# API Routes
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the main application page."""
    index_path = static_dir / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse("<h1>PostgreSQL Backup Manager</h1><p>Static files not found.</p>")


@app.get("/api/status")
async def get_status():
    """Get current operation status."""
    return {
        "running": current_operation["running"],
        "operation": current_operation["type"]
    }


@app.get("/api/config")
async def get_config():
    """Get application configuration."""
    return {
        "host_root": HOST_ROOT,
        "default_backup_dir": "/tmp/postgres-backups",  # Host-relative path
        "is_docker": os.path.exists("/.dockerenv")
    }


# ============================================================================
# Saved Connections API
# ============================================================================

@app.get("/api/connections")
async def get_connections():
    """Get all saved database connections."""
    connections = load_saved_connections()
    # Don't send passwords to frontend for security
    safe_connections = []
    for conn in connections:
        safe_conn = conn.model_dump()
        safe_conn["has_password"] = bool(conn.password)
        safe_conn["password"] = None  # Don't expose password
        safe_connections.append(safe_conn)
    return {"success": True, "connections": safe_connections}


@app.post("/api/connections")
async def save_connection(request: SaveConnectionRequest):
    """Save a new database connection."""
    import uuid
    
    connections = load_saved_connections()
    
    # Check if name already exists
    for conn in connections:
        if conn.name.lower() == request.name.lower():
            raise HTTPException(status_code=400, detail=f"Connection '{request.name}' already exists")
    
    # Create new connection
    new_conn = SavedConnection(
        id=str(uuid.uuid4()),
        name=request.name,
        host=request.host,
        port=request.port,
        username=request.username,
        password=request.password,
        color=request.color
    )
    
    connections.append(new_conn)
    save_connections_to_file(connections)
    
    return {"success": True, "id": new_conn.id, "message": f"Connection '{request.name}' saved"}


@app.put("/api/connections/{connection_id}")
async def update_connection(connection_id: str, request: SaveConnectionRequest):
    """Update an existing database connection."""
    connections = load_saved_connections()
    
    for i, conn in enumerate(connections):
        if conn.id == connection_id:
            # Check if new name conflicts with another connection
            for other in connections:
                if other.id != connection_id and other.name.lower() == request.name.lower():
                    raise HTTPException(status_code=400, detail=f"Connection '{request.name}' already exists")
            
            # Update connection (keep existing password if not provided)
            connections[i] = SavedConnection(
                id=connection_id,
                name=request.name,
                host=request.host,
                port=request.port,
                username=request.username,
                password=request.password if request.password else conn.password,
                color=request.color
            )
            save_connections_to_file(connections)
            return {"success": True, "message": f"Connection '{request.name}' updated"}
    
    raise HTTPException(status_code=404, detail="Connection not found")


@app.delete("/api/connections/{connection_id}")
async def delete_connection(connection_id: str):
    """Delete a saved database connection."""
    connections = load_saved_connections()
    
    for i, conn in enumerate(connections):
        if conn.id == connection_id:
            name = conn.name
            connections.pop(i)
            save_connections_to_file(connections)
            return {"success": True, "message": f"Connection '{name}' deleted"}
    
    raise HTTPException(status_code=404, detail="Connection not found")


@app.get("/api/connections/{connection_id}")
async def get_connection(connection_id: str, include_password: bool = False):
    """Get a specific connection by ID."""
    connections = load_saved_connections()
    
    for conn in connections:
        if conn.id == connection_id:
            result = conn.model_dump()
            if not include_password:
                result["has_password"] = bool(conn.password)
                result["password"] = None
            return {"success": True, "connection": result}
    
    raise HTTPException(status_code=404, detail="Connection not found")


@app.post("/api/connections/{connection_id}/test")
async def test_saved_connection(connection_id: str):
    """Test a saved database connection."""
    connections = load_saved_connections()
    
    for conn in connections:
        if conn.id == connection_id:
            # Use the test_connection logic
            request = TestConnectionRequest(
                host=conn.host,
                port=conn.port,
                username=conn.username,
                password=conn.password
            )
            return await test_connection(request)
    
    raise HTTPException(status_code=404, detail="Connection not found")


# ============================================================================
# Database Operations API
# ============================================================================

@app.post("/api/test-connection")
async def test_connection(request: TestConnectionRequest):
    """Test database connection."""
    try:
        # Find any available psql first
        psql_path = shutil.which("psql")
        if not psql_path:
            for v in get_installed_pg_versions():
                paths = [
                    f"/usr/lib/postgresql/{v}/bin/psql",
                    f"/usr/pgsql-{v}/bin/psql",
                ]
                for p in paths:
                    if os.path.isfile(p) and os.access(p, os.X_OK):
                        psql_path = p
                        break
                if psql_path:
                    break
        
        if not psql_path:
            return {"success": False, "error": "psql not found. Install PostgreSQL client tools."}
        
        cmd = [
            psql_path,
            f"-h{request.host}",
            f"-p{request.port}",
            f"-U{request.username}",
            "postgres",
            "-c", "SELECT version();"
        ]
        
        env = os.environ.copy()
        if request.password:
            env["PGPASSWORD"] = request.password
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=10
        )
        
        if result.returncode == 0:
            # Extract version
            version = None
            for line in result.stdout.splitlines():
                if "PostgreSQL" in line:
                    version = line.strip()
                    break
            
            return {"success": True, "version": version}
        else:
            return {"success": False, "error": result.stderr.strip()}
    
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Connection timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/list-databases")
async def list_databases(request: TestConnectionRequest):
    """List all available databases."""
    try:
        # Find compatible psql tool
        psql_path = find_compatible_pg_tool("psql", request.host, request.port, request.username, request.password)
        
        cmd = [
            psql_path,
            f"-h{request.host}",
            f"-p{request.port}",
            f"-U{request.username}",
            "-d", "postgres",
            "-t", "-A",
            "-c", "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname;"
        ]
        
        env = os.environ.copy()
        if request.password:
            env["PGPASSWORD"] = request.password
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=10
        )
        
        if result.returncode == 0:
            databases = [db.strip() for db in result.stdout.splitlines() if db.strip()]
            return {"success": True, "databases": databases}
        else:
            return {"success": False, "error": result.stderr.strip(), "databases": []}
    
    except Exception as e:
        return {"success": False, "error": str(e), "databases": []}


@app.post("/api/backup")
async def start_backup(request: BackupRequest, background_tasks: BackgroundTasks):
    """Start a backup operation."""
    if current_operation["running"]:
        raise HTTPException(status_code=409, detail="An operation is already running")
    
    cmd = build_backup_command(request)
    background_tasks.add_task(run_backup_command, cmd, "backup")
    
    return {"status": "started", "operation": "backup"}


@app.post("/api/restore")
async def start_restore(request: RestoreRequest, background_tasks: BackgroundTasks):
    """Start a restore operation."""
    if current_operation["running"]:
        raise HTTPException(status_code=409, detail="An operation is already running")
    
    if not request.database:
        raise HTTPException(status_code=400, detail="Database name is required for restore")
    
    cmd = build_restore_command(request)
    background_tasks.add_task(run_backup_command, cmd, "restore")
    
    return {"status": "started", "operation": "restore"}


@app.post("/api/stop")
async def stop_operation():
    """Stop the current operation."""
    if current_operation["process"]:
        current_operation["process"].terminate()
        return {"status": "stopping"}
    return {"status": "no_operation"}


# ============================================================================
# File Browser API
# ============================================================================

@app.get("/api/files/browse")
async def browse_files(path: str = "/host"):
    """Browse files and directories from the host machine only."""
    try:
        # Always start from /host (host filesystem)
        # Normalize the path to always be relative to /host
        if path == "/" or path == "":
            path = "/host"
        
        # If path doesn't start with /host, prepend it
        if not path.startswith("/host"):
            # If it's an absolute path, make it relative to /host
            if path.startswith("/"):
                path = "/host" + path
            else:
                path = "/host/" + path
        
        # Map to actual host filesystem path
        # HOST_ROOT is /host (the mount point)
        # When path is /host, we want to browse HOST_ROOT directly
        # When path is /host/something, we want to browse HOST_ROOT/something
        if path == "/host":
            actual_path = HOST_ROOT
        else:
            # Remove /host prefix to get relative path
            host_relative = path[5:] if path.startswith("/host") else path
            # Ensure it starts with / for proper joining
            if not host_relative.startswith("/"):
                host_relative = "/" + host_relative
            # Join with HOST_ROOT
            if HOST_ROOT == "/host":
                actual_path = HOST_ROOT + host_relative
            else:
                actual_path = os.path.join(HOST_ROOT, host_relative.lstrip("/"))
        
        # Debug: Log the actual path being browsed
        # print(f"DEBUG: path={path}, HOST_ROOT={HOST_ROOT}, actual_path={actual_path}", file=sys.stderr)
        
        if not os.path.exists(actual_path):
            return {"success": False, "error": f"Path does not exist: {actual_path}", "items": []}
        
        if not os.path.isdir(actual_path):
            return {"success": False, "error": f"Path is not a directory: {actual_path}", "items": []}
        
        items = []
        try:
            for entry in os.scandir(actual_path):
                try:
                    # Use lstat() for symlinks to avoid following broken symlinks
                    # Use stat() for regular files/directories
                    if entry.is_symlink():
                        stat = entry.stat(follow_symlinks=False)
                    else:
                        stat = entry.stat()
                    # Build path with /host prefix for display
                    if path == "/host":
                        item_path = f"/host/{entry.name}"
                    else:
                        item_path = f"{path}/{entry.name}" if path.endswith("/") else f"{path}/{entry.name}"
                    items.append({
                        "name": entry.name,
                        "path": item_path,
                        "is_dir": entry.is_dir(follow_symlinks=False),
                        "is_symlink": entry.is_symlink(),
                        "size": stat.st_size if not entry.is_dir(follow_symlinks=False) else None,
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
                    })
                except (PermissionError, OSError) as e:
                    # Build path with /host prefix for display
                    if path == "/host":
                        item_path = f"/host/{entry.name}"
                    else:
                        item_path = f"{path}/{entry.name}" if path.endswith("/") else f"{path}/{entry.name}"
                    items.append({
                        "name": entry.name,
                        "path": item_path,
                        "is_dir": entry.is_dir(follow_symlinks=False),
                        "is_symlink": entry.is_symlink(),
                        "size": None,
                        "modified": None,
                        "error": str(e)
                    })
        except PermissionError:
            return {"success": False, "error": "Permission denied", "items": []}
        
        # Sort: directories first, then files, alphabetically
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        
        # Ensure display path always starts with /host
        display_path = path if path.startswith("/host") else f"/host{path if path.startswith('/') else '/' + path}"
        if display_path == "/host/":
            display_path = "/host"
        
        # Calculate parent path
        parent_path = None
        if display_path != "/host":
            parent = os.path.dirname(display_path)
            parent_path = parent if parent and parent != "/" else "/host"
        
        return {
            "success": True,
            "path": display_path,
            "parent": parent_path,
            "items": items
        }
    
    except Exception as e:
        return {"success": False, "error": str(e), "items": []}


@app.get("/api/files/backups")
async def list_backups(backup_dir: str = "/tmp/postgres-backups"):
    """List available backups."""
    try:
        # Normalize the path to container path
        actual_path = normalize_path(backup_dir)
        
        if not os.path.exists(actual_path):
            return {"success": True, "backups": [], "message": "Backup directory does not exist yet"}
        
        backups = []
        
        for db_dir in os.scandir(actual_path):
            if db_dir.is_dir() and db_dir.name != "temp_extract":
                db_backups = []
                
                try:
                    for item in os.scandir(db_dir.path):
                        if item.name.endswith("_backup.tar.gz"):
                            stat = item.stat()
                            db_backups.append({
                                "file": item.name,
                                "path": item.path,
                                "size": stat.st_size,
                                "created": datetime.fromtimestamp(stat.st_mtime).isoformat()
                            })
                        elif item.is_dir():
                            # Timestamp folder
                            dump_file = os.path.join(item.path, f"{db_dir.name}_dump.sql")
                            if os.path.exists(dump_file):
                                stat = os.stat(dump_file)
                                db_backups.append({
                                    "file": f"{item.name}/{db_dir.name}_dump.sql",
                                    "path": dump_file,
                                    "size": stat.st_size,
                                    "created": datetime.fromtimestamp(stat.st_mtime).isoformat()
                                })
                except PermissionError:
                    pass
                
                if db_backups:
                    db_backups.sort(key=lambda x: x["created"], reverse=True)
                    backups.append({
                        "database": db_dir.name,
                        "backups": db_backups
                    })
        
        return {"success": True, "backups": backups}
    
    except Exception as e:
        return {"success": False, "error": str(e), "backups": []}


@app.get("/api/disk-usage")
async def get_disk_usage():
    """Get disk usage information."""
    try:
        usage = shutil.disk_usage(HOST_ROOT)
        return {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": round((usage.used / usage.total) * 100, 1)
        }
    except Exception as e:
        return {"error": str(e)}


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

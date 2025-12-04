#!/usr/bin/env python3
"""
PG Backup Manager - Web Application
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
from typing import Optional, List, Dict, Any, Union
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

# ============================================================================
# Configuration
# ============================================================================

# Get the script directory
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_SCRIPT_PATH = os.environ.get("BACKUP_SCRIPT_PATH", "/app/backup_script.py")
HOST_ROOT = os.environ.get("HOST_ROOT", "/host")  # Root filesystem mount point
DEFAULT_BACKUP_DIR = os.environ.get("DEFAULT_BACKUP_DIR", "/host/tmp/db-backups")
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
    connection: Optional[ConnectionConfig] = None  # Optional for project restore
    database: Optional[str] = None  # Optional - if None, restore all databases
    backup_dir: str = DEFAULT_BACKUP_DIR
    backup_file: Optional[str] = None
    sql_dump_file: Optional[str] = None  # Optional - specific SQL dump file to restore
    drop_existing: bool = True
    restore_type: str = "database"  # "database" or "project"
    target_project_path: Optional[str] = None
    skip_database: bool = False
    post_restore_commands: Optional[List[str]] = None
    ignore_errors: bool = False  # Continue restore even if errors occur (e.g. duplicate constraints)


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
    title="PG Backup Manager",
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

# Mount static files with no-cache headers for development
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    from fastapi.responses import Response
    from starlette.staticfiles import StaticFiles as StarletteStaticFiles
    
    class NoCacheStaticFiles(StarletteStaticFiles):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
        
        async def __call__(self, scope, receive, send):
            async def send_wrapper(message):
                if message["type"] == "http.response.start":
                    # Add no-cache headers
                    headers = dict(message.get("headers", []))
                    headers[b"cache-control"] = b"no-cache, no-store, must-revalidate"
                    headers[b"pragma"] = b"no-cache"
                    headers[b"expires"] = b"0"
                    message["headers"] = list(headers.items())
                await send(message)
            
            await super().__call__(scope, receive, send_wrapper)
    
    app.mount("/static", NoCacheStaticFiles(directory=str(static_dir)), name="static")


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
    
    # Connection params - only added for database-only restore
    # For project restore, connection comes from .env file
    if request.restore_type == "database" and request.connection:
        cmd.extend(["--host", request.connection.host])
        cmd.extend(["--port", str(request.connection.port)])
        cmd.extend(["--username", request.connection.username])
        if request.connection.password:
            cmd.extend(["--password", request.connection.password])
    
    # Database - optional, if None will restore all databases
    if request.database:
        cmd.extend(["--database", request.database])
    
    # Normalize backup directory path
    backup_dir = normalize_path(request.backup_dir)
    cmd.extend(["--backup-dir", backup_dir])
    
    # Restore type
    cmd.extend(["--restore-type", request.restore_type])
    
    if request.backup_file:
        cmd.extend(["--backup-file", normalize_path(request.backup_file)])
    
    if request.sql_dump_file:
        cmd.extend(["--sql-dump-file", normalize_path(request.sql_dump_file)])
    
    if request.drop_existing:
        cmd.append("--drop-existing")
    
    if request.ignore_errors:
        cmd.append("--ignore-errors")
    
    if request.restore_type == "project":
        # Project restore
        if request.target_project_path:
            cmd.extend(["--target-project-path", normalize_path(request.target_project_path)])
        if request.skip_database:
            cmd.append("--skip-database")
        if request.post_restore_commands:
            cmd.append("--post-restore-commands")
            cmd.extend(request.post_restore_commands)
    
    return cmd


# ============================================================================
# API Routes
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the main application page."""
    index_path = static_dir / "index.html"
    if index_path.exists():
        # Read and inject timestamp into HTML for cache-busting
        content = index_path.read_text()
        import time
        timestamp = int(time.time())
        # Inject timestamp into CSS and JS URLs (handle both with and without version)
        import re
        content = re.sub(r'styles\.css(\?v=[^"]*)?', f'styles.css?v=3.0&t={timestamp}', content)
        content = re.sub(r'app\.js(\?v=[^"]*)?', f'app.js?v=3.0&t={timestamp}', content)
        response = HTMLResponse(content)
        # Add aggressive cache-busting headers
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["Last-Modified"] = "Thu, 01 Jan 1970 00:00:00 GMT"
        response.headers["ETag"] = f'"{timestamp}"'
        return response
    return HTMLResponse("<h1>PG Backup Manager</h1><p>Static files not found.</p>")


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
        "default_backup_dir": "/tmp/db-backups",  # Host-relative path
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
async def start_restore(http_request: Request, background_tasks: BackgroundTasks = BackgroundTasks()):
    """Start a restore operation."""
    if current_operation["running"]:
        raise HTTPException(status_code=409, detail="An operation is already running")
    
    # Parse JSON body manually to avoid FastAPI's automatic validation
    try:
        request_data = await http_request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
    
    # Create request object with defaults for optional fields
    # Add None defaults for optional fields if not present
    if "connection" not in request_data:
        request_data["connection"] = None
    if "database" not in request_data:
        request_data["database"] = None
    if "backup_file" not in request_data:
        request_data["backup_file"] = None
    if "target_project_path" not in request_data:
        request_data["target_project_path"] = None
    if "post_restore_commands" not in request_data:
        request_data["post_restore_commands"] = None
    
    # Handle connection - if it's None, keep it None, otherwise create ConnectionConfig
    connection_obj = None
    if request_data.get("connection") is not None and isinstance(request_data["connection"], dict):
        try:
            connection_obj = ConnectionConfig(**request_data["connection"])
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid connection config: {str(e)}")
    
    # Create RestoreRequest manually to avoid Pydantic validation issues
    request = RestoreRequest(
        restore_type=request_data.get("restore_type", "database"),
        backup_dir=request_data.get("backup_dir", DEFAULT_BACKUP_DIR),
        backup_file=request_data.get("backup_file"),
        sql_dump_file=request_data.get("sql_dump_file"),
        drop_existing=request_data.get("drop_existing", True),
        target_project_path=request_data.get("target_project_path"),
        skip_database=request_data.get("skip_database", False),
        post_restore_commands=request_data.get("post_restore_commands"),
        connection=connection_obj,
        database=request_data.get("database")
    )
    
    # Database name required only for database-only restore (unless sql_dump_file is provided)
    if request.restore_type == "database" and not request.database and not request.sql_dump_file:
        raise HTTPException(status_code=400, detail="Database name is required for database-only restore (or provide sql_dump_file)")
    
    # Connection required only for database-only restore
    if request.restore_type == "database" and not request.connection:
        raise HTTPException(status_code=400, detail="Database connection is required for database-only restore")
    
    # Target project path required for project restore
    if request.restore_type == "project" and not request.target_project_path:
        raise HTTPException(status_code=400, detail="Target project path is required for project restore")
    
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
async def browse_files(path: str = "", search: Optional[str] = None):
    """
    Browse files and directories.
    Restricts view to /host/host_mnt if it exists, acting as the root.
    """
    try:
        # Determine effective root
        # Check if /host/host_mnt exists (Docker Desktop/WSL pattern)
        # If it does, treat it as the restricted root.
        restricted_root = "/host/host_mnt" if os.path.exists(os.path.join(HOST_ROOT, "host_mnt")) else "/host"
        
        # Normalize incoming path
        if not path or path == "/":
            path = restricted_root
        
        # Ensure path starts with /host (security/sanity check)
        if not path.startswith("/host"):
             # If it's a relative path or absolute path not starting with /host
             if path.startswith("/"):
                 # Try to prepend /host if likely intended
                 path = "/host" + path
             else:
                 path = os.path.join("/host", path)
        
        # Enforce restriction: cannot go above restricted_root
        if not path.startswith(restricted_root):
            path = restricted_root

        # Map to actual filesystem path
        # HOST_ROOT is /host.
        # path is /host/host_mnt/some/dir
        # actual_path should be /host/host_mnt/some/dir (since HOST_ROOT is mount point)
        
        # We can just use the path directly if it starts with /host, as /host IS the mount point in container.
        # But we need to handle the case where path might have double slashes etc.
        actual_path = os.path.normpath(path)
        
        if not os.path.exists(actual_path):
            return {"success": False, "error": f"Path does not exist: {actual_path}", "items": []}
        
        if not os.path.isdir(actual_path):
            return {"success": False, "error": f"Path is not a directory: {actual_path}", "items": []}
        
        items = []
        try:
            # Use listdir and manual stat to avoid keeping file handles open
            entries = []
            try:
                for name in os.listdir(actual_path):
                    entry_path = os.path.join(actual_path, name)
                    entries.append((name, entry_path))
            except PermissionError:
                return {"success": False, "error": "Permission denied", "items": []}
            
            for name, entry_path in entries:
                # Filter out filesystem/system directories that users don't need to see
                # Keep user-relevant directories like home, tmp, opt, mnt, media
                system_dirs = {
                    'proc', 'sys', 'dev', 'run', 'boot', 'lib', 'lib64', 
                    'usr', 'var', 'etc', 'root', 'sbin', 'bin', 'srv',
                    'snap', 'lost+found', 'sysroot'
                }
                if name in system_dirs:
                    continue
                
                # Filter by search term if provided
                if search and search.lower() not in name.lower():
                    continue
                
                try:
                    # Use lstat to avoid following symlinks (prevents opening files)
                    stat_info = os.lstat(entry_path)
                    is_dir = os.path.isdir(entry_path) and not os.path.islink(entry_path)
                    is_link = os.path.islink(entry_path)
                    
                    items.append({
                        "name": name,
                        "path": entry_path,
                        "is_dir": is_dir,
                        "is_symlink": is_link,
                        "size": stat_info.st_size if not is_dir else None,
                        "modified": datetime.fromtimestamp(stat_info.st_mtime).isoformat()
                    })
                except (PermissionError, OSError) as e:
                    # If we can't stat it, still include it but mark as error
                    items.append({
                        "name": name,
                        "path": entry_path,
                        "is_dir": False,
                        "error": str(e)
                    })
        except Exception as e:
            return {"success": False, "error": f"Error reading directory: {str(e)}", "items": []}
        
        # Sort: directories first, then files, alphabetically
        items.sort(key=lambda x: (not x.get("is_dir", False), x["name"].lower()))
        
        # Calculate parent path (respecting restriction)
        parent_path = os.path.dirname(actual_path)
        if not parent_path.startswith(restricted_root):
            parent_path = None # Disable back button if at root
        
        return {
            "success": True,
            "path": actual_path,
            "parent": parent_path,
            "items": items,
            "root": restricted_root
        }
    
    except Exception as e:
        return {"success": False, "error": str(e), "items": []}


@app.post("/api/files/create-folder")
async def create_folder(request: Request):
    """Create a new folder in the specified path."""
    try:
        # Parse JSON body
        body = await request.json()
        path = body.get("path", "")
        folder_name = body.get("folder_name", "")
        
        if not path:
            return {"success": False, "error": "Path is required"}
        
        if not folder_name:
            return {"success": False, "error": "Folder name is required"}
        
        # Normalize the path to container path
        actual_path = normalize_path(path)
        
        if not os.path.exists(actual_path):
            return {"success": False, "error": f"Parent directory does not exist: {path}"}
        
        if not os.path.isdir(actual_path):
            return {"success": False, "error": f"Path is not a directory: {path}"}
        
        # Sanitize folder name
        folder_name = folder_name.strip()
        if not folder_name:
            return {"success": False, "error": "Folder name cannot be empty"}
        
        # Remove any path separators from folder name
        folder_name = folder_name.replace('/', '').replace('\\', '')
        if not folder_name:
            return {"success": False, "error": "Invalid folder name"}
        
        new_folder_path = os.path.join(actual_path, folder_name)
        
        # Check if folder already exists
        if os.path.exists(new_folder_path):
            return {"success": False, "error": f"Folder already exists: {folder_name}"}
        
        # Create the folder
        os.makedirs(new_folder_path, exist_ok=False)
        
        return {
            "success": True,
            "message": f"Folder '{folder_name}' created successfully",
            "path": new_folder_path
        }
    except PermissionError:
        return {"success": False, "error": "Permission denied: Cannot create folder in this location"}
    except OSError as e:
        return {"success": False, "error": f"Failed to create folder: {str(e)}"}
    except Exception as e:
        return {"success": False, "error": f"Unexpected error: {str(e)}"}


@app.get("/api/files/backups")
async def list_backups(backup_dir: str = "/tmp/db-backups"):
    """List available backups (Sessions)."""
    try:
        # Normalize the path to container path
        actual_path = normalize_path(backup_dir)
        
        if not os.path.exists(actual_path):
            return {"success": True, "backups": [], "message": "Backup directory does not exist yet"}
        
        backups = []
        
        # Scan for session directories (YYYY-MM-DD_HH-MM-SS)
        # Use listdir instead of scandir to avoid file handle leaks
        try:
            for item_name in os.listdir(actual_path):
                item_path = os.path.join(actual_path, item_name)
                if os.path.isdir(item_path) and item_name != "temp_extract":
                    # Basic check for timestamp format
                    if len(item_name.split('_')) >= 2:
                        try:
                            stat_info = os.stat(item_path)
                            session_info = {
                                "session_id": item_name,
                                "path": item_path,
                                "created": datetime.fromtimestamp(stat_info.st_mtime).isoformat(),
                                "databases": [],
                                "has_files": False
                            }
                            
                            # Check for databases inside
                            db_dir = os.path.join(item_path, 'databases')
                            if os.path.exists(db_dir):
                                try:
                                    for db_file in os.listdir(db_dir):
                                        if db_file.endswith('.sql'):
                                            session_info["databases"].append(db_file[:-4]) # remove .sql
                                except (PermissionError, OSError):
                                    pass
                            
                            # Check for files archive
                            if os.path.exists(os.path.join(item_path, 'files.tar.gz')):
                                session_info["has_files"] = True
                                
                            if session_info["databases"] or session_info["has_files"]:
                                backups.append(session_info)
                        except (PermissionError, OSError):
                            # Skip directories we can't access
                            continue
        except (PermissionError, OSError) as e:
            return {"success": False, "error": f"Error reading backup directory: {str(e)}", "backups": []}
        
        # Sort by creation time desc
        backups.sort(key=lambda x: x["created"], reverse=True)
        
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
# WebSocket Routes
# ============================================================================

@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    """WebSocket endpoint for real-time log streaming."""
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive and handle incoming messages
            data = await websocket.receive_text()
            # Echo back or handle client messages if needed
            await websocket.send_json({"type": "pong", "data": data})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        manager.disconnect(websocket)
        print(f"WebSocket error: {e}")


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

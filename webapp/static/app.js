/**
 * PG Backup Manager - Frontend Application
 */

// ============================================================================
// State Management
// ============================================================================

const state = {
    currentTab: 'backup',
    storageType: 'local',
    isRunning: false,
    ws: null,
    fileBrowserPath: '/host',
    modalFileBrowserPath: '/host',
    modalTargetInput: null,
    modalSelectDir: true,
    selectedFile: null,
    includeFolders: [],
    databases: [],
    savedConnections: [],
    activeConnectionId: null,
    editingConnectionId: null,
    selectedColor: '#00D4AA'
};

// ============================================================================
// WebSocket Connection
// ============================================================================

function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/logs`;
    
    state.ws = new WebSocket(wsUrl);
    
    state.ws.onopen = () => {
        console.log('WebSocket connected');
    };
    
    state.ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleWebSocketMessage(data);
    };
    
    state.ws.onclose = () => {
        console.log('WebSocket disconnected, reconnecting...');
        setTimeout(connectWebSocket, 3000);
    };
    
    state.ws.onerror = (error) => {
        console.error('WebSocket error:', error);
    };
}

function handleWebSocketMessage(data) {
    switch (data.type) {
        case 'log':
            appendLog(data.timestamp, data.message, data.level);
            break;
        case 'status':
            updateStatus(data.status, data.operation, data.success);
            break;
        case 'pong':
            // Connection alive
            break;
    }
}

// ============================================================================
// Terminal / Logging
// ============================================================================

function appendLog(timestamp, message, level = 'info') {
    const terminal = document.getElementById('terminalBody');
    const welcome = terminal.querySelector('.terminal-welcome');
    if (welcome) welcome.remove();
    
    const line = document.createElement('div');
    line.className = `log-line ${level}`;
    line.innerHTML = `
        <span class="log-timestamp">[${timestamp}]</span>
        <span class="log-message">${escapeHtml(message)}</span>
    `;
    
    terminal.appendChild(line);
    terminal.scrollTop = terminal.scrollHeight;
}

function clearLogs() {
    const terminal = document.getElementById('terminalBody');
    terminal.innerHTML = '<div class="terminal-welcome"><p>Logs cleared</p></div>';
}

function downloadLogs() {
    const terminal = document.getElementById('terminalBody');
    const lines = terminal.querySelectorAll('.log-line');
    let content = '';
    
    lines.forEach(line => {
        const timestamp = line.querySelector('.log-timestamp')?.textContent || '';
        const message = line.querySelector('.log-message')?.textContent || '';
        content += `${timestamp} ${message}\n`;
    });
    
    const blob = new Blob([content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `backup-logs-${new Date().toISOString().slice(0, 10)}.txt`;
    a.click();
    URL.revokeObjectURL(url);
}

// ============================================================================
// Status Management
// ============================================================================

function updateStatus(status, operation = null, success = null) {
    const indicator = document.getElementById('statusIndicator');
    const dot = indicator.querySelector('.status-dot');
    const text = indicator.querySelector('.status-text');
    const startBtn = document.getElementById('startBtn');
    const stopBtn = document.getElementById('stopBtn');
    
    // Remove all status classes
    dot.className = 'status-dot';
    
    switch (status) {
        case 'running':
            dot.classList.add('running');
            text.textContent = operation ? `${capitalize(operation)}ing...` : 'Running';
            state.isRunning = true;
            startBtn.disabled = true;
            stopBtn.disabled = false;
            break;
        case 'completed':
            // Check if operation was successful
            if (success === false) {
                // Operation failed
                dot.classList.add('error');
                text.textContent = 'Failed';
                state.isRunning = false;
                stopBtn.disabled = true;
                // Update button state - this will enable/disable based on form validation
                updateStartButtonState();
            } else {
                // Operation succeeded
                dot.classList.add('success');
                text.textContent = 'Completed';
                state.isRunning = false;
                stopBtn.disabled = true;
                // Update button state - this will enable/disable based on form validation
                updateStartButtonState();
            }
            setTimeout(() => {
                if (!state.isRunning) {
                    dot.className = 'status-dot idle';
                    text.textContent = 'Idle';
                }
            }, 5000);
            break;
        case 'error':
        case 'cancelled':
            dot.classList.add('error');
            text.textContent = status === 'error' ? 'Error' : 'Cancelled';
            state.isRunning = false;
            stopBtn.disabled = true;
            // Update button state - this will enable/disable based on form validation
            updateStartButtonState();
            setTimeout(() => {
                if (!state.isRunning) {
                    dot.className = 'status-dot idle';
                    text.textContent = 'Idle';
                }
            }, 5000);
            break;
        default:
            dot.classList.add('idle');
            text.textContent = 'Idle';
            state.isRunning = false;
            stopBtn.disabled = true;
            // Update button state - this will enable/disable based on form validation
            updateStartButtonState();
    }
}

// ============================================================================
// Authentication
// ============================================================================

async function checkAuth() {
    try {
        const response = await fetch('/api/status');
        if (response.status === 401) {
            window.location.href = '/login';
            return false;
        }
        return true;
    } catch (error) {
        console.error('Auth check failed:', error);
        return false;
    }
}

async function logout() {
    try {
        await fetch('/api/logout', { method: 'POST' });
        window.location.href = '/login';
    } catch (error) {
        console.error('Logout failed:', error);
        window.location.href = '/login';
    }
}

// Intercept fetch calls to handle 401 errors
const originalFetch = window.fetch;
window.fetch = async function(...args) {
    const response = await originalFetch(...args);
    if (response.status === 401 && !args[0].includes('/api/login')) {
        window.location.href = '/login';
    }
    return response;
};

// ============================================================================
// API Calls
// ============================================================================

async function testConnection() {
    const btn = document.getElementById('testConnection');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Testing...';
    
    try {
        const response = await fetch('/api/test-connection', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(getConnectionConfig())
        });
        
        const data = await response.json();
        
        if (data.success) {
            showToast('Connection successful!', 'success');
            if (data.version) {
                appendLog(getCurrentTime(), `Connected: ${data.version}`, 'success');
            }
        } else {
            showToast(`Connection failed: ${data.error}`, 'error');
            appendLog(getCurrentTime(), `Connection failed: ${data.error}`, 'error');
        }
    } catch (error) {
        showToast(`Error: ${error.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = `
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
                <polyline points="22,4 12,14.01 9,11.01"/>
            </svg>
            Test Connection
        `;
    }
}

async function listDatabases() {
    const btn = document.getElementById('listDatabases');
    btn.disabled = true;
    
    try {
        const response = await fetch('/api/list-databases', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(getConnectionConfig())
        });
        
        const data = await response.json();
        
        if (data.success && data.databases.length > 0) {
            state.databases = data.databases;
            showDatabaseDropdown(data.databases);
            showToast(`Found ${data.databases.length} database(s)`, 'success');
        } else if (data.databases.length === 0) {
            showToast('No user databases found', 'warning');
        } else {
            showToast(`Error: ${data.error}`, 'error');
        }
    } catch (error) {
        showToast(`Error: ${error.message}`, 'error');
    } finally {
        btn.disabled = false;
    }
}

function showDatabaseDropdown(databases) {
    const dropdown = document.getElementById('dbDropdown');
    dropdown.innerHTML = databases.map(db => 
        `<div class="dropdown-item" onclick="selectDatabase('${db}')">${db}</div>`
    ).join('');
    dropdown.classList.add('show');
    
    // Close on outside click
    document.addEventListener('click', function closeDropdown(e) {
        if (!dropdown.contains(e.target) && e.target.id !== 'listDatabases') {
            dropdown.classList.remove('show');
            document.removeEventListener('click', closeDropdown);
        }
    });
}

function selectDatabase(db) {
    document.getElementById('backupDatabase').value = db;
    document.getElementById('dbDropdown').classList.remove('show');
}

function validateBackupForm() {
    const tab = state.currentTab;
    if (tab !== 'backup') return true;
    
    // For backup, we need at least:
    // - A connection (host, username) OR
    // - A project path (for project backup)
    const projectPath = document.getElementById('projectPath')?.value.trim();
    const host = document.getElementById('dbHost')?.value.trim();
    const username = document.getElementById('dbUsername')?.value.trim();
    
    // If project path is provided, that's sufficient for backup
    if (projectPath) {
        return true;
    }
    
    // Otherwise, we need at least host and username for database connection
    if (host && username) {
        return true;
    }
    
    return false;
}

function validateRestoreForm() {
    const tab = state.currentTab;
    if (tab !== 'restore') return true;
    
    const restoreType = document.querySelector('.restore-type-tab.active')?.dataset.restoreType || 'database';
    
    if (restoreType === 'database') {
        const database = document.getElementById('restoreDatabase')?.value.trim();
        const sqlDumpFile = document.getElementById('restoreSqlDumpFile')?.value.trim();
        
        // Database name is required unless SQL dump file is provided
        if (!database && !sqlDumpFile) {
            return false;
        }
    } else if (restoreType === 'project') {
        const targetProjectPath = document.getElementById('targetProjectPath')?.value.trim();
        if (!targetProjectPath) {
            return false;
        }
    }
    
    return true;
}

function updateStartButtonState() {
    const startBtn = document.getElementById('startBtn');
    if (!startBtn) return;
    
    // Don't change button state if operation is running
    if (state.isRunning) {
        startBtn.disabled = true;
        return;
    }
    
    // Validate based on current tab
    let isValid = true;
    if (state.currentTab === 'backup') {
        isValid = validateBackupForm();
    } else if (state.currentTab === 'restore') {
        isValid = validateRestoreForm();
    }
    
    startBtn.disabled = !isValid;
}

async function startOperation() {
    // Prevent double-clicking
    if (state.isRunning) {
        showToast('An operation is already running', 'warning');
        return;
    }
    
    const tab = state.currentTab;
    let endpoint, payload;
    
    // Validate form before disabling button
    if (tab === 'restore') {
        const restoreType = document.querySelector('.restore-type-tab.active')?.dataset.restoreType || 'database';
        
        if (restoreType === 'database') {
            const database = document.getElementById('restoreDatabase').value.trim();
            const sqlDumpFile = document.getElementById('restoreSqlDumpFile').value.trim();
            
            if (!database && !sqlDumpFile) {
                showToast('Database name is required for database-only restore (or provide SQL dump file)', 'error');
                updateStartButtonState(); // Re-enable button
                return;
            }
        } else if (restoreType === 'project') {
            const targetProjectPath = document.getElementById('targetProjectPath').value.trim();
            if (!targetProjectPath) {
                showToast('Target project path is required for project restore', 'error');
                updateStartButtonState(); // Re-enable button
                return;
            }
        }
    }
    
    // Disable button immediately to prevent double-clicks
    state.isRunning = true;
    const startBtn = document.getElementById('startBtn');
    const stopBtn = document.getElementById('stopBtn');
    startBtn.disabled = true;
    stopBtn.disabled = false;
    
    if (tab === 'backup') {
        endpoint = '/api/backup';
        const projectPath = document.getElementById('projectPath').value.trim();
        payload = {
            connection: getConnectionConfig(),
            storage: getStorageConfig(),
            database: document.getElementById('backupDatabase').value || null,
            backup_dir: document.getElementById('backupDir').value || '/tmp/db-backups',
            project_path: projectPath || null,
            include_folders: (!projectPath && state.includeFolders.length > 0) ? state.includeFolders : null
        };
    } else if (tab === 'restore') {
        // Determine restore type from active tab
        const restoreType = document.querySelector('.restore-type-tab.active')?.dataset.restoreType || 'database';
        
        if (restoreType === 'database') {
            // Database Only Restore
            const database = document.getElementById('restoreDatabase').value.trim();
            const sqlDumpFile = document.getElementById('restoreSqlDumpFile').value.trim() || null;
            
            if (!database && !sqlDumpFile) {
                showToast('Database name is required for database-only restore (or provide SQL dump file)', 'error');
                state.isRunning = false;
                updateStartButtonState(); // Re-enable button
                return;
            }
            
            endpoint = '/api/restore';
            payload = {
                connection: getConnectionConfig(),
                database: database,
                backup_dir: document.getElementById('restoreBackupDir').value || '/tmp/db-backups',
                backup_file: document.getElementById('restoreBackupFile').value || null,
                sql_dump_file: sqlDumpFile,
                drop_existing: document.getElementById('dropExisting').checked,
                ignore_errors: document.getElementById('ignoreErrors').checked,
                restore_type: 'database'
            };
        } else {
            // Full Project Restore
            const targetProjectPath = document.getElementById('targetProjectPath').value.trim();
            if (!targetProjectPath) {
                showToast('Target project path is required for project restore', 'error');
                state.isRunning = false;
                updateStartButtonState(); // Re-enable button
                return;
            }
            
            const database = document.getElementById('restoreProjectDatabase').value.trim();
            
            endpoint = '/api/restore';
            payload = {
                connection: null, // No connection for project restore - uses .env
                database: database || null, // Optional - if empty, restores all databases
                backup_dir: document.getElementById('restoreProjectBackupDir').value || '/tmp/db-backups',
                backup_file: document.getElementById('restoreProjectBackupFile').value || null,
                drop_existing: true, // Always drop for project restore
                ignore_errors: document.getElementById('ignoreErrorsProject').checked,
                restore_type: 'project',
                target_project_path: targetProjectPath,
                skip_database: document.getElementById('skipDatabase').checked,
                post_restore_commands: (() => {
                    const commands = document.getElementById('postRestoreCommands').value.trim();
                    return commands ? commands.split('\n').map(c => c.trim()).filter(c => c) : null;
                })()
            };
        }
    } else {
        showToast('Please select Backup or Restore tab', 'warning');
        return;
    }
    
    try {
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showToast(`${capitalize(tab)} started`, 'success');
        } else {
            // Re-enable button on error
            state.isRunning = false;
            startBtn.disabled = false;
            stopBtn.disabled = true;
            showToast(data.detail || 'Operation failed', 'error');
        }
    } catch (error) {
        // Re-enable button on error
        state.isRunning = false;
        startBtn.disabled = false;
        stopBtn.disabled = true;
        showToast(`Error: ${error.message}`, 'error');
    }
}

async function stopOperation() {
    try {
        await fetch('/api/stop', { method: 'POST' });
        showToast('Stopping operation...', 'warning');
    } catch (error) {
        showToast(`Error: ${error.message}`, 'error');
    }
}

// ============================================================================
// File Browser
// ============================================================================

let searchTimeout = null;

async function loadFileBrowser(path = '/host', targetElement = 'fileList', pathInput = 'fileBrowserPath', search = '') {
    const fileList = document.getElementById(targetElement);
    fileList.innerHTML = '<div class="file-list-loading">Loading...</div>';
    
    try {
        let url = `/api/files/browse?path=${encodeURIComponent(path)}`;
        if (search) {
            url += `&search=${encodeURIComponent(search)}`;
        }
        
        const response = await fetch(url);
        const data = await response.json();
        
        if (data.success) {
            if (pathInput === 'fileBrowserPath') {
                state.fileBrowserPath = data.path; // Use server normalized path
            } else {
                state.modalFileBrowserPath = data.path;
            }
            
            const pathInputEl = document.getElementById(pathInput);
            if (pathInputEl) pathInputEl.value = data.path;
            
            // Update back button
            const backBtn = document.getElementById(pathInput === 'fileBrowserPath' ? 'fileBrowserBack' : 'modalBrowserBack');
            if (backBtn) {
                // If parent is null (root reached or restricted), disable back
                const hasParent = data.parent && data.parent !== data.path;
                backBtn.disabled = !hasParent;
                if (hasParent) {
                    backBtn.dataset.parent = data.parent;
                } else {
                    backBtn.dataset.parent = '';
                }
            }
            
            // Render file list
            if (data.items.length === 0) {
                fileList.innerHTML = '<div class="file-list-loading">No items found</div>';
                return;
            }
            
            fileList.innerHTML = data.items.map(item => `
                <div class="file-item ${item.is_dir ? 'directory' : 'file'}" 
                     data-path="${item.path}" 
                     data-is-dir="${item.is_dir}"
                     onclick="handleFileClick(this, '${targetElement}', '${pathInput}')">
                    <svg class="file-icon ${item.is_dir ? 'folder' : ''}" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        ${item.is_dir 
                            ? '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>'
                            : '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14,2 14,8 20,8"/>'
                        }
                    </svg>
                    <span class="file-name">${item.name}</span>
                    ${item.size !== null ? `<span class="file-meta">${formatFileSize(item.size)}</span>` : ''}
                </div>
            `).join('');
        } else {
            fileList.innerHTML = `<div class="file-list-loading">Error: ${data.error}</div>`;
        }
    } catch (error) {
        fileList.innerHTML = `<div class="file-list-loading">Error: ${error.message}</div>`;
    }
}

function handleFileClick(element, targetElement, pathInput) {
    const path = element.dataset.path;
    const isDir = element.dataset.isDir === 'true';
    
    if (isDir) {
        // Clear search when navigating
        const searchInputId = pathInput === 'fileBrowserPath' ? 'fileBrowserSearch' : 'modalBrowserSearch';
        const searchInput = document.getElementById(searchInputId);
        if (searchInput) {
            searchInput.value = '';
        }
        loadFileBrowser(path, targetElement, pathInput);
    } else {
        // Select file
        document.querySelectorAll(`#${targetElement} .file-item`).forEach(el => el.classList.remove('selected'));
        element.classList.add('selected');
        state.selectedFile = path;
    }
}

function navigateBack(targetElement, pathInput) {
    const backBtn = document.getElementById(pathInput === 'fileBrowserPath' ? 'fileBrowserBack' : 'modalBrowserBack');
    
    if (!backBtn) {
        console.error('Back button not found');
        return;
    }
    
    // Get current path from state or input
    const currentPath = pathInput === 'fileBrowserPath' ? state.fileBrowserPath : state.modalFileBrowserPath;
    
    // Get parent path from button's data attribute (set by loadFileBrowser)
    let parentPath = backBtn.dataset.parent;
    
    // Fallback: calculate parent manually if not set
    if (!parentPath && currentPath) {
        const parts = currentPath.split('/').filter(p => p);
        if (parts.length > 1) {
            parts.pop();
            parentPath = '/' + parts.join('/');
        } else if (parts.length === 1) {
            // If we're at /host/host_mnt or /host, go to parent
            parentPath = '/host';
        }
    }
    
    if (parentPath && !backBtn.disabled) {
        // Clear search when navigating back
        const searchInputId = pathInput === 'fileBrowserPath' ? 'fileBrowserSearch' : 'modalBrowserSearch';
        const searchInput = document.getElementById(searchInputId);
        if (searchInput) {
            searchInput.value = '';
        }
        loadFileBrowser(parentPath, targetElement, pathInput);
    }
}

// Store current folder creation context
let folderCreationContext = null;

function openCreateFolderModal(targetElement, pathInput) {
    console.log('openCreateFolderModal called', { targetElement, pathInput });
    
    // Get current path from state or input field
    let currentPath;
    if (pathInput === 'fileBrowserPath') {
        currentPath = state.fileBrowserPath;
        if (!currentPath) {
            const pathInputEl = document.getElementById('fileBrowserPath');
            currentPath = pathInputEl ? pathInputEl.value : null;
        }
    } else {
        currentPath = state.modalFileBrowserPath;
        if (!currentPath) {
            const pathInputEl = document.getElementById('modalBrowserPath');
            currentPath = pathInputEl ? pathInputEl.value : null;
        }
        // If still empty, default to restricted root
        if (!currentPath) {
            currentPath = '/host/host_mnt';
        }
    }
    
    console.log('Current path:', currentPath);
    
    if (!currentPath) {
        showToast('No path selected', 'error');
        return;
    }
    
    // Store context for when user confirms
    folderCreationContext = { targetElement, pathInput, currentPath };
    
    // Clear and show modal
    const input = document.getElementById('folderNameInput');
    if (input) {
        input.value = '';
        input.focus();
    } else {
        console.error('folderNameInput not found');
    }
    
    const modal = document.getElementById('createFolderModal');
    if (modal) {
        modal.classList.add('show');
        console.log('Create folder modal shown');
    } else {
        console.error('createFolderModal not found');
    }
}

function closeCreateFolderModal() {
    const modal = document.getElementById('createFolderModal');
    if (modal) {
        modal.classList.remove('show');
    }
    folderCreationContext = null;
}

async function createFolder() {
    if (!folderCreationContext) {
        return;
    }
    
    const { targetElement, pathInput, currentPath } = folderCreationContext;
    const input = document.getElementById('folderNameInput');
    
    if (!input || !input.value || !input.value.trim()) {
        showToast('Please enter a folder name', 'error');
        return;
    }
    
    const folderName = input.value.trim();
    closeCreateFolderModal();
    
    try {
        const response = await fetch('/api/files/create-folder', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                path: currentPath,
                folder_name: folderName
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showToast(data.message || 'Folder created successfully', 'success');
            // Refresh the file browser
            const searchInputId = pathInput === 'fileBrowserPath' ? 'fileBrowserSearch' : 'modalBrowserSearch';
            const searchInput = document.getElementById(searchInputId);
            const search = searchInput ? searchInput.value : '';
            loadFileBrowser(currentPath, targetElement, pathInput, search);
        } else {
            showToast(data.error || 'Failed to create folder', 'error');
        }
    } catch (error) {
        console.error('Error creating folder:', error);
        showToast(`Error: ${error.message}`, 'error');
    }
}

// Make functions globally available
window.openCreateFolderModal = openCreateFolderModal;
window.closeCreateFolderModal = closeCreateFolderModal;
window.createFolder = createFolder;

async function loadBackups() {
    const backupDir = document.getElementById('backupDir')?.value || '/tmp/db-backups';
    const container = document.getElementById('backupList');
    container.innerHTML = '<div class="backup-list-loading">Loading backups...</div>';
    
    try {
        const response = await fetch(`/api/files/backups?backup_dir=${encodeURIComponent(backupDir)}`);
        const data = await response.json();
        
        if (data.success && data.backups.length > 0) {
            container.innerHTML = data.backups.map(session => `
                <div class="backup-group">
                    <div class="backup-group-title">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <rect x="3" y="4" width="18" height="18" rx="2" ry="2"/>
                            <line x1="16" y1="2" x2="16" y2="6"/>
                            <line x1="8" y1="2" x2="8" y2="6"/>
                            <line x1="3" y1="10" x2="21" y2="10"/>
                        </svg>
                        Session: ${session.session_id}
                        <span style="font-weight: normal; font-size: 0.8em; margin-left: 10px; opacity: 0.7;">
                            ${formatDate(session.created)}
                        </span>
                    </div>
                    
                    ${session.has_files ? `
                        <div class="backup-item file-backup">
                            <div class="backup-item-info">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 8px;">
                                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                                    <polyline points="14,2 14,8 20,8"/>
                                </svg>
                                <span class="backup-item-name">Project Files</span>
                                ${session.file_sizes && session.file_sizes['files.tar.gz'] ? 
                                    `<span style="font-size: 0.8em; opacity: 0.7; margin-left: 8px;">${formatBytes(session.file_sizes['files.tar.gz'])}</span>` : ''}
                            </div>
                            <div class="backup-item-actions">
                                <button class="btn btn-sm btn-icon" onclick="downloadBackup('${session.session_id}', 'files', null, '${backupDir}')" title="Download files archive">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                        <polyline points="7,10 12,15 17,10"/>
                                        <line x1="12" y1="15" x2="12" y2="3"/>
                                    </svg>
                                </button>
                                <span class="badge">Included</span>
                            </div>
                        </div>
                    ` : ''}
                    
                    ${session.databases.map(db => {
                        const dbFile = `${db}.sql`;
                        const fileSize = session.file_sizes && session.file_sizes[dbFile] ? 
                            formatBytes(session.file_sizes[dbFile]) : '';
                        return `
                        <div class="backup-item">
                            <div class="backup-item-info">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 8px;">
                                    <ellipse cx="12" cy="5" rx="9" ry="3"/>
                                    <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/>
                                    <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
                                </svg>
                                <span class="backup-item-name">${db}</span>
                                ${fileSize ? `<span style="font-size: 0.8em; opacity: 0.7; margin-left: 8px;">${fileSize}</span>` : ''}
                            </div>
                            <div class="backup-item-actions">
                                <button class="btn btn-sm btn-icon" onclick="downloadBackup('${session.session_id}', 'database', '${db}', '${backupDir}')" title="Download database dump">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                        <polyline points="7,10 12,15 17,10"/>
                                        <line x1="12" y1="15" x2="12" y2="3"/>
                                    </svg>
                                </button>
                                <button class="btn btn-sm" onclick="useBackupForRestore('${session.path}', '${db}', 'specific')">
                                    Restore DB
                                </button>
                            </div>
                        </div>
                    `}).join('')}
                    
                    <div class="backup-group-footer" style="padding: 10px; border-top: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center;">
                        <button class="btn btn-sm btn-icon" onclick="downloadBackup('${session.session_id}', 'all', null, '${backupDir}')" title="Download entire backup session">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                <polyline points="7,10 12,15 17,10"/>
                                <line x1="12" y1="15" x2="12" y2="3"/>
                            </svg>
                            Download All
                        </button>
                        <button class="btn btn-sm btn-primary" onclick="useBackupForRestore('${session.path}', null, 'all')">
                            Restore Session (All)
                        </button>
                    </div>
                </div>
            `).join('');
        } else {
            container.innerHTML = '<div class="backup-list-loading">No backups found</div>';
        }
    } catch (error) {
        container.innerHTML = `<div class="backup-list-loading">Error: ${error.message}</div>`;
    }
}

function useBackupForRestore(path, database, mode) {
    // Switch to restore tab
    switchTab('restore');
    
    if (mode === 'specific' && database) {
        // Database-only restore
        switchRestoreType('database');
        document.getElementById('restoreBackupFile').value = path;
        document.getElementById('restoreDatabase').value = database;
        showToast(`Selected ${database} for database-only restore`, 'success');
    } else {
        // Full project restore (all databases)
        switchRestoreType('project');
        document.getElementById('restoreProjectBackupFile').value = path;
        document.getElementById('restoreProjectDatabase').value = ''; // Empty = all databases
        showToast('Selected full session for project restore', 'success');
    }
}

// ============================================================================
// File Browser Modal
// ============================================================================

function openFileBrowser(inputId, selectDir = true) {
    state.modalTargetInput = inputId;
    state.modalSelectDir = selectDir;
    state.selectedFile = null;
    
    document.getElementById('fileBrowserModal').classList.add('show');
    // Start from empty path to let API determine the restricted root
    loadFileBrowser('', 'modalFileList', 'modalBrowserPath');
}

function closeFileBrowserModal() {
    document.getElementById('fileBrowserModal').classList.remove('show');
}

function switchRestoreType(type) {
    // Update tab buttons
    const tabs = document.querySelectorAll('.restore-type-tab');
    tabs.forEach(tab => {
        tab.classList.remove('active');
        if (tab.dataset.restoreType === type) {
            tab.classList.add('active');
        }
    });
    
    // Show/hide content sections with animation
    const databaseOnlyContent = document.getElementById('restore-database-only');
    const fullProjectContent = document.getElementById('restore-full-project');
    const connectionSection = document.getElementById('connectionSection');
    
    if (type === 'project') {
        // Hide database-only content
        if (databaseOnlyContent) {
            databaseOnlyContent.classList.remove('active');
        }
        // Show full project content
        if (fullProjectContent) {
            fullProjectContent.classList.add('active');
        }
        // Hide connection section for project restore
        if (connectionSection) {
            connectionSection.setAttribute('hidden', 'true');
            connectionSection.classList.add('hidden');
        }
    } else {
        // Show database-only content
        if (databaseOnlyContent) {
            databaseOnlyContent.classList.add('active');
        }
        // Hide full project content
        if (fullProjectContent) {
            fullProjectContent.classList.remove('active');
        }
        // Show connection section for database-only restore
        if (connectionSection) {
            connectionSection.removeAttribute('hidden');
            connectionSection.classList.remove('hidden');
        }
    }
    
    // Update button state after switching restore type
    updateStartButtonState();
}

// Make function globally available
window.switchRestoreType = switchRestoreType;

// Legacy function for backward compatibility
function changeRestoreType(type) {
    switchRestoreType(type);
}

// Make connection functions globally available for onclick handlers
window.openConnectionModal = openConnectionModal;
window.closeConnectionModal = closeConnectionModal;
window.selectConnection = selectConnection;
window.editConnection = editConnection;
window.deleteConnection = deleteConnection;

// Toggle restore inputs based on SQL dump file selection
function toggleRestoreInputs() {
    const sqlDumpFile = document.getElementById('restoreSqlDumpFile');
    const backupDirGroup = document.getElementById('restoreBackupDirGroup');
    const backupFileGroup = document.getElementById('restoreBackupFileGroup');
    
    if (!sqlDumpFile || !backupDirGroup || !backupFileGroup) {
        return;
    }
    
    const hasSqlFile = sqlDumpFile.value.trim().length > 0;
    
    if (hasSqlFile) {
        // Hide backup directory and backup file inputs
        backupDirGroup.style.display = 'none';
        backupFileGroup.style.display = 'none';
    } else {
        // Show backup directory and backup file inputs
        backupDirGroup.style.display = 'block';
        backupFileGroup.style.display = 'block';
    }
}

function selectFromModal() {
    let path = state.modalSelectDir ? state.modalFileBrowserPath : state.selectedFile;
    
    if (!path) {
        showToast('Please select a path', 'warning');
        return;
    }
    
    // Convert /host paths to actual host paths (remove /host prefix for display)
    // But keep /host in the path for internal use
    let displayPath = path;
    if (path.startsWith('/host')) {
        // For display, show the actual host path (remove /host prefix)
        displayPath = path.substring(5) || '/';
    }
    
    if (state.modalTargetInput === 'projectPath' || state.modalTargetInput === 'targetProjectPath') {
        document.getElementById(state.modalTargetInput).value = displayPath;
        closeFileBrowserModal();
    } else if (state.modalTargetInput === 'includeFolders') {
        addIncludeFolder(displayPath);
    } else {
        document.getElementById(state.modalTargetInput).value = displayPath;
        // If SQL dump file was selected, trigger toggle
        if (state.modalTargetInput === 'restoreSqlDumpFile') {
            toggleRestoreInputs();
        }
    }
    
    closeFileBrowserModal();
}

// ============================================================================
// Include Folders Management
// ============================================================================

function addIncludeFolder(path) {
    if (!state.includeFolders.includes(path)) {
        state.includeFolders.push(path);
        renderIncludeFolders();
    }
}

function removeIncludeFolder(path) {
    state.includeFolders = state.includeFolders.filter(f => f !== path);
    renderIncludeFolders();
}

function renderIncludeFolders() {
    const container = document.querySelector('#includeFolders .tags-container');
    container.innerHTML = state.includeFolders.map(folder => `
        <span class="tag">
            ${folder.split('/').pop() || folder}
            <span class="tag-remove" onclick="removeIncludeFolder('${folder}')">×</span>
        </span>
    `).join('');
}

// ============================================================================
// Saved Connections Management
// ============================================================================

async function loadSavedConnections() {
    const container = document.getElementById('savedConnectionsList');
    
    try {
        // Show loading state
        if (container) {
            container.innerHTML = '<div class="loading-text">Loading connections...</div>';
        }
        
        // Add timeout to prevent infinite loading
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 10000); // 10 second timeout
        
        const response = await fetch('/api/connections', {
            signal: controller.signal
        });
        
        clearTimeout(timeoutId);
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.json();
        
        if (data.success) {
            state.savedConnections = data.connections || [];
            renderSavedConnections();
        } else {
            // Handle case where success is false
            state.savedConnections = [];
            if (container) {
                container.innerHTML = '<div class="no-connections">No saved connections yet</div>';
            }
            console.error('Failed to load connections:', data);
        }
    } catch (error) {
        console.error('Error loading connections:', error);
        state.savedConnections = [];
        if (container) {
            if (error.name === 'AbortError') {
                container.innerHTML = '<div class="no-connections">Connection timeout. Please check your network and refresh.</div>';
            } else {
                container.innerHTML = '<div class="no-connections">Error loading connections. Please refresh the page.</div>';
            }
        }
    }
}

function renderSavedConnections() {
    const container = document.getElementById('savedConnectionsList');
    const countBadge = document.getElementById('connectionCount');
    
    if (!container) {
        console.error('savedConnectionsList container not found');
        return;
    }
    
    // Update connection count badge
    if (countBadge) {
        countBadge.textContent = state.savedConnections.length > 0 ? state.savedConnections.length : '';
    }
    
    if (!state.savedConnections || state.savedConnections.length === 0) {
        container.innerHTML = '<div class="no-connections">No saved connections yet</div>';
        return;
    }
    
    try {
        container.innerHTML = state.savedConnections.map(conn => `
        <div class="saved-connection-item ${state.activeConnectionId === conn.id ? 'active' : ''}" 
             data-id="${conn.id}"
             onclick="selectConnection('${conn.id}')">
            <span class="connection-color-dot" style="background: ${conn.color || '#6B7280'}"></span>
            <div class="connection-info">
                <div class="connection-name">${escapeHtml(conn.name)}</div>
                <div class="connection-details">${conn.username}@${conn.host}:${conn.port}</div>
            </div>
            <div class="connection-actions">
                <button class="connection-action-btn" onclick="event.stopPropagation(); editConnection('${conn.id}')" title="Edit">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                    </svg>
                </button>
                <button class="connection-action-btn delete" onclick="event.stopPropagation(); deleteConnection('${conn.id}')" title="Delete">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="3,6 5,6 21,6"/>
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                    </svg>
                </button>
            </div>
        </div>
    `).join('');
    } catch (error) {
        console.error('Error rendering connections:', error);
        container.innerHTML = '<div class="no-connections">Error displaying connections</div>';
    }
}

async function selectConnection(connId) {
    const conn = state.savedConnections.find(c => c.id === connId);
    if (!conn) return;
    
    // Update form fields
    document.getElementById('dbHost').value = conn.host;
    document.getElementById('dbPort').value = conn.port;
    document.getElementById('dbUsername').value = conn.username;
    
    // Fetch password if needed
    try {
        const response = await fetch(`/api/connections/${connId}?include_password=true`);
        const data = await response.json();
        if (data.success && data.connection.password) {
            document.getElementById('dbPassword').value = data.connection.password;
        } else {
            document.getElementById('dbPassword').value = '';
        }
    } catch (error) {
        document.getElementById('dbPassword').value = '';
    }
    
    state.activeConnectionId = connId;
    renderSavedConnections();
    updateActiveConnectionBadge(conn.name);
    showToast(`Switched to "${conn.name}"`, 'success');
}

function updateActiveConnectionBadge(name) {
    const badge = document.getElementById('activeConnectionBadge');
    badge.textContent = name || '';
}

function openConnectionModal(editId = null) {
    const modal = document.getElementById('saveConnectionModal');
    const title = document.getElementById('connectionModalTitle');
    const saveBtn = document.getElementById('saveConnectionBtn');
    
    if (!modal || !title || !saveBtn) {
        console.error('Connection modal elements not found');
        return;
    }
    
    state.editingConnectionId = editId;
    
    if (editId) {
        const conn = state.savedConnections.find(c => c.id === editId);
        if (conn) {
            title.textContent = 'Edit Connection';
            saveBtn.textContent = 'Update Connection';
            document.getElementById('connectionName').value = conn.name;
            document.getElementById('modalConnHost').value = conn.host;
            document.getElementById('modalConnPort').value = conn.port;
            document.getElementById('modalConnUsername').value = conn.username;
            document.getElementById('modalConnPassword').value = '';
            document.getElementById('modalConnPassword').placeholder = conn.has_password ? '••••••• (unchanged)' : 'Enter password';
            selectColor(conn.color || '#00D4AA');
        }
    } else {
        title.textContent = 'Save Connection';
        saveBtn.textContent = 'Save Connection';
        document.getElementById('connectionName').value = '';
        document.getElementById('modalConnHost').value = document.getElementById('dbHost').value;
        document.getElementById('modalConnPort').value = document.getElementById('dbPort').value;
        document.getElementById('modalConnUsername').value = document.getElementById('dbUsername').value;
        document.getElementById('modalConnPassword').value = document.getElementById('dbPassword').value;
        document.getElementById('modalConnPassword').placeholder = 'Enter password';
        selectColor('#00D4AA');
    }
    
    modal.classList.add('show');
}

function closeConnectionModal() {
    const modal = document.getElementById('saveConnectionModal');
    if (modal) {
        modal.classList.remove('show');
    }
    state.editingConnectionId = null;
}

function selectColor(color) {
    state.selectedColor = color;
    document.querySelectorAll('.color-option').forEach(btn => {
        btn.classList.toggle('selected', btn.dataset.color === color);
    });
}

async function saveConnection() {
    const name = document.getElementById('connectionName').value.trim();
    const host = document.getElementById('modalConnHost').value || 'localhost';
    const port = parseInt(document.getElementById('modalConnPort').value) || 5432;
    const username = document.getElementById('modalConnUsername').value || 'postgres';
    const password = document.getElementById('modalConnPassword').value || null;
    
    if (!name) {
        showToast('Please enter a connection name', 'error');
        return;
    }
    
    const payload = {
        name,
        host,
        port,
        username,
        password: password || undefined,
        color: state.selectedColor
    };
    
    try {
        let response;
        if (state.editingConnectionId) {
            response = await fetch(`/api/connections/${state.editingConnectionId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
        } else {
            response = await fetch('/api/connections', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
        }
        
        const data = await response.json();
        
        if (response.ok) {
            showToast(data.message, 'success');
            closeConnectionModal();
            await loadSavedConnections();
        } else {
            showToast(data.detail || 'Failed to save connection', 'error');
        }
    } catch (error) {
        showToast(`Error: ${error.message}`, 'error');
    }
}

function editConnection(connId) {
    openConnectionModal(connId);
}

async function deleteConnection(connId) {
    const conn = state.savedConnections.find(c => c.id === connId);
    if (!conn) return;
    
    if (!confirm(`Delete connection "${conn.name}"?`)) return;
    
    try {
        const response = await fetch(`/api/connections/${connId}`, {
            method: 'DELETE'
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showToast(data.message, 'success');
            if (state.activeConnectionId === connId) {
                state.activeConnectionId = null;
                updateActiveConnectionBadge('');
            }
            await loadSavedConnections();
        } else {
            showToast(data.detail || 'Failed to delete connection', 'error');
        }
    } catch (error) {
        showToast(`Error: ${error.message}`, 'error');
    }
}

// ============================================================================
// Collapsible Saved Connections
// ============================================================================

function toggleSavedConnections() {
    const section = document.getElementById('savedConnectionsSection');
    if (section) {
        section.classList.toggle('collapsed');
        // Save state to localStorage
        localStorage.setItem('savedConnectionsCollapsed', section.classList.contains('collapsed'));
    }
}

function loadSavedConnectionsState() {
    const isCollapsed = localStorage.getItem('savedConnectionsCollapsed') === 'true';
    const section = document.getElementById('savedConnectionsSection');
    if (section && isCollapsed) {
        section.classList.add('collapsed');
    }
}

// ============================================================================
// Configuration Helpers
// ============================================================================

function getConnectionConfig() {
    return {
        host: document.getElementById('dbHost').value || 'host.docker.internal',
        port: parseInt(document.getElementById('dbPort').value) || 5432,
        username: document.getElementById('dbUsername').value || 'postgres',
        password: document.getElementById('dbPassword').value || null
    };
}

function getStorageConfig() {
    let type = state.storageType;
    // Ensure storage type is valid, default to 'local' if not
    const validTypes = ['local', 's3', 'minio', 'gdrive'];
    if (!validTypes.includes(type)) {
        type = 'local';
        state.storageType = 'local';
    }
    const config = { storage_type: type };
    
    if (type === 's3') {
        config.access_key = document.getElementById('s3AccessKey')?.value;
        config.secret_key = document.getElementById('s3SecretKey')?.value;
        config.bucket = document.getElementById('s3Bucket')?.value;
        config.region = document.getElementById('s3Region')?.value;
    } else if (type === 'minio') {
        config.endpoint = document.getElementById('minioEndpoint')?.value;
        config.access_key = document.getElementById('minioAccessKey')?.value;
        config.secret_key = document.getElementById('minioSecretKey')?.value;
        config.bucket = document.getElementById('minioBucket')?.value;
    } else if (type === 'gdrive') {
        config.credentials_path = document.getElementById('gdriveCredentials')?.value;
    }
    
    return config;
}

// ============================================================================
// Tab Management
// ============================================================================

function switchTab(tabName) {
    state.currentTab = tabName;
    
    // Update tab buttons
    document.querySelectorAll('.tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.tab === tabName);
    });
    
    // Update tab content
    document.querySelectorAll('.tab-content').forEach(content => {
        content.classList.toggle('active', content.id === `tab-${tabName}`);
    });
    
    // Show/hide connection section based on tab
    const connectionSection = document.getElementById('connectionSection');
    if (connectionSection) {
        if (tabName === 'backup') {
            // Always show connection section on backup tab
            connectionSection.removeAttribute('hidden');
            connectionSection.classList.remove('hidden');
        } else if (tabName === 'restore') {
            // Connection section visibility is managed by switchRestoreType
            // Don't change it here, let switchRestoreType handle it
        } else {
            // For other tabs, show connection section
            connectionSection.removeAttribute('hidden');
            connectionSection.classList.remove('hidden');
        }
    }
    
    // Update start button text
    const startBtn = document.getElementById('startBtn');
    if (tabName === 'backup') {
        startBtn.innerHTML = `
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polygon points="5,3 19,12 5,21"/>
            </svg>
            Start Backup
        `;
    } else if (tabName === 'restore') {
        startBtn.innerHTML = `
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polygon points="5,3 19,12 5,21"/>
            </svg>
            Start Restore
        `;
    }
    
    // Update button state when switching tabs
    updateStartButtonState();
    
    // Load data for specific tabs
    if (tabName === 'files') {
        loadFileBrowser(state.fileBrowserPath);
        loadBackups();
    }
}

function switchStorageType(type) {
    state.storageType = type;
    
    // Update segment buttons - only storage type segments
    document.querySelectorAll('#storageConfig .segment').forEach(seg => {
        seg.classList.toggle('active', seg.dataset.value === type);
    });
    
    // Update storage config visibility
    document.querySelectorAll('#storageConfig > div').forEach(div => {
        div.classList.toggle('active', div.classList.contains(`storage-${type}`));
    });
}

// ============================================================================
// Theme Management
// ============================================================================

function toggleTheme() {
    const html = document.documentElement;
    const currentTheme = html.dataset.theme || 'dark';
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
    html.dataset.theme = newTheme;
    localStorage.setItem('theme', newTheme);
}

function loadTheme() {
    const savedTheme = localStorage.getItem('theme') || 'dark';
    document.documentElement.dataset.theme = savedTheme;
}

// ============================================================================
// Toast Notifications
// ============================================================================

function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
        <span class="toast-message">${message}</span>
        <button class="toast-close" onclick="this.parentElement.remove()">×</button>
    `;
    
    container.appendChild(toast);
    
    // Auto remove after 5 seconds
    setTimeout(() => {
        toast.style.animation = 'slideIn 0.3s ease reverse';
        setTimeout(() => toast.remove(), 300);
    }, 5000);
}

// ============================================================================
// Utility Functions
// ============================================================================

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function capitalize(str) {
    return str.charAt(0).toUpperCase() + str.slice(1);
}

function getCurrentTime() {
    return new Date().toLocaleTimeString('en-US', { hour12: false });
}

function formatFileSize(bytes) {
    if (bytes === null || bytes === undefined) return '';
    const units = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    while (bytes >= 1024 && i < units.length - 1) {
        bytes /= 1024;
        i++;
    }
    return `${bytes.toFixed(1)} ${units[i]}`;
}

function formatDate(isoString) {
    return new Date(isoString).toLocaleString();
}

// ============================================================================
// Event Listeners
// ============================================================================

document.addEventListener('DOMContentLoaded', async () => {
    // Check authentication first
    const isAuthenticated = await checkAuth();
    if (!isAuthenticated) {
        return;
    }
    
    // Load theme
    loadTheme();
    
    // Load saved connections collapsed state
    loadSavedConnectionsState();
    
    // Connect WebSocket
    connectWebSocket();
    
    // Load saved connections
    loadSavedConnections();
    
    // Initialize restore type (default to database-only)
    switchRestoreType('database');
    
    // Handle SQL dump file selection - hide/show other inputs
    const sqlDumpFileInput = document.getElementById('restoreSqlDumpFile');
    if (sqlDumpFileInput) {
        sqlDumpFileInput.addEventListener('input', () => {
            toggleRestoreInputs();
            updateStartButtonState();
        });
        sqlDumpFileInput.addEventListener('change', () => {
            toggleRestoreInputs();
            updateStartButtonState();
        });
    }
    
    // Add event listeners to backup form inputs to update button state
    const projectPathInput = document.getElementById('projectPath');
    if (projectPathInput) {
        projectPathInput.addEventListener('input', updateStartButtonState);
        projectPathInput.addEventListener('change', updateStartButtonState);
    }
    
    const dbHostInput = document.getElementById('dbHost');
    if (dbHostInput) {
        dbHostInput.addEventListener('input', updateStartButtonState);
        dbHostInput.addEventListener('change', updateStartButtonState);
    }
    
    const dbUsernameInput = document.getElementById('dbUsername');
    if (dbUsernameInput) {
        dbUsernameInput.addEventListener('input', updateStartButtonState);
        dbUsernameInput.addEventListener('change', updateStartButtonState);
    }
    
    // Add event listeners to restore form inputs to update button state
    const restoreDatabaseInput = document.getElementById('restoreDatabase');
    if (restoreDatabaseInput) {
        restoreDatabaseInput.addEventListener('input', updateStartButtonState);
        restoreDatabaseInput.addEventListener('change', updateStartButtonState);
    }
    
    const targetProjectPathInput = document.getElementById('targetProjectPath');
    if (targetProjectPathInput) {
        targetProjectPathInput.addEventListener('input', updateStartButtonState);
        targetProjectPathInput.addEventListener('change', updateStartButtonState);
    }
    
    // Initialize restore inputs visibility
    toggleRestoreInputs();
    
    // Initialize button state
    updateStartButtonState();
    
    // Initialize file browser to restricted root
    loadFileBrowser('', 'fileList', 'fileBrowserPath');
    
    // Tab switching
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => switchTab(tab.dataset.tab));
    });
    
    // Storage type switching
    // Storage type segments only
    document.querySelectorAll('#storageConfig .segment').forEach(seg => {
        seg.addEventListener('click', () => switchStorageType(seg.dataset.value));
    });
    
    // Theme toggle
    document.getElementById('themeToggle').addEventListener('click', toggleTheme);
    
    // Connection buttons
    document.getElementById('testConnection').addEventListener('click', testConnection);
    document.getElementById('listDatabases').addEventListener('click', listDatabases);
    
    // Saved connections buttons
    document.getElementById('addConnectionBtn').addEventListener('click', () => openConnectionModal());
    document.getElementById('saveCurrentConnection').addEventListener('click', () => openConnectionModal());
    document.getElementById('saveConnectionBtn').addEventListener('click', saveConnection);
    
    // Color picker
    document.querySelectorAll('.color-option').forEach(btn => {
        btn.addEventListener('click', () => selectColor(btn.dataset.color));
    });
    
    // Close connection modal on backdrop click
    document.getElementById('saveConnectionModal').addEventListener('click', (e) => {
        if (e.target.id === 'saveConnectionModal') {
            closeConnectionModal();
        }
    });
    
    // Action buttons
    document.getElementById('startBtn').addEventListener('click', startOperation);
    document.getElementById('stopBtn').addEventListener('click', stopOperation);
    
    // Terminal buttons
    document.getElementById('clearLogs').addEventListener('click', clearLogs);
    document.getElementById('downloadLogs').addEventListener('click', downloadLogs);
    
    // File browser navigation
    const fileBrowserBackBtn = document.getElementById('fileBrowserBack');
    if (fileBrowserBackBtn) {
        fileBrowserBackBtn.addEventListener('click', () => {
            navigateBack('fileList', 'fileBrowserPath');
        });
    } else {
        console.error('fileBrowserBack button not found');
    }
    document.getElementById('fileBrowserRefresh').addEventListener('click', () => {
        const search = document.getElementById('fileBrowserSearch')?.value || '';
        loadFileBrowser(state.fileBrowserPath, 'fileList', 'fileBrowserPath', search);
    });
    const fileBrowserCreateBtn = document.getElementById('fileBrowserCreateFolder');
    if (fileBrowserCreateBtn) {
        fileBrowserCreateBtn.addEventListener('click', () => {
            openCreateFolderModal('fileList', 'fileBrowserPath');
        });
    } else {
        console.error('fileBrowserCreateFolder button not found');
    }
    
    // File browser search (main browser)
    const fileSearchInput = document.getElementById('fileBrowserSearch');
    if (fileSearchInput) {
        fileSearchInput.addEventListener('input', (e) => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                loadFileBrowser(state.fileBrowserPath, 'fileList', 'fileBrowserPath', e.target.value);
            }, 300);
        });
    }
    
    // File browser search (modal browser)
    const modalSearchInput = document.getElementById('modalBrowserSearch');
    if (modalSearchInput) {
        modalSearchInput.addEventListener('input', (e) => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                loadFileBrowser(state.modalFileBrowserPath, 'modalFileList', 'modalBrowserPath', e.target.value);
            }, 300);
        });
    }
    
    // Modal file browser back button
    const modalBrowserBackBtn = document.getElementById('modalBrowserBack');
    if (modalBrowserBackBtn) {
        modalBrowserBackBtn.addEventListener('click', () => {
            navigateBack('modalFileList', 'modalBrowserPath');
        });
    } else {
        console.error('modalBrowserBack button not found');
    }
    
    // Modal file browser create folder button
    const modalBrowserCreateBtn = document.getElementById('modalBrowserCreateFolder');
    if (modalBrowserCreateBtn) {
        modalBrowserCreateBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            console.log('Create folder button clicked in modal');
            openCreateFolderModal('modalFileList', 'modalBrowserPath');
        });
        console.log('Modal create folder button listener attached');
    } else {
        console.error('modalBrowserCreateFolder button not found');
    }
    
    // Create folder modal confirm button
    const createFolderConfirmBtn = document.getElementById('createFolderConfirmBtn');
    if (createFolderConfirmBtn) {
        createFolderConfirmBtn.addEventListener('click', createFolder);
    }
    
    // Create folder modal - Enter key support
    const folderNameInput = document.getElementById('folderNameInput');
    if (folderNameInput) {
        folderNameInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                createFolder();
            }
        });
    }
    
    // Close modal on backdrop click
    const createFolderModal = document.getElementById('createFolderModal');
    if (createFolderModal) {
        createFolderModal.addEventListener('click', (e) => {
            if (e.target === createFolderModal) {
                closeCreateFolderModal();
            }
        });
    }
    
    // Modal file browser select button
    document.getElementById('modalSelectBtn').addEventListener('click', selectFromModal);
    
    // Close modal on backdrop click
    document.getElementById('fileBrowserModal').addEventListener('click', (e) => {
        if (e.target.id === 'fileBrowserModal') {
            closeFileBrowserModal();
        }
    });
    
    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            closeFileBrowserModal();
            closeConnectionModal();
        }
    });
    
    // Keep WebSocket alive
    setInterval(() => {
        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            state.ws.send('ping');
        }
    }, 30000);
    
    // Load uploaded SQL files when restore tab is opened
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => {
            if (tab.dataset.tab === 'restore') {
                loadUploadedSqlFiles();
            }
        });
    });
    
    // Initial load of uploaded SQL files
    loadUploadedSqlFiles();
});

// ============================================================================
// SQL File Upload
// ============================================================================

function openSqlUploadDialog() {
    document.getElementById('sqlUploadModal').classList.add('show');
    document.getElementById('sqlFileInput').value = '';
    document.getElementById('sqlUploadStatus').style.display = 'none';
    document.getElementById('sqlUploadConfirmBtn').disabled = true;
}

function closeSqlUploadModal() {
    document.getElementById('sqlUploadModal').classList.remove('show');
}

function handleSqlFileSelect() {
    const fileInput = document.getElementById('sqlFileInput');
    const uploadBtn = document.getElementById('sqlUploadConfirmBtn');
    const status = document.getElementById('sqlUploadStatus');
    
    if (fileInput.files && fileInput.files.length > 0) {
        const file = fileInput.files[0];
        if (file.name.endsWith('.sql') || file.name.endsWith('.dump')) {
            uploadBtn.disabled = false;
            status.style.display = 'none';
        } else {
            uploadBtn.disabled = true;
            status.style.display = 'block';
            status.className = 'upload-status error';
            status.textContent = 'Please select a .sql or .dump file';
        }
    } else {
        uploadBtn.disabled = true;
    }
}

async function uploadSqlFile() {
    const fileInput = document.getElementById('sqlFileInput');
    const backupDir = document.getElementById('sqlUploadBackupDir').value || '/tmp/db-backups';
    const status = document.getElementById('sqlUploadStatus');
    const uploadBtn = document.getElementById('sqlUploadConfirmBtn');
    
    if (!fileInput.files || fileInput.files.length === 0) {
        status.style.display = 'block';
        status.className = 'upload-status error';
        status.textContent = 'Please select a file';
        return;
    }
    
    const file = fileInput.files[0];
    const formData = new FormData();
    formData.append('file', file);
    formData.append('backup_dir', backupDir);
    
    uploadBtn.disabled = true;
    status.style.display = 'block';
    status.className = 'upload-status info';
    status.textContent = 'Uploading...';
    
    try {
        const response = await fetch('/api/upload-sql', {
            method: 'POST',
            body: formData
        });
        
        const data = await response.json();
        
        if (data.success) {
            status.className = 'upload-status success';
            status.textContent = `File uploaded successfully: ${data.filename}`;
            
            // Update the SQL dump file input
            document.getElementById('restoreSqlDumpFile').value = data.path;
            toggleRestoreInputs();
            
            // Reload uploaded files list
            await loadUploadedSqlFiles();
            
            // Close modal after a short delay
            setTimeout(() => {
                closeSqlUploadModal();
                showToast('SQL file uploaded successfully', 'success');
            }, 1500);
        } else {
            status.className = 'upload-status error';
            status.textContent = data.error || 'Upload failed';
            uploadBtn.disabled = false;
        }
    } catch (error) {
        status.className = 'upload-status error';
        status.textContent = `Upload error: ${error.message}`;
        uploadBtn.disabled = false;
    }
}

async function loadUploadedSqlFiles() {
    const backupDir = document.getElementById('restoreBackupDir')?.value || '/tmp/db-backups';
    const select = document.getElementById('uploadedSqlFiles');
    const group = document.getElementById('uploadedSqlFilesGroup');
    
    if (!select || !group) return;
    
    try {
        const response = await fetch(`/api/list-uploaded-sql?backup_dir=${encodeURIComponent(backupDir)}`);
        const data = await response.json();
        
        if (data.success && data.files && data.files.length > 0) {
            select.innerHTML = '<option value="">Select an uploaded SQL file...</option>' +
                data.files.map(file => 
                    `<option value="${file.path}" data-size="${file.size}">${file.filename} (${formatBytes(file.size)})</option>`
                ).join('');
            group.style.display = 'block';
        } else {
            group.style.display = 'none';
        }
    } catch (error) {
        console.error('Error loading uploaded SQL files:', error);
        group.style.display = 'none';
    }
}

function selectUploadedSqlFile() {
    const select = document.getElementById('uploadedSqlFiles');
    const sqlDumpInput = document.getElementById('restoreSqlDumpFile');
    
    if (select && select.value && sqlDumpInput) {
        sqlDumpInput.value = select.value;
        toggleRestoreInputs();
        updateStartButtonState();
    }
}

// ============================================================================
// Backup Download
// ============================================================================

async function downloadBackup(sessionId, fileType, databaseName, backupDir) {
    try {
        const params = new URLSearchParams({
            session_id: sessionId,
            file_type: fileType,
            backup_dir: backupDir || '/tmp/db-backups'
        });
        
        if (databaseName) {
            params.append('database_name', databaseName);
        }
        
        const url = `/api/download-backup?${params.toString()}`;
        
        // Create a temporary link and click it to trigger download
        const link = document.createElement('a');
        link.href = url;
        link.style.display = 'none';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        
        showToast('Download started', 'success');
    } catch (error) {
        showToast(`Download error: ${error.message}`, 'error');
    }
}

// Make functions globally available
window.openSqlUploadDialog = openSqlUploadDialog;
window.closeSqlUploadModal = closeSqlUploadModal;
window.handleSqlFileSelect = handleSqlFileSelect;
window.uploadSqlFile = uploadSqlFile;
window.loadUploadedSqlFiles = loadUploadedSqlFiles;
window.selectUploadedSqlFile = selectUploadedSqlFile;
window.downloadBackup = downloadBackup;
window.logout = logout;


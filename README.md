# PostgreSQL Backup & Restore Utility

A comprehensive PostgreSQL database backup and restore utility with cloud storage support and automatic filestore management.

## 🚀 Features

- **Full Database Backup**: Complete PostgreSQL database dumps with custom options
- **Filestore Integration**: Automatic backup and restoration of file attachments
- **Cloud Storage Support**: MinIO, AWS S3, and Google Drive integration
- **Flexible Restore**: Database and filestore restoration with conflict resolution
- **Automated Setup**: Virtual environment and dependency management
- **Comprehensive Logging**: Detailed operation logs and error handling
- **Smart Backup Selection**: Automatically finds latest backup from timestamp folders or archives
- **Robust Error Handling**: Connection testing, timeout management, and graceful failure recovery
- **Cross-Platform**: Works on Linux, macOS, and Windows

## 📋 Prerequisites

- **Python 3.7+** (automatically checked by the script)
- **PostgreSQL Client** (automatically installed)
- **Sudo/Root Access** (for system package installation)
- **Network Access** (for cloud storage operations)

## 🛠️ Installation

The script automatically handles all dependencies and setup:

```bash
# Make the script executable
chmod +x run.sh

# The script will automatically:
# - Check Python version
# - Create virtual environment
# - Install required packages
# - Set up PostgreSQL client
```

## 📖 Usage

### Basic Syntax

```bash
sudo ./run.sh --action [backup|restore] [OPTIONS]
```

### 🔄 Backup Operations

#### Local Backup (All Databases)
```bash
sudo ./run.sh --action=backup \
    --backup-dir=/tmp/backups \
    --host=localhost \
    --port=5432 \
    --username=postgres \
    --password=your_password
```

#### Backup Specific Database with Filestore
```bash
sudo ./run.sh --action=backup \
    --database=myapp_db \
    --include-folders=/path/to/filestore \
    --backup-dir=/tmp/backups \
    --host=localhost \
    --port=5432 \
    --username=postgres \
    --password=your_password
```

#### MinIO Cloud Backup
```bash
sudo ./run.sh --action=backup \
    --database=myapp_db \
    --include-folders=/path/to/filestore \
    --backup-dir=/tmp/backups \
    --host=localhost \
    --port=5432 \
    --username=postgres \
    --password=your_password \
    --storage-type=minio \
    --storage-endpoint=your-minio-server:9000 \
    --storage-access-key=your_access_key \
    --storage-secret-key=your_secret_key \
    --storage-bucket=your_bucket
```

#### AWS S3 Backup
```bash
sudo ./run.sh --action=backup \
    --database=myapp_db \
    --include-folders=/path/to/filestore \
    --backup-dir=/tmp/backups \
    --host=localhost \
    --port=5432 \
    --username=postgres \
    --password=your_password \
    --storage-type=s3 \
    --storage-access-key=your_access_key \
    --storage-secret-key=your_secret_key \
    --storage-bucket=your_bucket \
    --storage-region=us-east-1
```

### 🔄 Restore Operations

#### Database Only Restore
```bash
sudo ./run.sh --action=restore \
    --database=new_database_name \
    --backup-dir=/tmp/backups \
    --host=localhost \
    --port=5432 \
    --username=postgres \
    --password=your_password \
    --drop-existing
```

#### Database + Filestore Restore (Default Behavior)
```bash
sudo ./run.sh --action=restore \
    --database=new_database_name \
    --backup-dir=/tmp/backups \
    --host=localhost \
    --port=5432 \
    --username=postgres \
    --password=your_password \
    --drop-existing
    # Filestore is automatically restored to /home/ubuntu/projects/{database}/filestore
```

#### Database Only Restore (Skip Filestore)
```bash
sudo ./run.sh --action=restore \
    --database=new_database_name \
    --backup-dir=/tmp/backups \
    --host=localhost \
    --port=5432 \
    --username=postgres \
    --password=your_password \
    --drop-existing \
    --skip-filestore
```

#### Restore with Custom Project Path
```bash
sudo ./run.sh --action=restore \
    --database=new_database_name \
    --backup-dir=/tmp/backups \
    --host=localhost \
    --port=5432 \
    --username=postgres \
    --password=your_password \
    --drop-existing \
    --project-path=/path/to/your/project
    # Filestore will be restored to /path/to/your/project/filestore
```

#### Restore with Custom Filestore Path
```bash
sudo ./run.sh --action=restore \
    --database=new_database_name \
    --backup-dir=/tmp/backups \
    --host=localhost \
    --port=5432 \
    --username=postgres \
    --password=your_password \
    --drop-existing \
    --filestore-path=/custom/path/to/filestore
```

> **Note:** Restore operations work with local backups only. Cloud storage parameters are ignored during restore as the backup should already exist locally.

## 📝 Command Line Arguments

### Common Arguments
| Argument | Description | Required | Default |
|----------|-------------|----------|---------|
| `--action` | Operation to perform: `backup` or `restore` | ✅ | - |
| `--database` | Database name to backup/restore | ❌ | All databases (backup only) |
| `--backup-dir` | Local backup directory | ❌ | `/tmp/postgres-backups` |
| `--backup-file` | Specific backup file for restore | ❌ | Latest backup |

### Database Connection
| Argument | Description | Required | Default |
|----------|-------------|----------|---------|
| `--host` | PostgreSQL server host | ❌ | `localhost` |
| `--port` | PostgreSQL server port | ❌ | `5432` |
| `--username` | Database username | ❌ | `postgres` |
| `--password` | Database password | ❌ | - |

### Backup Options
| Argument | Description | Required | Default |
|----------|-------------|----------|---------|
| `--include-folders` | Folders to include in backup | ❌ | - |

### Restore Options
| Argument | Description | Required | Default |
|----------|-------------|----------|---------|
| `--drop-existing` | Drop existing database before restore | ❌ | `false` |
| `--skip-filestore` | Skip filestore restoration (database only) | ❌ | `false` |
| `--project-path` | Project root path where filestore will be restored | ❌ | `/home/ubuntu/projects/{database}` |
| `--filestore-path` | Custom filestore path (overrides project-path) | ❌ | `{project-path}/filestore` |

### Cloud Storage (Backup Only)
> **Note:** Cloud storage parameters are only used during backup operations. Restore operations work with local backups only.

| Argument | Description | Required | Default |
|----------|-------------|----------|---------|
| `--storage-type` | Storage backend: `local`, `s3`, `minio`, `gdrive` | ❌ | `local` |
| `--storage-endpoint` | Cloud storage endpoint | ❌ | - |
| `--storage-access-key` | Cloud storage access key | ❌ | - |
| `--storage-secret-key` | Cloud storage secret key | ❌ | - |
| `--storage-bucket` | Cloud storage bucket name | ❌ | - |
| `--storage-region` | Cloud storage region (S3 only) | ❌ | - |
| `--storage-credentials` | Path to credentials file (Google Drive) | ❌ | - |

## 🏗️ Architecture

### Backup Process
1. **Database Dump**: Uses `pg_dump` with optimized settings
2. **Filestore Archive**: Compresses specified folders into tar.gz
3. **Combined Archive**: Creates final backup archive
4. **Cloud Upload**: Uploads to configured cloud storage (optional)

### Restore Process
1. **Database Drop**: Removes existing database (if `--drop-existing`)
2. **Database Create**: Creates fresh database
3. **Database Restore**: Restores from SQL dump
4. **Filestore Restore**: Extracts and places filestore (if `--restore-filestore`)

## 📁 File Structure

```
postgres-backup/
├── run.sh                 # Main launcher script
├── backup_script.py       # Core backup/restore logic
├── README.md             # This documentation
├── backup.log            # Operation logs
└── pg_backup_venv/       # Python virtual environment
```

## 🔧 Advanced Usage

### Custom Backup Directory Structure
```
/tmp/backups/
├── database1/
│   ├── 2024-01-15_10-30-00/
│   │   ├── database1_dump.sql
│   │   └── filestore_backup.tar.gz
│   └── database1_2024-01-15_10-30-00_backup.tar.gz
└── database2/
    └── ...
```

### Environment Variables
You can set environment variables to avoid passing credentials:

```bash
export PGPASSWORD="your_password"
export AWS_ACCESS_KEY_ID="your_access_key"
export AWS_SECRET_ACCESS_KEY="your_secret_key"
```

### Cron Job Setup
```bash
# Daily backup at 2 AM
0 2 * * * /path/to/postgres-backup/run.sh --action=backup --database=myapp_db --include-folders=/var/lib/myapp/filestore --backup-dir=/backups --host=localhost --username=postgres --password=my_password --storage-type=s3 --storage-bucket=my-backups
```

## 🚨 Troubleshooting

### Common Issues

#### Permission Denied
```bash
# Ensure script is executable
chmod +x run.sh

# Run with sudo for system package installation
sudo ./run.sh [options]
```

#### Database Connection Failed
```bash
# Check PostgreSQL is running
sudo systemctl status postgresql

# Test connection manually
psql -h localhost -p 5432 -U postgres -d postgres
```

#### Cloud Storage Upload Failed
```bash
# Check network connectivity
ping your-storage-endpoint

# Verify credentials
# Test with cloud storage client directly
```

### Log Files
- **backup.log**: Detailed operation logs
- **Console Output**: Real-time status updates
- **Error Messages**: Specific failure details

## 🔒 Security Considerations

- **Credential Management**: Passwords and keys via command line (consider using environment variables)
- **File Permissions**: Backup files inherit system permissions
- **Network Security**: Use HTTPS endpoints for cloud storage
- **Access Control**: Limit script execution to authorized users

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## 📄 License

This project is open source. Please check the license file for details.

## 🆘 Support

For issues and questions:
1. Check the troubleshooting section
2. Review log files
3. Create an issue with detailed information
4. Include system information and error messages

---

**Made with ❤️ for PostgreSQL users**

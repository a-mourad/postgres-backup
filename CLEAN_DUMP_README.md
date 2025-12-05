# Clean Dump File Script

This script cleans PostgreSQL dump files to remove problematic statements that cause restore errors.

## Problem

When restoring a database dump, you may encounter errors like:
- `ERROR: duplicate key value violates unique constraint "pg_constraint_conrelid_contypid_conname_index"`
- `ERROR: relation "public.discuss_channel" does not exist`

These errors occur because:
1. The dump file contains direct `INSERT INTO pg_constraint` statements (system catalog modifications)
2. The dump includes constraint definitions for tables that don't exist in the dump

## Solution

The `clean_dump_file.py` script removes:
1. All `INSERT INTO pg_constraint` statements (system catalog modifications)
2. All `\copy pg_constraint` commands
3. `ALTER TABLE ... ADD CONSTRAINT` statements that reference non-existent tables
4. Constraints referencing specific problematic tables (like `discuss_channel`)

## Usage

### Basic Usage

```bash
python clean_dump_file.py input.sql output.sql
```

### Verbose Mode

```bash
python clean_dump_file.py input.sql output.sql --verbose
```

### In-Place Modification

```bash
python clean_dump_file.py input.sql --in-place
```

This creates a backup (`input.sql.backup`) and modifies the original file.

### Examples

```bash
# Clean a dump file
python clean_dump_file.py ruetech_dump.sql ruetech_dump_cleaned.sql

# Clean with verbose output
python clean_dump_file.py ruetech_dump.sql ruetech_dump_cleaned.sql -v

# Clean in place (creates backup automatically)
python clean_dump_file.py ruetech_dump.sql --in-place
```

## What It Does

1. **Tracks all tables** created in the dump file
2. **Removes system catalog INSERTs** - All `INSERT INTO pg_constraint` statements
3. **Removes problematic constraints** - Constraints on tables that don't exist
4. **Removes foreign key constraints** - FKs referencing non-existent tables
5. **Preserves valid constraints** - Keeps all constraints for tables that exist

## Output

The script provides a summary:
```
======================================================================
Cleanup Summary
======================================================================
Tables tracked:           668
Lines removed:            89
Constraints removed:      58
Output file:              ruetech_dump_cleaned.sql
======================================================================
```

## Integration with Backup Script

The backup script has been updated to use better `pg_dump` options:
- `--no-tablespaces` - Prevents tablespace issues
- `--no-privileges` - Prevents privilege-related issues

However, if you still get errors, use this script to clean the dump file before restoring.

## Workflow

1. **Create a backup** (or use existing dump file)
2. **Clean the dump file**:
   ```bash
   python clean_dump_file.py original_dump.sql cleaned_dump.sql
   ```
3. **Restore from cleaned dump**:
   ```bash
   python backup_script.py --action restore --sql-dump-file cleaned_dump.sql ...
   ```

## Notes

- The script preserves all valid SQL statements
- Only problematic lines are removed
- The cleaned dump should restore without constraint errors
- Always test the cleaned dump on a development database first

## Troubleshooting

If you still get errors after cleaning:
1. Check the verbose output to see what was removed
2. Verify the dump file is complete (not truncated)
3. Consider fixing the source database first (see `fix_database_constraints.sql`)
4. Try creating a fresh dump after fixing the source database



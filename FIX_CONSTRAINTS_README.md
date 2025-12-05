# Database Constraint Fix Script

This script fixes common PostgreSQL database issues that cause restore errors:

1. **Duplicate constraints** in the system catalog (`pg_constraint`)
2. **Orphaned constraints** referencing non-existent tables
3. **Foreign keys** referencing non-existent tables
4. **Orphaned indexes** associated with removed constraints

## Problem

When restoring a database, you may encounter errors like:
- `ERROR: duplicate key value violates unique constraint "pg_constraint_conrelid_contypid_conname_index"`
- `ERROR: relation "public.discuss_channel" does not exist` (when constraints reference it)

These errors occur when:
- The database has duplicate constraint entries in system catalogs
- Constraints reference tables that were dropped but the constraints weren't cleaned up
- Foreign keys point to non-existent tables

## Solution

The fix script (`fix_database_constraints.sql`) will:
1. Identify all duplicate and orphaned constraints
2. Remove orphaned constraints (constraints on non-existent tables)
3. Remove foreign keys referencing non-existent tables
4. Remove duplicate constraints (keeping the first one)
5. Clean up associated orphaned indexes
6. Verify the cleanup was successful

## Usage

### Option 1: Using the helper script (Recommended)

```bash
./run_fix_constraints.sh [host] [port] [username] [database] [password]
```

**Example:**
```bash
./run_fix_constraints.sh host.docker.internal 2320 odoo ruetech
```

The script will prompt for password if not provided, or you can pass it as the 5th argument.

### Option 2: Direct psql execution

```bash
psql -h host.docker.internal -p 2320 -U odoo -d ruetech -f fix_database_constraints.sql
```

Or with password:
```bash
PGPASSWORD=your_password psql -h host.docker.internal -p 2320 -U odoo -d ruetech -f fix_database_constraints.sql
```

## What the Script Does

The script performs the following steps:

1. **Reports duplicate constraints** - Shows which constraints are duplicated
2. **Reports orphaned constraints** - Shows constraints on non-existent tables
3. **Reports orphaned foreign keys** - Shows FKs referencing non-existent tables
4. **Removes orphaned constraints** - Drops constraints on non-existent tables
5. **Removes orphaned foreign keys** - Drops FKs referencing non-existent tables
6. **Removes duplicate constraints** - Keeps the first constraint, removes duplicates
7. **Cleans up orphaned indexes** - Removes indexes associated with removed constraints
8. **Verifies results** - Shows remaining issues (should be none)

## Important Notes

⚠️ **WARNING**: This script modifies PostgreSQL system catalogs directly. 

- **Always backup your database first** before running this script
- The script uses `CASCADE` when dropping constraints, which may affect dependent objects
- Run this on a **test/development** database first to verify it works
- After running, create a new backup to verify the issues are resolved

## After Running the Fix

1. **Optional but recommended**: Run `VACUUM FULL ANALYZE;` to clean up the database
2. **Create a new backup** to verify the issues are resolved
3. **Test the restore** to ensure no more errors occur

## Troubleshooting

If you encounter permission errors:
- Ensure the database user has sufficient privileges (superuser or database owner)
- For system catalog modifications, superuser privileges may be required

If errors persist after running the script:
- Check the verification output at the end of the script
- Some issues may require manual intervention
- Consider running `REINDEX DATABASE ruetech;` after the fix

## Example Output

```
Step 1: Checking for duplicate constraints...
 table_name | contype | conname | duplicate_count 
------------+---------+---------+-----------------
(0 rows)

Step 2: Checking for orphaned constraints...
 oid | conname | table_name | constraint_type 
-----+---------+------------+------------------
(0 rows)

...

Database cleanup completed!
```

## Related Files

- `fix_database_constraints.sql` - The main SQL fix script
- `run_fix_constraints.sh` - Helper script to run the fix easily
- `backup_script.py` - The backup/restore script



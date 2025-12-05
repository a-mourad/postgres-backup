-- ============================================================================
-- Database Cleanup Script for PostgreSQL
-- Fixes duplicate constraints and orphaned constraints
-- ============================================================================
-- 
-- This script fixes:
-- 1. Duplicate constraint entries in pg_constraint system catalog
-- 2. Orphaned constraints referencing non-existent tables
-- 3. Other catalog inconsistencies
--
-- Usage:
--   psql -h host.docker.internal -p 2320 -U odoo -d ruetech -f fix_database_constraints.sql
--
-- WARNING: This script modifies system catalogs. Run on a backup first!
-- ============================================================================

\echo 'Starting database cleanup...'
\echo ''

-- ============================================================================
-- Step 1: Find and report duplicate constraints
-- ============================================================================
\echo 'Step 1: Checking for duplicate constraints...'

SELECT 
    conrelid::regclass AS table_name,
    contype,
    conname,
    COUNT(*) as duplicate_count
FROM pg_constraint
WHERE conrelid != 0  -- Exclude system constraints
GROUP BY conrelid, contype, conname
HAVING COUNT(*) > 1
ORDER BY duplicate_count DESC, table_name;

\echo ''

-- ============================================================================
-- Step 2: Find and report orphaned constraints (constraints on non-existent tables)
-- ============================================================================
\echo 'Step 2: Checking for orphaned constraints...'

SELECT 
    c.oid,
    c.conname,
    c.conrelid::regclass AS table_name,
    c.contype,
    CASE c.contype
        WHEN 'p' THEN 'PRIMARY KEY'
        WHEN 'u' THEN 'UNIQUE'
        WHEN 'f' THEN 'FOREIGN KEY'
        WHEN 'c' THEN 'CHECK'
        WHEN 't' THEN 'TRIGGER'
        WHEN 'x' THEN 'EXCLUDE'
    END AS constraint_type
FROM pg_constraint c
LEFT JOIN pg_class cl ON c.conrelid = cl.oid
WHERE c.conrelid != 0  -- Exclude system constraints
  AND cl.oid IS NULL    -- Table doesn't exist
ORDER BY c.conname;

\echo ''

-- ============================================================================
-- Step 3: Find constraints referencing non-existent tables (for foreign keys)
-- ============================================================================
\echo 'Step 3: Checking for foreign keys referencing non-existent tables...'

SELECT 
    c.oid,
    c.conname,
    c.conrelid::regclass AS source_table,
    confrelid::regclass AS referenced_table,
    'FOREIGN KEY' AS constraint_type
FROM pg_constraint c
LEFT JOIN pg_class cl ON c.confrelid = cl.oid
WHERE c.contype = 'f'  -- Foreign key constraints
  AND c.confrelid != 0  -- Exclude system constraints
  AND cl.oid IS NULL    -- Referenced table doesn't exist
ORDER BY c.conname;

\echo ''

-- ============================================================================
-- Step 4: Remove orphaned constraints (constraints on non-existent tables)
-- ============================================================================
\echo 'Step 4: Removing orphaned constraints...'

DO $$
DECLARE
    constraint_rec RECORD;
    removed_count INTEGER := 0;
BEGIN
    -- Remove constraints on non-existent tables
    FOR constraint_rec IN 
        SELECT c.oid, c.conname, c.conrelid::regclass::text AS table_name
        FROM pg_constraint c
        LEFT JOIN pg_class cl ON c.conrelid = cl.oid
        WHERE c.conrelid != 0
          AND cl.oid IS NULL
    LOOP
        BEGIN
            EXECUTE format('ALTER TABLE %I DROP CONSTRAINT IF EXISTS %I CASCADE', 
                          constraint_rec.table_name, constraint_rec.conname);
            removed_count := removed_count + 1;
            RAISE NOTICE 'Removed orphaned constraint: % on table %', 
                        constraint_rec.conname, constraint_rec.table_name;
        EXCEPTION WHEN OTHERS THEN
            -- If direct DROP fails, try deleting from catalog
            DELETE FROM pg_constraint WHERE oid = constraint_rec.oid;
            removed_count := removed_count + 1;
            RAISE NOTICE 'Removed orphaned constraint from catalog: % (oid: %)', 
                        constraint_rec.conname, constraint_rec.oid;
        END;
    END LOOP;
    
    RAISE NOTICE 'Removed % orphaned constraint(s)', removed_count;
END $$;

\echo ''

-- ============================================================================
-- Step 5: Remove foreign keys referencing non-existent tables
-- ============================================================================
\echo 'Step 5: Removing foreign keys referencing non-existent tables...'

DO $$
DECLARE
    fk_rec RECORD;
    removed_count INTEGER := 0;
BEGIN
    FOR fk_rec IN 
        SELECT 
            c.oid,
            c.conname,
            c.conrelid::regclass::text AS source_table,
            c.confrelid::regclass::text AS referenced_table
        FROM pg_constraint c
        LEFT JOIN pg_class cl ON c.confrelid = cl.oid
        WHERE c.contype = 'f'
          AND c.confrelid != 0
          AND cl.oid IS NULL
    LOOP
        BEGIN
            EXECUTE format('ALTER TABLE %I DROP CONSTRAINT IF EXISTS %I CASCADE', 
                          fk_rec.source_table, fk_rec.conname);
            removed_count := removed_count + 1;
            RAISE NOTICE 'Removed foreign key: % (references non-existent table %)', 
                        fk_rec.conname, fk_rec.referenced_table;
        EXCEPTION WHEN OTHERS THEN
            DELETE FROM pg_constraint WHERE oid = fk_rec.oid;
            removed_count := removed_count + 1;
            RAISE NOTICE 'Removed foreign key from catalog: % (oid: %)', 
                        fk_rec.conname, fk_rec.oid;
        END;
    END LOOP;
    
    RAISE NOTICE 'Removed % foreign key(s) referencing non-existent tables', removed_count;
END $$;

\echo ''

-- ============================================================================
-- Step 6: Remove duplicate constraints (keep the first one, remove duplicates)
-- ============================================================================
\echo 'Step 6: Removing duplicate constraints...'

DO $$
DECLARE
    dup_rec RECORD;
    removed_count INTEGER := 0;
    constraint_oid INTEGER;
    constraint_oids INTEGER[];
BEGIN
    -- Find duplicate constraints and keep only the first one (lowest oid)
    FOR dup_rec IN 
        SELECT 
            conrelid,
            contype,
            conname,
            array_agg(oid ORDER BY oid) as oids
        FROM pg_constraint
        WHERE conrelid != 0
        GROUP BY conrelid, contype, conname
        HAVING COUNT(*) > 1
    LOOP
        -- Keep the first constraint (lowest oid), remove the rest
        constraint_oids := dup_rec.oids[2:array_length(dup_rec.oids, 1)];
        
        FOREACH constraint_oid IN ARRAY constraint_oids
        LOOP
            BEGIN
                -- Try to drop via ALTER TABLE first
                EXECUTE format(
                    'ALTER TABLE %s DROP CONSTRAINT IF EXISTS %I CASCADE',
                    dup_rec.conrelid::regclass::text,
                    dup_rec.conname
                );
            EXCEPTION WHEN OTHERS THEN
                -- If that fails, delete directly from catalog
                NULL;
            END;
            
            -- Delete from catalog (this is the definitive removal)
            DELETE FROM pg_constraint WHERE oid = constraint_oid;
            removed_count := removed_count + 1;
            RAISE NOTICE 'Removed duplicate constraint: % (oid: %) on table %', 
                        dup_rec.conname, constraint_oid, dup_rec.conrelid::regclass;
        END LOOP;
    END LOOP;
    
    RAISE NOTICE 'Removed % duplicate constraint(s)', removed_count;
END $$;

\echo ''

-- ============================================================================
-- Step 7: Clean up indexes associated with removed constraints
-- ============================================================================
\echo 'Step 7: Cleaning up orphaned indexes...'

DO $$
DECLARE
    idx_rec RECORD;
    removed_count INTEGER := 0;
BEGIN
    -- Find indexes that are not associated with any constraint or table
    FOR idx_rec IN 
        SELECT i.oid, i.indexrelid::regclass::text AS index_name, i.indrelid::regclass::text AS table_name
        FROM pg_index i
        LEFT JOIN pg_class cl ON i.indrelid = cl.oid
        WHERE i.indrelid != 0
          AND cl.oid IS NULL  -- Table doesn't exist
    LOOP
        BEGIN
            EXECUTE format('DROP INDEX IF EXISTS %I CASCADE', idx_rec.index_name);
            removed_count := removed_count + 1;
            RAISE NOTICE 'Removed orphaned index: %', idx_rec.index_name;
        EXCEPTION WHEN OTHERS THEN
            -- If DROP fails, delete from catalog
            DELETE FROM pg_index WHERE oid = idx_rec.oid;
            removed_count := removed_count + 1;
            RAISE NOTICE 'Removed orphaned index from catalog: % (oid: %)', 
                        idx_rec.index_name, idx_rec.oid;
        END;
    END LOOP;
    
    RAISE NOTICE 'Removed % orphaned index(es)', removed_count;
END $$;

\echo ''

-- ============================================================================
-- Step 8: Verify cleanup results
-- ============================================================================
\echo 'Step 8: Verifying cleanup results...'

\echo ''
\echo 'Remaining duplicate constraints:'
SELECT 
    conrelid::regclass AS table_name,
    contype,
    conname,
    COUNT(*) as count
FROM pg_constraint
WHERE conrelid != 0
GROUP BY conrelid, contype, conname
HAVING COUNT(*) > 1
ORDER BY count DESC, table_name;

\echo ''
\echo 'Remaining orphaned constraints:'
SELECT 
    c.oid,
    c.conname,
    c.conrelid::regclass AS table_name
FROM pg_constraint c
LEFT JOIN pg_class cl ON c.conrelid = cl.oid
WHERE c.conrelid != 0
  AND cl.oid IS NULL
ORDER BY c.conname;

\echo ''
\echo 'Database cleanup completed!'
\echo ''
\echo 'Next steps:'
\echo '1. Run VACUUM FULL ANALYZE to clean up the database'
\echo '2. Create a new backup to verify the issues are resolved'
\echo ''


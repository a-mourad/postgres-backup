#!/usr/bin/env python3
"""
Clean PostgreSQL dump file by removing problematic statements.

This script removes:
1. Direct INSERTs into pg_constraint system catalog (causes duplicate key errors)
2. Constraint definitions referencing non-existent tables
3. Other problematic system catalog modifications
"""

import sys
import re
import argparse
from pathlib import Path
from typing import Set, List, Tuple


def extract_table_name_from_create(line: str) -> str:
    """Extract table name from CREATE TABLE statement."""
    # Match: CREATE TABLE [IF NOT EXISTS] schema.table_name
    match = re.search(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:(\w+)\.)?(\w+)', line, re.IGNORECASE)
    if match:
        schema = match.group(1) or 'public'
        table = match.group(2)
        return f"{schema}.{table}"
    return None


def extract_table_name_from_alter(line: str) -> str:
    """Extract table name from ALTER TABLE statement."""
    # Match: ALTER TABLE [ONLY] schema.table_name
    match = re.search(r'ALTER\s+TABLE\s+(?:ONLY\s+)?(?:(\w+)\.)?(\w+)', line, re.IGNORECASE)
    if match:
        schema = match.group(1) or 'public'
        table = match.group(2)
        return f"{schema}.{table}"
    return None


def extract_table_name_from_constraint(line: str) -> str:
    """Extract table name from constraint definition."""
    # Look for ON TABLE schema.table_name or similar patterns
    match = re.search(r'(?:ON\s+TABLE|TABLE)\s+(?:(\w+)\.)?(\w+)', line, re.IGNORECASE)
    if match:
        schema = match.group(1) or 'public'
        table = match.group(2)
        return f"{schema}.{table}"
    return None


def clean_dump_file(input_file: str, output_file: str, verbose: bool = False) -> Tuple[int, int, int]:
    """
    Clean a PostgreSQL dump file.
    
    Returns: (lines_removed, constraints_removed, tables_tracked)
    """
    input_path = Path(input_file)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")
    
    output_path = Path(output_file)
    
    # Track which tables exist in the dump
    existing_tables: Set[str] = set()
    lines_removed = 0
    constraints_removed = 0
    
    # Patterns to match problematic lines
    pg_constraint_insert_pattern = re.compile(
        r'INSERT\s+INTO\s+pg_constraint',
        re.IGNORECASE
    )
    
    pg_constraint_copy_pattern = re.compile(
        r'\\copy\s+pg_constraint',
        re.IGNORECASE
    )
    
    # Track current multi-line statement
    current_statement_lines: List[str] = []
    in_constraint_statement = False
    constraint_table = None
    
    print(f"Reading dump file: {input_file}")
    print(f"Writing cleaned dump to: {output_file}")
    print("")
    
    with open(input_path, 'r', encoding='utf-8', errors='ignore') as infile, \
         open(output_path, 'w', encoding='utf-8') as outfile:
        
        for line_num, line in enumerate(infile, 1):
            original_line = line
            
            # Track CREATE TABLE statements
            if re.search(r'CREATE\s+TABLE', line, re.IGNORECASE):
                table_name = extract_table_name_from_create(line)
                if table_name:
                    existing_tables.add(table_name)
                    if verbose:
                        print(f"  [Line {line_num}] Found table: {table_name}")
            
            # Check for direct INSERTs into pg_constraint (system catalog)
            if pg_constraint_insert_pattern.search(line):
                lines_removed += 1
                constraints_removed += 1
                if verbose:
                    print(f"  [Line {line_num}] Removed: INSERT INTO pg_constraint")
                continue
            
            # Check for COPY commands for pg_constraint
            if pg_constraint_copy_pattern.search(line):
                lines_removed += 1
                constraints_removed += 1
                if verbose:
                    print(f"  [Line {line_num}] Removed: \\copy pg_constraint")
                continue
            
            # Track ALTER TABLE ... ADD CONSTRAINT statements
            # These can span multiple lines, so we need to track them
            if re.search(r'ALTER\s+TABLE.*ADD\s+CONSTRAINT', line, re.IGNORECASE):
                in_constraint_statement = True
                current_statement_lines = [line]
                constraint_table = extract_table_name_from_alter(line)
                continue  # Don't write this line yet, wait for complete statement
            elif in_constraint_statement:
                current_statement_lines.append(line)
                # Check if statement ends (semicolon on its own line or at end)
                if line.strip() == ';' or (line.strip().endswith(';') and not line.strip().startswith('--')):
                    # Statement complete, check if table exists
                    full_statement = ''.join(current_statement_lines)
                    
                    # Extract table name if not already extracted
                    if not constraint_table:
                        constraint_table = extract_table_name_from_alter(full_statement)
                    
                    # Check if this constraint references a non-existent table
                    should_remove = False
                    remove_reason = ""
                    
                    # Check if constraint is for a table that doesn't exist
                    if constraint_table and constraint_table not in existing_tables:
                        should_remove = True
                        remove_reason = f"constraint on non-existent table: {constraint_table}"
                    
                    # Also check for foreign key references to non-existent tables
                    fk_matches = re.finditer(r'REFERENCES\s+(?:(\w+)\.)?(\w+)', full_statement, re.IGNORECASE)
                    for fk_match in fk_matches:
                        ref_schema = fk_match.group(1) or 'public'
                        ref_table = fk_match.group(2)
                        ref_table_name = f"{ref_schema}.{ref_table}"
                        
                        if ref_table_name not in existing_tables:
                            should_remove = True
                            remove_reason = f"constraint references non-existent table: {ref_table_name}"
                            break
                    
                    # Check for specific problematic table names mentioned in errors
                    if 'discuss_channel' in full_statement.lower():
                        # Check if discuss_channel table exists
                        if 'public.discuss_channel' not in existing_tables and 'discuss_channel' not in existing_tables:
                            should_remove = True
                            remove_reason = "constraint references discuss_channel (table does not exist)"
                    
                    if should_remove:
                        lines_removed += len(current_statement_lines)
                        constraints_removed += 1
                        if verbose:
                            print(f"  [Line {line_num}] Removed {remove_reason}")
                    else:
                        # Statement is valid, write it
                        outfile.writelines(current_statement_lines)
                    
                    in_constraint_statement = False
                    current_statement_lines = []
                    constraint_table = None
                # Continue to next line (don't write individual lines of multi-line constraint)
                continue
            
            # Not in a constraint statement, write the line
            outfile.write(original_line)
    
    return lines_removed, constraints_removed, len(existing_tables)


def main():
    parser = argparse.ArgumentParser(
        description='Clean PostgreSQL dump file by removing problematic statements',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s input.sql output.sql
  %(prog)s input.sql output.sql --verbose
  %(prog)s input.sql --in-place
        """
    )
    
    parser.add_argument('input_file', help='Input SQL dump file')
    parser.add_argument('output_file', nargs='?', help='Output SQL dump file (default: input_file.cleaned.sql)')
    parser.add_argument('--in-place', action='store_true', 
                       help='Modify input file in place (creates backup)')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Show detailed information about removed lines')
    
    args = parser.parse_args()
    
    # Determine output file
    if args.in_place:
        # Create backup and modify in place
        input_path = Path(args.input_file)
        backup_path = input_path.with_suffix(input_path.suffix + '.backup')
        import shutil
        shutil.copy2(input_path, backup_path)
        print(f"Created backup: {backup_path}")
        output_file = args.input_file
    elif args.output_file:
        output_file = args.output_file
    else:
        input_path = Path(args.input_file)
        output_file = str(input_path.with_suffix(input_path.suffix + '.cleaned'))
    
    try:
        lines_removed, constraints_removed, tables_tracked = clean_dump_file(
            args.input_file, 
            output_file, 
            verbose=args.verbose
        )
        
        print("")
        print("=" * 70)
        print("Cleanup Summary")
        print("=" * 70)
        print(f"Tables tracked:           {tables_tracked}")
        print(f"Lines removed:            {lines_removed}")
        print(f"Constraints removed:      {constraints_removed}")
        print(f"Output file:              {output_file}")
        print("=" * 70)
        print("")
        print("The cleaned dump file should now restore without constraint errors.")
        print("")
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()


#!/usr/bin/env python3
"""
CASCO Production Code Verification Script

Run this on your production server to check if updated_at is present in the running code.

Usage:
    python verify_production_code.py
"""

import sys
import os
import importlib.util
import inspect

# Fix Windows console encoding
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except:
        pass

print("=" * 80)
print("CASCO PRODUCTION CODE VERIFICATION")
print("=" * 80)
print()

# Add current directory to path
sys.path.insert(0, os.path.abspath('.'))

# Test 1: Check if app.casco.persistence is importable
print("[1] Importing app.casco.persistence...")
try:
    from app.casco import persistence
    print("    ✅ SUCCESS")
    print(f"    📁 File: {persistence.__file__}")
except Exception as e:
    print(f"    ❌ FAILED: {e}")
    sys.exit(1)

print()

# Test 2: Get source code of fetch_casco_offers_by_inquiry
print("[2] Checking fetch_casco_offers_by_inquiry() source code...")
try:
    func = persistence.fetch_casco_offers_by_inquiry
    source = inspect.getsource(func)
    
    # Count lines
    lines = source.split('\n')
    print(f"    📊 Function has {len(lines)} lines")
    
    # Check for updated_at
    if 'updated_at' in source:
        print("    ❌ FOUND 'updated_at' in source code!")
        print()
        print("    🔍 Lines containing 'updated_at':")
        for i, line in enumerate(lines, 1):
            if 'updated_at' in line.lower():
                print(f"        Line {i}: {line.strip()}")
    else:
        print("    ✅ NO 'updated_at' found")
    
    # Count columns in SELECT
    if 'SELECT' in source:
        select_start = source.index('SELECT')
        from_pos = source.index('FROM', select_start)
        select_block = source[select_start:from_pos]
        columns = [line.strip().rstrip(',') for line in select_block.split('\n') 
                   if line.strip() and line.strip() != 'SELECT']
        columns = [c for c in columns if c]  # Remove empty strings
        print(f"    📊 SELECT statement has {len(columns)} columns")
        print(f"    📋 Columns: {', '.join(columns[:5])}{'...' if len(columns) > 5 else ''}")
        
except Exception as e:
    print(f"    ❌ FAILED: {e}")

print()

# Test 3: Check fetch_casco_offers_by_reg_number
print("[3] Checking fetch_casco_offers_by_reg_number() source code...")
try:
    func = persistence.fetch_casco_offers_by_reg_number
    source = inspect.getsource(func)
    
    if 'updated_at' in source:
        print("    ❌ FOUND 'updated_at' in source code!")
    else:
        print("    ✅ NO 'updated_at' found")
        
except Exception as e:
    print(f"    ❌ FAILED: {e}")

print()

# Test 4: Check app.routes.casco_routes
print("[4] Importing app.routes.casco_routes...")
try:
    from app.routes import casco_routes
    print("    ✅ SUCCESS")
    print(f"    📁 File: {casco_routes.__file__}")
except Exception as e:
    print(f"    ❌ FAILED: {e}")
    sys.exit(1)

print()

# Test 5: Check sync fetch functions
print("[5] Checking _fetch_casco_offers_by_inquiry_sync() source code...")
try:
    func = casco_routes._fetch_casco_offers_by_inquiry_sync
    source = inspect.getsource(func)
    
    if 'updated_at' in source:
        print("    ❌ FOUND 'updated_at' in source code!")
        # Show the exact lines
        lines = source.split('\n')
        for i, line in enumerate(lines, 1):
            if 'updated_at' in line.lower():
                print(f"        Line {i}: {line.strip()}")
    else:
        print("    ✅ NO 'updated_at' found")
        
except Exception as e:
    print(f"    ❌ FAILED: {e}")

print()

print("[6] Checking _fetch_casco_offers_by_reg_number_sync() source code...")
try:
    func = casco_routes._fetch_casco_offers_by_reg_number_sync
    source = inspect.getsource(func)
    
    if 'updated_at' in source:
        print("    ❌ FOUND 'updated_at' in source code!")
        lines = source.split('\n')
        for i, line in enumerate(lines, 1):
            if 'updated_at' in line.lower():
                print(f"        Line {i}: {line.strip()}")
    else:
        print("    ✅ NO 'updated_at' found")
        
except Exception as e:
    print(f"    ❌ FAILED: {e}")

print()
print("=" * 80)
print("VERIFICATION COMPLETE")
print("=" * 80)
print()
print("📊 SUMMARY:")
print()
print("If you see ❌ 'FOUND updated_at' above, your production code is OUTDATED.")
print("If you see all ✅ 'NO updated_at found', your code is correct.")
print()
print("Next steps if code is outdated:")
print("  1. git pull origin main")
print("  2. rm -rf app/__pycache__ app/casco/__pycache__ app/routes/__pycache__")
print("  3. Restart your application server")
print()


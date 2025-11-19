#!/usr/bin/env python3
"""
Verification script for CASCO Job ID implementation.
Checks that all required components are in place.
Run this after making changes to verify implementation integrity.
"""

import os
import re


def check_file_contains(filepath: str, patterns: list[str], description: str) -> bool:
    """Check if file contains all specified patterns."""
    if not os.path.exists(filepath):
        print(f"[FAIL] {description}: File not found: {filepath}")
        return False
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    missing = []
    for pattern in patterns:
        if not re.search(pattern, content, re.MULTILINE):
            missing.append(pattern)
    
    if missing:
        print(f"[FAIL] {description}: Missing patterns in {filepath}:")
        for p in missing:
            print(f"   - {p}")
        return False
    else:
        print(f"[PASS] {description}")
        return True


def main():
    print("=" * 70)
    print("CASCO Job ID Implementation Verification")
    print("=" * 70)
    print()
    
    all_checks = []
    
    # 1. Database Migration
    all_checks.append(check_file_contains(
        "backend/scripts/create_casco_jobs_table.sql",
        [
            r"CREATE TABLE.*casco_jobs",
            r"casco_job_id",
            r"offers_casco_casco_job_id_fkey",
            r"idx_casco_jobs_reg_number",
        ],
        "Database migration script"
    ))
    
    # 2. Persistence Layer
    all_checks.append(check_file_contains(
        "app/casco/persistence.py",
        [
            r"casco_job_id:\s*str",  # Required field in CascoOfferRecord (UUID string)
            r"def create_casco_job",
            r"def fetch_casco_offers_by_job",
            r"INSERT INTO public\.casco_jobs",
            r"WHERE casco_job_id = \$1",
            r"import uuid",  # UUID generation
        ],
        "Persistence layer (CascoOfferRecord + job functions)"
    ))
    
    # 3. Routes Layer
    all_checks.append(check_file_contains(
        "app/routes/casco_routes.py",
        [
            r"def _create_casco_job_sync",
            r"def _fetch_casco_offers_by_job_sync",
            r"casco_job_id = _create_casco_job_sync",
            r'@router\.get\("/job/\{casco_job_id\}/compare"\)',
            r'@router\.get\("/job/\{casco_job_id\}/offers"\)',
            r"deprecated=True",  # Vehicle endpoints deprecated
        ],
        "Routes layer (upload + comparison endpoints)"
    ))
    
    # 4. Share Links
    all_checks.append(check_file_contains(
        "app/main.py",
        [
            r"casco_job_id.*Optional\[str\]",  # ShareCreateBody field (UUID string)
            r'product_line.*"casco"',
            r"if product_line == .casco.:",
            r"_fetch_casco_offers_by_job_sync",
        ],
        "Share links (CASCO support)"
    ))
    
    # 5. Verify NO inquiry_id in upload endpoints
    print()
    print("-" * 70)
    print("Negative checks (should NOT exist):")
    print("-" * 70)
    
    with open("app/routes/casco_routes.py", 'r', encoding='utf-8') as f:
        routes_content = f.read()
    
    # Check that upload endpoints don't have inquiry_id parameter
    upload_single = re.search(
        r'@router\.post\("/upload"\).*?async def upload_casco_offer\((.*?)\):',
        routes_content,
        re.DOTALL
    )
    
    upload_batch = re.search(
        r'@router\.post\("/upload/batch"\).*?async def upload_casco_offers_batch\((.*?)\):',
        routes_content,
        re.DOTALL
    )
    
    inquiry_id_found = False
    
    if upload_single and "inquiry_id" in upload_single.group(1):
        print("[FAIL] inquiry_id parameter found in POST /casco/upload")
        inquiry_id_found = True
    else:
        print("[PASS] inquiry_id removed from POST /casco/upload")
    
    if upload_batch and "inquiry_id" in upload_batch.group(1):
        print("[FAIL] inquiry_id parameter found in POST /casco/upload/batch")
        inquiry_id_found = True
    else:
        print("[PASS] inquiry_id removed from POST /casco/upload/batch")
    
    all_checks.append(not inquiry_id_found)
    
    # 6. Verify job-based endpoints exist
    if r'@router.get("/job/{casco_job_id}/compare")' in routes_content:
        print("[PASS] GET /casco/job/{job_id}/compare endpoint exists")
        all_checks.append(True)
    else:
        print("[FAIL] GET /casco/job/{job_id}/compare endpoint missing")
        all_checks.append(False)
    
    if r'@router.get("/job/{casco_job_id}/offers")' in routes_content:
        print("[PASS] GET /casco/job/{job_id}/offers endpoint exists")
        all_checks.append(True)
    else:
        print("[FAIL] GET /casco/job/{job_id}/offers endpoint missing")
        all_checks.append(False)
    
    # Summary
    print()
    print("=" * 70)
    if all(all_checks):
        print("[SUCCESS] ALL CHECKS PASSED!")
        print()
        print("Implementation complete. Next steps:")
        print("1. Run database migration: psql $DATABASE_URL < backend/scripts/create_casco_jobs_table.sql")
        print("2. Update frontend to use casco_job_id instead of inquiry_id")
        print("3. Test upload -> compare -> share flow")
        print("4. Verify HEALTH endpoints remain untouched")
    else:
        print(f"[FAILURE] {sum(all_checks)}/{len(all_checks)} checks passed")
        print()
        print("Please review failed checks above and fix before deployment.")
    print("=" * 70)
    
    return 0 if all(all_checks) else 1


if __name__ == "__main__":
    exit(main())


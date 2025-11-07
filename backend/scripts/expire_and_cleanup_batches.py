#!/usr/bin/env python3
"""
Cleanup script for expired offer batches.
Removes files from OpenAI vector stores and deletes local/S3 files.
"""
import os
import sys
import traceback
from pathlib import Path

import psycopg2
import psycopg2.extras
from psycopg.rows import dict_row
from app.services.openai_client import client
from app.services.vectorstores import ensure_offer_vs

DATABASE_URL = os.getenv("DATABASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not DATABASE_URL or not OPENAI_API_KEY:
    print("Error: DATABASE_URL and OPENAI_API_KEY are required", file=sys.stderr)
    sys.exit(1)


def get_db_connection():
    """Get database connection."""
    return psycopg2.connect(DATABASE_URL)


def delete_openai_file(vector_store_id: str, file_id: str) -> bool:
    """Delete file from OpenAI vector store and files API."""
    try:
        # Try to remove from vector store first
        try:
            client.vector_stores.files.delete(vector_store_id, file_id)
            print(f"  Removed from vector store: {file_id}")
        except Exception as e:
            if "not_found" not in str(e).lower():
                print(f"  Warning: vector store delete failed: {e}")
        
        # Then delete the file itself
        try:
            client.files.delete(file_id)
            print(f"  Deleted OpenAI file: {file_id}")
            return True
        except Exception as e:
            if "not_found" not in str(e).lower():
                print(f"  Warning: file delete failed: {e}")
            return False
    except Exception as e:
        print(f"  Error deleting OpenAI file {file_id}: {e}")
        return False


def delete_storage_file(storage_path: str) -> bool:
    """Delete file from local or S3 storage."""
    try:
        if storage_path.startswith("s3://"):
            # S3 deletion
            import boto3
            s3_client = boto3.client('s3')
            
            # Parse s3://bucket/key
            path_parts = storage_path[5:].split('/', 1)
            bucket = path_parts[0]
            key = path_parts[1]
            
            s3_client.delete_object(Bucket=bucket, Key=key)
            print(f"  Deleted S3 file: {storage_path}")
        else:
            # Local file deletion
            file_path = Path(storage_path)
            if file_path.exists():
                file_path.unlink()
                print(f"  Deleted local file: {storage_path}")
            else:
                print(f"  Warning: local file not found: {storage_path}")
        
        return True
    except Exception as e:
        print(f"  Error deleting storage file {storage_path}: {e}")
        return False


def cleanup_batch(batch_id: int) -> dict:
    """Clean up a single batch."""
    result = {
        "batch_id": batch_id,
        "files_processed": 0,
        "openai_deleted": 0,
        "storage_deleted": 0,
        "errors": []
    }
    
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Get all files for this batch
            cur.execute(
                """
                SELECT id, filename, storage_path, retrieval_file_id, vector_store_id
                FROM public.offer_files
                WHERE batch_id = %s
                """,
                (batch_id,)
            )
            files = cur.fetchall()
            
            for file_row in files:
                result["files_processed"] += 1
                file_id = file_row["id"]
                storage_path = file_row["storage_path"]
                retrieval_file_id = file_row["retrieval_file_id"]
                vector_store_id = file_row["vector_store_id"]
                
                print(f"  Processing file {file_id}: {file_row['filename']}")
                
                # Delete from OpenAI if we have the IDs
                if retrieval_file_id and vector_store_id:
                    if delete_openai_file(vector_store_id, retrieval_file_id):
                        result["openai_deleted"] += 1
                
                # Delete from storage
                if storage_path:
                    if delete_storage_file(storage_path):
                        result["storage_deleted"] += 1
                
                # Delete database record
                cur.execute("DELETE FROM public.offer_files WHERE id = %s", (file_id,))
            
            # Mark batch as deleted
            cur.execute(
                "UPDATE public.offer_batches SET status = 'deleted' WHERE id = %s",
                (batch_id,)
            )
            
            conn.commit()
    
    return result


def main():
    """Main cleanup function."""
    print("Starting batch cleanup...")
    
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Find expired batches
            cur.execute(
                """
                SELECT id, batch_token, title, expires_at
                FROM public.offer_batches
                WHERE status IN ('active', 'expired')
                AND expires_at < now()
                ORDER BY expires_at
                """
            )
            expired_batches = cur.fetchall()
    
    if not expired_batches:
        print("No expired batches found.")
        return
    
    print(f"Found {len(expired_batches)} expired batches to cleanup:")
    
    total_stats = {
        "batches_processed": 0,
        "files_processed": 0,
        "openai_deleted": 0,
        "storage_deleted": 0,
        "errors": []
    }
    
    for batch in expired_batches:
        batch_id = batch["id"]
        print(f"\nCleaning up batch {batch_id}: {batch['batch_token']} ({batch['title']})")
        print(f"  Expired: {batch['expires_at']}")
        
        try:
            result = cleanup_batch(batch_id)
            total_stats["batches_processed"] += 1
            total_stats["files_processed"] += result["files_processed"]
            total_stats["openai_deleted"] += result["openai_deleted"]
            total_stats["storage_deleted"] += result["storage_deleted"]
            total_stats["errors"].extend(result["errors"])
            
            print(f"  Completed: {result['files_processed']} files, "
                  f"{result['openai_deleted']} OpenAI deletions, "
                  f"{result['storage_deleted']} storage deletions")
        
        except Exception as e:
            error_msg = f"Batch {batch_id} cleanup failed: {e}"
            print(f"  ERROR: {error_msg}")
            traceback.print_exc()
            total_stats["errors"].append(error_msg)
    
    # Print summary
    print(f"\n=== CLEANUP SUMMARY ===")
    print(f"Batches processed: {total_stats['batches_processed']}")
    print(f"Files processed: {total_stats['files_processed']}")
    print(f"OpenAI files deleted: {total_stats['openai_deleted']}")
    print(f"Storage files deleted: {total_stats['storage_deleted']}")
    
    if total_stats["errors"]:
        print(f"Errors encountered: {len(total_stats['errors'])}")
        for error in total_stats["errors"]:
            print(f"  - {error}")
    
    print("Cleanup completed.")


if __name__ == "__main__":
    main()

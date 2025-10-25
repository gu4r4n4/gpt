#!/usr/bin/env python3
"""
CLI script to manually re-embed a file by extracting text, chunking, and storing in offer_chunks.

Usage:
    python backend/scripts/reembed_file.py --file-id 46
    python backend/scripts/reembed_file.py --file-id 46 --chunk-size 1500 --overlap 300
    python backend/scripts/reembed_file.py --batch-id 5  # Re-embed all files in a batch

Environment variables required:
    DATABASE_URL - PostgreSQL connection string
"""

import argparse
import os
import sys
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from pypdf import PdfReader
from typing import List

# Add parent directory to path to import from backend
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract text from PDF file."""
    try:
        reader = PdfReader(pdf_path)
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n\n".join(pages)
    except Exception as e:
        raise Exception(f"Failed to extract text from PDF: {e}")


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> List[dict]:
    """
    Split text into overlapping chunks.
    
    Args:
        text: Text to chunk
        chunk_size: Target size of each chunk in characters
        overlap: Number of characters to overlap between chunks
    
    Returns:
        List of dicts with 'text' and 'metadata' keys
    """
    if not text or not text.strip():
        return []
    
    chunks = []
    start = 0
    chunk_index = 0
    
    while start < len(text):
        # Calculate end position
        end = start + chunk_size
        
        # If this is not the last chunk, try to break at a sentence or paragraph
        if end < len(text):
            # Look for paragraph break first
            paragraph_break = text.rfind("\n\n", start, end)
            if paragraph_break > start + chunk_size // 2:
                end = paragraph_break + 2
            else:
                # Look for sentence break
                sentence_break = max(
                    text.rfind(". ", start, end),
                    text.rfind("! ", start, end),
                    text.rfind("? ", start, end)
                )
                if sentence_break > start + chunk_size // 2:
                    end = sentence_break + 2
        
        chunk_text = text[start:end].strip()
        
        if chunk_text:
            chunks.append({
                "text": chunk_text,
                "metadata": {
                    "chunk_index": chunk_index,
                    "start_pos": start,
                    "end_pos": end,
                    "length": len(chunk_text)
                }
            })
            chunk_index += 1
        
        # Move start position (with overlap)
        start = end - overlap if end < len(text) else len(text)
    
    return chunks


def reembed_file(file_id: int, conn, chunk_size: int = 1000, overlap: int = 200, dry_run: bool = False) -> dict:
    """
    Re-embed a file by extracting text, chunking, and storing in offer_chunks.
    
    Args:
        file_id: ID of the file in offer_files table
        conn: Database connection
        chunk_size: Target chunk size in characters
        overlap: Overlap between chunks in characters
        dry_run: If True, don't actually insert chunks
    
    Returns:
        Dict with status and details
    """
    print(f"[embedding] start file_id={file_id}")
    
    try:
        # Step 1: Load file record
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, filename, storage_path, mime_type, org_id, batch_id
                FROM public.offer_files
                WHERE id = %s
            """, (file_id,))
            file_record = cur.fetchone()
        
        if not file_record:
            raise Exception(f"File ID {file_id} not found in offer_files table")
        
        storage_path = file_record.get("storage_path")
        filename = file_record.get("filename", "unknown.pdf")
        
        print(f"[embedding] file={filename} path={storage_path}")
        
        if not storage_path:
            raise Exception("storage_path is blank in database")
        
        if not os.path.exists(storage_path):
            raise Exception(f"File not found at path: {storage_path}")
        
        # Step 2: Extract text from PDF
        print(f"[embedding] extracting text from {storage_path}")
        text = extract_text_from_pdf(storage_path)
        
        if not text or len(text.strip()) < 10:
            raise Exception("Extracted text is empty or too short")
        
        print(f"[embedding] extracted {len(text)} characters")
        
        # Step 3: Split into chunks
        print(f"[embedding] chunking text (size={chunk_size}, overlap={overlap})")
        chunks = chunk_text(text, chunk_size=chunk_size, overlap=overlap)
        
        if not chunks:
            raise Exception("No chunks created from text")
        
        print(f"[embedding] created {len(chunks)} chunks")
        
        if dry_run:
            print(f"[embedding] DRY RUN - not modifying database")
            print(f"[embedding] Would delete existing chunks and insert {len(chunks)} new chunks")
            return {
                "ok": True,
                "dry_run": True,
                "file_id": file_id,
                "filename": filename,
                "text_length": len(text),
                "chunks_would_create": len(chunks)
            }
        
        # Step 4: Delete existing chunks for this file
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM public.offer_chunks
                WHERE file_id = %s
            """, (file_id,))
            deleted_count = cur.rowcount
            print(f"[embedding] deleted {deleted_count} existing chunks")
        
        # Step 5: Insert new chunks
        inserted_count = 0
        with conn.cursor() as cur:
            for idx, chunk in enumerate(chunks):
                cur.execute("""
                    INSERT INTO public.offer_chunks
                    (file_id, chunk_index, text, metadata)
                    VALUES (%s, %s, %s, %s)
                """, (
                    file_id,
                    idx,
                    chunk["text"],
                    json.dumps(chunk["metadata"])
                ))
                inserted_count += 1
        
        conn.commit()
        print(f"[embedding] inserted {inserted_count} chunks")
        
        # Step 6: Set embeddings_ready = true only if chunks were stored
        embeddings_ready = inserted_count > 0
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE public.offer_files
                SET embeddings_ready = %s
                WHERE id = %s
            """, (embeddings_ready, file_id))
            conn.commit()
        
        print(f"[embedding] done file_id={file_id} chunks={inserted_count} ready={embeddings_ready}")
        
        return {
            "ok": True,
            "file_id": file_id,
            "filename": filename,
            "text_length": len(text),
            "chunks_created": inserted_count,
            "chunks_deleted": deleted_count,
            "embeddings_ready": embeddings_ready
        }
        
    except Exception as e:
        print(f"[embedding] error file_id={file_id}: {e}")
        
        # Set embeddings_ready = false on error
        if not dry_run:
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE public.offer_files
                        SET embeddings_ready = false
                        WHERE id = %s
                    """, (file_id,))
                    conn.commit()
            except:
                pass
        
        raise


def reembed_batch(batch_id: int, conn, chunk_size: int = 1000, overlap: int = 200, dry_run: bool = False) -> dict:
    """Re-embed all files in a batch."""
    print(f"[embedding] batch start batch_id={batch_id}")
    
    # Get all files in the batch
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, filename
            FROM public.offer_files
            WHERE batch_id = %s
            ORDER BY id
        """, (batch_id,))
        files = cur.fetchall()
    
    if not files:
        print(f"[embedding] No files found for batch_id={batch_id}")
        return {"ok": False, "error": "No files found in batch"}
    
    print(f"[embedding] Found {len(files)} files in batch {batch_id}")
    
    results = []
    success_count = 0
    error_count = 0
    
    for file_record in files:
        file_id = file_record["id"]
        filename = file_record["filename"]
        
        try:
            print(f"\n[embedding] Processing {filename} (id={file_id})")
            result = reembed_file(file_id, conn, chunk_size, overlap, dry_run)
            results.append(result)
            success_count += 1
        except Exception as e:
            print(f"[embedding] Failed to process {filename}: {e}")
            results.append({
                "ok": False,
                "file_id": file_id,
                "filename": filename,
                "error": str(e)
            })
            error_count += 1
    
    print(f"\n[embedding] batch done batch_id={batch_id} success={success_count} errors={error_count}")
    
    return {
        "ok": True,
        "batch_id": batch_id,
        "total_files": len(files),
        "success_count": success_count,
        "error_count": error_count,
        "results": results
    }


def main():
    parser = argparse.ArgumentParser(
        description="Re-embed files by extracting text, chunking, and storing in offer_chunks"
    )
    
    # Mutually exclusive: either file-id or batch-id
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--file-id",
        type=int,
        help="ID of the file to re-embed"
    )
    group.add_argument(
        "--batch-id",
        type=int,
        help="ID of the batch to re-embed (all files in batch)"
    )
    
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1000,
        help="Target chunk size in characters (default: 1000)"
    )
    parser.add_argument(
        "--overlap",
        type=int,
        default=200,
        help="Overlap between chunks in characters (default: 200)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't actually modify database, just show what would happen"
    )
    
    args = parser.parse_args()
    
    # Check environment
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL environment variable not set", file=sys.stderr)
        sys.exit(1)
    
    # Connect to database
    try:
        conn = psycopg2.connect(database_url, cursor_factory=RealDictCursor)
        print(f"[embedding] Connected to database")
    except Exception as e:
        print(f"ERROR: Failed to connect to database: {e}", file=sys.stderr)
        sys.exit(1)
    
    try:
        if args.file_id:
            # Re-embed single file
            result = reembed_file(
                args.file_id,
                conn,
                chunk_size=args.chunk_size,
                overlap=args.overlap,
                dry_run=args.dry_run
            )
            
            print("\n" + "="*60)
            print("RESULT:")
            print(json.dumps(result, indent=2))
            print("="*60)
            
            if result.get("ok"):
                sys.exit(0)
            else:
                sys.exit(1)
        
        elif args.batch_id:
            # Re-embed all files in batch
            result = reembed_batch(
                args.batch_id,
                conn,
                chunk_size=args.chunk_size,
                overlap=args.overlap,
                dry_run=args.dry_run
            )
            
            print("\n" + "="*60)
            print("BATCH RESULT:")
            print(f"Total files: {result['total_files']}")
            print(f"Success: {result['success_count']}")
            print(f"Errors: {result['error_count']}")
            print("="*60)
            
            if result.get("error_count", 0) == 0:
                sys.exit(0)
            else:
                sys.exit(1)
    
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    finally:
        conn.close()


if __name__ == "__main__":
    main()


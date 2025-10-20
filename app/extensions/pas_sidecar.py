"""
PAS Sidecar - Vector Store Integration
Runs after successful v1 upload to add vector store functionality without breaking contracts.
"""
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from app.services.vector_batches import ensure_batch_vector_store, add_file_to_batch_vs
from app.services.vectorstores import get_offer_vs


def get_db_connection():
    """Get database connection."""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(db_url)


def read_file_from_storage(storage_path: str) -> bytes | None:
    """
    Read file bytes from storage path.
    Returns None if file not found or error.
    """
    try:
        if not storage_path or not os.path.exists(storage_path):
            print(f"[sidecar] File not found at storage_path: {storage_path}")
            return None
        
        with open(storage_path, "rb") as f:
            return f.read()
    except Exception as e:
        print(f"[sidecar] Error reading file {storage_path}: {e}")
        return None


def run_batch_ingest_sidecar(org_id: int, batch_id: int) -> None:
    """
    Run vector store ingestion sidecar for a batch.
    This runs in background after v1 response is returned.
    All errors are caught and logged - never thrown to client.
    """
    print("[sidecar] start", org_id, batch_id)
    
    try:
        # Step a) Get batch token
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT token FROM public.offer_batches 
                    WHERE id = %s AND org_id = %s
                """, (batch_id, org_id))
                batch_row = cur.fetchone()
                
                if not batch_row:
                    print(f"[sidecar] Batch {batch_id} not found for org {org_id}")
                    return
                
                batch_token = batch_row["token"]
                print("[sidecar] token", batch_token)
        
        # Step b) Ensure vector store
        try:
            vector_store_id = ensure_batch_vector_store(org_id, batch_token)
            print("[sidecar] vs-ready", vector_store_id)
        except Exception as e:
            print(f"[sidecar] Failed to ensure vector store: {e}")
            return
        
        # Step c) Get files in batch that need processing
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, storage_path, filename, retrieval_file_id 
                    FROM public.offer_files 
                    WHERE batch_id = %s AND retrieval_file_id IS NULL
                """, (batch_id,))
                files_to_process = cur.fetchall()
        
        print("[sidecar] files", len(files_to_process))
        
        # Step d) Process each file
        processed_count = 0
        for file_row in files_to_process:
            file_id = file_row["id"]
            storage_path = file_row["storage_path"]
            filename = file_row["filename"]
            
            print(f"[sidecar] Processing file {file_id}: {filename}")
            
            try:
                # Read file bytes from storage
                file_bytes = read_file_from_storage(storage_path)
                if not file_bytes:
                    print(f"[sidecar] Skipping file {file_id} - could not read from storage")
                    continue
                
                # Upload to vector store
                retrieval_file_id = add_file_to_batch_vs(vector_store_id, file_bytes, filename)
                
                # Update database
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE public.offer_files 
                            SET vector_store_id = %s, retrieval_file_id = %s, embeddings_ready = true
                            WHERE id = %s
                        """, (vector_store_id, retrieval_file_id, file_id))
                        conn.commit()
                
                print("[sidecar] file-ok", file_id, retrieval_file_id)
                processed_count += 1
                
            except Exception as e:
                print("[sidecar] file-fail", file_id, e)
                # Continue processing other files
                continue
        
        print("[sidecar] done", batch_id)
        
    except Exception as e:
        print(f"[sidecar] Fatal error for batch {batch_id}: {e}")
        # Never throw - sidecar errors should not affect client


def infer_batch_token_for_docs(document_ids: list[str], org_id: int | None = None) -> str | None:
    """
    Infer batch token from document IDs using robust filename matching.
    For each doc_id, extracts filename and matches against offer_files.
    Returns the most recent batch token or None if not found.
    """
    if not document_ids:
        return None
    
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Build candidate filenames from document IDs
                candidate_names = []
                for doc_id in document_ids:
                    # Extract filename from doc_id format: "prefix::idx::filename"
                    parts = doc_id.split('::')
                    if len(parts) >= 3:
                        candidate_names.append(parts[2])
                
                # Also collect original_filename from offers table
                if candidate_names:
                    placeholders = ','.join(['%s'] * len(candidate_names))
                    cur.execute(f"""
                        SELECT DISTINCT raw_json->>'original_filename' as original_filename
                        FROM public.offers
                        WHERE filename IN ({placeholders}) 
                        AND raw_json->>'original_filename' IS NOT NULL
                    """, candidate_names)
                    
                    for row in cur.fetchall():
                        if row['original_filename']:
                            candidate_names.append(row['original_filename'])
                
                # Remove duplicates and nulls
                candidate_names = list(set([name for name in candidate_names if name]))
                
                if not candidate_names:
                    print(f"[sidecar] No candidate names found for document_ids={document_ids}")
                    return None
                
                # Find batch by matching filenames
                cur.execute("""
                    WITH names AS (
                        SELECT unnest(%s::text[]) AS name
                    ),
                    ofs AS (
                        SELECT of.batch_id
                        FROM public.offer_files of
                        JOIN names n ON n.name = of.filename
                        WHERE (%s IS NULL OR of.org_id = %s)
                        GROUP BY of.batch_id
                        ORDER BY MAX(of.created_at) DESC
                        LIMIT 1
                    )
                    SELECT ob.token
                    FROM public.offer_batches ob
                    JOIN ofs ON ofs.batch_id = ob.id
                """, (candidate_names, org_id, org_id))
                
                row = cur.fetchone()
                if row:
                    token = row["token"]
                    print(f"[sidecar] Inferred batch_token={token} for document_ids={document_ids}")
                    return token
                
                print(f"[sidecar] Could not infer batch_token for document_ids={document_ids}")
                return None
                
    except Exception as e:
        print(f"[sidecar] Error inferring batch_token: {e}")
        return None

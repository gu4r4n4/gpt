import os
import psycopg2
from pathlib import Path
from psycopg2.extensions import connection as _Conn
from app.services.openai_client import client
from app.services.openai_compat import create_vector_store, attach_file_to_vector_store

def ensure_tc_vs(conn: _Conn, org_id: int, product_line: str) -> str:
    """org × product_line vector store (permanent T&C)."""
    product_line = product_line.upper()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.org_vector_stores (
              org_id BIGINT NOT NULL,
              product_line TEXT NOT NULL,
              vector_store_id TEXT NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY (org_id, product_line)
            )
        """)
        conn.commit()
        cur.execute("""SELECT vector_store_id FROM public.org_vector_stores
                       WHERE org_id=%s AND product_line=%s""", (org_id, product_line))
        row = cur.fetchone()
        if row: return row["vector_store_id"]
        vs_id = create_vector_store(client, name=f"org_{org_id}_{product_line}".lower())
        cur.execute("""INSERT INTO public.org_vector_stores(org_id, product_line, vector_store_id)
                       VALUES (%s,%s,%s)""", (org_id, product_line, vs_id))
        conn.commit()
        return vs_id

def ensure_offer_vs(conn: _Conn, org_id: int, batch_token: str) -> str:
    """org × batch_token vector store (offer files)."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.org_batch_vector_stores (
              org_id BIGINT NOT NULL,
              batch_token TEXT NOT NULL,
              vector_store_id TEXT NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY (org_id, batch_token)
            )
        """)
        conn.commit()
        cur.execute("""SELECT vector_store_id FROM public.org_batch_vector_stores
                       WHERE org_id=%s AND batch_token=%s""", (org_id, batch_token))
        row = cur.fetchone()
        if row: return row["vector_store_id"]
        vs_id = create_vector_store(client, name=f"org_{org_id}_offer_{batch_token}".lower())
        cur.execute("""INSERT INTO public.org_batch_vector_stores(org_id, batch_token, vector_store_id)
                       VALUES (%s,%s,%s)""", (org_id, batch_token, vs_id))
        conn.commit()
        return vs_id

def get_tc_vs(conn: _Conn, org_id: int, product_line: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("""SELECT vector_store_id FROM public.org_vector_stores
                       WHERE org_id=%s AND product_line=%s""", (org_id, product_line.upper()))
        r = cur.fetchone()
        return r["vector_store_id"] if r else None

def get_offer_vs(conn: _Conn, org_id: int, batch_token: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("""SELECT vector_store_id FROM public.org_batch_vector_stores
                       WHERE org_id=%s AND batch_token=%s""", (org_id, batch_token))
        r = cur.fetchone()
        return r["vector_store_id"] if r else None

def ensure_tc_vector_store(org_id: int) -> str:
    """
    Ensure insurer T&C vector store exists for org_id.
    Creates vector store if missing and uploads canonical T&C PDFs.
    Returns the vector_store_id.
    """
    # Get database connection
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    
    conn = psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor)
    
    try:
        with conn.cursor() as cur:
            # Lookup existing vector store
            cur.execute("""
                SELECT vector_store_id FROM public.org_vector_stores
                WHERE org_id=%s AND product_line=%s
            """, (org_id, "insurer_tc"))
            row = cur.fetchone()
            
            if row:
                vector_store_id = row["vector_store_id"]
                print(f"[tc] vs-ready {vector_store_id}")
                return vector_store_id
            
            # Create new vector store
            vector_store_id = create_vector_store(client, name=f"org_{org_id}_insurer_tc")
            print(f"[tc] vs-ready {vector_store_id}")
            
            # Upsert mapping
            cur.execute("""
                INSERT INTO public.org_vector_stores (org_id, product_line, vector_store_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (org_id, product_line) DO UPDATE
                SET vector_store_id = EXCLUDED.vector_store_id
            """, (org_id, "insurer_tc", vector_store_id))
            conn.commit()
            
            # Upload canonical T&C PDFs
            storage_root = os.getenv("STORAGE_ROOT", "/tmp")
            tc_dir = Path(storage_root) / "tc"
            
            if tc_dir.exists():
                pdf_files = list(tc_dir.glob("*.pdf"))
                uploaded_count = 0
                
                for pdf_file in pdf_files:
                    try:
                        # Check if file already uploaded
                        cur.execute("""
                            SELECT retrieval_file_id FROM public.offer_files
                            WHERE org_id=%s AND filename=%s AND is_permanent=true
                            AND product_line=%s AND insurer_code=%s
                        """, (org_id, pdf_file.name, "insurer_tc", "canonical"))
                        
                        if cur.fetchone():
                            print(f"[tc] file-ok {pdf_file.name} (already uploaded)")
                            uploaded_count += 1
                            continue
                        
                        # Upload to OpenAI
                        with open(pdf_file, "rb") as f:
                            file_obj = client.files.create(
                                file=(pdf_file.name, f, "application/pdf"),
                                purpose="assistants"
                            )
                        
                        # Attach to vector store
                        attach_file_to_vector_store(client, vector_store_id, file_obj.id)
                        
                        # Store retrieval file id in database
                        cur.execute("""
                            INSERT INTO public.offer_files
                            (org_id, filename, mime_type, size_bytes, storage_path, 
                             vector_store_id, retrieval_file_id, is_permanent,
                             product_line, insurer_code)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (org_id, pdf_file.name, "application/pdf", pdf_file.stat().st_size,
                              str(pdf_file), vector_store_id, file_obj.id, True,
                              "insurer_tc", "canonical"))
                        
                        print(f"[tc] file-ok {pdf_file.name} {file_obj.id}")
                        uploaded_count += 1
                        
                    except Exception as e:
                        print(f"[tc] file-fail {pdf_file.name}: {e}")
                        continue
                
                conn.commit()
                print(f"[tc] done uploaded={uploaded_count} total={len(pdf_files)}")
            else:
                print(f"[tc] done uploaded=0 (no tc directory at {tc_dir})")
            
            return vector_store_id
            
    finally:
        conn.close()

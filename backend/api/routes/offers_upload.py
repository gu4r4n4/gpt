from fastapi import APIRouter, UploadFile, File, HTTPException, Query
from typing import Dict, Any
import os, traceback
from psycopg2.extras import RealDictCursor
from backend.api.routes.util import get_db_connection, safe_filename

try:
    from backend.api.routes.qa import _reembed_file
except Exception:
    from backend.scripts.reembed_file import reembed_file as _reembed_file

router = APIRouter(prefix="/api/offers", tags=["offers"])

def _storage_root() -> str:
    return os.getenv("STORAGE_ROOT", "/var/app/uploads")

@router.post("/upload")
async def upload_and_chunk(
    pdf: UploadFile = File(...),
    org_id: int = Query(..., description="Existing org id"),
    batch_token: str = Query(..., description="Existing batch token to attach this file to")
) -> Dict[str, Any]:
    if not (pdf.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="PDF required")

    # 1) Persist file to disk under provided batch token
    safe = safe_filename(pdf.filename or "uploaded.pdf")
    batch_dir = os.path.join(_storage_root(), "offers", batch_token)
    os.makedirs(batch_dir, exist_ok=True)
    path = os.path.join(batch_dir, safe)
    data = await pdf.read()
    with open(path, "wb") as wf:
        wf.write(data)
    print(f"[offers_upload] saved: {path}")

    conn = get_db_connection()
    file_id = None
    try:
        # 2) Resolve batch_id (no creation here; we only attach)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id FROM public.offer_batches
                WHERE token = %s AND org_id = %s
            """, (batch_token, org_id))
            b = cur.fetchone()
            if not b:
                raise HTTPException(status_code=404, detail=f"Batch not found (org_id={org_id}, batch_token={batch_token})")
            batch_id = b["id"]
            print(f"[offers_upload] resolved batch_id={batch_id} for token={batch_token}")

        # 3) Insert offer_files linked to that batch/org
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO public.offer_files
                (filename, mime_type, size_bytes, storage_path, is_permanent, org_id, batch_id)
                VALUES (%s, %s, %s, %s, false, %s, %s)
                RETURNING id
            """, (safe, pdf.content_type or "application/pdf", len(data), path, org_id, batch_id))
            r = cur.fetchone()
            if not r:
                raise HTTPException(status_code=500, detail="Failed to insert offer_files row")
            file_id = r["id"]
            conn.commit()
        print(f"[offers_upload] offer_files.id={file_id}")

        # 4) Try to (re)embed; if it fails, return 200 with a warning so you see the reason
        reembed_warning = None
        try:
            print(f"[offers_upload] reembed start file_id={file_id}")
            _ = _reembed_file(file_id, conn)
            print(f"[offers_upload] reembed done file_id={file_id}")
        except HTTPException as e:
            reembed_warning = f"_reembed_file HTTPException: {e.detail}"
            print(f"[offers_upload] WARN: {reembed_warning}")
        except Exception as e:
            reembed_warning = f"_reembed_file error: {e}"
            print("[offers_upload] REEMBED TRACEBACK:\n" + traceback.format_exc())

        # 5) Report chunk count (even if reembed failed we still respond with details)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) AS chunks FROM public.offer_chunks WHERE file_id=%s", (file_id,))
            c = cur.fetchone() or {}
            chunks_created = int(c.get("chunks", 0))
            conn.commit()

        resp: Dict[str, Any] = {
            "ok": True,
            "file_id": file_id,
            "org_id": org_id,
            "batch_token": batch_token,
            "storage_path": path,
            "chunks": chunks_created,
            "text_length": None,
        }
        if reembed_warning:
            # Surface the exact cause so you don't need to tail logs
            resp["reembed_warning"] = reembed_warning
        print(f"[offers_upload] ok file_id={file_id} chunks={chunks_created}")
        return resp

    except HTTPException:
        raise
    except Exception as e:
        print("[offers_upload] ERROR TRACEBACK:\n" + traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")
    finally:
        conn.close()

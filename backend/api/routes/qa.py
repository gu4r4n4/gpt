from fastapi import APIRouter, Depends, HTTPException, Query, Header
from pydantic import BaseModel, Field
from typing import List, Optional
import os, json, psycopg2
from psycopg2.extras import RealDictCursor
from openai import OpenAI
from datetime import datetime
from app.services.vectorstores import get_tc_vs, get_offer_vs

# Compat: pydantic v1 uses @validator, v2 uses @field_validator
try:
    from pydantic import field_validator as _validator
except ImportError:  # pydantic v1
    from pydantic import validator as _validator  # type: ignore

router = APIRouter(prefix="/api/qa", tags=["qa"])
client = OpenAI()

def get_db():
    conn = psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)
    try: yield conn
    finally: conn.close()

class RankedInsurer(BaseModel):
    insurer_code: str = Field(..., description="e.g. BALTA, BTA")
    score: float = Field(..., ge=0, le=1)
    reason: str
    sources: List[str]  # validate length via validator below

    @_validator("sources")
    def _min_one_source(cls, v):
        if not v or len(v) < 1:
            raise ValueError("at least one source is required")
        return v


class Top3Response(BaseModel):
    product_line: str
    top3: List[RankedInsurer]
    notes: Optional[str] = None

    @_validator("product_line")
    def _upcase_pl(cls, v):
        return (v or "").upper()

    @_validator("top3")
    def _exact_three(cls, v):
        if not isinstance(v, list) or len(v) != 3:
            raise ValueError("top3 must contain exactly 3 items")
        return v

class AskRequest(BaseModel):
    org_id: int
    batch_token: str
    product_line: str  # validate with decorator (letters only)
    asked_by_user_id: int
    question: str
    debug: Optional[int] = 0

    @_validator("product_line")
    def _pl_letters_only(cls, v: str) -> str:
        if not v or not v.isalpha():
            raise ValueError("product_line must contain only letters (e.g., HEALTH)")
        return v.upper()

SYSTEM_INSTRUCTIONS = """You are an insurance underwriting analyst.
Return STRICT JSON only, no prose. Output format:
{
  "product_line": "<UPPER>",
  "top3": [
    {"insurer_code":"<CODE>","score":0.00,"reason":"...","sources":["<filename or retrieval_id>", "..."]},
    {"insurer_code":"<CODE>","score":0.00,"reason":"...","sources":["..."]},
    {"insurer_code":"<CODE>","score":0.00,"reason":"...","sources":["..."]}
  ],
  "notes":"optional"
}
Scoring must be 0..1. Use the attached documents (offer batch + T&C). Cite 2–5 sources per item (filenames or file ids).
If uncertain, still output valid JSON with your best effort and mention uncertainty in notes."""

@router.post("/ask")
def ask(req: AskRequest, conn = Depends(get_db)):
    tc_vs = get_tc_vs(conn, req.org_id, req.product_line)
    offer_vs = get_offer_vs(conn, req.org_id, req.batch_token)
    if not tc_vs:
        raise HTTPException(status_code=404, detail=f"No T&C vector store for {req.product_line}")
    if not offer_vs:
        raise HTTPException(status_code=404, detail=f"No offer vector store for batch_token={req.batch_token}")

    # 1) create thread with user message
    thread = client.beta.threads.create(messages=[{"role":"system","content":SYSTEM_INSTRUCTIONS},
                                                  {"role":"user","content":req.question}])

    # 2) run with both vector stores
    run = client.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=os.getenv("ASSISTANT_ID_TOP3"),
        tool_resources={"file_search": {"vector_store_ids": [tc_vs, offer_vs]}}
    )

    # 3) poll until completed/failed
    while True:
        r = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
        if r.status in ("completed","failed","cancelled","expired","requires_action"): break

    if r.status != "completed":
        raise HTTPException(status_code=502, detail=f"Run failed: {r.status}")

    # 4) read last message, parse JSON
    msgs = client.beta.threads.messages.list(thread_id=thread.id, order="desc", limit=1)
    content = ""
    for part in msgs.data[0].content:
        if part.type == "text": content += part.text.value
    content = content.strip()

    # be strict: JSON only
    try:
        parsed = json.loads(content)
        top3 = Top3Response(**parsed)  # validation
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Model did not return valid Top-3 JSON: {e}")

    # 5) log
    with conn.cursor() as cur:
        cur.execute("""
          INSERT INTO public.offer_qa_logs (batch_id, org_id, asked_by_user_id, question, answer_summary, sources)
          SELECT b.id, %s, %s, %s, %s, %s
          FROM public.offer_batches b
          WHERE b.token=%s AND b.org_id=%s
          RETURNING id
        """, (req.org_id, req.asked_by_user_id, req.question,
              json.dumps({"product_line": top3.product_line, "top3": [i.dict() for i in top3.top3]}),
              json.dumps({"source_files": [s for i in top3.top3 for s in i.sources]}),
              req.batch_token, req.org_id))
        row = cur.fetchone()
        conn.commit()
        log_id = row["id"] if row else None

    resp = top3.dict()
    if req.debug:
        resp["_debug"] = {
            "thread_id": thread.id,
            "run_id": run.id,
            "tc_vector_store": tc_vs,
            "offer_vector_store": offer_vs,
        }
        resp["_log_id"] = log_id
    return resp

class LogsQuery(BaseModel):
    org_id: int
    batch_token: Optional[str] = None
    limit: int = 25
    offset: int = 0

@router.get("/logs")
def logs(org_id: int, batch_token: Optional[str] = None, limit: int = 25, offset: int = 0, conn = Depends(get_db)):
    cond = ["org_id=%s"]
    params = [org_id]
    if batch_token:
        cond.append("batch_id = (SELECT id FROM public.offer_batches WHERE token=%s AND org_id=%s)")
        params.extend([batch_token, org_id])
    where = " AND ".join(cond)
    with conn.cursor() as cur:
        cur.execute(f"""
          SELECT id, batch_id, org_id, asked_by_user_id, question, answer_summary, sources, created_at
          FROM public.offer_qa_logs
          WHERE {where}
          ORDER BY created_at DESC
          LIMIT %s OFFSET %s
        """, (*params, limit, offset))
        return {"items": cur.fetchall(), "next_offset": offset + limit}


# New scoped Q&A endpoint
class QASource(BaseModel):
    filename: str
    retrieval_file_id: str
    score: float

class QAAskRequest(BaseModel):
    question: str
    share_token: str
    insurer_only: Optional[str] = None
    lang: Optional[str] = None

class QAAskResponse(BaseModel):
    answer: str
    sources: List[QASource]

@router.post("/ask-share", response_model=QAAskResponse)
def ask_share_qa(req: QAAskRequest, conn = Depends(get_db)):
    """Ask questions scoped to a share's batch and insurer T&C KB."""
    import time
    start_time = time.time()
    
    # Validation
    if not req.question or not req.share_token:
        raise HTTPException(status_code=400, detail="Missing question or share_token")
    
    # Load share record
    try:
        # Import the helper from main app
        import sys
        sys.path.append('/app')
        from app.main import _load_share_record
        from app.extensions.pas_sidecar import infer_batch_token_for_docs
        
        share_record = _load_share_record(req.share_token)
        if not share_record:
            raise HTTPException(status_code=404, detail="Share not found")
        
        payload = share_record.get("payload", {})
        batch_token = payload.get("batch_token")
        
        # Get org_id from share or use default
        org_id = share_record.get("org_id")
        if not org_id:
            try:
                org_id = int(os.getenv("DEFAULT_ORG_ID", "0"))
                if org_id <= 0:
                    org_id = None
            except:
                org_id = None
        
        if not org_id:
            raise HTTPException(status_code=404, detail="Organization not found")
        
        # Try batch_token from share → if missing, run inference
        if not batch_token:
            document_ids = payload.get("document_ids", [])
            if document_ids:
                batch_token = infer_batch_token_for_docs(document_ids, org_id)
        
        print(f"[qa] start share={req.share_token} org={org_id} batch={batch_token or '-'}")
        
        # Build vector_store_ids list
        vector_store_ids = []
        
        # Include batch store if found
        batch_vs_id = None
        if batch_token:
            batch_vs_id = get_offer_vs(conn, org_id, batch_token)
            if batch_vs_id:
                vector_store_ids.append(batch_vs_id)
        
        # Include T&C store if exists
        tc_vs_id = get_tc_vs(conn, org_id, "insurer_tc")
        if tc_vs_id:
            vector_store_ids.append(tc_vs_id)
        
        print(f"[qa] stores batch={batch_vs_id or '-'} tnc={tc_vs_id or '-'}")
        
        # Assert preconditions
        if not vector_store_ids:
            raise HTTPException(status_code=404, detail="No vector stores available (no batch for this share and insurer T&C store not seeded)")
        
        if not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured")
        
        assistant_id = os.getenv("ASSISTANT_ID_QA")
        if not assistant_id:
            raise HTTPException(status_code=500, detail="ASSISTANT_ID_QA not configured")
        
        # Wrap OpenAI calls in try/except
        try:
            # Create thread and run
            thread = client.beta.threads.create(messages=[{
                "role": "user",
                "content": req.question
            }])
            
            # --- begin compatibility wrapper ---
            use_tool_resources = True
            try:
                # Newer SDK path (Assistants v2): pass tool_resources at run-time
                run = client.beta.threads.runs.create(
                    thread_id=thread.id,
                    assistant_id=assistant_id,
                    tool_resources={"file_search": {"vector_store_ids": vector_store_ids}},
                )
                print(f"[qa] run-create using tool_resources ok")
            except TypeError as e:
                # Older SDK fallback: bind vector stores on an assistant and run without tool_resources
                print(f"[qa] run-create TypeError; falling back to assistant-level tool_resources: {e}")
                use_tool_resources = False

            if not use_tool_resources:
                # Create a short-lived, per-request assistant so we don't mutate the global one
                tmp_asst = client.beta.assistants.create(
                    name="Offer QA (tmp)",
                    model=os.getenv("ASSISTANT_MODEL", "gpt-4.1-mini"),
                    tools=[{"type": "file_search"}],
                    tool_resources={"file_search": {"vector_store_ids": vector_store_ids}},
                    instructions=os.getenv(
                        "ASSISTANT_QA_INSTRUCTIONS",
                        "You are a broker assistant. Answer only from the provided files (uploaded offers for the share and the organization T&C store). When unsure, say so. Be concise."
                    ),
                )
                try:
                    run = client.beta.threads.runs.create(
                        thread_id=thread.id,
                        assistant_id=tmp_asst.id,
                    )
                    print(f"[qa] run-create with tmp assistant ok id={tmp_asst.id}")
                finally:
                    try:
                        # best-effort cleanup; if it fails, it's still harmless
                        client.beta.assistants.delete(tmp_asst.id)
                        print(f"[qa] tmp assistant deleted id={tmp_asst.id}")
                    except Exception as del_err:
                        print(f"[qa] tmp assistant delete failed: {del_err}")
            # --- end compatibility wrapper ---
            
            # Poll for completion
            max_wait = 60  # 60 second timeout
            wait_time = 0
            while wait_time < max_wait:
                r = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
                if r.status in ("completed", "failed", "cancelled", "expired"):
                    break
                time.sleep(1)
                wait_time += 1
            
            if r.status != "completed":
                raise HTTPException(status_code=502, detail=f"Query failed: {r.status}")
            
            # Get response
            messages = client.beta.threads.messages.list(thread_id=thread.id, order="desc", limit=1)
            answer = ""
            sources = []
            
            for message in messages.data:
                if message.role == "assistant":
                    for content in message.content:
                        if content.type == "text":
                            answer = content.text.value
                            
                            # Extract citations from annotations
                            for annotation in content.text.annotations:
                                if hasattr(annotation, 'file_id') and annotation.file_id:
                                    # Map file_id to filename and retrieval_file_id
                                    sources.append(QASource(
                                        filename=f"file_{annotation.file_id[:8]}",
                                        retrieval_file_id=annotation.file_id,
                                        score=0.8  # Default score
                                    ))
                    break
            
            # Filter by insurer if specified
            if req.insurer_only and sources:
                # This is a simplified filter - in practice you'd want to check file metadata
                sources = [s for s in sources if req.insurer_only.lower() in s.filename.lower()]
            
            latency_ms = int((time.time() - start_time) * 1000)
            print(f"[qa] done ms={latency_ms}")
            
            return QAAskResponse(answer=answer, sources=sources)
            
        except Exception as e:
            print(f"[qa] oai-error type={type(e).__name__} msg={e}")
            raise HTTPException(status_code=502, detail=f"OpenAI error: {e}")
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"[qa] error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/seed-tc")
def seed_tc(org_id: int = Query(...)):
    """Admin endpoint to seed T&C vector store with canonical PDFs."""
    try:
        # Import the function
        from app.services.vectorstores import ensure_tc_vector_store
        
        # Call the function
        vector_store_id = ensure_tc_vector_store(org_id)
        
        return {
            "ok": True,
            "org_id": org_id,
            "vector_store_id": vector_store_id,
            "message": "T&C vector store seeded successfully"
        }
        
    except Exception as e:
        print(f"[qa] seed-tc error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to seed T&C vector store: {e}")


# Models for chunks-report endpoint
class ChunkData(BaseModel):
    chunk_index: int
    text: str = Field(..., description="First ~200 chars of chunk text")
    metadata: dict = Field(default_factory=dict)
    created_at: Optional[str] = None
    file_id: Optional[int] = None
    filename: Optional[str] = None


class ChunksReportResponse(BaseModel):
    ok: bool = True
    share_token: str
    batch_token: Optional[str] = None
    org_id: int
    total_chunks: int
    chunks: List[ChunkData]


def _validate_share_token(share_token: str, conn) -> dict:
    """
    Validate share_token and return share record with batch_token, org_id, document_ids.
    Raises HTTPException if invalid or expired.
    """
    if not share_token:
        raise HTTPException(status_code=400, detail="share_token is required")
    
    with conn.cursor() as cur:
        cur.execute("""
            SELECT token, org_id, payload, expires_at
            FROM public.share_links
            WHERE token = %s
        """, (share_token,))
        row = cur.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="Share token not found")
    
    # Check expiration
    expires_at = row.get("expires_at")
    if expires_at:
        from datetime import datetime, timezone
        if isinstance(expires_at, str):
            exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        else:
            exp_dt = expires_at
        if exp_dt.tzinfo:
            exp_dt = exp_dt.astimezone(timezone.utc).replace(tzinfo=None)
        if datetime.utcnow() > exp_dt:
            raise HTTPException(status_code=403, detail="Share token expired")
    
    payload = row.get("payload") or {}
    return {
        "token": row["token"],
        "org_id": row["org_id"],
        "batch_token": payload.get("batch_token"),
        "document_ids": payload.get("document_ids", []),
        "payload": payload
    }


def _check_authorization(share_record: dict, user_org_id: Optional[int], user_role: Optional[str]) -> None:
    """
    Check if user is authorized to access this share's chunks.
    Authorized if: admin role OR same org_id as share.
    """
    # Admin role can access anything
    if user_role and user_role.lower() == "admin":
        return
    
    # Same org can access
    if user_org_id and user_org_id == share_record["org_id"]:
        return
    
    raise HTTPException(
        status_code=403, 
        detail="Unauthorized: only admin or same organization can access chunks report"
    )


@router.get("/chunks-report", response_model=ChunksReportResponse)
def get_chunks_report(
    share_token: str = Query(..., description="Share token to identify the batch"),
    limit: int = Query(100, ge=1, le=500, description="Maximum number of chunks to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    x_org_id: Optional[int] = Header(None, alias="X-Org-Id"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role"),
    conn = Depends(get_db)
):
    """
    Get chunks report for documents in a batch identified by share_token.
    
    This endpoint:
    1. Validates the share_token (checks share_links table)
    2. Retrieves batch_token, org_id, document_ids from the share
    3. Queries offer_chunks for matching documents
    4. Returns chunk data with pagination
    5. Protected: only accessible to admin role or same organization
    
    Returns:
    - chunk_index: Index of the chunk within the document
    - text: First ~200 characters of chunk text
    - metadata: Chunk metadata (page numbers, positions, etc.)
    - created_at: When the chunk was created
    - file_id: Reference to the source file
    - filename: Name of the source file
    """
    print(f"[qa] chunks-report start share_token={share_token}")
    
    try:
        # Step 1: Validate share_token and get share details
        share_record = _validate_share_token(share_token, conn)
        org_id = share_record["org_id"]
        batch_token = share_record["batch_token"]
        document_ids = share_record["document_ids"]
        
        print(f"[qa] chunks-report validated share org_id={org_id} batch_token={batch_token}")
        
        # Step 2: Check authorization
        _check_authorization(share_record, x_org_id, x_user_role)
        
        # Step 3: Query chunks from database
        # First, try to get file_ids from offer_files table based on batch
        file_ids = []
        if batch_token:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT of.id, of.filename, of.retrieval_file_id
                    FROM public.offer_files of
                    JOIN public.offer_batches ob ON of.batch_id = ob.id
                    WHERE ob.token = %s AND ob.org_id = %s
                    ORDER BY of.id
                """, (batch_token, org_id))
                file_records = cur.fetchall()
                file_ids = [r["id"] for r in file_records]
        
        # Alternative: query by document_ids if no batch_token
        if not file_ids and document_ids:
            with conn.cursor() as cur:
                # Try to find files by matching filename with document_ids
                cur.execute("""
                    SELECT of.id, of.filename, of.retrieval_file_id
                    FROM public.offer_files of
                    WHERE of.org_id = %s AND of.filename = ANY(%s)
                    ORDER BY of.id
                """, (org_id, document_ids))
                file_records = cur.fetchall()
                file_ids = [r["id"] for r in file_records]
        
        print(f"[qa] chunks-report found {len(file_ids)} files")
        
        # Step 4: Query offer_chunks table
        chunks_data = []
        total_chunks = 0
        
        if file_ids:
            with conn.cursor() as cur:
                # Get total count
                cur.execute("""
                    SELECT COUNT(*) as total
                    FROM public.offer_chunks
                    WHERE file_id = ANY(%s)
                """, (file_ids,))
                count_row = cur.fetchone()
                total_chunks = count_row["total"] if count_row else 0
                
                # Get paginated chunks
                cur.execute("""
                    SELECT 
                        oc.id,
                        oc.file_id,
                        oc.chunk_index,
                        LEFT(oc.text, 200) as text_preview,
                        oc.metadata,
                        oc.created_at,
                        of.filename
                    FROM public.offer_chunks oc
                    JOIN public.offer_files of ON oc.file_id = of.id
                    WHERE oc.file_id = ANY(%s)
                    ORDER BY oc.file_id, oc.chunk_index
                    LIMIT %s OFFSET %s
                """, (file_ids, limit, offset))
                
                rows = cur.fetchall()
                for row in rows:
                    chunks_data.append(ChunkData(
                        chunk_index=row["chunk_index"],
                        text=row["text_preview"] or "",
                        metadata=row["metadata"] or {},
                        created_at=row["created_at"].isoformat() if row["created_at"] else None,
                        file_id=row["file_id"],
                        filename=row["filename"]
                    ))
        
        print(f"[qa] chunks-report done total={total_chunks} returned={len(chunks_data)}")
        
        return ChunksReportResponse(
            ok=True,
            share_token=share_token,
            batch_token=batch_token,
            org_id=org_id,
            total_chunks=total_chunks,
            chunks=chunks_data
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"[qa] chunks-report error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to retrieve chunks report: {str(e)}"
        )
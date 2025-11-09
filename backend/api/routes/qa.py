from fastapi import APIRouter, Depends, HTTPException, Query, Header
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Set
import os, json, psycopg2, time, math
from psycopg2.extras import RealDictCursor
from app.services.openai_client import client
from datetime import datetime
from app.services.vectorstores import get_tc_vs, get_offer_vs
from pypdf import PdfReader

def _cosine(a, b):
    na = math.sqrt(sum(x*x for x in a)) or 1e-9
    nb = math.sqrt(sum(x*x for x in b)) or 1e-9
    return sum(x*y for x, y in zip(a, b)) / (na * nb)

def _embed(texts: list[str]) -> list[list[float]]:
    res = client.embeddings.create(
        model=os.getenv("EMBED_MODEL", "text-embedding-3-small"),
        input=texts
    )
    return [d.embedding for d in res.data]

def _select_offer_chunks_from_db(conn, org_id: int, batch_token: str | None,
                                 document_ids: list[str] | None,
                                 insurer_only: str | None,
                                 top_k: int = 12) -> tuple[str, list[str]]:
    """
    Returns (context_block, source_filenames) from Postgres offer_chunks for this share.
    Ranks chunks by cosine similarity vs. query later (callers will embed the question).
    This function ONLY fetches raw rows; scoring happens elsewhere.
    """
    rows = []
    with conn.cursor() as cur:
        params = []
        where_parts = []
        # by batch
        if batch_token:
            where_parts.append("ob.token = %s")
            params.append(batch_token)
            where_parts.append("ob.org_id = %s")
            params.append(org_id)
            base_sql = """
              SELECT oc.file_id, of.filename, oc.chunk_index, oc.text,
                     of.insurer_code
              FROM public.offer_chunks oc
              JOIN public.offer_files of ON of.id = oc.file_id
              JOIN public.offer_batches ob ON ob.id = of.batch_id
              WHERE {WHERE}
            """
        else:
            # fallback by filename list (rare)
            where_parts.append("of.org_id = %s")
            params.append(org_id)
            if document_ids:
                where_parts.append("of.filename = ANY(%s)")
                params.append(document_ids)
            base_sql = """
              SELECT oc.file_id, of.filename, oc.chunk_index, oc.text,
                     of.insurer_code
              FROM public.offer_chunks oc
              JOIN public.offer_files of ON of.id = oc.file_id
              WHERE {WHERE}
            """

        if insurer_only:
            where_parts.append("(of.insurer_code ILIKE %s OR of.filename ILIKE %s)")
            like = f"%{insurer_only}%"
            params.extend([like, like])

        where_sql = " AND ".join(where_parts) if where_parts else "TRUE"
        cur.execute(base_sql.format(WHERE=where_sql), tuple(params))
        rows = cur.fetchall()

    # Return rows; scoring is done by caller
    return rows

# Compat: pydantic v1 uses @validator, v2 uses @field_validator
try:
    from pydantic import field_validator as _validator
except ImportError:  # pydantic v1
    from pydantic import validator as _validator  # type: ignore

router = APIRouter(prefix="/api/qa", tags=["qa"])

def get_db():
    conn = psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()

# --------------------------------------------------------------------
# Top-3 model (unchanged behavior)
# --------------------------------------------------------------------

class RankedInsurer(BaseModel):
    insurer_code: str = Field(..., description="e.g. BALTA, BTA")
    score: float = Field(..., ge=0, le=1)
    reason: str
    sources: List[str]

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
    product_line: str
    asked_by_user_id: int
    question: str
    debug: Optional[int] = 0

    @_validator("product_line")
    def _pl_letters_only(cls, v: str) -> str:
        if not v or not v.isalpha():
            raise ValueError("product_line must contain only letters (e.g., HEALTH)")
        return v.upper()

def _detect_latvian(text: str) -> bool:
    """Simple heuristic for Latvian text detection."""
    latvian_chars = set('āčēģīķļņōŗšūžĀČĒĢĪĶĻŅŌŖŠŪŽ')
    text_chars = set(text)
    return any(c in latvian_chars for c in text_chars)

def _get_system_instructions(query: str) -> str:
    """Get system instructions with appropriate language based on query."""
    is_latvian = _detect_latvian(query)
    
    if is_latvian:
        return """Tu esi apdrošināšanas risku analītiķis.
Atgriezt TIKAI JSON formātu, bez papildus paskaidrojumiem. Atbildes formāts:
{
  "product_line": "<UPPER>",
  "top3": [
    {"insurer_code":"<KODS>","score":0.00,"reason":"...","sources":["<faila_nosaukums vai retrieval_id>", "..."]},
    {"insurer_code":"<KODS>","score":0.00,"reason":"...","sources":["..."]},
    {"insurer_code":"<KODS>","score":0.00,"reason":"...","sources":["..."]}
  ],
  "notes":"neobligāti"
}
Score jābūt 0..1. Izmanto pievienotos dokumentus (piedāvājumu kopumu + noteikumus). Katrai pozīcijai norādīt 2-5 avotus (failu nosaukumus vai ID).
Ja esi nedrošs, tomēr atgriezt derīgu JSON ar labāko pieņēmumu un pieminēt šaubas notes laukā."""
    
    return """You are an insurance underwriting analyst.
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

    system = _get_system_instructions(req.question)
    thread = client.beta.threads.create(messages=[{"role":"system","content":system},
                                                  {"role":"user","content":req.question}])

    run = client.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=os.getenv("ASSISTANT_ID_TOP3"),
        tool_resources={"file_search": {"vector_store_ids": [tc_vs, offer_vs]}}
    )

    # poll
    while True:
        r = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
        if r.status in ("completed","failed","cancelled","expired","requires_action"):
            break

    if r.status != "completed":
        raise HTTPException(status_code=502, detail=f"Run failed: {r.status}")

    msgs = client.beta.threads.messages.list(thread_id=thread.id, order="desc", limit=1)
    content = ""
    for part in msgs.data[0].content:
        if part.type == "text":
            content += part.text.value
    content = content.strip()

    try:
        parsed = json.loads(content)
        top3 = Top3Response(**parsed)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Model did not return valid Top-3 JSON: {e}")

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

# --------------------------------------------------------------------
# Logs (unchanged)
# --------------------------------------------------------------------

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

# --------------------------------------------------------------------
# Scoped Q&A (/ask-share) with fixed citations + strict instructions
# --------------------------------------------------------------------

class QAAskRequest(BaseModel):
    question: str
    share_token: str
    insurer_only: Optional[str] = None
    lang: Optional[str] = None

class QAAskResponse(BaseModel):
    answer: str
    sources: List[str]


def _format_source_label(filename: Optional[str], retrieval_file_id: Optional[str]) -> str:
    parts = [p.strip() for p in (filename, retrieval_file_id) if p and p.strip()]
    return " · ".join(parts) if parts else "source"


def _normalize_source_strings(raw_sources: List[Any]) -> List[str]:
    labels: List[str] = []
    seen: Set[str] = set()
    for item in raw_sources:
        if isinstance(item, str):
            label = item.strip() or "source"
        elif isinstance(item, dict):
            filename = item.get("filename")
            retrieval_file_id = item.get("retrieval_file_id")
            label = _format_source_label(filename, retrieval_file_id)
        else:
            label = "source"
        if label not in seen:
            seen.add(label)
            labels.append(label)
    return labels

def _count_vs_files(vector_store_id: str) -> int:
    try:
        page = client.vector_stores.files.list(vector_store_id=vector_store_id, limit=100)
        total = 0
        while True:
            total += len(page.data or [])
            if not getattr(page, "has_more", False):
                break
            page = client.vector_stores.files.list(vector_store_id=vector_store_id, limit=100, after=page.last_id)
        return total
    except Exception as e:
        print(f"[qa] vs-count warn for {vector_store_id}: {e}")
        return -1

@router.post("/ask-share", response_model=QAAskResponse)
def ask_share_qa(req: QAAskRequest, conn = Depends(get_db)):
    """Answer using DB chunks for OFFERS and OpenAI VS for T&C."""
    start_time = time.time()

    if not req.question or not req.share_token:
        raise HTTPException(status_code=400, detail="Missing question or share_token")

    try:
        import sys
        sys.path.append('/app')
        from app.main import _load_share_record
        from app.extensions.pas_sidecar import infer_batch_token_for_docs

        share_record = _load_share_record(req.share_token)
        if not share_record:
            raise HTTPException(status_code=404, detail="Share not found")

        payload = share_record.get("payload", {}) or {}
        batch_token = payload.get("batch_token")
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

        if not batch_token:
            document_ids = payload.get("document_ids", []) or []
            if document_ids:
                batch_token = infer_batch_token_for_docs(document_ids, org_id)

        # ---------- OFFERS: DB chunks (source of truth) ----------
        rows = _select_offer_chunks_from_db(
            conn=conn,
            org_id=org_id,
            batch_token=batch_token,
            document_ids=payload.get("document_ids", []),
            insurer_only=req.insurer_only
        )
        if not rows:
            raise HTTPException(status_code=404, detail="No offer chunks available for this share")

        # Rank chunks vs the question
        question = req.question.strip()
        q_emb = _embed([question])[0]
        chunk_texts = [ (r["text"][:2000] or "") for r in rows ]
        chunk_embs = _embed(chunk_texts)
        scored = [ (_cosine(q_emb, e), r) for e, r in zip(chunk_embs, rows) ]
        scored.sort(reverse=True)

        TOP_K = 12
        top = scored[:TOP_K]

        # Build context & sources (filenames only)
        context_parts = []
        sources = []
        seen = set()
        for _, r in top:
            fname = r["filename"] or "source"
            idx = r["chunk_index"]
            context_parts.append(f"\n\n# {fname} (chunk {idx})\n{r['text']}")
            if fname not in seen:
                seen.add(fname)
                sources.append(fname)
        context = "".join(context_parts) if context_parts else "—"

        # ---------- T&C: keep OpenAI vector store ----------
        vector_store_ids = []
        try:
            tc_vs_id = get_tc_vs(conn, org_id, "insurer_tc")
            if tc_vs_id:
                vector_store_ids.append(tc_vs_id)
        except Exception as e:
            print(f"[qa] T&C vector store lookup warn: {e}")

        if not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured")

        assistant_id = os.getenv("ASSISTANT_ID_QA")
        if not assistant_id:
            raise HTTPException(status_code=500, detail="ASSISTANT_ID_QA not configured")

        try:
            # Create thread with the user's message
            thread = client.beta.threads.create(messages=[{
                "role": "user",
                "content": req.question
            }])

            # Enforce behavior at run-time (no uploads ask; always cite; LV/EN output)
            out_lang = "lv" if (req.lang or "").lower().startswith("lv") else "en"
            run_instructions = (
                "You are a broker assistant.\n"
                "Answer ONLY from the provided files (the share’s uploaded offers and the organization T&C store).\n"
                "NEVER ask the user to upload files. If some information is missing, state briefly that some data is unavailable "
                "and still provide the best possible answer. If a table is requested, output the table and use '—' for missing cells.\n"
                "Always include citations to the supporting files using the file search tool so annotations are present.\n"
                f"Write the answer in {'Latvian' if out_lang=='lv' else 'English'}."
            )

            # Try run with tool_resources on run (Assistants v2)
            use_tool_resources = True
            try:
                run = client.beta.threads.runs.create(
                    thread_id=thread.id,
                    assistant_id=assistant_id,
                    tool_resources={"file_search": {"vector_store_ids": vector_store_ids}},
                    instructions=run_instructions,
                )
                print(f"[qa] run-create using tool_resources ok")
            except TypeError as e:
                print(f"[qa] run-create TypeError; falling back to assistant-level tool_resources: {e}")
                use_tool_resources = False

            if not use_tool_resources:
                # Short-lived assistant with the same instructions
                tmp_asst = client.beta.assistants.create(
                    name="Offer QA (tmp)",
                    model=os.getenv("ASSISTANT_MODEL", "gpt-4.1-mini"),
                    tools=[{"type": "file_search"}],
                    tool_resources={"file_search": {"vector_store_ids": vector_store_ids}},
                    instructions=run_instructions,
                )
                try:
                    run = client.beta.threads.runs.create(
                        thread_id=thread.id,
                        assistant_id=tmp_asst.id,
                    )
                    print(f"[qa] run-create with tmp assistant ok id={tmp_asst.id}")
                finally:
                    try:
                        client.beta.assistants.delete(tmp_asst.id)
                        print(f"[qa] tmp assistant deleted id={tmp_asst.id}")
                waited = 0
                while waited < 30:
                    r = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
                    if r.status in ("completed","failed","cancelled","expired"): break
                    time.sleep(1); waited += 1
                if r.status == "completed":
                    msgs = client.beta.threads.messages.list(thread_id=thread.id, order="desc", limit=1)
                    for m in msgs.data:
                        if m.role != "assistant": continue
                        for c in m.content:
                            if c.type != "text": continue
                            for ann in (c.text.annotations or []):
                                fid = None; fname = None
                                t = getattr(ann, "type", "")
                                if t == "file_citation" and getattr(ann, "file_citation", None):
                                    fc = ann.file_citation
                                    fid = getattr(fc, "file_id", None)
                                    fname = getattr(fc, "filename", None) or getattr(fc, "file_name", None)
                                elif t == "file_path" and getattr(ann, "file_path", None):
                                    fp = ann.file_path
                                    fid = getattr(fp, "file_id", None)
                                    fname = getattr(fp, "display_name", None) or getattr(fp, "filename", None)
                                if fname and fname not in extra_labels:
                                    extra_labels.append(fname)            def _label_for_file(file_id: Optional[str], filename_hint: Optional[str] = None) -> str:
                if not file_id:
                    return _format_source_label(filename_hint, None)
                if file_id in file_label_cache:
                    cached = file_label_cache[file_id]
                    if filename_hint and " · " not in cached:
                        # Upgrade cached label with filename hint if we did not include it earlier
                        updated = _format_source_label(filename_hint, file_id)
                        file_label_cache[file_id] = updated
                        return updated
                    return cached
                resolved_name = filename_hint
                if resolved_name is None:
                    try:
                        retrieved = client.files.retrieve(file_id)
                        resolved_name = getattr(retrieved, "filename", None)
                    except Exception as fetch_err:
                        print(f"[qa] file-name lookup warn id={file_id}: {fetch_err}")
                label = _format_source_label(resolved_name, file_id)
                file_label_cache[file_id] = label
                return label

            def _append_label(file_id: Optional[str], filename_hint: Optional[str] = None) -> None:
                label = _label_for_file(file_id, filename_hint)
                if label not in seen_labels:
                    seen_labels.add(label)
                    source_labels.append(label)

            for message in messages.data:
                if message.role == "assistant":
                    for content in message.content:
                        if content.type == "text":
                            answer = content.text.value or ""

                            # Extract citations from annotations (SDK 2.x nesting)
                            for annotation in (content.text.annotations or []):
                                fid: Optional[str] = None
                                filename_hint: Optional[str] = None
                                annotation_type = getattr(annotation, "type", "")
                                if annotation_type == "file_citation" and getattr(annotation, "file_citation", None):
                                    file_citation = annotation.file_citation
                                    fid = getattr(file_citation, "file_id", None)
                                    filename_hint = getattr(file_citation, "filename", None) or getattr(file_citation, "file_name", None)
                                elif annotation_type == "file_path" and getattr(annotation, "file_path", None):
                                    file_path = annotation.file_path
                                    fid = getattr(file_path, "file_id", None)
                                    filename_hint = (
                                        getattr(file_path, "display_name", None)
                                        or getattr(file_path, "filename", None)
                                        or getattr(file_path, "title", None)
                                    )
                                elif getattr(annotation, "file_id", None):
                                    fid = getattr(annotation, "file_id")

                                if fid or filename_hint:
                                    _append_label(fid, filename_hint)
                    break

            # If model forgot to cite, backfill from VS (best-effort, up to 4)
            if not source_labels:
                try:
                    backfill_files: List[Any] = []
                    for vsid in vector_store_ids:
                        page = client.vector_stores.files.list(vector_store_id=vsid, limit=10)
                        backfill_files.extend(page.data or [])
                    for vs_file in backfill_files:
                        fid = getattr(vs_file, "id", None)
                        filename_hint = getattr(vs_file, "filename", None)
                        _append_label(fid, filename_hint)
                        if len(source_labels) >= 4:
                            break
                    if source_labels:
                        print(f"[qa] sources backfilled with {len(source_labels)} vector store files")
                except Exception as e:
                    print(f"[qa] backfill warn: {e}")

        # Merge DB sources + optional VS labels (unique, keep DB first)
        for lbl in extra_labels:
            if lbl not in sources:
                sources.append(lbl)

        # Optional filter
        if req.insurer_only and sources:
            needle = req.insurer_only.lower()
            sources = [label for label in sources if needle in label.lower()]

        latency_ms = int((time.time() - start_time) * 1000)
        print(f"[qa] done hybrid ms={latency_ms} sources={len(sources)}")
        return QAAskResponse(answer=answer, sources=sources)

    except HTTPException:
        raise
    except Exception as e:
        print(f"[qa] error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# --------------------------------------------------------------------
# Seed T&C (unchanged)
# --------------------------------------------------------------------

@router.post("/seed-tc")
def seed_tc(org_id: int = Query(...)):
    """Admin endpoint to seed T&C vector store with canonical PDFs."""
    try:
        from app.services.vectorstores import ensure_tc_vector_store
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

# --------------------------------------------------------------------
# Chunks report (unchanged behavior)
# --------------------------------------------------------------------

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
    if user_role and user_role.lower() == "admin":
        return
    if user_org_id and user_org_id == share_record["org_id"]:
        return
    raise HTTPException(status_code=403, detail="Unauthorized: only admin or same organization can access chunks report")

@router.get("/chunks-report", response_model=ChunksReportResponse)
def get_chunks_report(
    share_token: str = Query(..., description="Share token to identify the batch"),
    limit: int = Query(100, ge=1, le=500, description="Maximum number of chunks to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    x_org_id: Optional[int] = Header(None, alias="X-Org-Id"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role"),
    conn = Depends(get_db)
):
    print(f"[qa] chunks-report start share_token={share_token}")

    try:
        share_record = _validate_share_token(share_token, conn)
        org_id = share_record["org_id"]
        batch_token = share_record["batch_token"]
        document_ids = share_record["document_ids"]

        print(f"[qa] chunks-report validated share org_id={org_id} batch_token={batch_token}")

        _check_authorization(share_record, x_org_id, x_user_role)

        file_ids = []
        file_records: List[Dict[str, Any]] = []
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

        if not file_ids and document_ids:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT of.id, of.filename, of.retrieval_file_id
                    FROM public.offer_files of
                    WHERE of.org_id = %s AND of.filename = ANY(%s)
                    ORDER BY of.id
                """, (org_id, document_ids))
                file_records = cur.fetchall()
                file_ids = [r["id"] for r in file_records]

        print(f"[qa] chunks-report found {len(file_ids)} files")

        chunks_data = []
        total_chunks = 0

        if file_ids:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) as total
                    FROM public.offer_chunks
                    WHERE file_id = ANY(%s)
                """, (file_ids,))
                count_row = cur.fetchone()
                total_chunks = count_row["total"] if count_row else 0

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
        raise HTTPException(status_code=500, detail=f"Failed to retrieve chunks report: {str(e)}")

# --------------------------------------------------------------------
# Visibility audit for a share (unchanged behavior, uses client.vector_stores)
# --------------------------------------------------------------------

class AuditFile(BaseModel):
    file_id: int
    filename: str
    storage_path: Optional[str]
    embeddings_ready: Optional[bool] = None
    retrieval_file_id: Optional[str] = None
    chunk_count: int = 0
    in_vector_stores: List[str] = []

class AuditShareResponse(BaseModel):
    ok: bool = True
    org_id: int
    share_token: str
    batch_token: Optional[str] = None
    vector_store_ids: List[str]
    files: List[AuditFile]

def _list_vs_file_ids(vs_id: str) -> set:
    ids = set()
    try:
        page = client.vector_stores.files.list(vector_store_id=vs_id, limit=100)
        while True:
            for f in page.data:
                ids.add(f.id)
            if not getattr(page, "has_more", False):
                break
            page = client.vector_stores.files.list(vector_store_id=vs_id, limit=100, after=page.last_id)
    except Exception as e:
        print(f"[audit] list vs={vs_id} failed: {e}")
    return ids

@router.get("/audit-share", response_model=AuditShareResponse)
def audit_share(share_token: str = Query(...), conn = Depends(get_db)):
    """
    Audit what the QA can see for a share:
    - Files in DB (offer_files) tied to the share's batch/org
    - Chunk counts in offer_chunks
    - retrieval_file_id presence
    - Actual attachment of those file IDs to the Vector Store(s) used by /ask-share
    """
    share = _validate_share_token(share_token, conn)
    org_id = share["org_id"]
    batch_token = share["batch_token"]

    # Recreate the vector store set as used by /ask-share
    vector_store_ids: List[str] = []
    if batch_token:
        b_vs = get_offer_vs(conn, org_id, batch_token)
        if b_vs:
            vector_store_ids.append(b_vs)
    t_vs = get_tc_vs(conn, org_id, "insurer_tc")
    if t_vs:
        vector_store_ids.append(t_vs)
    if not vector_store_ids:
        raise HTTPException(status_code=404, detail="No vector stores available for this share")

    # Build cache of vector store file ids
    vs_file_sets = {vs_id: _list_vs_file_ids(vs_id) for vs_id in vector_store_ids}

    # Collect files for this batch/org
    files = []
    with conn.cursor() as cur:
        cur.execute("""
            SELECT of.id, of.filename, of.storage_path, of.embeddings_ready, of.retrieval_file_id
            FROM public.offer_files of
            JOIN public.offer_batches ob ON of.batch_id = ob.id
            WHERE ob.token=%s AND ob.org_id=%s
            ORDER BY of.id
        """, (batch_token, org_id))
        frows = cur.fetchall()

    # Chunk counts
    id_list = [r["id"] for r in frows] or [-1]
    chunk_counts = {}
    with conn.cursor() as cur:
        cur.execute("""
            SELECT file_id, COUNT(*) AS cnt
            FROM public.offer_chunks
            WHERE file_id = ANY(%s)
            GROUP BY file_id
        """, (id_list,))
        for r in cur.fetchall():
            chunk_counts[r["file_id"]] = r["cnt"]

    # Assemble audit rows
    for r in frows:
        rfid = r.get("retrieval_file_id")
        attached_to = [vs for vs, s in vs_file_sets.items() if (rfid and rfid in s)]
        files.append(AuditFile(
            file_id=r["id"],
            filename=r["filename"],
            storage_path=r.get("storage_path"),
            embeddings_ready=r.get("embeddings_ready"),
            retrieval_file_id=rfid,
            chunk_count=chunk_counts.get(r["id"], 0),
            in_vector_stores=attached_to
        ))

    return AuditShareResponse(
        ok=True,
        org_id=org_id,
        share_token=share_token,
        batch_token=batch_token,
        vector_store_ids=vector_store_ids,
        files=files
    )

# --------------------------------------------------------------------
# Attach-only (no text extraction)
# --------------------------------------------------------------------

class AttachResult(BaseModel):
    ok: bool = True
    file_id: int
    filename: Optional[str] = None
    retrieval_file_id: str
    vector_store_id: Optional[str] = None
    action: str  # "attached", "already_present", "uploaded_and_attached"

@router.post("/attach-file-to-vs", response_model=AttachResult)
def attach_file_to_vs(
    file_id: int = Query(..., description="ID of the file to attach"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role"),
    conn = Depends(get_db)
):
    if not x_user_role or x_user_role.lower() != "admin":
        raise HTTPException(status_code=403, detail="Unauthorized: only admin users")

    with conn.cursor() as cur:
        cur.execute("""
            SELECT of.id, of.filename, of.storage_path, of.retrieval_file_id, of.org_id, ob.token AS batch_token
            FROM public.offer_files of
            JOIN public.offer_batches ob ON of.batch_id = ob.id
            WHERE of.id = %s
        """, (file_id,))
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"File ID {file_id} not found")

    filename = row.get("filename")
    storage_path = row.get("storage_path")
    retrieval_file_id = row.get("retrieval_file_id")
    org_id = row.get("org_id")
    batch_token = row.get("batch_token")

    if not org_id or not batch_token:
        raise HTTPException(status_code=400, detail="Missing org_id or batch_token for file")

    vs_id = get_offer_vs(conn, org_id, batch_token)
    if not vs_id:
        raise HTTPException(status_code=404, detail="Offer vector store not found for this batch")

    action = "attached"

    if not retrieval_file_id:
        if not storage_path or not os.path.exists(storage_path):
            raise HTTPException(status_code=400, detail="Original PDF is missing on disk and cannot be uploaded")
        try:
            up = client.files.create(file=open(storage_path, "rb"), purpose="assistants")
            retrieval_file_id = up.id
            with conn.cursor() as cur:
                cur.execute("UPDATE public.offer_files SET retrieval_file_id=%s WHERE id=%s",
                            (retrieval_file_id, file_id))
                conn.commit()
            action = "uploaded_and_attached"
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Upload failed: {e}")

    try:
        listing = client.vector_stores.files.list(vector_store_id=vs_id, limit=100)
        present = any(f.id == retrieval_file_id for f in listing.data or [])
        if present:
            return AttachResult(ok=True, file_id=file_id, filename=filename,
                                retrieval_file_id=retrieval_file_id, vector_store_id=vs_id,
                                action="already_present")

        client.vector_stores.files.create(vector_store_id=vs_id, file_id=retrieval_file_id)
    except Exception as e:
        msg = str(e).lower()
        if "already" in msg or "exists" in msg or "conflict" in msg:
            return AttachResult(ok=True, file_id=file_id, filename=filename,
                                retrieval_file_id=retrieval_file_id, vector_store_id=vs_id,
                                action="already_present")
        raise HTTPException(status_code=500, detail=f"Attach failed: {e}")

    return AttachResult(ok=True, file_id=file_id, filename=filename,
                        retrieval_file_id=retrieval_file_id, vector_store_id=vs_id,
                        action=action)

# --------------------------------------------------------------------
# Re-embedding (unchanged, plus attach to VS)
# --------------------------------------------------------------------

def _extract_text_from_pdf(pdf_path: str) -> str:
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

def _chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> List[dict]:
    if not text or not text.strip():
        return []

    chunks = []
    start = 0
    chunk_index = 0

    while start < len(text):
        end = start + chunk_size

        if end < len(text):
            paragraph_break = text.rfind("\n\n", start, end)
            if paragraph_break > start + chunk_size // 2:
                end = paragraph_break + 2
            else:
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

        start = end - overlap if end < len(text) else len(text)

    return chunks

def _reembed_file(file_id: int, conn) -> dict:
    print(f"[embedding] start file_id={file_id}")

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, filename, storage_path, mime_type, org_id, batch_id
                FROM public.offer_files
                WHERE id = %s
            """, (file_id,))
            file_record = cur.fetchone()

        if not file_record:
            raise HTTPException(status_code=404, detail=f"File ID {file_id} not found")

        storage_path = file_record.get("storage_path")
        filename = file_record.get("filename", "unknown.pdf")

        print(f"[embedding] file={filename} path={storage_path}")

        if not storage_path:
            raise Exception("storage_path is blank")

        if not os.path.exists(storage_path):
            raise Exception(f"File not found at path: {storage_path}")

        print(f"[embedding] extracting text from {storage_path}")
        text = _extract_text_from_pdf(storage_path)

        if not text or len(text.strip()) < 10:
            raise Exception("Extracted text is empty or too short")

        print(f"[embedding] extracted {len(text)} characters")

        print(f"[embedding] chunking text")
        chunks = _chunk_text(text, chunk_size=1000, overlap=200)

        if not chunks:
            raise Exception("No chunks created from text")

        print(f"[embedding] created {len(chunks)} chunks")

        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM public.offer_chunks
                WHERE file_id = %s
            """, (file_id,))
            deleted_count = cur.rowcount
            print(f"[embedding] deleted {deleted_count} existing chunks")

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

        embeddings_ready = inserted_count > 0
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE public.offer_files
                SET embeddings_ready = %s
                WHERE id = %s
            """, (embeddings_ready, file_id))
            conn.commit()

        retrieval_file_id = None
        with conn.cursor() as cur:
            cur.execute("""
                SELECT of.retrieval_file_id, ob.token AS batch_token, of.org_id
                FROM public.offer_files of
                JOIN public.offer_batches ob ON of.batch_id = ob.id
                WHERE of.id = %s
            """, (file_id,))
            meta = cur.fetchone() or {}
            retrieval_file_id = meta.get("retrieval_file_id")
            org_id = meta.get("org_id")
            batch_token = meta.get("batch_token")

        vs_id = None
        if org_id and batch_token:
            vs_id = get_offer_vs(conn, org_id, batch_token)

        if not retrieval_file_id:
            try:
                up = client.files.create(file=open(storage_path, "rb"), purpose="assistants")
                retrieval_file_id = up.id
                with conn.cursor() as cur:
                    cur.execute("UPDATE public.offer_files SET retrieval_file_id=%s WHERE id=%s",
                                (retrieval_file_id, file_id))
                    conn.commit()
                print(f"[embedding] uploaded to OpenAI files id={retrieval_file_id}")
            except Exception as e:
                print(f"[embedding] upload skipped/failed: {e}")

        if retrieval_file_id and vs_id:
            try:
                client.vector_stores.files.create(vector_store_id=vs_id, file_id=retrieval_file_id)
                print(f"[embedding] attached file to vector_store={vs_id}")
            except Exception as e:
                print(f"[embedding] attach warn vs={vs_id}: {e}")

        print(f"[embedding] done file_id={file_id} chunks={inserted_count} ready={embeddings_ready} vs={vs_id or '-'}")

        return {
            "ok": True,
            "file_id": file_id,
            "filename": filename,
            "text_length": len(text),
            "chunks_created": inserted_count,
            "chunks_deleted": deleted_count,
            "embeddings_ready": embeddings_ready
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[embedding] error file_id={file_id}: {e}")
        import traceback
        traceback.print_exc()

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

        raise HTTPException(status_code=500, detail=f"Re-embedding failed: {str(e)}")

@router.post("/reembed-file")
def reembed_file(
    file_id: int = Query(..., description="ID of the file to re-embed"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role"),
    conn = Depends(get_db)
):
    """
    Admin endpoint to manually re-embed a file.
    - Stores fresh chunks in DB
    - Ensures PDF is uploaded to OpenAI Files (retrieval_file_id)
    - Ensures it's attached to the correct batch Vector Store
    Protected: Only accessible to admin users.
    """
    print(f"[embedding] reembed-file request file_id={file_id}")

    if not x_user_role or x_user_role.lower() != "admin":
        raise HTTPException(status_code=403, detail="Unauthorized: only admin users can re-embed files")

    return _reembed_file(file_id, conn)

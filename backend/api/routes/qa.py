# Temporary file for validating the fixed qa.py contents
from fastapi import APIRouter, Depends, HTTPException, Query, Header
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Set
import os, json, psycopg2, time, math
from psycopg2.extras import RealDictCursor
from app.services.openai_client import client
from datetime import datetime
from app.services.vectorstores import get_tc_vs, get_offer_vs
from pypdf import PdfReader

# Compat: pydantic v1 uses @validator, v2 uses @field_validator
try:
    from pydantic import field_validator as _validator
except ImportError:  # pydantic v1
    from pydantic import validator as _validator  # type: ignore

router = APIRouter(prefix="/api/qa", tags=["qa"])

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
                                 top_k: int = 12) -> List[Dict[str, Any]]:
    """
    Returns list of rows from Postgres offer_chunks for this share.
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
        return cur.fetchall()

class QAAskRequest(BaseModel):
    question: str
    share_token: str
    insurer_only: Optional[str] = None
    lang: Optional[str] = None

class QAAskResponse(BaseModel):
    answer: str
    sources: List[str]

@router.post("/ask-share", response_model=QAAskResponse)
def ask_share_qa(req: QAAskRequest, conn = Depends(get_db)):
    """Answer using DB chunks for OFFERS and OpenAI VS for T&C."""
    start_time = time.time()
    answer = None
    sources = []
    extra_labels = []

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
        chunk_texts = [(r.get("text", "")[:2000] or "") for r in rows]
        chunk_embs = _embed(chunk_texts)
        scored = [(_cosine(q_emb, e), r) for e, r in zip(chunk_embs, rows)]
        scored.sort(reverse=True)

        TOP_K = 12
        top = scored[:TOP_K]

        # Build context & sources (filenames only)
        context_parts = []
        sources = []
        seen = set()
        for _, r in top:
            fname = r.get("filename") or "source"
            idx = r.get("chunk_index", 0)
            context_parts.append(f"\n\n# {fname} (chunk {idx})\n{r.get('text', '')}")
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

        # Compose prompt that allows citing both: (1) filenames we pass, (2) any VS citations model adds
        out_lang = "lv" if (req.lang or "").lower().startswith("lv") else "en"
        system_msg = (
            "You are a broker assistant.\n"
            "Answer ONLY from the provided 'Context' and the Insurer T&C vector store.\n"
            "If some data is missing, say it briefly and continue. Keep answers concise.\n"
            "If a table is requested, output it; use '—' for missing cells.\n"
            "Always include short inline references like [ERGO_-_VA.pdf] where relevant."
        )
        user_msg = (
            f"Question ({'Latvian' if out_lang=='lv' else 'English'}): {question}\n\n"
            f"Context (offers from database):\n{context}\n\n"
            "If you use additional info from the T&C store, still keep the references concise."
        )

        # Prefer Chat Completions w/ DB context
        try:
            chat = client.chat.completions.create(
                model=os.getenv("QA_MODEL", "gpt-4o-mini"),
                messages=[{"role": "system", "content": system_msg},
                        {"role": "user", "content": user_msg}],
                temperature=0.1,
            )
            answer = (chat.choices[0].message.content or "").strip()
        except Exception as e:
            print(f"[qa] chat completion error: {e}")
            raise HTTPException(status_code=502, detail=str(e))

        # Optional: try to pull extra citations via Assistants just to add labels (will not affect text)
        extra_labels = []
        if vector_store_ids:
            try:
                thread = client.beta.threads.create(messages=[{"role":"user","content": question}])
                run = client.beta.threads.runs.create(
                    thread_id=thread.id,
                    assistant_id=os.getenv("ASSISTANT_ID_QA"),
                    tool_resources={"file_search": {"vector_store_ids": vector_store_ids}},
                    instructions="Search T&C for brief supporting references only."
                )
                
                # Poll for completion
                waited = 0
                while waited < 30:
                    r = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
                    if r.status in ("completed","failed","cancelled","expired"): break
                    time.sleep(1)
                    waited += 1

                if r.status == "completed":
                    msgs = client.beta.threads.messages.list(thread_id=thread.id, order="desc", limit=1)
                    for m in msgs.data:
                        if m.role != "assistant": continue
                        for c in m.content:
                            if c.type != "text": continue
                            for ann in (c.text.annotations or []):
                                fid = None
                                fname = None
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
                                    extra_labels.append(fname)

            except Exception as e:
                print(f"[qa] VS citation harvest warn: {e}")

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
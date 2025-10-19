import os
from openai import OpenAI
from psycopg2.extensions import connection as _Conn

client = OpenAI()

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
        vs = client.beta.vector_stores.create(name=f"org_{org_id}_{product_line}".lower())
        cur.execute("""INSERT INTO public.org_vector_stores(org_id, product_line, vector_store_id)
                       VALUES (%s,%s,%s)""", (org_id, product_line, vs.id))
        conn.commit()
        return vs.id

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
        vs = client.beta.vector_stores.create(name=f"org_{org_id}_offer_{batch_token}".lower())
        cur.execute("""INSERT INTO public.org_batch_vector_stores(org_id, batch_token, vector_store_id)
                       VALUES (%s,%s,%s)""", (org_id, batch_token, vs.id))
        conn.commit()
        return vs.id

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

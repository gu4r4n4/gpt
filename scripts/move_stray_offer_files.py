import os, psycopg2
from openai import OpenAI

ORG=1
TC_VS="vs_68f3edc50fa08191be7148669125761f"
BATCH="seed-2025-10-18"

client = OpenAI()
conn = psycopg2.connect(os.getenv("DATABASE_URL"))

# ensure per-batch VS
with conn.cursor() as cur:
    cur.execute("""SELECT vector_store_id FROM public.org_batch_vector_stores WHERE org_id=%s AND batch_token=%s""",
                (ORG, BATCH))
    r = cur.fetchone()
    if r: vs_ob = r[0]
    else:
        vs = client.beta.vector_stores.create(name=f"org_{ORG}_offer_{BATCH}".lower())
        cur.execute("""INSERT INTO public.org_batch_vector_stores(org_id,batch_token,vector_store_id)
                       VALUES (%s,%s,%s)""", (ORG, BATCH, vs.id))
        conn.commit()
        vs_ob = vs.id

with conn.cursor() as cur:
    cur.execute("""SELECT id, filename, retrieval_file_id FROM public.offer_files
                   WHERE vector_store_id=%s AND is_permanent=false""", (TC_VS,))
    rows = cur.fetchall()

for (fid, fname, file_id) in rows:
    try:
        client.beta.vector_stores.files.delete(vector_store_id=TC_VS, file_id=file_id)
    except Exception:
        pass
    client.beta.vector_stores.files.create(vector_store_id=vs_ob, file_id=file_id)
    with conn.cursor() as cur:
        cur.execute("""UPDATE public.offer_files
                       SET vector_store_id=%s, product_line=NULL, insurer_code=NULL
                       WHERE id=%s""", (vs_ob, fid))
conn.commit(); conn.close()
print("moved:", [r[1] for r in rows], "->", vs_ob)

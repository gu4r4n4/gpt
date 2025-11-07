import os, sys
import psycopg2
from app.services.openai_client import client

ORG_ID = int(os.getenv("ORG_ID", "1"))

db_url = os.getenv("DATABASE_URL")
api_key = os.getenv("OPENAI_API_KEY")
if not db_url or not api_key:
    print("Missing env: DATABASE_URL and OPENAI_API_KEY are required", file=sys.stderr)
    sys.exit(1)

# 1) Create vector store
vs = client.vector_stores.create(name=f"org-{ORG_ID}-offers")
print("Created vector store:", vs.id)

# 2) Upsert into org_vector_stores
conn = psycopg2.connect(db_url)
conn.autocommit = True
with conn, conn.cursor() as cur:
    cur.execute("""
        INSERT INTO public.org_vector_stores (org_id, vector_store_id)
        VALUES (%s, %s)
        ON CONFLICT (org_id) DO UPDATE SET vector_store_id = EXCLUDED.vector_store_id
    """, (ORG_ID, vs.id))
print("Saved to org_vector_stores for org_id", ORG_ID)

if __name__ == "__main__":
    main()

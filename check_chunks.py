# check_chunks.py
import os
from openai import OpenAI

VECTOR_STORE_ID = "vs_690d83e728488191aa76c75d2a483041"

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

print(f"📦 Checking files in vector store: {VECTOR_STORE_ID}\n")

# 1) List vector-store file relations (gives you file IDs + status)
rel_list = client.vector_stores.files.list(vector_store_id=VECTOR_STORE_ID)

if not rel_list.data:
    print("⚠️ No files found in the vector store!")
else:
    for rel in rel_list.data:
        file_id = rel.id

        # 2) Get filename from the Files API
        try:
            fmeta = client.files.retrieve(file_id)
            filename = getattr(fmeta, "filename", fmeta.get("filename", "(unknown)")) if hasattr(fmeta, "__dict__") else "(unknown)"
        except Exception as e:
            filename = "(unknown)"
            print(f"   ⚠️ Could not retrieve filename for {file_id}: {e}")

        # 3) Count chunks via the chunks list endpoint
        chunk_count = 0
        try:
            chunks_page = client.vector_stores.files.chunks.list(
                vector_store_id=VECTOR_STORE_ID,
                file_id=file_id,
                limit=100  # paginate if needed
            )
            chunk_count += len(chunks_page.data)
            # paginate if there are more
            while getattr(chunks_page, "has_more", False):
                chunks_page = client.vector_stores.files.chunks.list(
                    vector_store_id=VECTOR_STORE_ID,
                    file_id=file_id,
                    limit=100,
                    after=chunks_page.last_id
                )
                chunk_count += len(chunks_page.data)
        except Exception as e:
            print(f"   ⚠️ Could not list chunks for {file_id}: {e}")
            chunk_count = -1  # indicates unknown

        print(f"🗂️ File ID: {file_id}")
        print(f"   Filename: {filename}")
        print(f"   Status: {getattr(rel, 'status', '(unknown)')}")
        print(f"   Chunk count: {chunk_count if chunk_count >= 0 else '(not available)'}")
        print(f"   Created at: {getattr(rel, 'created_at', '(n/a)')}")
        print("-" * 60)

print("\n✅ Done.")

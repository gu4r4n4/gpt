# /app/check_chunks.py
import os
import sys
from openai import OpenAI

VS_ID = os.getenv("OPENAI_VECTOR_STORE_ID") or os.getenv("VECTOR_STORE_ID") or ""
if not VS_ID and len(sys.argv) > 1:
    VS_ID = sys.argv[1]

if not VS_ID:
    print("Usage: OPENAI_VECTOR_STORE_ID=<vs_id> python check_chunks.py")
    sys.exit(1)

api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    print("ERROR: OPENAI_API_KEY not set")
    sys.exit(1)

client = OpenAI()

print(f"📦 Checking files in vector store: {VS_ID}\n")

# List files in the vector store (GA)
files = client.vector_stores.files.list(vector_store_id=VS_ID)

for f in files.data:
    try:
        file_id = f.id
        filename = getattr(f, "filename", None) or "(unknown)"
        status = getattr(f, "status", None) or "-"
        created_at = getattr(f, "created_at", None)

        # Try listing chunks (GA)
        chunk_count = "n/a"
        try:
            chunks = client.vector_stores.files.list_chunks(
                vector_store_id=VS_ID,
                file_id=file_id
            )
            chunk_count = len(chunks.data)
        except Exception as e:
            print(f"   ⚠️ Could not list chunks for {file_id}: {e}")

        print(f"🗂️ File ID: {file_id}")
        print(f"   Filename: {filename}")
        print(f"   Status: {status}")
        print(f"   Chunk count: {chunk_count}")
        print(f"   Created at: {created_at}")
        print("------------------------------------------------------------")
    except Exception as e:
        print(f"   ⚠️ Error reading file meta: {e}")

print("\n✅ Done.")

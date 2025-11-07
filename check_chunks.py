from openai import OpenAI
import os

# Load your API key (must be set in environment)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

VECTOR_STORE_ID = "vs_690d83e728488191aa76c75d2a483041"

print(f"📦 Checking files in vector store: {VECTOR_STORE_ID}\n")

files = client.vector_stores.files.list(vector_store_id=VECTOR_STORE_ID)

if not files.data:
    print("⚠️ No files found in the vector store!")
else:
    for f in files.data:
        print(f"🗂️ File ID: {f.id}")
        print(f"   Filename: {getattr(f, 'filename', '(unknown)')}")
        print(f"   Status: {getattr(f, 'status', '(unknown)')}")
        print(f"   Chunk count: {getattr(f, 'chunk_count', '(not reported)')}")
        print(f"   Created at: {getattr(f, 'created_at', '(n/a)')}")
        print("-" * 60)

print("\n✅ Done.")

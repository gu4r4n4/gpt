# check_chunks.py
import os
from openai import OpenAI
from datetime import datetime

VS_ID = os.getenv("VECTOR_STORE_ID", "vs_690d83e728488191aa76c75d2a483041")

def ts(sec):
    try:
        return datetime.utcfromtimestamp(int(sec)).isoformat() + "Z"
    except Exception:
        return str(sec)

def main():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("❌ OPENAI_API_KEY is not set")
        return

    client = OpenAI()

    print(f"📦 Checking files in vector store: {VS_ID}\n")

    # 1) List files attached to the vector store
    vs_files = client.vector_stores.files.list(vector_store_id=VS_ID)

    # Older SDKs return .data, newer can be iterable; handle both
    items = getattr(vs_files, "data", vs_files) or []

    if not items:
        print("⚠️ No files listed by vector store. Are files attached?")
        return

    for vsf in items:
        file_id = getattr(vsf, "id", None)
        status = getattr(vsf, "status", "unknown")
        created_at = getattr(vsf, "created_at", None)

        # 2) Retrieve filename via Files API
        filename = "(unknown)"
        try:
            fobj = client.files.retrieve(file_id)
            # FileObject has attributes (not dict)
            filename = getattr(fobj, "filename", "(unknown)")
        except Exception as e:
            print(f"   ⚠️ Could not retrieve filename for {file_id}: {e}")

        # 3) Try to list chunks (SDK 2.x: vector_stores.files.list_chunks)
        chunk_count = None
        chunk_err = None
        try:
            # Newer method name:
            chunks = client.vector_stores.files.list_chunks(
                vector_store_id=VS_ID,
                file_id=file_id,
            )
            chunk_list = getattr(chunks, "data", chunks) or []
            chunk_count = len(chunk_list)
        except Exception as e:
            chunk_err = e

        print(f"🗂️ File ID: {file_id}")
        print(f"   Filename: {filename}")
        print(f"   Status: {status}")
        if chunk_count is not None:
            print(f"   Chunk count: {chunk_count}")
        else:
            print(f"   Chunk count: (not available)")
            if chunk_err:
                print(f"   ⚠️ list_chunks not available in this SDK/env: {chunk_err}")
        print(f"   Created at: {ts(created_at)}")
        print("-" * 60)

    print("\n✅ Done.")

if __name__ == "__main__":
    main()

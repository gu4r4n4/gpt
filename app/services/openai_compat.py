from __future__ import annotations
from typing import Any


def _vs(client: Any):
    vs_api = getattr(client, "vector_stores", None)
    if vs_api is None:
        raise RuntimeError("OpenAI SDK lacks GA vector_stores API. Upgrade 'openai'.")
    return vs_api

def create_vector_store(client: Any, name: str) -> str:
    vs_api = _vs(client)
    vs = vs_api.create(name=name)
    return vs.id

def attach_file_to_vector_store(client: Any, vector_store_id: str, file_id: str) -> None:
    vs_api = _vs(client)
    vs_api.files.create(vector_store_id=vector_store_id, file_id=file_id)

def delete_file_from_vector_store(client: Any, vector_store_id: str, file_id: str) -> None:
    vs_api = _vs(client)
    try:
        vs_api.files.delete(vector_store_id=vector_store_id, file_id=file_id)
    except Exception:
        # swallow; not critical for our flow
        pass

def ensure_vector_store(client: Any, store_hint: str) -> Any:
    """
    Create a vector store with the given hint.
    Returns a VectorStore-like object with .id attribute.
    """
    vs_api = _vs(client)
    return vs_api.create(name=store_hint)

def add_file_to_store(client: Any, vector_store_id: str, file_bytes: bytes, filename: str) -> Any:
    """
    Upload file bytes and attach to vector store.
    Returns a RetrievalFile-like object with .id attribute.
    """
    # Upload file to OpenAI
    file_obj = client.files.create(
        file=(filename, file_bytes, "application/pdf"),
        purpose="assistants",
    )

    # Attach to vector store
    attach_file_to_vector_store(client, vector_store_id, file_obj.id)

    return file_obj

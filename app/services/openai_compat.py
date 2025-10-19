from __future__ import annotations
from typing import Any

def _vs(client: Any):
    # returns (vector_stores_api, is_beta)
    if hasattr(client, "vector_stores"):
        return getattr(client, "vector_stores"), False
    if hasattr(client, "beta") and hasattr(client.beta, "vector_stores"):
        return client.beta.vector_stores, True
    raise RuntimeError("OpenAI SDK lacks vector_stores API. Upgrade 'openai' to >=1.51.0.")

def create_vector_store(client: Any, name: str) -> str:
    vs_api, _ = _vs(client)
    vs = vs_api.create(name=name)
    # objects return .id across SDKs
    return vs.id

def attach_file_to_vector_store(client: Any, vector_store_id: str, file_id: str) -> None:
    vs_api, is_beta = _vs(client)
    files_api = vs_api.files
    # Both shapes support .create(vector_store_id=?, file_id=?)
    files_api.create(vector_store_id=vector_store_id, file_id=file_id)

def delete_file_from_vector_store(client: Any, vector_store_id: str, file_id: str) -> None:
    vs_api, _ = _vs(client)
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
    vs_api, _ = _vs(client)
    vs = vs_api.create(name=store_hint)
    return vs

def add_file_to_store(client: Any, vector_store_id: str, file_bytes: bytes, filename: str) -> Any:
    """
    Upload file bytes and attach to vector store.
    Returns a RetrievalFile-like object with .id attribute.
    """
    # Upload file to OpenAI
    file_obj = client.files.create(
        file=(filename, file_bytes, "application/pdf"),
        purpose="assistants"
    )
    
    # Attach to vector store
    attach_file_to_vector_store(client, vector_store_id, file_obj.id)
    
    return file_obj

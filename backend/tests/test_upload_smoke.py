"""
Smoke test for file upload endpoint.
"""
import json
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Skip if OpenAI API key not set
if not os.getenv("OPENAI_API_KEY"):
    pytest.skip("OPENAI_API_KEY not set", allow_module_level=True)


def test_upload_smoke():
    """Test basic file upload functionality."""
    # Import here to avoid import errors if dependencies missing
    from backend.api.routes.offers_upload import router
    from fastapi import FastAPI
    
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    
    # Create a small test JSON file
    test_data = {"test": "offer", "premium": 100, "insurer": "TestCorp"}
    test_json = json.dumps(test_data).encode('utf-8')
    
    # Create temporary file
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.json', delete=False) as tmp_file:
        tmp_file.write(test_json)
        tmp_file.flush()
        
        try:
            # Upload the file
            with open(tmp_file.name, 'rb') as f:
                response = client.post(
                    "/api/offers/upload",
                    data={
                        "org_id": 1,
                        "created_by_user_id": 1,
                        "offer_id": None
                    },
                    files={"file": ("test_offer.json", f, "application/json")}
                )
            
            # Check response
            assert response.status_code == 200
            result = response.json()
            
            # Verify response structure
            assert "id" in result
            assert "filename" in result
            assert "sha256" in result
            assert "size_bytes" in result
            assert "storage_path" in result
            assert "vector_store_id" in result
            
            # If OpenAI integration worked, should have retrieval_file_id
            if result.get("embeddings_ready"):
                assert "retrieval_file_id" in result
                assert result["retrieval_file_id"] is not None
            
            print(f"Upload successful: {result}")
            
        finally:
            # Clean up
            os.unlink(tmp_file.name)


def test_upload_validation():
    """Test upload validation."""
    from backend.api.routes.offers_upload import router
    from fastapi import FastAPI
    
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    
    # Test missing file
    response = client.post(
        "/api/offers/upload",
        data={"org_id": 1, "created_by_user_id": 1}
    )
    assert response.status_code == 422  # Validation error
    
    # Test unsupported file type
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.exe', delete=False) as tmp_file:
        tmp_file.write(b"fake executable")
        tmp_file.flush()
        
        try:
            with open(tmp_file.name, 'rb') as f:
                response = client.post(
                    "/api/offers/upload",
                    data={"org_id": 1, "created_by_user_id": 1},
                    files={"file": ("test.exe", f, "application/x-executable")}
                )
            
            assert response.status_code == 415  # Unsupported media type
            
        finally:
            os.unlink(tmp_file.name)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

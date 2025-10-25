"""
Test script for the chunks-report endpoint.

To test manually:
1. Set environment variables (DATABASE_URL, etc.)
2. Run: python -m pytest backend/tests/test_chunks_report.py -v
3. Or test manually with curl:

   curl -X GET "http://localhost:8000/api/qa/chunks-report?share_token=YOUR_SHARE_TOKEN&limit=10" \
     -H "X-Org-Id: 1" \
     -H "X-User-Role: admin"
"""

import os
import pytest
from unittest.mock import Mock, patch, MagicMock
from fastapi import HTTPException
from backend.api.routes.qa import (
    _validate_share_token,
    _check_authorization,
    get_chunks_report
)


class TestShareTokenValidation:
    """Test suite for share_token validation"""
    
    def test_validate_share_token_missing(self):
        """Test that missing share_token raises 400"""
        mock_conn = Mock()
        with pytest.raises(HTTPException) as exc_info:
            _validate_share_token("", mock_conn)
        assert exc_info.value.status_code == 400
    
    def test_validate_share_token_not_found(self):
        """Test that non-existent share_token raises 404"""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        
        with pytest.raises(HTTPException) as exc_info:
            _validate_share_token("invalid_token", mock_conn)
        assert exc_info.value.status_code == 404
    
    def test_validate_share_token_expired(self):
        """Test that expired share_token raises 403"""
        from datetime import datetime, timedelta
        
        mock_conn = Mock()
        mock_cursor = Mock()
        expired_time = (datetime.utcnow() - timedelta(days=1)).isoformat() + "Z"
        mock_cursor.fetchone.return_value = {
            "token": "test_token",
            "org_id": 1,
            "payload": {"batch_token": "bt_123"},
            "expires_at": expired_time
        }
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        
        with pytest.raises(HTTPException) as exc_info:
            _validate_share_token("test_token", mock_conn)
        assert exc_info.value.status_code == 403
        assert "expired" in exc_info.value.detail.lower()
    
    def test_validate_share_token_valid(self):
        """Test that valid share_token returns correct data"""
        from datetime import datetime, timedelta
        
        mock_conn = Mock()
        mock_cursor = Mock()
        future_time = (datetime.utcnow() + timedelta(days=1)).isoformat() + "Z"
        mock_cursor.fetchone.return_value = {
            "token": "test_token",
            "org_id": 1,
            "payload": {"batch_token": "bt_123", "document_ids": ["doc1", "doc2"]},
            "expires_at": future_time
        }
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        
        result = _validate_share_token("test_token", mock_conn)
        
        assert result["token"] == "test_token"
        assert result["org_id"] == 1
        assert result["batch_token"] == "bt_123"
        assert result["document_ids"] == ["doc1", "doc2"]


class TestAuthorization:
    """Test suite for authorization checks"""
    
    def test_check_authorization_admin(self):
        """Test that admin role is authorized"""
        share_record = {"org_id": 1}
        # Should not raise
        _check_authorization(share_record, 999, "admin")
    
    def test_check_authorization_same_org(self):
        """Test that same org is authorized"""
        share_record = {"org_id": 1}
        # Should not raise
        _check_authorization(share_record, 1, "user")
    
    def test_check_authorization_different_org(self):
        """Test that different org is not authorized"""
        share_record = {"org_id": 1}
        with pytest.raises(HTTPException) as exc_info:
            _check_authorization(share_record, 2, "user")
        assert exc_info.value.status_code == 403
    
    def test_check_authorization_no_credentials(self):
        """Test that no credentials is not authorized"""
        share_record = {"org_id": 1}
        with pytest.raises(HTTPException) as exc_info:
            _check_authorization(share_record, None, None)
        assert exc_info.value.status_code == 403


class TestChunksReportEndpoint:
    """Integration tests for the chunks-report endpoint"""
    
    @patch('backend.api.routes.qa._validate_share_token')
    @patch('backend.api.routes.qa._check_authorization')
    def test_chunks_report_success(self, mock_auth, mock_validate):
        """Test successful chunks report retrieval"""
        # Mock validation
        mock_validate.return_value = {
            "token": "test_token",
            "org_id": 1,
            "batch_token": "bt_123",
            "document_ids": ["doc1"]
        }
        
        # Mock database connection
        mock_conn = Mock()
        mock_cursor = Mock()
        
        # Mock file query
        mock_cursor.fetchall.side_effect = [
            [{"id": 1, "filename": "test.pdf", "retrieval_file_id": "file-123"}],  # File records
        ]
        
        # Mock chunks count
        mock_cursor.fetchone.return_value = {"total": 5}
        
        # Mock chunks data
        from datetime import datetime
        mock_cursor.fetchall.side_effect.append([
            {
                "id": 1,
                "file_id": 1,
                "chunk_index": 0,
                "text_preview": "This is a test chunk...",
                "metadata": {"page": 1},
                "created_at": datetime.now(),
                "filename": "test.pdf"
            }
        ])
        
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        
        # Call endpoint
        result = get_chunks_report(
            share_token="test_token",
            limit=100,
            offset=0,
            x_org_id=1,
            x_user_role="admin",
            conn=mock_conn
        )
        
        # Verify response
        assert result.ok is True
        assert result.org_id == 1
        assert result.batch_token == "bt_123"
        assert result.total_chunks == 5
    
    def test_chunks_report_missing_token(self):
        """Test that missing share_token raises error"""
        mock_conn = Mock()
        
        with pytest.raises(HTTPException) as exc_info:
            get_chunks_report(
                share_token="",
                limit=100,
                offset=0,
                x_org_id=1,
                x_user_role="admin",
                conn=mock_conn
            )
        assert exc_info.value.status_code in [400, 500]


# Manual testing helper
def manual_test():
    """
    Manual test script - requires actual database connection.
    Run with: python -c "from backend.tests.test_chunks_report import manual_test; manual_test()"
    """
    import psycopg2
    from psycopg2.extras import RealDictCursor
    
    DATABASE_URL = os.getenv("DATABASE_URL")
    SHARE_TOKEN = os.getenv("TEST_SHARE_TOKEN", "YOUR_SHARE_TOKEN_HERE")
    
    if not DATABASE_URL:
        print("❌ DATABASE_URL not set")
        return
    
    print(f"🔍 Testing chunks-report endpoint...")
    print(f"   Share Token: {SHARE_TOKEN}")
    
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        
        result = get_chunks_report(
            share_token=SHARE_TOKEN,
            limit=10,
            offset=0,
            x_org_id=1,
            x_user_role="admin",
            conn=conn
        )
        
        print(f"✅ Success!")
        print(f"   Org ID: {result.org_id}")
        print(f"   Batch Token: {result.batch_token}")
        print(f"   Total Chunks: {result.total_chunks}")
        print(f"   Returned Chunks: {len(result.chunks)}")
        
        if result.chunks:
            print(f"\n📄 First chunk preview:")
            chunk = result.chunks[0]
            print(f"   File: {chunk.filename}")
            print(f"   Index: {chunk.chunk_index}")
            print(f"   Text: {chunk.text[:100]}...")
        
        conn.close()
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    print(__doc__)
    print("\nRun pytest to execute tests, or uncomment manual_test() for live testing.")
    # Uncomment to run manual test:
    # manual_test()


"""
Test script for the re-embedding functionality.

Run with:
    python -m pytest backend/tests/test_reembed.py -v
"""

import os
import json
import pytest
from unittest.mock import Mock, patch, MagicMock
from backend.api.routes.qa import _extract_text_from_pdf, _chunk_text, _reembed_file


class TestTextExtraction:
    """Test PDF text extraction"""
    
    @patch('backend.api.routes.qa.PdfReader')
    def test_extract_text_success(self, mock_pdf_reader):
        """Test successful text extraction from PDF"""
        # Mock PDF pages
        mock_page1 = Mock()
        mock_page1.extract_text.return_value = "This is page 1"
        mock_page2 = Mock()
        mock_page2.extract_text.return_value = "This is page 2"
        
        mock_reader = Mock()
        mock_reader.pages = [mock_page1, mock_page2]
        mock_pdf_reader.return_value = mock_reader
        
        result = _extract_text_from_pdf("/fake/path.pdf")
        
        assert result == "This is page 1\n\nThis is page 2"
    
    @patch('backend.api.routes.qa.PdfReader')
    def test_extract_text_error(self, mock_pdf_reader):
        """Test extraction error handling"""
        mock_pdf_reader.side_effect = Exception("PDF read error")
        
        with pytest.raises(Exception) as exc_info:
            _extract_text_from_pdf("/fake/path.pdf")
        
        assert "Failed to extract text" in str(exc_info.value)


class TestTextChunking:
    """Test text chunking logic"""
    
    def test_chunk_empty_text(self):
        """Test that empty text returns no chunks"""
        assert _chunk_text("") == []
        assert _chunk_text("   ") == []
    
    def test_chunk_short_text(self):
        """Test chunking of text shorter than chunk_size"""
        text = "This is a short text."
        chunks = _chunk_text(text, chunk_size=100, overlap=20)
        
        assert len(chunks) == 1
        assert chunks[0]["text"] == text
        assert chunks[0]["metadata"]["chunk_index"] == 0
    
    def test_chunk_long_text(self):
        """Test chunking of text longer than chunk_size"""
        # Create text with multiple sentences
        text = ". ".join([f"This is sentence {i}" for i in range(100)])
        chunks = _chunk_text(text, chunk_size=100, overlap=20)
        
        assert len(chunks) > 1
        
        # Verify chunk indices are sequential
        for i, chunk in enumerate(chunks):
            assert chunk["metadata"]["chunk_index"] == i
    
    def test_chunk_paragraph_breaks(self):
        """Test that chunking respects paragraph breaks"""
        text = "Paragraph 1 with some text.\n\nParagraph 2 with more text.\n\nParagraph 3 with even more text."
        chunks = _chunk_text(text, chunk_size=50, overlap=10)
        
        # Should create multiple chunks
        assert len(chunks) > 1
        
        # Each chunk should have metadata
        for chunk in chunks:
            assert "metadata" in chunk
            assert "chunk_index" in chunk["metadata"]
            assert "length" in chunk["metadata"]
    
    def test_chunk_overlap(self):
        """Test that chunks have proper overlap"""
        text = "A" * 1000 + "B" * 1000  # 2000 chars
        chunks = _chunk_text(text, chunk_size=1000, overlap=100)
        
        # Should create 2+ chunks
        assert len(chunks) >= 2
        
        # Check overlap exists (second chunk should start before first ends)
        if len(chunks) >= 2:
            chunk1_end = chunks[0]["metadata"]["end_pos"]
            chunk2_start = chunks[1]["metadata"]["start_pos"]
            assert chunk2_start < chunk1_end  # Overlap detected
    
    def test_chunk_metadata(self):
        """Test that chunk metadata is correct"""
        text = "Test text for chunking with metadata."
        chunks = _chunk_text(text, chunk_size=20, overlap=5)
        
        for chunk in chunks:
            meta = chunk["metadata"]
            
            # Verify all metadata fields exist
            assert "chunk_index" in meta
            assert "start_pos" in meta
            assert "end_pos" in meta
            assert "length" in meta
            
            # Verify metadata is consistent
            assert meta["length"] == len(chunk["text"])
            assert meta["start_pos"] >= 0
            assert meta["end_pos"] > meta["start_pos"]


class TestReembedFile:
    """Test the full re-embedding process"""
    
    @patch('backend.api.routes.qa._extract_text_from_pdf')
    @patch('backend.api.routes.qa._chunk_text')
    @patch('os.path.exists')
    def test_reembed_success(self, mock_exists, mock_chunk, mock_extract):
        """Test successful re-embedding"""
        # Mock file exists
        mock_exists.return_value = True
        
        # Mock text extraction
        mock_extract.return_value = "This is extracted text from the PDF document."
        
        # Mock chunking
        mock_chunk.return_value = [
            {
                "text": "This is extracted text",
                "metadata": {"chunk_index": 0, "start_pos": 0, "end_pos": 22, "length": 22}
            },
            {
                "text": "from the PDF document.",
                "metadata": {"chunk_index": 1, "start_pos": 18, "end_pos": 40, "length": 22}
            }
        ]
        
        # Mock database connection
        mock_conn = Mock()
        mock_cursor = Mock()
        
        # Mock file record query
        mock_cursor.fetchone.return_value = {
            "id": 46,
            "filename": "test.pdf",
            "storage_path": "/storage/test.pdf",
            "mime_type": "application/pdf",
            "org_id": 1,
            "batch_id": 5
        }
        
        # Mock rowcount for DELETE
        mock_cursor.rowcount = 0
        
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        
        # Call function
        result = _reembed_file(46, mock_conn)
        
        # Verify result
        assert result["ok"] is True
        assert result["file_id"] == 46
        assert result["filename"] == "test.pdf"
        assert result["chunks_created"] == 2
        assert result["embeddings_ready"] is True
    
    def test_reembed_file_not_found(self):
        """Test re-embedding with non-existent file"""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        
        from fastapi import HTTPException
        
        with pytest.raises(HTTPException) as exc_info:
            _reembed_file(999, mock_conn)
        
        assert exc_info.value.status_code == 404
    
    @patch('os.path.exists')
    def test_reembed_blank_storage_path(self, mock_exists):
        """Test re-embedding with blank storage path"""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = {
            "id": 46,
            "filename": "test.pdf",
            "storage_path": None,  # Blank path
            "mime_type": "application/pdf",
            "org_id": 1,
            "batch_id": 5
        }
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        
        from fastapi import HTTPException
        
        with pytest.raises(HTTPException) as exc_info:
            _reembed_file(46, mock_conn)
        
        assert exc_info.value.status_code == 500
        assert "storage_path is blank" in str(exc_info.value.detail)


# Manual testing helper
def manual_test_reembed():
    """
    Manual test - requires actual database and files.
    Run with: python -c "from backend.tests.test_reembed import manual_test_reembed; manual_test_reembed()"
    """
    import psycopg2
    from psycopg2.extras import RealDictCursor
    
    DATABASE_URL = os.getenv("DATABASE_URL")
    FILE_ID = int(os.getenv("TEST_FILE_ID", "46"))
    
    if not DATABASE_URL:
        print("❌ DATABASE_URL not set")
        return
    
    print(f"🔍 Testing re-embed for file_id={FILE_ID}")
    
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        
        result = _reembed_file(FILE_ID, conn)
        
        print("✅ Success!")
        print(json.dumps(result, indent=2))
        
        conn.close()
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    print(__doc__)
    print("\nRun pytest to execute tests, or uncomment manual_test_reembed() for live testing.")
    # Uncomment to run manual test:
    # manual_test_reembed()


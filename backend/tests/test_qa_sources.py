from backend.api.routes.qa import _normalize_source_strings


def test_normalize_source_strings_handles_objects_and_strings():
    raw_sources = [
        {"filename": "policy.pdf", "retrieval_file_id": "file_12345"},
        {"filename": None, "retrieval_file_id": "file_12345"},
        "Custom Label",
        {"filename": "", "retrieval_file_id": None},
    ]

    normalized = _normalize_source_strings(raw_sources)

    assert normalized[0] == "policy.pdf · file_12345"
    assert "Custom Label" in normalized
    assert normalized[-1] == "source"
    # duplicates should be de-duped
    assert len(normalized) == 3

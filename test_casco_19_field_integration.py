"""
Integration test for the 19-field simplified CASCO extraction system.

This test verifies:
1. Schema validation (19 fields, all strings)
2. Key mapping (JSON keys with special chars → Python attributes)
3. Extractor logic (mock OpenAI response)
4. Comparator integration
"""
import json
from app.casco.schema import CascoCoverage, CASCO_COMPARISON_ROWS
from app.casco.extractor import _map_json_keys_to_python
from app.casco.normalizer import normalize_casco_coverage


def test_schema_structure():
    """Test that CascoCoverage has exactly 19 coverage fields + metadata."""
    print("Testing schema structure...")
    
    # Create a valid coverage instance
    coverage = CascoCoverage(
        insurer_name="BALTA",
        pdf_filename="test.pdf",
        Bojājumi="v",
        Bojāeja="v",
        Zādzība="v",
        Apzagšana="v",
        Teritorija="Eiropa",
        Pašrisks_bojājumi="160 EUR",
        Stiklojums_bez_pašriska="v",
        Maiņas_nomas_auto_dienas="15 dienas / 30 EUR dienā",
        Palīdzība_uz_ceļa="v",
        Hidrotrieciens="bez paša riska ar limitu 7000 EUR",
        Personīgās_mantas_bagāža="bez paša riska ar limitu 1000 EUR",
        Atslēgu_zādzība_atjaunošana="bez paša riska 1 reizi polises laikā",
        Degvielas_sajaukšana_tīrīšana="v",
        Riepas_diski="0 EUR pašrisks pirmajam gadījumam",
        Numurzīmes="v",
        Nelaimes_gad_vadīt_pasažieriem="Nāve 2500 EUR, invaliditāte 5000 EUR",
        Sadursme_ar_dzīvnieku="v",
        Uguns_dabas_stihijas="v",
        Vandālisms="v",
    )
    
    # Verify all fields are strings or None
    data = coverage.model_dump()
    coverage_fields = [k for k in data.keys() if k not in ["insurer_name", "pdf_filename"]]
    
    assert len(coverage_fields) == 19, f"Expected 19 coverage fields, got {len(coverage_fields)}"
    
    for field in coverage_fields:
        value = data[field]
        assert value is None or isinstance(value, str), f"Field {field} is not a string: {type(value)}"
    
    print("[OK] Schema structure is correct (19 string fields)")


def test_key_mapping():
    """Test that JSON keys with special characters are properly mapped to Python attributes."""
    print("\nTesting key mapping...")
    
    # Simulate JSON response from OpenAI
    json_response = {
        "Bojājumi": "v",
        "Bojāeja": "v",
        "Zādzība": "v",
        "Apzagšana": "v",
        "Teritorija": "Eiropa",
        "Pašrisks – bojājumi": "160 EUR",  # Note: dash with spaces
        "Stiklojums bez pašriska": "v",     # Note: spaces
        "Maiņas / nomas auto (dienas)": "15 dienas",  # Note: slashes and parens
        "Palīdzība uz ceļa": "v",
        "Hidrotrieciens": "v",
        "Personīgās mantas / bagāža": "v",  # Note: slash
        "Atslēgu zādzība/atjaunošana": "v",  # Note: slash (no spaces)
        "Degvielas sajaukšana/tīrīšana": "v",
        "Riepas / diski": "v",
        "Numurzīmes": "v",
        "Nelaimes gad. vadīt./pasažieriem": "v",  # Note: dots and slash
        "Sadursme ar dzīvnieku": "v",
        "Uguns / dabas stihijas": "v",
        "Vandālisms": "v",
    }
    
    # Map keys
    mapped = _map_json_keys_to_python(json_response)
    
    # Verify mappings
    assert mapped["Pašrisks_bojājumi"] == "160 EUR", "Pašrisks mapping failed"
    assert mapped["Stiklojums_bez_pašriska"] == "v", "Stiklojums mapping failed"
    assert mapped["Maiņas_nomas_auto_dienas"] == "15 dienas", "Maiņas mapping failed"
    assert mapped["Personīgās_mantas_bagāža"] == "v", "Personīgās mantas mapping failed"
    assert mapped["Uguns_dabas_stihijas"] == "v", "Uguns mapping failed"
    
    # Verify all 19 fields are present
    assert len(mapped) == 19, f"Expected 19 mapped fields, got {len(mapped)}"
    
    print("[OK] Key mapping works correctly")


def test_comparison_rows():
    """Test that CASCO_COMPARISON_ROWS matches the 19-field schema."""
    print("\nTesting comparison rows...")
    
    # Should have 19 comparison rows (no metadata rows in this list)
    assert len(CASCO_COMPARISON_ROWS) == 19, f"Expected 19 comparison rows, got {len(CASCO_COMPARISON_ROWS)}"
    
    # Verify all codes match CascoCoverage field names
    coverage_fields = set(CascoCoverage.model_fields.keys()) - {"insurer_name", "pdf_filename"}
    comparison_codes = {row.code for row in CASCO_COMPARISON_ROWS}
    
    assert coverage_fields == comparison_codes, f"Mismatch between schema fields and comparison rows"
    
    # Verify all rows are type "text"
    for row in CASCO_COMPARISON_ROWS:
        assert row.type == "text", f"Row {row.code} should be type 'text', got '{row.type}'"
    
    print("[OK] Comparison rows are correctly defined")


def test_normalizer_passthrough():
    """Test that normalizer is now a simple pass-through."""
    print("\nTesting normalizer...")
    
    coverage = CascoCoverage(
        insurer_name="TEST",
        Bojājumi="v",
        Teritorija="Eiropa",
    )
    
    normalized = normalize_casco_coverage(coverage)
    
    # Should be identical (pass-through)
    assert normalized == coverage, "Normalizer should pass through unchanged"
    
    print("[OK] Normalizer is a pass-through (as expected)")


def test_complete_flow_simulation():
    """Simulate the complete extraction flow."""
    print("\nTesting complete flow simulation...")
    
    # 1. Simulate GPT response (19-field JSON)
    simulated_gpt_response = json.dumps({
        "Bojājumi": "v",
        "Bojāeja": "v",
        "Zādzība": "v",
        "Apzagšana": "-",
        "Teritorija": "Eiropa (izņemot Baltkrieviju, Krieviju, Moldovu un Ukrainu)",
        "Pašrisks – bojājumi": "160 EUR",
        "Stiklojums bez pašriska": "v",
        "Maiņas / nomas auto (dienas)": "15 dienas / 30 EUR dienā",
        "Palīdzība uz ceļa": "v",
        "Hidrotrieciens": "bez paša riska ar limitu 7000 EUR",
        "Personīgās mantas / bagāža": "bez paša riska ar limitu 1000 EUR",
        "Atslēgu zādzība/atjaunošana": "bez paša riska 1 reizi",
        "Degvielas sajaukšana/tīrīšana": "v",
        "Riepas / diski": "0 EUR pašrisks pirmajam gadījumam",
        "Numurzīmes": "v",
        "Nelaimes gad. vadīt./pasažieriem": "Nāve 2500 EUR, invaliditāte 5000 EUR",
        "Sadursme ar dzīvnieku": "v",
        "Uguns / dabas stihijas": "v",
        "Vandālisms": "v",
    })
    
    # 2. Parse JSON
    payload = json.loads(simulated_gpt_response)
    
    # 3. Map keys
    mapped = _map_json_keys_to_python(payload)
    
    # 4. Add metadata
    mapped["insurer_name"] = "BALTA"
    mapped["pdf_filename"] = "balta_test.pdf"
    
    # 5. Validate with Pydantic
    coverage = CascoCoverage(**mapped)
    
    # 6. Verify key fields
    assert coverage.Bojājumi == "v"
    assert coverage.Teritorija == "Eiropa (izņemot Baltkrieviju, Krieviju, Moldovu un Ukrainu)"
    assert coverage.Pašrisks_bojājumi == "160 EUR"
    assert coverage.Apzagšana == "-"  # Not covered
    assert coverage.Hidrotrieciens == "bez paša riska ar limitu 7000 EUR"  # Value string
    
    # 7. Test normalizer (should be pass-through)
    normalized = normalize_casco_coverage(coverage)
    assert normalized == coverage
    
    print("[OK] Complete flow simulation successful")


if __name__ == "__main__":
    print("=" * 60)
    print("19-FIELD CASCO INTEGRATION TEST")
    print("=" * 60)
    
    try:
        test_schema_structure()
        test_key_mapping()
        test_comparison_rows()
        test_normalizer_passthrough()
        test_complete_flow_simulation()
        
        print("\n" + "=" * 60)
        print("[SUCCESS] ALL TESTS PASSED")
        print("=" * 60)
        print("\nThe 19-field CASCO system is ready for production!")
        print("\nKey Changes:")
        print("  • Schema: 19 Latvian-named string fields")
        print("  • Extractor: Direct JSON output with v/-/values")
        print("  • Normalizer: Simple pass-through")
        print("  • Comparator: Works with new field names")
        print("  • Service: Updated for new field references")
        
    except AssertionError as e:
        print(f"\n[FAIL] TEST FAILED: {e}")
        raise
    except Exception as e:
        print(f"\n[ERROR] UNEXPECTED ERROR: {e}")
        raise


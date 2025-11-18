"""
CASCO Normalizer - Simplified for 19-Field String Model

With the new string-based extraction ("v", "-", or descriptive values),
normalization is no longer needed. The GPT model already returns standardized values.

This module now provides a simple pass-through for backwards compatibility.
"""
from __future__ import annotations

from .schema import CascoCoverage


def normalize_casco_coverage(c: CascoCoverage) -> CascoCoverage:
    """
    Pass-through normalizer for 19-field string-based model.
    
    In the new simplified CASCO extraction:
    - All coverage fields are already strings ("v", "-", or values)
    - No type conversion needed
    - GPT model enforces standardization via prompt
    
    This function exists only for backwards compatibility with service.py.
    """
    return c

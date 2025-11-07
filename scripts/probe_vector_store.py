# scripts/probe_vector_store.py
import os, sys
from openai import OpenAI

VS_ID = os.environ.get("VS_ID") or "vs_690d83e728488191aa76c75d2a483041"
QUESTION = os.environ.get("QUESTION") or "Which insurers are mentioned in these documents?"

client = OpenAI()  # uses OPENAI_API_KEY

resp = client.responses.create(
    model="gpt-4o-mini",
    input=QUESTION,
    tools=[{"type": "file_search", "vector_store_ids": [VS_ID]}],
)

print("\n=== ANSWER ===")
print(resp.output_text)

# Print out source attributions if present
print("\n=== SOURCES ===")
try:
    # Walk the output for file_citation annotations
    for item in resp.output:
        if item.type == "message":
            for content in item.content:
                if getattr(content, "type", None) == "output_text" and getattr(content, "annotations", None):
                    for ann in content.annotations:
                        if getattr(ann, "type", "") == "file_citation":
                            print(f"- file_id={ann.file_citation.file_id} | quote={ann.file_citation.quote!r}")
except Exception:
    pass

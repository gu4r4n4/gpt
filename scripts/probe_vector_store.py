# scripts/probe_vector_store.py
import os, sys
from openai import OpenAI

VS_ID = os.environ.get("VS_ID") or "vs_690d83e728488191aa76c75d2a483041"
QUESTION = os.environ.get("QUESTION") or "Which insurers are mentioned in these documents?"

client = OpenAI()  # uses OPENAI_API_KEY

# NOTE: Vector store file_search requires Assistants API, not chat.completions
# Using chat.completions.create() instead (standard API in SDK 1.52.0)
resp = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": QUESTION}],
    temperature=0,
)

print("\n=== ANSWER ===")
answer = resp.choices[0].message.content or ""
print(answer)

print("\n=== SOURCES ===")
print("(Note: File search/citations require Assistants API - not available with chat.completions)")

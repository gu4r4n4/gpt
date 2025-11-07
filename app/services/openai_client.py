from __future__ import annotations

from openai import OpenAI

# Single shared OpenAI client instance used across the backend
client = OpenAI()

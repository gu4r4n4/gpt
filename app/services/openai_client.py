from __future__ import annotations

import os
from openai import OpenAI

# Single shared OpenAI client instance used across the backend
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

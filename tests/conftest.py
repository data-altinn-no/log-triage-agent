import os
import sys
from pathlib import Path

# Ensure repo root is on sys.path so `agents`, `api`, `shared` resolve.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("GITHUB_TOKEN", "test-token")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "test")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")

# Force-off, not setdefault: a real .env would send spans to live Langfuse.
os.environ["LANGFUSE_ENABLED"] = "false"
os.environ["DEV_MODE"] = "false"

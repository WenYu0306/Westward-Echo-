"""Application configuration. All values sourced from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Project root ---
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
OUTPUT_DIR = ROOT_DIR / "output"

VERSION = "0.16.0"

# --- DeepSeek V4 (primary LLM) ---
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

DEEPSEEK_FLASH_MODEL = os.getenv("DEEPSEEK_FLASH_MODEL", "deepseek-v4-flash")
DEEPSEEK_PRO_MODEL = os.getenv("DEEPSEEK_PRO_MODEL", "deepseek-v4-pro")
DEEPSEEK_CHAT_MODEL = os.getenv("DEEPSEEK_CHAT_MODEL", "deepseek-chat")

# --- Anthropic Claude (optional arbitration) ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# --- Chroma ---
# Redirect Chroma's ONNX model cache into the project dir (default ~/.cache is blocked by sandbox).
os.environ.setdefault("XDG_CACHE_HOME", str(DATA_DIR / "cache"))
CHROMA_PERSIST_PATH = os.getenv("CHROMA_PERSIST_PATH", str(DATA_DIR / "chroma"))

# --- SQLite checkpoint ---
CHECKPOINT_DB_PATH = os.getenv("CHECKPOINT_DB_PATH", str(DATA_DIR / "checkpoints.db"))

# --- Redis / Celery ---
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# --- Auth ---
API_KEY = os.getenv("API_KEY", "")  # Empty = auth disabled (dev mode)

# --- Logging ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", str(DATA_DIR / "app.log"))
HOST = os.getenv("HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))

# --- v0.17 Style Memo enhancement ---
# When True (default), the full 6-drawer memo is active: characters + prose
# drawers receive data from READ analysis and cold-reader feedback.
# Set to "false" to revert to v0.16 behaviour (terms/bridges/pacing only).
STYLE_MEMO_ENHANCED = os.getenv("STYLE_MEMO_ENHANCED", "true").lower() == "true"

# --- Rate limiting ---
RATE_LIMIT_RPM = int(os.getenv("RATE_LIMIT_RPM", "30"))
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"

# --- Translation defaults ---
DEFAULT_TARGET_LANG = "en-US"
QUALITY_CHECK_INTERVAL = 20           # Run QA every N chapters
MAX_RETRANSLATION_ATTEMPTS = 2        # Max retries for failed chapters
CHAPTER_COOLDOWN_SECONDS = 0.5        # Rate-limit buffer between chapters

# --- CMS ---
CMS_SOURCE_TYPE = os.getenv("CMS_SOURCE_TYPE", "file")     # 'file' or 'webhook'
CMS_FILE_BASE_DIR = os.getenv("CMS_FILE_BASE_DIR", str(ROOT_DIR / "novels"))
CMS_WEBHOOK_URL = os.getenv("CMS_WEBHOOK_URL", "")
CMS_WEBHOOK_API_KEY = os.getenv("CMS_WEBHOOK_API_KEY", "")

# --- File upload security ---
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "50"))  # 50MB default
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024

# --- Per-node model routing ---
# All nodes use deepseek-chat (the preview/stable V4 Flash alias). This is
# deliberately NOT split into per-node Pro/Flash, despite the original plan:
#
# - deepseek-v4-flash (the OFFICIAL V4 Flash name) had output issues in
#   Westward Echo — reverted back to deepseek-chat (2026-08-17).
# - deepseek-v4-pro hangs on large multi-thousand-char prompts with JSON
#   output, and is 3× more expensive.
# - deepseek-chat is the only model name that is stable + reasonably priced
#   for Westward Echo's strict-JSON pipeline.
#
# Do NOT switch to glm-* or kimi-k2.6: they are reasoning models that emit
# reasoning_content (burning ~1000+ tokens of "thinking" per call) and do not
# reliably honor response_format json_object. Verified 2026-08-18.
MODEL_MAP = {
    "translate":              DEEPSEEK_CHAT_MODEL,  # WRITE agent
    "translate_critical":     DEEPSEEK_CHAT_MODEL,  # Reserved (same tier as translate)
    "read":                   DEEPSEEK_CHAT_MODEL,  # READ agent
    "readback":               DEEPSEEK_CHAT_MODEL,  # Cold reader
    "fix":                    DEEPSEEK_CHAT_MODEL,  # Editor
}

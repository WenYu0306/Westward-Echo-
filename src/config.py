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
# READ keeps Pro because cultural analysis is the hardest cognitive task —
# misidentifying an image gap or cultural bridge degrades the entire pipeline.
# WRITE, READBACK, and FIX use Flash: the quality delta is small (<5% in A/B
# testing) while Pro hangs on large inputs (confirmed: DeepSeek V4 Pro accepts
# the connection but never sends a complete response for multi-thousand-char
# prompts with JSON output).  Flash costs 3× less and completes reliably.
#
# deepseek-v4-flash:  $0.14/M input,  $0.28/M output
# deepseek-v4-pro:    $0.435/M input, $0.87/M output (but hangs on large prompts)
MODEL_MAP = {
    "translate":              DEEPSEEK_CHAT_MODEL,  # WRITE agent
    "translate_critical":     DEEPSEEK_CHAT_MODEL,  # Reserved (same tier as translate)
    "read":                   DEEPSEEK_CHAT_MODEL,  # READ agent
    "readback":               DEEPSEEK_CHAT_MODEL,  # Cold reader
    "fix":                    DEEPSEEK_CHAT_MODEL,  # Editor
}

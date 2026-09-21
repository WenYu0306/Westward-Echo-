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

# --- LLM providers (2026-09 定案：全面弃 DeepSeek) ---
# 两个 provider：
#   - GLM（智谱）：文字思考（READ/FIX 用 full）+ 文字输出（WRITE 用 flash）
#   - Qwen（通义千问）：读取判断（READBACK/REVIEW_TERMS 用 3.8-max）

# Qwen — 判断层（LLM_* 即 Qwen 的 key/base_url）
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen3.8-max")
QWEN_THINKING_BUDGET = int(os.getenv("QWEN_THINKING_BUDGET", "2048"))

# GLM — 文字思考/输出
GLM_API_KEY = os.getenv("GLM_API_KEY", "")
GLM_BASE_URL = os.getenv("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
GLM_MODEL = os.getenv("GLM_MODEL", "glm-5.3-flash")       # WRITE 文字输出（便宜10倍）
GLM_FULL_MODEL = os.getenv("GLM_FULL_MODEL", "glm-5.3")   # READ/FIX 文字思考（深度）

# 思考强度（GLM 用 reasoning_effort 档位；默认 max 贵 35 倍，必须显式设）
GLM_WRITE_EFFORT = os.getenv("GLM_WRITE_EFFORT", "low")   # WRITE 忠实输出
GLM_FIX_EFFORT = os.getenv("GLM_FIX_EFFORT", "high")      # FIX 深度修复

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
# 按节点角色分 provider（2026-09 定案，全面弃 DeepSeek）：
#   - READ / FIX：GLM 5.3（full）— 文字思考（找文化缺口 / 靶向修复）
#   - WRITE：GLM 5.3 flash — 文字输出（忠实编译，便宜10倍）
#   - READBACK / REVIEW_TERMS：Qwen 3.8 max — 读取判断
MODEL_MAP = {
    "read":          QWEN_MODEL,      # READ — Qwen 3.8 max（GLM 的内容过滤会拦真实网文）
    "write":         GLM_MODEL,       # WRITE — GLM 5.3 flash 文字输出
    "readback":      QWEN_MODEL,      # READBACK — Qwen 3.8 max 判断
    "fix":           GLM_FULL_MODEL,  # FIX — GLM 5.3 文字思考
    "review_terms":  QWEN_MODEL,      # REVIEW_TERMS — Qwen 3.8 max 判断
}

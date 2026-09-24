"""Application configuration. All values sourced from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Project root ---
ROOT_DIR = Path(__file__).resolve().parent.parent

# Both directories are env-overridable so the test suite can repoint every
# derived path (token ledger, translation_events.db, jobs.db, style memos,
# deliverables) at a throwaway directory. Without this, `pytest` writes real
# rows into data/ and hundreds of stray job files into output/.
# Production leaves these unset and keeps the repo-relative defaults.
DATA_DIR = Path(os.getenv("XIDU_DATA_DIR", "") or ROOT_DIR / "data")
OUTPUT_DIR = Path(os.getenv("XIDU_OUTPUT_DIR", "") or ROOT_DIR / "output")

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

# --- Health checks ---
# The LLM health probe makes a REAL (billable) 1-token call. /api/health is
# unauthenticated and un-rate-limited, and the dashboard polls it every 5 s, so
# the probe is off by default. Set to "true" only for a one-off startup check.
HEALTH_LIVE_LLM_PROBE = os.getenv("HEALTH_LIVE_LLM_PROBE", "false").lower() == "true"

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
# SUPPORTED_LANGUAGES is the single source of truth for which target languages
# the pipeline can address. It lives in config.py because config imports nothing
# from the project, so every layer (prompts, api, web_ui, evaluator) can import
# it without a cycle.
#
# Before this, the same four/five-item list was re-typed in 12 places and had
# already drifted: routes.py carried a bare "en" that no language table knew
# about, while editor.py's copy omitted it — so the two could disagree about
# which output file to look for. Adding a language meant editing all 12; missing
# one failed silently because genre had a three-way sync test but language had
# only a two-way one. tests/test_language_registry.py now asserts every
# language-keyed table matches this tuple.
SUPPORTED_LANGUAGES: tuple[str, ...] = ("en-US", "es-ES", "de", "fr")

DEFAULT_TARGET_LANG = SUPPORTED_LANGUAGES[0]
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

# ── 真实单价（元 / 百万 token，输入价, 输出价）──────────────────────
# 唯一权威来源，供 src/token_ledger.py 算钱。抓取于 2026-09-22：
#   https://docs.bigmodel.cn/cn/guide/start/pricing        (GLM)
#   https://help.aliyun.com/zh/model-studio/model-pricing  (Qwen，中国内地/全球)
# 智谱页与百炼页对 GLM-5.3 都写 8/28，已交叉验证。
# 注意：stats.py 里那套 DeepSeek USD 价目是历史的，不要再拿来算钱。
MODEL_PRICES_CNY_PER_M = {
    "qwen3.8-max":       (12.0, 36.0),   # 0<Token<=1M，不阶梯
    "qwen3.8-flash":     (0.8,  2.7),
    "qwen3.7-flash":     (0.2,  0.8),    # 0<Token<=32K
    "qwen3.8-max-prime": (24.0, 72.0),
    "glm-5.3":           (8.0,  28.0),
    "glm-5.3-flash":     (0.8,  2.8),
    "glm-5.3-flashx":    (2.0,  7.0),
}

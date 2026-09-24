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

# Column-header label for the *target* language, for glossary tables injected
# into prompts. Lives here (not in agent/prompts/translation.py) because the
# glossary layer sits below the agent layer and prompts already import config —
# importing the other way would be a cycle.
#
# This is real data, not a 14th copy of the language list: its keys are asserted
# equal to SUPPORTED_LANGUAGES and its values are asserted equal to the leading
# word of LANGUAGE_NAMES, so the two tables cannot drift silently
# (tests/test_language_registry.py).
LANGUAGE_SHORT_LABELS: dict[str, str] = {
    "en-US": "English",
    "es-ES": "Spanish",
    "de": "German",
    "fr": "French",
}


def target_language_label(target_lang: str = "") -> str:
    """The target-language column label for prompt tables.

    Falls back to the raw code rather than to "English": an unregistered
    language must not be silently described as English, which is the exact
    defect this function replaced (the headers used to be hard-coded
    ``| Chinese | English |`` and told the model the Spanish column was
    English).
    """
    return LANGUAGE_SHORT_LABELS.get(target_lang or "", (target_lang or "").strip() or "Target")


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
#   - READ / READBACK / REVIEW_TERMS：Qwen 3.8 max — 读取判断
#     （READ 从 GLM 移开是因为智谱内容过滤会以 1301 拒付真实网文）
#   - WRITE：GLM 5.3 flash — 文字输出（忠实编译，便宜10倍）
#   - WRITE_FALLBACK：Qwen 3.8 max — GLM 1301 时的换 provider 兜底
#   - FIX：GLM 5.3（full）— 文字思考（靶向修复）
#
# ⚠️ 这张表是**每节点档位的单一来源**，节点必须经 `node_llm_kwargs()` 取模型，
# 不得直接 import `QWEN_MODEL`/`GLM_MODEL`/`GLM_FULL_MODEL` 来建模。
#
# 为什么强调：本表此前是**死配置**——它声明了分工，但五个生产节点全都直接
# import provider 常量建模，只有 dashboard 和 adversarial_test.py 读它。所以
# "改 MODEL_MAP 给 READBACK 降档"改了也一分钱省不下来（COST_MODEL_20260922.md
# §0.5 指出的 10–17 倍杠杆因此一直是断开的）。与 round7 修的"语种清单复制 12
# 份"是同一类缺陷：配置声明与真实行为各走各的。
#
# 每个节点都可用环境变量单独降/升档，无需改代码：
#   XIDU_NODE_MODEL_READBACK=qwen3.8-flash  → READBACK 单价 12/36 降到 0.8/2.7
#   XIDU_NODE_EFFORT_READBACK=low           → 若切到 GLM，思考档位
# provider（base_url / api_key / 思考参数形态）由**模型名**派生，不由节点写死，
# 所以跨 provider 降档（READBACK → glm-5.3-flash）也能正确路由。
DEFAULT_NODE_MODELS = {
    "read":           QWEN_MODEL,      # Qwen 3.8 max（GLM 的 1301 内容过滤会拦真实网文）
    "write":          GLM_MODEL,       # GLM 5.3 flash 文字输出
    "write_fallback": QWEN_MODEL,      # GLM 1301 时的 Qwen 兜底
    "readback":       QWEN_MODEL,      # Qwen 3.8 max 判断（**降档首选目标**）
    "fix":            GLM_FULL_MODEL,  # GLM 5.3 文字思考
    "review_terms":   QWEN_MODEL,      # Qwen 3.8 max 判断
}

# GLM 用 reasoning_effort 档位；默认 max 贵 35 倍，必须显式设。
# Qwen 节点若被切到 GLM 模型，用 "low" 兜底（判断/输出类任务，非深度思考）。
DEFAULT_NODE_EFFORTS = {
    "read":           "low",
    "write":          GLM_WRITE_EFFORT,   # low — 忠实输出
    "write_fallback": "low",
    "readback":       "low",
    "fix":            GLM_FIX_EFFORT,     # high — 深度修复
    "review_terms":   "low",
}


def _env_overridden(table: dict, prefix: str) -> dict:
    """Apply per-node ``XIDU_NODE_<prefix>_<NODE>`` overrides to a default table."""
    out = {}
    for node, default in table.items():
        out[node] = os.getenv(f"XIDU_NODE_{prefix}_{node.upper()}", default)
    return out


NODE_MODELS = _env_overridden(DEFAULT_NODE_MODELS, "MODEL")
NODE_EFFORTS = _env_overridden(DEFAULT_NODE_EFFORTS, "EFFORT")

# Public alias kept for dashboard / health / adversarial_test / token_ledger test.
MODEL_MAP = dict(NODE_MODELS)


def node_provider(model: str) -> str:
    """Which provider serves ``model`` — derived from the name, not the node.

    Raises on an unknown prefix rather than silently routing a Qwen model name
    to the GLM endpoint (which would fail as an auth/model-not-found error far
    from the misconfiguration that caused it).
    """
    name = (model or "").strip().lower()
    if name.startswith("qwen"):
        return "qwen"
    if name.startswith("glm"):
        return "glm"
    raise ValueError(
        f"cannot route model {model!r}: provider must be inferable from the name "
        "(qwen* → LLM_BASE_URL, glm* → GLM_BASE_URL). Set XIDU_NODE_MODEL_<NODE> "
        "to a model from MODEL_PRICES_CNY_PER_M."
    )


def node_model(node: str) -> str:
    """The model ``node`` is routed to. Unknown node → ValueError, not a silent default."""
    try:
        return NODE_MODELS[node]
    except KeyError:
        raise ValueError(f"unknown pipeline node {node!r}; expected one of {sorted(NODE_MODELS)}") from None


def node_llm_kwargs(node: str, *, api_key_override: str = "") -> dict:
    """Everything provider-specific a node needs to build its ChatOpenAI client.

    Returns ``model`` / ``base_url`` / ``api_key`` plus the reasoning control in
    the form that provider expects — ``extra_body={"thinking_budget": N}`` for
    Qwen, ``reasoning_effort=<tier>`` for GLM. **The two must not be set
    together**, which is why the choice lives here instead of in each node.

    Node-owned generation parameters (temperature / max_tokens / request_timeout /
    model_kwargs) stay in the node: they are per-task, not per-provider.

    ``api_key_override`` is the per-request key from job state; when empty the
    environment default for the routed provider is used.
    """
    model = node_model(node)
    if node_provider(model) == "qwen":
        return {
            "model": model,
            "base_url": LLM_BASE_URL,
            "api_key": api_key_override or LLM_API_KEY,
            "extra_body": {"thinking_budget": QWEN_THINKING_BUDGET},
        }
    return {
        "model": model,
        "base_url": GLM_BASE_URL,
        "api_key": api_key_override or GLM_API_KEY,
        "reasoning_effort": NODE_EFFORTS[node],
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

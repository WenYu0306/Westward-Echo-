"""Application configuration. All values sourced from environment variables.

════════════════════════════════════════════════════════════════════════
【中文·全局配置文件——整个项目的"开关面板"】
════════════════════════════════════════════════════════════════════════

这个文件是整个西渡项目的**唯一配置中心**。所有"用哪个模型、花多少钱、
数据存哪里、支持哪些语言"之类的开关，都在这里定义。其他代码只读这里
的变量，不自己硬编码。

【中文·新手阅读顺序建议】
  ① SUPPORTED_LANGUAGES    → 支持哪些目标语言
  ② LLM_API_KEY/GLM_API_KEY → 调用大模型的密钥
  ③ DEFAULT_NODE_MODELS    → 每个角色用哪个模型
  ④ MODEL_PRICES_CNY_PER_M → 真实单价表（算钱用）
  ⑤ DATA_DIR / OUTPUT_DIR  → 数据存在哪
  ⑥ QUALITY_CHECK_INTERVAL → 质量抽检频率

【中文·怎么改配置】
  大部分值可以通过环境变量覆盖（写到 .env 文件里），不需要改这个文件。
  比如想把 READBACK 换成便宜模型：
    在 .env 里加一行：XIDU_NODE_MODEL_READBACK=qwen3.8-flash
  这样不改代码就能降档省钱。
"""

import os
from pathlib import Path

# 【中文】加载 .env 文件里的环境变量。
# .env 是一个纯文本文件，格式是 KEY=VALUE，每行一个。
# 程序启动时读取它，这样 API 密钥等敏感信息不用写死在代码里，
# 也不会被 git 提交到仓库（.env 通常在 .gitignore 里）。
from dotenv import load_dotenv

load_dotenv()

# --- Project root ---
# 【中文】项目根目录的绝对路径。
# Path(__file__) 是当前文件（config.py）的路径，
# .resolve().parent.parent 往上跳两级到项目根目录。
# 后面很多路径都基于它拼接，比如 data/、output/、novels/。
ROOT_DIR = Path(__file__).resolve().parent.parent

# Both directories are env-overridable so the test suite can repoint every
# derived path (token ledger, translation_events.db, jobs.db, style memos,
# deliverables) at a throwaway directory. Without this, `pytest` writes real
# rows into data/ and hundreds of stray job files into output/.
# Production leaves these unset and keeps the repo-relative defaults.
#
# 【中文·测试重定向机制（重要！）】
# DATA_DIR 和 OUTPUT_DIR 是两个"根目录"，项目里所有数据文件的路径
# 都是从它们拼出来的：
#   DATA_DIR   → checkpoints.db、token_ledger.jsonl、chroma 向量库、
#                翻译经验本（style memo）、app.log 等
#   OUTPUT_DIR → 翻译成品 .md 文件、质量报告 _quality.json 等
#
# 正常跑（生产模式）：这两个变量不设，默认指向项目下的 data/ 和 output/。
# 跑测试时：测试框架会把它们指向一个临时目录，这样测试不会污染真实数据。
# ⚠️ 如果你手动设了 XIDU_DATA_DIR，所有数据都会写到那个目录去，
#    包括 checkpoint 和术语库——别指错了，否则生产数据会"消失"。
DATA_DIR = Path(os.getenv("XIDU_DATA_DIR", "") or ROOT_DIR / "data")
OUTPUT_DIR = Path(os.getenv("XIDU_OUTPUT_DIR", "") or ROOT_DIR / "output")

# 【中文】项目版本号，目前 0.16.0。
VERSION = "0.16.0"

# --- LLM providers (2026-09 定案：全面弃 DeepSeek) ---
# 两个 provider：
#   - GLM（智谱）：文字思考（READ/FIX 用 full）+ 文字输出（WRITE 用 flash）
#   - Qwen（通义千问）：读取判断（READBACK/REVIEW_TERMS 用 3.8-max）
#
# 【中文·API 密钥说明】
# API 密钥就是调用大模型服务的"账号密码"，服务商靠它认出你并计费。
# ⚠️ 这两个密钥必须写在 .env 文件里，不要提交到 git：
#   LLM_API_KEY=sk-xxxxx     （Qwen / 通义千问的密钥）
#   GLM_API_KEY=xxxxx        （智谱 GLM 的密钥）
# 没有密钥，所有翻译脚本都会报认证错误。

# Qwen — 判断层（LLM_* 即 Qwen 的 key/base_url）
# 【中文】LLM_API_KEY 和 LLM_BASE_URL 虽然名字里没有 Qwen，
# 但实际上就是 Qwen（通义千问）的密钥和接口地址。
# 这是历史命名，改的话要改很多地方，所以保留了。
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen3.8-max")
# 【中文】Qwen 的"思考预算"（thinking_budget），单位是 token 数。
# Qwen 模型会先"想"一段再回答，这个值限制它最多想多少 token。
# 2048 是默认值，够用于判断类任务（READBACK/REVIEW_TERMS）。
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
# 【中文】Claude 的密钥，目前仅用于可选的"仲裁"功能（adversarial test）。
# 正常翻译流程不需要它，不设也行。
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# --- Chroma ---
# Redirect Chroma's ONNX model cache into the project dir (default ~/.cache is blocked by sandbox).
# 【中文】Chroma 是向量数据库，用来做"语义术语匹配"。
# 比如原文出现"鬼差"，即使术语库里只有"阴差"，语义相近也能命中。
# XDG_CACHE_HOME 把 Chroma 的模型缓存重定向到项目目录下，
# 因为沙箱环境不允许写 ~/.cache。
os.environ.setdefault("XDG_CACHE_HOME", str(DATA_DIR / "cache"))
CHROMA_PERSIST_PATH = os.getenv("CHROMA_PERSIST_PATH", str(DATA_DIR / "chroma"))

# --- SQLite checkpoint ---
# 【中文】断点续翻的存档数据库路径。
# 翻译跑到第 300 章时崩了或你按 Ctrl+C 停了，重启后能从第 300 章接着跑，
# 前面的成果不丢。这个 .db 文件就是存"跑到哪了、术语库快照"的地方。
CHECKPOINT_DB_PATH = os.getenv("CHECKPOINT_DB_PATH", str(DATA_DIR / "checkpoints.db"))

# --- Redis / Celery ---
# 【中文】Redis 是后台任务队列用的消息中间件。
# Celery 把翻译任务丢进 Redis，worker 从里面取任务执行。
# 如果你只跑命令行脚本（scripts/），不需要启动 Redis；
# 但如果要用网页界面（FastAPI + Celery），就必须先起 Redis。
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# --- Auth ---
# 【中文】网页 API 的访问密钥。留空 = 不鉴权（开发模式）。
# 生产部署时设一个随机字符串，防止别人直接调你的 API。
API_KEY = os.getenv("API_KEY", "")  # Empty = auth disabled (dev mode)

# --- Health checks ---
# The LLM health probe makes a REAL (billable) 1-token call. /api/health is
# unauthenticated and un-rate-limited, and the dashboard polls it every 5 s, so
# the probe is off by default. Set to "true" only for a one-off startup check.
# 【中文】健康检查开关。⚠️ 开启后会每 5 秒调一次大模型（花真钱！），
# 所以默认关闭。只在刚启动服务想确认"API 通不通"时临时打开。
HEALTH_LIVE_LLM_PROBE = os.getenv("HEALTH_LIVE_LLM_PROBE", "false").lower() == "true"

# --- Logging ---
# 【中文】日志配置。LOG_LEVEL 控制详细程度（DEBUG/INFO/WARNING/ERROR），
# LOG_FILE 是日志文件路径，默认写到 data/app.log。
# HOST 和 API_PORT 是网页服务的监听地址和端口。
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", str(DATA_DIR / "app.log"))
HOST = os.getenv("HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))

# 【中文】跨域白名单。默认空 = 仅同源（本应用自己托管前端，无需跨域）。
# 如需独立前端，设 CORS_ORIGINS=https://frontend.example.com,https://...
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

# --- v0.17 Style Memo enhancement ---
# When True (default), the full 6-drawer memo is active: characters + prose
# drawers receive data from READ analysis and cold-reader feedback.
# Set to "false" to revert to v0.16 behaviour (terms/bridges/pacing only).
# 【中文】翻译经验本增强开关。
# 经验本（style memo）是项目积累翻译经验的笔记本，越翻越厚。
# v0.17 增加了"角色"和"文风"两个抽屉，让经验更丰富。
# 设为 false 可回退到 v0.16 行为（只有术语/文化桥接/节奏三个抽屉）。
STYLE_MEMO_ENHANCED = os.getenv("STYLE_MEMO_ENHANCED", "true").lower() == "true"

# --- Rate limiting ---
# 【中文】API 调用限速。RATE_LIMIT_RPM = 每分钟最多调几次。
# 大模型 API 有并发限制，超了会被暂时封禁（返回 429 错误）。
# 默认 30 次/分钟，对大部分账号够用。如果你的账号限额更高可以调大。
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
#
# 【中文·语种清单的历史教训】
# 这个元组是"支持哪些目标语言"的**唯一权威来源**。
# 历史上同样的列表被复制了 12 份散落在各个文件里，结果各改各的导致不一致：
# 比如 routes.py 写了个 "en"，但其他表里没有，编辑器就找不到输出文件。
# 现在统一到这里，加新语种只需改这一处。
# 目前有 4 种：英语(en-US)、西班牙语(es-ES)、德语(de)、法语(fr)。
# ⚠️ 曾有过阿拉伯语(ar-SA)，已在 commit 5bba508 移除。
SUPPORTED_LANGUAGES: tuple[str, ...] = ("en-US", "es-ES", "de", "fr")

# 【中文】默认目标语言，取列表第一个（英语）。
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
#
# 【中文】语种代码 → 显示名称的对照表。
# 用在喂给 AI 的术语表里，告诉它"这一列是 English / Spanish / ..."。
# 测试会验证这张表的 key 和 SUPPORTED_LANGUAGES 完全一致，防止漂移。
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

    【中文·获取目标语言的显示名称】
    作用：给提示词里的术语表提供列标题（如 "English"）。
    参数：target_lang — 语种代码，如 "en-US"、"es-ES"。
    返回：对应的英文名；如果传了未注册的代码，返回原始代码而不是
         错误地显示 "English"（这是历史 bug 的修复）。
    坑：以前写死了 "| Chinese | English |"，翻西班牙语时 AI 以为
       那一列也是英语，输出就错了。
    """
    return LANGUAGE_SHORT_LABELS.get(target_lang or "", (target_lang or "").strip() or "Target")


# 【中文·三个关键阈值】
# QUALITY_CHECK_INTERVAL：每隔多少章做一次完整质量抽检（含冷读者）。
#   默认 20，即每 20 章走一次完整流水线（READ→WRITE→READBACK），
#   其余章节走快速模式（跳过 READBACK）省钱。
# MAX_RETRANSLATION_ATTEMPTS：一章最多修补几轮。
#   默认 2，即"FIX→READBACK"最多循环 2 次。超过就带着问题放行，
#   防止 AI 反复挑剔导致无限烧钱。
# CHAPTER_COOLDOWN_SECONDS：两章之间的冷却时间（秒）。
#   默认 0.5 秒，防止请求太快触发 API 限速。
QUALITY_CHECK_INTERVAL = 20           # Run QA every N chapters
MAX_RETRANSLATION_ATTEMPTS = 2        # Max retries for failed chapters
CHAPTER_COOLDOWN_SECONDS = 0.5        # Rate-limit buffer between chapters

# --- CMS ---
# 【中文·内容管理系统（CMS）配置】
# CMS 是"原文小说从哪里来"的配置。两种模式：
#   file    — 从本地文件读取（默认），小说放在 novels/ 目录下
#   webhook — 从外部服务推送（高级用法，一般不用）
CMS_SOURCE_TYPE = os.getenv("CMS_SOURCE_TYPE", "file")     # 'file' or 'webhook'
CMS_FILE_BASE_DIR = os.getenv("CMS_FILE_BASE_DIR", str(ROOT_DIR / "novels"))
CMS_WEBHOOK_URL = os.getenv("CMS_WEBHOOK_URL", "")
CMS_WEBHOOK_API_KEY = os.getenv("CMS_WEBHOOK_API_KEY", "")

# --- File upload security ---
# 【中文】网页上传文件的大小限制。默认 50MB。
# 超过这个大小的上传会被拒绝，防止有人传超大文件撑爆磁盘。
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
    "read_fallback":  GLM_MODEL,       # Qwen 内容过滤/审查时的 GLM 兜底
    "write":          GLM_MODEL,       # GLM 5.3 flash 文字输出
    "write_fallback": QWEN_MODEL,      # GLM 1301 时的 Qwen 兜底
    "readback":       QWEN_MODEL,      # Qwen 3.8 max 判断（**降档首选目标**）
    "readback_fallback": GLM_MODEL,    # Qwen 内容过滤/审查时的 GLM 兜底
    "fix":            GLM_FULL_MODEL,  # GLM 5.3 文字思考
    "review_terms":   QWEN_MODEL,      # Qwen 3.8 max 判断
    "review_terms_fallback": GLM_MODEL,  # Qwen 内容过滤/审查时的 GLM 兜底
}

# GLM 用 reasoning_effort 档位；默认 max 贵 35 倍，必须显式设。
# Qwen 节点若被切到 GLM 模型，用 "low" 兜底（判断/输出类任务，非深度思考）。
DEFAULT_NODE_EFFORTS = {
    "read":           "low",
    "read_fallback":  "low",
    "write":          GLM_WRITE_EFFORT,   # low — 忠实输出
    "write_fallback": "low",
    "readback":       "low",
    "readback_fallback": "low",
    "fix":            GLM_FIX_EFFORT,     # high — 深度修复
    "review_terms":   "low",
    "review_terms_fallback": "low",
}


def _env_overridden(table: dict, prefix: str) -> dict:
    """Apply per-node ``XIDU_NODE_<prefix>_<NODE>`` overrides to a default table.

    【中文】用环境变量覆盖默认配置表的通用函数。
    比如 prefix="MODEL" 时，会检查 XIDU_NODE_MODEL_READ、
    XIDU_NODE_MODEL_WRITE 等环境变量，有就用环境变量的值，没有就用默认值。
    这样不改代码就能给某个角色换模型。
    """
    out = {}
    for node, default in table.items():
        out[node] = os.getenv(f"XIDU_NODE_{prefix}_{node.upper()}", default)
    return out


# 【中文】最终生效的节点模型表和思考档位表。
# 由默认值 + 环境变量覆盖合并而成。其他代码应该读这两个变量，
# 不要直接读 DEFAULT_NODE_MODELS（那只是默认值，不含覆盖）。
NODE_MODELS = _env_overridden(DEFAULT_NODE_MODELS, "MODEL")
NODE_EFFORTS = _env_overridden(DEFAULT_NODE_EFFORTS, "EFFORT")


def node_provider(model: str) -> str:
    """Which provider serves ``model`` — derived from the name, not the node.

    Raises on an unknown prefix rather than silently routing a Qwen model name
    to the GLM endpoint (which would fail as an auth/model-not-found error far
    from the misconfiguration that caused it).

    【中文·根据模型名判断它属于哪个服务商】
    作用：看模型名的前缀，返回 "qwen" 或 "glm"。
    规则：以 "qwen" 开头 → Qwen；以 "glm" 开头 → GLM。
    坑：如果传了一个不认识的名字（比如拼写错误），直接报错而不是
       默默走错通道——后者会报一个莫名其妙的认证错误，很难排查。
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
    """The model ``node`` is routed to. Unknown node → ValueError, not a silent default.

    【中文·查询某个角色用的是哪个模型】
    参数：node — 角色名，如 "read"、"write"、"readback"、"fix"。
    返回：该角色当前配置的模型名（可能已被环境变量覆盖）。
    坑：传了不认识的角色名会报错，不会默默返回默认值。
    """
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

    【中文·获取某个角色调用大模型所需的全部连接参数】
    作用：返回一个字典，包含 model/base_url/api_key 以及思考控制参数，
         节点拿这个字典就能创建 AI 客户端。
    为什么存在：Qwen 和 GLM 的思考参数格式不同（Qwen 用 thinking_budget，
              GLM 用 reasoning_effort），不能混着设。这个函数根据模型名
              自动选择正确的格式，节点不用关心差异。
    参数：
      node — 角色名（"read"/"write"/"readback"/"fix"/"review_terms"）
      api_key_override — 可选，单次请求用的密钥（多租户场景）；
                         留空则用 .env 里的默认密钥。
    返回：dict，含 model、base_url、api_key、思考参数。
    ⚠️ 所有节点必须通过这个函数获取模型配置，不许自己 import
       QWEN_MODEL/GLM_MODEL 来建模（否则改 MODEL_MAP 不生效）。
    """
    model = node_model(node)
    # 【中文】根据模型名判断走 Qwen 还是 GLM 通道，
    # 然后组装对应格式的连接参数。
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
#
# 【中文·怎么读这张表】
# 每个值是 (输入价, 输出价)，单位是"元人民币 / 百万 token"。
# 比如 qwen3.8-max: (12.0, 36.0) 意思是：
#   输入 100 万 token → ¥12；输出 100 万 token → ¥36。
# 一章大约消耗几千到几万 token，所以单章成本通常在几分钱到几毛钱。
# ⚠️ 如果你换了模型但没在这张表里加对应行，token_ledger 会报
#    "unpriced_calls"，花费统计就不准了。
MODEL_PRICES_CNY_PER_M = {
    "qwen3.8-max":       (12.0, 36.0),   # 0<Token<=1M，不阶梯
    "qwen3.8-flash":     (0.8,  2.7),
    "qwen3.7-flash":     (0.2,  0.8),    # 0<Token<=32K
    "qwen3.8-max-prime": (24.0, 72.0),
    "glm-5.3":           (8.0,  28.0),
    "glm-5.3-flash":     (0.8,  2.8),
    "glm-5.3-flashx":    (2.0,  7.0),
}

"""Semantic glossary layer backed by Chroma.

This layer handles fuzzy / context-dependent term retrieval. When the exact
layer misses a term (or when the chapter's theme suggests additional cultural
terms that don't literally appear in the text), Chroma finds semantically
related entries via vector search.

【中文·语义术语库层】
一、这个文件是干什么的？
    术语库的第二层——"语义匹配层"。
    精确匹配层（exact_store）像查字典，一个字都不能差；
    这一层像问"跟这个词意思接近的有哪些"，字面不同也能匹配上。

二、为什么需要这一层？
    比如原文写了"修炼"，精确库里只存了"修仙 → cultivation"，
    字面对不上就漏掉了。但"修炼"和"修仙"意思相关，
    语义匹配能把它们关联起来，补上这个缺口。

三、核心概念——什么是向量库？
    Chroma 是一个向量数据库。它把每个词的"意思"变成一串数字坐标
    （比如 [0.12, -0.34, 0.78, ...]），意思相近的词坐标就挨得近。
    查询时把问题也变成坐标，然后找距离最近的几个点，
    就能找到"意思相关"的条目，哪怕字面完全不同。
    这就像地图上找附近的餐厅：不看名字，只看距离。

四、它在整个流程里的位置：
    · graph.py 的 _make_state() 里调用 search() 获取语义匹配结果
    · prefetch.py 的后台线程也会调用 search() 做预取
    · 新术语入库时由 graph.py 的 _post_process() 调用 add_term()

五、本文件只有一个类 SemanticGlossary，主要方法：
    · add_term()   —— 往向量库里存一个术语
    · add_batch()  —— 批量存入
    · search()     —— 用语义搜索查找相关术语
    · is_healthy() —— 检查向量库是否正常工作
"""

import logging
import os as _os
import typing
from pathlib import Path

# tenacity 是一个重试库：操作失败时自动按指数退避重试
# （第一次等 2 秒，第二次等 4 秒，第三次等 8 秒…）
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

from ..config import CHROMA_PERSIST_PATH

# ── ONNX 模型缓存重定向 ──
# Chroma 用一个小模型（all-MiniLM-L6-v2）把文字变成向量坐标。
# 这个模型首次使用时会下载到本地缓存。默认缓存目录是 ~/.cache，
# 但沙箱环境禁止写那里，所以必须重定向到项目目录内。
# ⚠️ 必须在 import chromadb 之前设好缓存路径，否则模型还是会下到 ~/.cache。
# Redirect Chroma's ONNX model cache to project dir — the sandbox
# blocks writes to ~/.cache where Chroma defaults. The cache path must
# exist BEFORE importing chromadb, and DOWNLOAD_PATH must be patched
# before first use, so the imports below are intentionally deferred (E402).
_ONNX_CACHE = Path(__file__).resolve().parent.parent.parent / "data" / "onnx_cache"
_onnx_path = str(_ONNX_CACHE / "all-MiniLM-L6-v2")
_os.makedirs(_onnx_path, exist_ok=True)

# 【中文】导入 ONNX 嵌入模型类，并修改它的下载路径。
# E402 是 flake8 的"import 不在文件顶部"警告，这里故意延后导入，
# 因为必须先设好缓存路径才能导入。
from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2  # noqa: E402

ONNXMiniLM_L6_V2.DOWNLOAD_PATH = Path(_onnx_path)

# 【中文】以下两个导入也必须在缓存路径设置之后。
import chromadb  # noqa: E402
from chromadb.config import Settings  # noqa: E402

logger = logging.getLogger("westward_echo.glossary")

# Retry decorator for Chroma / ONNX model initialisation.
# 3 attempts with exponential backoff: 2s, 4s, 8s.
#
# 【中文】Chroma 初始化的重试装饰器。
# ONNX 模型首次加载可能需要下载，网络不稳时会失败。
# 这个装饰器让初始化最多试 3 次，每次间隔翻倍（2→4→8 秒）。
_INIT_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=8),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)


class SemanticGlossary:
    """
    Chroma-backed semantic glossary.

    Used for:
    - Retrieving culturally relevant terms for a chapter's theme
      (e.g. a cultivation chapter pulls in cultivation-related terms even
      if the exact character names don't appear)
    - Fallback fuzzy matching when exact string-contains fails

    【中文】基于 Chroma 向量库的语义术语库。
    用途：
    · 根据章节主题拉取文化相关术语（比如修仙章节自动关联修仙类术语，
      即使这些术语没有逐字出现在原文中）
    · 精确匹配找不到时的模糊兜底

    对外提供的主要方法：
    · add_term() / add_batch() —— 存入术语
    · search()                 —— 语义搜索
    · is_healthy()             —— 健康检查
    · try_recover()            —— 故障后尝试恢复
    """

    # Class-level flag shared across instances so recovery is attempted
    # at most once per process lifecycle (or after a 60s cooldown).
    #
    # 【中文】类级别的恢复时间戳，所有实例共享。
    # 防止多个实例同时疯狂重试恢复，限制为每 60 秒最多试一次。
    _last_retry_time: float = 0.0

    def __init__(self, persist_path: typing.Optional[str] = None, book_id: str = "default"):
        # 【中文】初始化语义术语库。
        # · persist_path：Chroma 数据持久化目录，默认用 config 里的路径
        # · book_id：书籍标识，用于隔离不同书的术语
        # 初始化可能失败（比如 ONNX 模型下载出错），
        # 失败时 _ready=False，后续操作会被跳过但不影响精确匹配层。
        path = persist_path or CHROMA_PERSIST_PATH
        import os
        os.makedirs(path, exist_ok=True)

        self._ready = False
        self._warned = False  # guard against log spam per instance
        # 【中文】_warned 标记：未就绪时只打一次警告日志，避免刷屏。
        self._persist_path = path
        self._book_id = book_id
        self.client: typing.Any = None

        try:
            self.client = self._init_chroma(path)
            self._ready = True
            logger.info("Chroma semantic store initialised successfully")
        except Exception as exc:
            # 初始化失败不抛异常，只记警告。
            # 语义匹配不可用时翻译仍能继续（只是少了模糊匹配补充）。
            self._ready = False
            logger.warning(
                "Chroma semantic store failed to initialise: %s. "
                "Semantic term retrieval and fuzzy matching are unavailable. "
                "Exact term lookups, translation, and chapter processing are unaffected.",
                exc,
            )

    def try_recover(self) -> bool:
        """Attempt re-initialisation if the store was previously unhealthy.

        Returns True on successful recovery, False if still unhealthy.

        【中文】尝试恢复之前初始化失败的语义库。
        · 如果已经正常，直接返回 True
        · 如果距上次重试不到 60 秒，跳过（节流），返回 False
        · 否则重新初始化，成功返回 True，失败返回 False
        """
        import time as _time
        now = _time.monotonic()
        if self._ready:
            # Already healthy — nothing to do but update the retry timestamp
            # so is_healthy() re-checks correctly.
            return True
        if now - SemanticGlossary._last_retry_time < 60:
            # Throttle recovery attempts to once every 60 seconds
            # 【中文】60 秒冷却期：防止频繁重试浪费资源
            return False
        SemanticGlossary._last_retry_time = now
        try:
            self.client = self._init_chroma(self._persist_path)
            self._ready = True
            self._warned = False
            logger.info("Chroma semantic store recovered")
            return True
        except Exception as exc:
            logger.warning(
                "Chroma semantic store recovery failed: %s", exc
            )
            return False

    @_INIT_RETRY
    def _init_chroma(self, path: str) -> typing.Any:
        """Initialise the Chroma client and probe the embedding model.

        Two modes, selected by CHROMA_HOST env var:
          - CHROMA_HOST set → HTTP client (production, standalone Chroma server
            so multiple worker processes share one Chroma safely)
          - CHROMA_HOST unset → PersistentClient (local dev / tests fallback)

        Wrapped with tenacity retry: up to 3 attempts with exponential backoff
        (2s, 4s, 8s) to survive transient ONNX download failures.

        【中文】初始化 Chroma 客户端并探测嵌入模型是否可用。
        两种模式：
        · 设了 CHROMA_HOST 环境变量 → 连远程 Chroma 服务器（生产环境，
          多个 worker 进程共享同一个 Chroma 实例）
        · 没设 → 用本地持久化客户端（开发和测试用）
        初始化时会做一次 upsert+delete 探测，确保 ONNX 模型已下载可用。
        被 @_INIT_RETRY 装饰，失败会自动重试 3 次。
        """
        import os as _os
        host = _os.getenv("CHROMA_HOST", "")
        port = _os.getenv("CHROMA_PORT", "8000")

        if host:
            # 生产模式：连接独立的 Chroma HTTP 服务
            client = chromadb.HttpClient(
                host=host,
                port=int(port),
                settings=Settings(anonymized_telemetry=False),
            )
        else:
            # 开发/测试模式：数据存在本地目录
            client = chromadb.PersistentClient(
                path=path,
                settings=Settings(
                    anonymized_telemetry=False,
                    persist_directory=path,
                ),
            )

        # Probe embedding to trigger model download early
        # 【中文】探测性写入+删除：触发 ONNX 模型下载，
        # 确保后续正式使用时模型已就绪。
        coll = client.get_or_create_collection("probe_init")
        coll.upsert(documents=["test"], ids=["test"])
        coll.delete(ids=["test"])
        return client

    def is_healthy(self) -> bool:
        """Return True when Chroma is initialised and the embedding model is loaded.

        If the store was previously unhealthy, automatically attempt recovery
        (throttled to once every 60 seconds).

        Intended for API health-checks so operators know whether semantic
        search is actually working.

        【中文】检查语义库是否健康可用。
        如果之前不健康，会自动尝试恢复（每 60 秒最多一次）。
        供 API 健康检查端点使用，让运维知道语义搜索是否在正常工作。
        """
        if self._ready and self.client is not None:
            return True
        # Auto-attempt recovery if enough time has passed
        return self.try_recover()

    def _warn_once(self):
        """Emit a single WARNING when ops are attempted on an unready store.

        【中文】未就绪时只打一次警告日志，避免每次操作都刷屏。
        """
        if not self._warned:
            logger.warning(
                "Chroma semantic store is not ready — operation skipped. "
                "Semantic term retrieval is unavailable; exact term lookups, "
                "translation, and chapter processing are unaffected."
            )
            self._warned = True

    def get_or_create_collection(self, target_lang: str = "en-US") -> chromadb.Collection:
        """Each book + target language gets its own collection for isolation.

        【中文】获取或创建 Chroma 集合（collection）。
        每本书 × 每种目标语言 = 一个独立集合，互不干扰。
        集合名由 book_id 和 target_lang 拼接而成，
        其中的 '-' 和 '.' 被替换为 '_'（Chroma 命名规则限制）。
        """
        safe_book = self._book_id.replace('-', '_').replace('.', '_')
        name = f"terms_{safe_book}_{target_lang.replace('-', '_')}"
        assert self.client is not None
        return self.client.get_or_create_collection(name=name)  # type: ignore[no-any-return]

    def add_term(self, term_cn: str, term_en: str, category: str = "culture",
                 context: str = "", target_lang: str = "en-US"):
        """【中文】往语义库里存一个术语。
        · 参数 term_cn：中文原词
        · 参数 term_en：目标语言译文
        · 参数 category：分类标签（如 "culture"、"character" 等）
        · 参数 context：上下文说明（可选）
        · 参数 target_lang：目标语言代码
        存入的内容格式为 "[分类] 中文词: 上下文"，
        Chroma 会把这段文字转成向量坐标供后续搜索。
        如果语义库未就绪，只打一次警告然后跳过。
        """
        if not self._ready:
            self._warn_once()
            return
        collection = self.get_or_create_collection(target_lang)
        # 构造存入文档：把分类、中文词、上下文拼成一段文字
        doc_text = f"[{category}] {term_cn}: {context}" if context else f"[{category}] {term_cn}"
        collection.upsert(
            documents=[doc_text],
            # metadata 存原始字段，搜索时可以取回
            metadatas=[{"term_cn": term_cn, "term_en": term_en, "category": category}],
            ids=[term_cn],  # 用中文原词做唯一 ID
        )

    def add_batch(self, terms: list[dict], target_lang: str = "en-US"):
        """【中文】批量存入术语。
        · 参数 terms：术语字典列表，每个字典含 term_cn、term_en、
          可选的 category 和 context
        · 参数 target_lang：目标语言代码
        内部把多条术语打包成一次 Chroma upsert 调用，比逐条存更高效。
        """
        if not self._ready:
            self._warn_once()
            return
        collection = self.get_or_create_collection(target_lang)
        docs, metas, ids = [], [], []
        for t in terms:
            cn = t["term_cn"]
            doc_text = f"[{t.get('category', 'culture')}] {cn}"
            if t.get("context"):
                doc_text += f": {t['context']}"
            docs.append(doc_text)
            metas.append(
                {"term_cn": cn, "term_en": t["term_en"], "category": t.get("category", "culture")}
            )
            ids.append(cn)
        if ids:
            collection.upsert(documents=docs, metadatas=metas, ids=ids)  # type: ignore[arg-type]

    def search(self, query_text: str, top_k: int = 15,
               target_lang: str = "en-US") -> list[dict]:
        """Search for semantically related terms.

        【中文】用语义搜索查找与查询文本相关的术语。
        · 参数 query_text：查询文本（通常是章节内容或摘要）
        · 参数 top_k：返回最相关的前 N 条结果，默认 15
          （15 这个数字是经验值：太少会漏掉有用术语，太多会引入噪音）
        · 参数 target_lang：目标语言代码
        · 返回：字典列表，每个含 term_cn、term_en、category
        · 如果语义库未就绪或集合为空，返回空列表
        """
        if not self._ready:
            self._warn_once()
            return []
        collection = self.get_or_create_collection(target_lang)
        if collection.count() == 0:
            return []
        # Chroma 的 query 接口：把查询文本转成向量，找最近的 top_k 个点
        results = collection.query(query_texts=[query_text], n_results=top_k)
        if not results["metadatas"] or not results["metadatas"][0]:
            return []
        return [
            {
                "term_cn": m["term_cn"], "term_en": m["term_en"],
                "category": m.get("category", "culture"),
            }
            for m in results["metadatas"][0]
        ]

    def count(self, target_lang: str = "en-US") -> int:
        """【中文】返回指定语言的语义库中有多少条术语。
        未就绪时返回 0。
        """
        if not self._ready:
            return 0
        return self.get_or_create_collection(target_lang).count()

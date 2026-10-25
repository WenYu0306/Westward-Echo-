"""Per-chapter, per-node, per-model token accounting that actually persists.

Why this module exists
----------------------
Before it, the project's only token numbers were *process-global in-memory*
counters, logged once when a web job finished
(``src/api/routes.py`` -> ``TranslationStats.token_snapshot``).  Consequences:

* ``scripts/run_novel.py`` — the path that actually produced the delivered
  corpus (地府 2301 ch, 无限恐怖 772 ch) — recorded **nothing at all**;
* nothing was attributed to a node, a model or a chapter, so "what did chapter
  1238 cost" was unanswerable without re-running the pipeline with a hook;
* every capture helper was wrapped in ``except Exception: pass``, so a change in
  the provider's usage payload would silently stop all accounting;
* the tier label ("flash"/"pro") came from DeepSeek, and the prices in
  ``stats.py`` were DeepSeek USD prices, so the one cost figure the project did
  print was wrong for the GLM/Qwen stack.

What this does instead
----------------------
One append-only JSONL row per API response, carrying book / chapter / node /
model / input / output tokens.  Cost is derived from real current prices in
``config.MODEL_PRICES_CNY_PER_M``.  A response whose usage cannot be read is
recorded as **unmetered** rather than dropped, so missing accounting is visible
instead of silent.  An unknown model is reported as **unpriced**, never priced
at zero.

Reading the ledger::

    from src.token_ledger import summary
    summary(book_id="difu_xiao_xiansheng")   # tokens + CNY per node/model

【中文·这个文件是干什么的】
这是整个项目的**记账本**。每次调用大模型 API，都会在这里记一笔：
哪本书、第几章、哪个节点（READ/WRITE/FIX…）、用的什么模型、
输入了多少 token、输出了多少 token、折合人民币多少钱。

【中文·什么是 token】
大模型计费和计量文字的最小单位。大致上 1 个英文单词 ≈ 1.3 个 token，
1 个汉字 ≈ 1~2 个 token。调用大模型花的钱就是按 token 数算的，
所以精确记录 token 就等于精确记录花了多少钱。

【中文·为什么需要它（不这么做会怎样）】
在这个模块出现之前，项目只有内存里的全局计数器，进程一结束就没了。
后果：① 脚本跑完 2301 章翻译后，完全不知道花了多少钱；
② 没法回答"第 1238 章花了多少"这种问题；③ 旧的计数器用的是
DeepSeek 的美元价格表，但项目早就换成了 GLM/Qwen，所以显示的成本
数字是错的。本模块用 JSONL 文件持久化每一笔记录，并用 config.py 里
的**真实人民币单价表**算钱，彻底解决了这三个问题。

【中文·它在整个流程里的位置】
每个翻译节点（READ/WRITE/FIX/READBACK/REVIEW_TERMS）在拿到大模型
响应后，调 ``record_response()`` 记一笔。看板和分析页面调
``summary()`` 读取汇总数据。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from .config import MODEL_PRICES_CNY_PER_M

_REPO_ROOT = Path(__file__).resolve().parent.parent
# 【中文】账本文件的默认存放位置。JSONL 格式（每行一个 JSON 对象），
# 追加写入，不会覆盖历史记录。
DEFAULT_LEDGER_PATH = _REPO_ROOT / "data" / "token_ledger.jsonl"

# Usage payloads we understand.  LangChain's OpenAI-compatible wrapper fills
# ``response_metadata["token_usage"]``; newer AIMessage objects also expose
# ``usage_metadata``.  Both are checked so accounting survives either shape.
#
# 【中文】大模型返回的响应里，token 用量可能藏在两个不同的位置：
# ① response_metadata → token_usage（旧版 LangChain 格式）
# ② usage_metadata（新版 AIMessage 格式）
# 两种都查，这样不管底层库怎么升级，记账都不会断。
# 元组结构：(外层键, 内层嵌套键或None, 输入token键名, 输出token键名)
_METADATA_KEYS = (
    ("response_metadata", "token_usage", "prompt_tokens", "completion_tokens"),
    ("usage_metadata", None, "input_tokens", "output_tokens"),
)

# 【中文】"未计量"标记。当大模型响应里没有可读的 token 用量时，
# 记为 UNMETERED 而不是悄悄丢掉——这样看汇总时能发现"有多少次没记到"。
UNMETERED = "UNMETERED"


def timed_call(fn, *args, **kwargs):
    """Run one provider call and return ``(result, elapsed_seconds)``.

    【中文】给一次大模型调用计时，返回（结果, 耗时秒数）。
    调用方把耗时随 token 一起记进账本，这样"这一章跑了多久"才是可查的。
    只对成功的调用计时；失败的调用本来就不会产生账本行。
    """
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, time.perf_counter() - start


class _Ledger:
    """Append-only token ledger with in-memory aggregation.

    【中文·这个类做什么】
    核心记账器。每次 API 调用记一行到内存列表和 JSONL 文件，
    同时提供按书/按节点/按模型的汇总查询。
    对外暴露的方法很少，大部分逻辑封装在内部，保证线程安全。
    """

    def __init__(self, path: Path | None = None):
        """【中文】初始化账本。

        参数：
          · path：自定义账本文件路径。传 None 时走默认路径
            （data/token_ledger.jsonl），也可通过环境变量 XIDU_TOKEN_LEDGER 覆盖。
        """
        self._lock = threading.Lock()
        self._path = path
        self._rows: list[dict] = []
        self._write_failures = 0

    # ── path handling (lazy so tests can repoint via env) ──
    # 【中文】路径延迟解析：不在构造时定死，而是每次用的时候才查环境变量。
    # 这样测试可以在运行中途改环境变量指向临时目录，而不用重建对象。
    @property
    def path(self) -> Path:
        if self._path is not None:
            return self._path
        env = os.getenv("XIDU_TOKEN_LEDGER")
        return Path(env) if env else DEFAULT_LEDGER_PATH

    def reset(self) -> None:
        """【中文】清空内存中的记录。**仅供测试用**。
        JSONL 文件是追加式的，不会被这个操作影响——设计意图就是
        历史账单永不丢失，reset 只清当前会话的内存缓存。
        """
        with self._lock:
            self._rows = []
            self._write_failures = 0

    # ── recording ──
    def record(
        self,
        node: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        *,
        book_id: str = "",
        chapter: int = 0,
        elapsed_s: float | None = None,
    ) -> dict:
        """Record one API response's usage. Returns the row that was written.

        【中文】记录一次 API 调用的 token 用量。

        参数：
          · node：流水线节点名（read/write/fix/readback/review_terms）
          · model：实际使用的模型名（如 qwen3.8-max、glm-5.3-flash）
          · input_tokens / output_tokens：输入/输出 token 数，None 表示未读到
          · book_id：书的标识符（可选）
          · chapter：章节号（可选）
          · elapsed_s：这次调用的墙钟耗时秒数（可选）。
            旧的账本行没有这个字段，读取时一律用 .get 兼容。

        返回：写入的那一行字典，包含 metered（是否计量到）、
        priced（是否能算钱）、cost_cny（人民币成本，算不出则为 None）。
        """
        # 【中文】只有输入和输出 token 都读到了，才算"已计量"。
        metered = input_tokens is not None and output_tokens is not None
        row = {
            "book_id": book_id,
            "chapter": chapter,
            "node": node,
            "model": model,
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "metered": metered,
            # 【中文】"已计价"= 已计量 且 模型在价格表里有登记。
            # 未知模型不算钱（标为 unpriced），绝不按零价算——
            # 否则汇总表看起来完整，实际漏了一大块成本。
            "priced": metered and model in MODEL_PRICES_CNY_PER_M,
            "cost_cny": cost_cny(model, input_tokens, output_tokens) if metered else None,
            "elapsed_s": round(float(elapsed_s), 3) if elapsed_s is not None else None,
        }
        with self._lock:
            self._rows.append(row)
            self._append(row)
        return row

    def record_response(
        self,
        response,
        node: str,
        model: str,
        *,
        book_id: str = "",
        chapter: int = 0,
        elapsed_s: float | None = None,
    ) -> dict:
        """Extract usage from a provider response and record it.

        A response with no readable usage is recorded as unmetered — never
        dropped, so the gap is countable.

        【中文】从大模型响应对象里自动提取 token 用量并记录。
        这是最常用的入口——节点代码不需要自己解析响应结构，
        直接把响应对象丢进来就行。读不到用量时记为 unmetered，不丢弃。
        """
        usage_in, usage_out = extract_usage(response)
        return self.record(
            node, model, usage_in, usage_out,
            book_id=book_id, chapter=chapter, elapsed_s=elapsed_s,
        )

    def _append(self, row: dict) -> None:
        """【中文】把一行记录追加写入 JSONL 文件。

        写在 try 里是因为磁盘故障不能让整个翻译崩溃——内存里的
        记录还在，本次会话的汇总仍然能用。但写入失败会被计数
        （_write_failures），这样汇总报告里能看到"有多少条没落盘"，
        而不是假装一切都好。
        """
        path = self.path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            # Losing the file must not lose the run: the in-memory rows still
            # let the caller report this session. But a failure here means the
            # on-disk ledger is incomplete, so it is counted rather than
            # swallowed — a silently unwritable ledger would make the cost
            # report look complete while persisting nothing.
            self._write_failures += 1

    # ── reading ──
    def rows(self, book_id: str | None = None) -> list[dict]:
        """【中文】获取所有记录行。传 book_id 可按书过滤。
        返回的是副本，改它不会影响内部数据。
        """
        with self._lock:
            rows = list(self._rows)
        if book_id is None:
            return rows
        return [r for r in rows if r.get("book_id") == book_id]

    def summary(self, book_id: str | None = None) -> dict:
        """Aggregate tokens and cost per node and per model.

        【中文】生成汇总报告。

        参数：
          · book_id：按书过滤，None 表示全部

        返回字典包含：
          · calls / metered_calls / unmetered_calls / unpriced_calls：各类调用计数
          · write_failures：JSONL 写入失败次数
          · input_tokens / output_tokens：总 token 数（仅已计量的）
          · cost_cny：总成本（人民币，保留6位小数）
          · per_node：按节点分组的明细
          · per_model：按模型分组的明细
        """
        rows = self.rows(book_id)
        per_node: dict[str, dict] = {}
        per_model: dict[str, dict] = {}
        unmetered = 0
        unpriced = 0
        total_cny = 0.0
        for r in rows:
            if not r.get("metered"):
                unmetered += 1
                continue
            if not r.get("priced"):
                unpriced += 1
            # 【中文】同时往 per_node 和 per_model 两个桶里累加，
            # 一次遍历出两份分组统计。
            for bucket, key in ((per_node, r["node"]), (per_model, r["model"])):
                agg = bucket.setdefault(
                    key, {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                          "cost_cny": 0.0, "unpriced_calls": 0}
                )
                agg["calls"] += 1
                agg["input_tokens"] += r["input_tokens"]
                agg["output_tokens"] += r["output_tokens"]
                if r.get("priced"):
                    agg["cost_cny"] += r["cost_cny"] or 0.0
                else:
                    agg["unpriced_calls"] += 1
            if r.get("priced"):
                total_cny += r["cost_cny"] or 0.0

        # Wall-clock timing. Rows written before the field existed carry
        # elapsed_s=None and are simply left out, so old ledgers still load.
        # 【中文】墙钟耗时汇总。旧账本行没有 elapsed_s，直接跳过，不影响读取。
        per_node_s: dict[str, float] = {}
        chapter_s: dict[int, float] = {}
        for r in rows:
            elapsed = r.get("elapsed_s")
            if elapsed is None:
                continue
            per_node_s[r["node"]] = per_node_s.get(r["node"], 0.0) + elapsed
            ch = r.get("chapter") or 0
            if ch:
                chapter_s[ch] = chapter_s.get(ch, 0.0) + elapsed
        chapter_values = list(chapter_s.values())
        slowest = sorted(chapter_s.items(), key=lambda kv: kv[1], reverse=True)[:5]

        return {
            "book_id": book_id,
            "calls": len(rows),
            "metered_calls": len(rows) - unmetered,
            "unmetered_calls": unmetered,
            "unpriced_calls": unpriced,
            "write_failures": self._write_failures,
            "input_tokens": sum(r["input_tokens"] for r in rows if r.get("metered")),
            "output_tokens": sum(r["output_tokens"] for r in rows if r.get("metered")),
            "cost_cny": round(total_cny, 6),
            "per_node": per_node,
            "per_model": per_model,
            "timing": {
                "timed_calls": sum(1 for r in rows if r.get("elapsed_s") is not None),
                "total_s": round(sum(per_node_s.values()), 3),
                "per_node_s": {k: round(v, 3) for k, v in per_node_s.items()},
                "chapters_timed": len(chapter_s),
                "chapter_total_s": round(sum(chapter_values), 3),
                "chapter_p50_s": _round_opt(_percentile(chapter_values, 0.50)),
                "chapter_p95_s": _round_opt(_percentile(chapter_values, 0.95)),
                "slowest_chapters": [
                    {"chapter": ch, "elapsed_s": round(secs, 3)}
                    for ch, secs in slowest
                ],
            },
        }


def _percentile(values: list[float], p: float) -> float | None:
    """Linear-interpolated percentile. ``None`` for an empty list.

    【中文】线性插值分位数。空列表返回 None（不是 0——"没有数据"和
    "耗时 0 秒"是两件事，混在一起会给出假的快时间）。
    """
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _round_opt(value: float | None) -> float | None:
    """【中文】分位数可能为 None，只在有值时四舍五入。"""
    return None if value is None else round(value, 3)


def cost_cny(model: str, input_tokens: int | None, output_tokens: int | None):
    """Cost in CNY for one call. ``None`` if the model has no known price.

    【中文】算一次调用的人民币成本。

    算法：输入token数 ÷ 100万 × 输入单价 + 输出token数 ÷ 100万 × 输出单价。
    单价来自 config.MODEL_PRICES_CNY_PER_M（元/百万token）。
    模型不在价格表里、或 token 数为 None → 返回 None（不是返回 0）。
    """
    price = MODEL_PRICES_CNY_PER_M.get(model)
    if price is None or input_tokens is None or output_tokens is None:
        return None
    price_in, price_out = price
    return (input_tokens / 1_000_000) * price_in + (output_tokens / 1_000_000) * price_out


def extract_usage(response) -> tuple[int | None, int | None]:
    """Return ``(input_tokens, output_tokens)`` from a provider response.

    ``(None, None)`` means the payload carried no usage at all — the caller
    records that as unmetered instead of silently counting zero.

    【中文】从大模型响应对象里提取 token 用量。
    依次尝试 _METADATA_KEYS 里定义的两种格式，找到第一个有数据的就返回。
    两种都没有 → 返回 (None, None)，调用方会把它记为 unmetered。
    """
    for container, nested, in_key, out_key in _METADATA_KEYS:
        holder = getattr(response, container, None)
        if not isinstance(holder, dict):
            continue
        # 【中文】nested 不为 None 时需要再进一层（如 response_metadata.token_usage），
        # 为 None 时直接用 holder 本身（如 usage_metadata 就是最内层）。
        usage = holder.get(nested) if nested else holder
        if not isinstance(usage, dict):
            continue
        if in_key in usage or out_key in usage:
            return _as_int(usage.get(in_key)), _as_int(usage.get(out_key))
    return None, None


def _as_int(value) -> int | None:
    """【中文】安全地把值转成整数。转不了就返回 None，不抛异常。
    大模型返回的 token 数有时是字符串或浮点数，这里做一层容错。
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# 【中文】全模块唯一的账本实例。外部代码直接 import 这个对象来用，
# 不需要自己创建 _Ledger()。
ledger = _Ledger()


def record(node, model, input_tokens, output_tokens, **kw) -> dict:
    """Module-level shorthand for :meth:`_Ledger.record`.

    【中文】模块级快捷函数，省掉 ``ledger.record(...)`` 的前缀。
    """
    return ledger.record(node, model, input_tokens, output_tokens, **kw)


def record_response(response, node, model, **kw) -> dict:
    """Module-level shorthand for :meth:`_Ledger.record_response`.

    【中文】模块级快捷函数，省掉 ``ledger.record_response(...)`` 的前缀。
    """
    return ledger.record_response(response, node, model, **kw)


def summary_from_file(book_id: str | None = None, path: Path | None = None) -> dict:
    """Aggregate the persisted JSONL ledger in a fresh process.

    ``summary()`` only sees rows recorded by *this* process. A report generator
    run separately from the pipeline would otherwise read zero, which looks
    identical to "the run cost nothing". This reads the append-only file, and
    counts unparseable lines as ``corrupt_rows`` rather than dropping them
    silently.

    【中文】读取磁盘上的 JSONL 账本并汇总。summary() 只看本进程内存里的记录，
    所以单独运行的报告脚本会读到 0——这和"这次没花钱"看起来一模一样。
    本函数直接读文件；无法解析的行会被跳过并计入 corrupt_rows，
    绝不把"读取失败"伪装成"没有数据"。
    """
    target = Path(path) if path is not None else ledger.path
    rows: list[dict] = []
    corrupt = 0
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                corrupt += 1
    snapshot = _Ledger(path=target)
    snapshot._rows = rows
    result = snapshot.summary(book_id)
    result["corrupt_rows"] = corrupt
    return result


def summary(book_id: str | None = None) -> dict:
    """Module-level shorthand for :meth:`_Ledger.summary`.

    【中文】模块级快捷函数，省掉 ``ledger.summary(...)`` 的前缀。
    """
    return ledger.summary(book_id)


def reset() -> None:
    """Drop in-memory rows (tests; the JSONL file is append-only by design).

    【中文】清空内存记录，仅供测试用。JSONL 文件不受影响。
    """
    ledger.reset()

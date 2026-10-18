"""Thread-safe translation statistics collectors for the dashboard.

Records per-language API calls, successes/failures, chapter completions,
and throughput.  Used by the observability dashboard and circuit breaker.

【中文·这个文件是干什么的】
这是翻译过程的"实时仪表盘数据源"。它用线程安全的计数器记录：
每种语言调了多少次 API、成功/失败各多少、完成了多少章、吞吐量是多少。
网页看板（dashboard）每隔几秒就来这里取一次最新数据显示给你看。

【中文·⚠️ 重要历史遗留：这里的 token 计数和成本无关】
本文件里的 ``_tokens_input`` / ``_tokens_output`` **只记总量，不算钱**。
历史上它曾经按 DeepSeek 的"flash/pro"分桶并用美元价格算成本，但：
① DeepSeek 已在 2026-09 被完全弃用，那些美元价格是废数据；
② "flash/pro"的分桶逻辑也早就和实际模型对不上了（READ 跑的是
qwen3.8-max 却被归为 pro，FIX 跑的是 glm-5.3 却被归为 flash）。
**真正的成本核算在 src/token_ledger.py**，那里按每次调用的真实模型名
和 config.MODEL_PRICES_CNY_PER_M 里的人民币单价算钱。
config.py 的注释里也明确说了："stats.py 里那套 DeepSeek USD 价目是历史的，
不要再拿来算钱"。

【中文·它在整个流程里的位置】
每个翻译节点在调用 API 前后分别调 record_api_call / record_api_success /
record_api_failure；每完成一章调 record_success。看板页面通过 snapshot()
读取所有指标。熔断器（circuit_breaker.py）也会读这里的错误率来判断
是否需要跳闸。
"""

import threading
import time
from collections import defaultdict


class TranslationStats:
    """Thread-safe counters for dashboard metrics.

    【中文·这个类做什么】
    一组线程安全的全局计数器。所有方法都是类方法（@classmethod），
    不需要创建实例，直接 ``TranslationStats.record_xxx()`` 调用。
    内部用 threading.Lock 保证多线程同时写入不会丢数据。

    Usage::

        TranslationStats.record_api_call("en-US")
        TranslationStats.record_api_success("en-US")
        TranslationStats.record_api_failure("en-US")
        TranslationStats.record_chapter_complete("en-US")
        TranslationStats.record_chapter_failed("en-US")
        snapshot = TranslationStats.snapshot()
    """

    _lock = threading.Lock()

    # Global counters
    # 【中文】全局计数器：不分语言的总计。
    _chapters_translated: int = 0
    _chapters_failed: int = 0
    _api_calls_total: int = 0
    _api_calls_failed: int = 0

    # Per-language counters: lang -> count
    # 【中文】按语言分类的计数器。比如 en-US 调了 500 次、es-ES 调了 200 次，
    # 各自独立统计。这样某种语言的 API 出问题时，不会影响其他语言的计数。
    _api_calls_per_lang: dict[str, int] = defaultdict(int)
    _api_failures_per_lang: dict[str, int] = defaultdict(int)
    _chapters_per_lang: dict[str, int] = defaultdict(int)
    _chapters_failed_per_lang: dict[str, int] = defaultdict(int)

    # Recent history for sliding-window throughput (last N calls)
    # 【中文】最近 N 条记录，用来算滑动窗口的吞吐量和错误率。
    # 不保留全部历史（内存会爆），只留最近的 _max_recent 条。
    _recent_completions: list[float] = []  # timestamps of completed chapters
    _recent_api_calls: list[tuple] = []    # [(timestamp, lang, success), ...]
    _max_recent = 200

    # Token accounting (session totals).
    #
    # The old design split this into "flash" / "pro" buckets and priced them at
    # DeepSeek V4 USD rates. Both halves are now wrong:
    #   * the tiers no longer exist — READ runs qwen3.8-max and was still being
    #     bucketed as "pro", while FIX runs glm-5.3 and was bucketed as "flash";
    #   * DeepSeek was abandoned in 2026-09, so the USD constants priced models
    #     nobody calls.
    # snapshot() fed this straight to the dashboard, which is why the dashboard
    # displayed a cost figure computed from the wrong prices AND the wrong
    # model mapping. Authoritative cost now comes from src/token_ledger.py
    # (per-call, per-model, priced in CNY at current vendor rates).
    #
    # 【中文】⚠️ 下面这两个计数器只记 token 总量，**不算钱**。
    # 历史上它们按 DeepSeek 的 flash/pro 分桶并用美元计价，但 DeepSeek
    # 已在 2026-09 被弃用，分桶逻辑也和实际模型对不上。
    # 要看真实成本请用 token_ledger.summary()。
    _tokens_input: int = 0
    _tokens_output: int = 0

    # Session start time
    # 【中文】会话开始时间（用 monotonic 时钟，不受系统时间调整影响）。
    _start_time: float = time.monotonic()

    @classmethod
    def record_api_call(cls, lang: str = "en-US"):
        """Record that an API call was initiated.

        【中文】记录"发起了一次 API 调用"。
        参数 lang 是目标语言代码，默认 en-US。
        同时累加全局计数和该语言的计数。
        """
        with cls._lock:
            cls._api_calls_total += 1
            cls._api_calls_per_lang[lang] = cls._api_calls_per_lang.get(lang, 0) + 1

    @classmethod
    def record_api_success(cls, lang: str = "en-US"):
        """Record a successful API response.

        【中文】记录"一次 API 调用成功了"。
        同时往最近调用历史里追加一条 (时间戳, 语言, True)，
        用于后续算滑动窗口错误率。超过 _max_recent 条时裁掉最旧的。
        """
        with cls._lock:
            cls._recent_api_calls.append((time.monotonic(), lang, True))
            if len(cls._recent_api_calls) > cls._max_recent:
                cls._recent_api_calls = cls._recent_api_calls[-cls._max_recent:]

    @classmethod
    def record_api_failure(cls, lang: str = "en-US"):
        """Record a failed API call.

        【中文】记录"一次 API 调用失败了"。
        同时累加失败计数和往最近历史里追加 (时间戳, 语言, False)。
        """
        with cls._lock:
            cls._api_calls_failed += 1
            cls._api_failures_per_lang[lang] = cls._api_failures_per_lang.get(lang, 0) + 1
            cls._recent_api_calls.append((time.monotonic(), lang, False))
            if len(cls._recent_api_calls) > cls._max_recent:
                cls._recent_api_calls = cls._recent_api_calls[-cls._max_recent:]

    @classmethod
    def record_success(cls, lang: str = "en-US"):
        """Record a completed chapter translation (convenience name).

        【中文】记录"一章翻译完成了"。
        累加完成计数，并把完成时间戳加入最近完成列表（用于算吞吐量）。
        """
        with cls._lock:
            cls._chapters_translated += 1
            cls._chapters_per_lang[lang] = cls._chapters_per_lang.get(lang, 0) + 1
            cls._recent_completions.append(time.monotonic())
            if len(cls._recent_completions) > cls._max_recent:
                cls._recent_completions = cls._recent_completions[-cls._max_recent:]

    @classmethod
    def record_failure(cls, lang: str = "en-US"):
        """Record a failed chapter translation.

        【中文】记录"一章翻译失败了"。
        """
        with cls._lock:
            cls._chapters_failed += 1
            cls._chapters_failed_per_lang[lang] = cls._chapters_failed_per_lang.get(lang, 0) + 1

    @classmethod
    def record_chapter_complete(cls, lang: str = "en-US"):
        """Alias for ``record_success`` — chapter completed.

        【中文】record_success 的别名，语义更明确。
        """
        cls.record_success(lang)

    @classmethod
    def record_chapter_failed(cls, lang: str = "en-US"):
        """Alias for ``record_failure`` — chapter failed.

        【中文】record_failure 的别名，语义更明确。
        """
        cls.record_failure(lang)

    @classmethod
    def record_tokens(cls, input_tokens: int, output_tokens: int, tier: str = ""):
        """Accumulate session token totals from an LLM API response.

        ``tier`` is accepted and ignored. It used to select a DeepSeek
        "flash"/"pro" bucket; those tiers no longer exist and the mapping had
        drifted from reality (READ on qwen3.8-max was bucketed as "pro", FIX on
        glm-5.3 as "flash"), so per-tier figures were meaningless. Callers still
        pass it — keeping the parameter avoids touching five call sites for a
        value that no longer carries information.

        Per-model attribution lives in src/token_ledger.py, which records the
        model name on every call and prices it in CNY at current vendor rates.

        【中文】累加本次调用的 token 数到会话总量。
        ⚠️ tier 参数已被忽略（接受但不使用）。历史上它用来选 DeepSeek 的
        flash/pro 桶，但那些桶已经不存在了。保留参数只是为了不改调用方代码。
        要查按模型分的 token 和成本，请用 token_ledger.summary()。
        """
        with cls._lock:
            cls._tokens_input += input_tokens
            cls._tokens_output += output_tokens

    @classmethod
    def token_snapshot(cls) -> dict:
        """Return session token totals.

        Deliberately contains no cost figure. This class cannot know which model
        served a call (it only ever saw a stale tier label), so any price it
        computed would be wrong — and it used to be wrong by a wide margin,
        using DeepSeek USD rates for GLM/Qwen traffic. Read cost from
        ``token_ledger.summary()`` instead.

        【中文】返回本次会话的 token 总量。
        ⚠️ 故意不包含成本数字。这个类不知道每次调用用的是哪个模型，
        算出来的钱一定是错的。要看成本请用 token_ledger.summary()。
        """
        with cls._lock:
            return {
                "total": cls._tokens_input + cls._tokens_output,
                "input": cls._tokens_input,
                "output": cls._tokens_output,
            }

    @classmethod
    def reset_tokens(cls):
        """Reset token counters (e.g. at start of a new job).

        【中文】把 token 计数器清零。通常在新任务开始时调用。
        """
        with cls._lock:
            cls._tokens_input = 0
            cls._tokens_output = 0

    @classmethod
    def snapshot(cls) -> dict:
        """Return a point-in-time snapshot of all metrics.

        【中文】生成一份完整的指标快照，供看板显示。

        返回字段说明：
          · chapters_translated / chapters_failed：完成/失败的章节总数
          · api_calls_total / api_calls_failed：API 调用总次数/失败次数
          · throughput_chapters_per_minute：最近 5 分钟的每分钟完成章数
          · error_rates_per_language：每种语言在最近 100 次调用中的错误率
          · chapters_per_language / chapters_failed_per_language：按语言的章节计数
          · api_calls_per_language / api_failures_per_language：按语言的 API 计数
          · uptime_seconds：会话运行时长（秒）
          · tokens_input / tokens_output / tokens_total：token 总量（⚠️ 不含成本）
        """
        with cls._lock:
            now = time.monotonic()

            # Throughput: chapters/min over the last 5 minutes
            # 【中文】算最近 5 分钟的吞吐量：这 5 分钟内完成了多少章 ÷ 5。
            cutoff = now - 300  # 5 minutes
            recent_5m = sum(1 for t in cls._recent_completions if t >= cutoff)
            throughput = recent_5m / 5.0  # chapters per minute over last 5 min

            # Error rate per language (last 100 API calls)
            # 【中文】算每种语言在最近 100 次 API 调用中的错误率。
            # 用滑动窗口而不是全量历史，让指标能及时反映当前状态。
            recent_calls = cls._recent_api_calls[-100:]
            error_rates = {}
            for _ts, _lang, _ok in recent_calls:
                if _lang not in error_rates:
                    error_rates[_lang] = {"total": 0, "failed": 0}
                error_rates[_lang]["total"] += 1
                if not _ok:
                    error_rates[_lang]["failed"] += 1

            per_lang_error = {}
            for lang, counts in error_rates.items():
                total = counts["total"]
                failed = counts["failed"]
                per_lang_error[lang] = {
                    "total": total,
                    "failed": failed,
                    "error_rate": round(failed / total, 4) if total > 0 else 0.0,
                }

            uptime = now - cls._start_time

            return {
                "chapters_translated": cls._chapters_translated,
                "chapters_failed": cls._chapters_failed,
                "api_calls_total": cls._api_calls_total,
                "api_calls_failed": cls._api_calls_failed,
                "throughput_chapters_per_minute": round(throughput, 2),
                "error_rates_per_language": per_lang_error,
                "chapters_per_language": dict(cls._chapters_per_lang),
                "chapters_failed_per_language": dict(cls._chapters_failed_per_lang),
                "api_calls_per_language": dict(cls._api_calls_per_lang),
                "api_failures_per_language": dict(cls._api_failures_per_lang),
                "uptime_seconds": round(uptime, 1),
                "session_start": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(cls._start_time)
                ),
                # Session token totals only. No cost figure and no per-tier
                # buckets: the tiers were DeepSeek's and the USD prices priced a
                # provider this project no longer calls. dashboard.py exposed
                # this dict, so it used to show a wrong amount. Authoritative
                # cost: src/token_ledger.py summary (per model, CNY).
                #
                # 【中文】⚠️ 以下三个字段只是 token 总量，不含成本。
                # 历史上 dashboard 曾把这些数字乘以 DeepSeek 美元单价显示，
                # 结果是错的。现在成本统一从 token_ledger 获取。
                "tokens_input": cls._tokens_input,
                "tokens_output": cls._tokens_output,
                "tokens_total": cls._tokens_input + cls._tokens_output,
            }

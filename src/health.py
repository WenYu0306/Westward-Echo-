"""Startup health checks for Westward Echo subsystems.

Runs pre-flight checks at startup and provides a detailed health report.

【中文·这个文件是干什么的】
这是系统的"开机体检器"。服务启动时，它会挨个检查所有关键依赖：
磁盘空间够不够、配置文件有没有、数据库能不能连、大模型 API 密钥有没有设……
任何一项不通过都会给出详细报告。

【中文·它防止什么坏事发生】
如果带着故障启动服务，表面上看一切正常，等翻译跑到第 300 章才崩，
前面几小时的进度和钱就白费了。在门口拦住问题，排查成本最低。
main.py 里的 create_app() 会在启动时调用 check_all()，
判定为 "unhealthy" 就直接拒绝启动并抛出错误。

【中文·三种体检结果】
  · healthy：一切正常
  · degraded：能用但有部件不正常（比如可选服务没起），记警告后照常启动
  · unhealthy：有致命问题（磁盘满、数据库锁死、密钥缺失），拒绝启动

【中文·⚠️ 关于花钱的探测】
check_llm_api() 有一个"真实调用大模型"的选项（live_llm=True）。
⚠️ 这会花真钱（虽然很少，只发 1 个 token）。因为 /api/health 接口
不需要身份验证、看板每 5 秒轮询一次，如果默认开启就会悄悄烧钱。
所以默认关闭（live_llm=False），只做零成本的配置检查。
只有你明确设置环境变量 HEALTH_LIVE_LLM_PROBE=true 时才会花钱。

【中文·它在整个流程里的位置】
main.py → create_app() → HealthChecker.check_all()。
看板也通过 /api/health 接口定期调用它来获取系统状态。
"""

import logging
import os
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import httpx

from .config import (
    CHECKPOINT_DB_PATH,
    CHROMA_PERSIST_PATH,
    LLM_API_KEY,
    LLM_BASE_URL,
    OUTPUT_DIR,
    QWEN_MODEL,
)

if TYPE_CHECKING:
    from .glossary.semantic_store import SemanticGlossary

logger = logging.getLogger("westward_echo.health")


class HealthChecker:
    """Runs all subsystem health checks and returns a structured report.

    The report has three overall statuses:
    - healthy:   all checks pass
    - degraded:  Chroma or LLM check fails but core functionality works
    - unhealthy: disk full, SQLite locked, or config missing — app should not start

    ⚠️ Cost rule: ``check_all()`` backs ``GET /api/health`` (no auth, no rate
    limit) and the dashboard, which polls every 5 seconds. It must never spend
    money. The live LLM probe is opt-in only — see :meth:`check_llm_api`.

    【中文·这个类做什么】
    把所有子系统的健康检查打包在一起，返回一份结构化的体检报告。
    ⚠️ 核心原则：默认路径绝不花钱。真实的大模型探测必须显式开启。
    """

    def __init__(
        self,
        exact_store=None,
        semantic_store: Optional["SemanticGlossary"] = None,
        live_llm: bool = False,
    ):
        """Stores allow the checker to probe live subsystems.

        If stores are not supplied, checks that depend on them gracefully
        report a degraded result rather than crashing.

        ``live_llm=True`` opts in to a real (billable) 1-token LLM call. It
        defaults to False so the unauthenticated ``/api/health`` endpoint and
        the 5-second dashboard poll cannot silently burn API credits.

        【中文】初始化体检器。

        参数：
          · exact_store / semantic_store：术语库实例，传了就能做更深入的检查。
            不传也不会崩，相关检查会报 degraded 而不是 error。
          · live_llm：是否做大模型的真实调用探测。默认 False（不花钱）。
            ⚠️ 设为 True 时每次 check_all() 都会产生一笔真实的 API 费用。
        """
        self._exact_store = exact_store
        self._semantic_store = semantic_store
        self._live_llm = live_llm

    # ── public entry point ──────────────────────────────────────────

    def check_all(self) -> dict:
        """Run every health check and return the composite report.

        【中文】执行所有健康检查并返回综合报告。

        检查项包括：磁盘空间、配置、输出目录、SQLite、大模型 API、
        内存、磁盘 I/O、Chroma 向量库。

        返回字典结构：
          · status："healthy" / "degraded" / "unhealthy"
          · checks：每项检查的详细结果
          · summary：ok / warn / error 各多少项
          · timestamp：检查时间

        判定规则：
          · 任何 critical=True 且 status=error 的检查 → unhealthy
          · 有任何 error 或 warn → degraded
          · 全部 ok → healthy
        """
        checks = {}
        checks["disk_space"] = self.check_disk_space()
        checks["config"] = self.check_config()
        checks["output_dir"] = self.check_output_dir_writable()
        checks["sqlite"] = self.check_sqlite()
        checks["llm_api"] = self.check_llm_api()
        checks["memory"] = check_memory()
        checks["disk_io"] = check_disk_io()
        checks["chroma"] = self.check_chroma(exact_store=self._exact_store,
                                              semantic_store=self._semantic_store)

        # Determine overall status
        # 【中文】统计各项检查的结果，决定整体状态。
        ok_count = sum(1 for c in checks.values() if c["status"] == "ok")
        warn_count = sum(1 for c in checks.values() if c["status"] == "warn")
        error_count = sum(1 for c in checks.values() if c["status"] == "error")

        if any(c["status"] == "error" and c.get("critical", False)
               for c in checks.values()):
            # 【中文】有致命错误 → unhealthy，服务不应该启动。
            overall = "unhealthy"
        elif error_count > 0 or warn_count > 0:
            # 【中文】有非致命错误或警告 → degraded，能跑但要留意。
            overall = "degraded"
        else:
            overall = "healthy"

        return {
            "status": overall,
            "checks": checks,
            "summary": {
                "ok": ok_count,
                "warn": warn_count,
                "error": error_count,
                "total": len(checks),
            },
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    # ── individual checks ────────────────────────────────────────────

    def check_disk_space(self, min_free_mb: int = 500) -> dict:
        """Check that the data volume has enough free space.

        【中文】检查磁盘剩余空间。
        默认阈值 500 MB。低于这个值就报 error（critical=True），
        因为磁盘满了数据库写不进去、翻译成品存不下来，服务没法正常工作。
        """
        start = time.monotonic()
        try:
            path = Path(CHROMA_PERSIST_PATH).parent  # data/ directory
            path.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(path)
            free_mb = usage.free // (1024 * 1024)
            if free_mb < min_free_mb:
                elapsed = round((time.monotonic() - start) * 1000, 1)
                return {
                    "status": "error",
                    "message": f"Only {free_mb} MB free (threshold: {min_free_mb} MB)",
                    "latency_ms": elapsed,
                    "critical": True,
                }
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return {
                "status": "ok",
                "message": f"{free_mb} MB free",
                "latency_ms": elapsed,
            }
        except Exception as exc:
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return {"status": "error", "message": str(exc), "latency_ms": elapsed, "critical": True}

    def check_config(self) -> dict:
        """Verify the environment variables the pipeline actually uses.

        Both providers are load-bearing in MODEL_MAP: Qwen (LLM_*) serves
        READ/READBACK/REVIEW_TERMS and GLM (GLM_*) serves WRITE/FIX. Either one
        missing means the pipeline genuinely cannot run, so both are critical.

        This used to require DEEPSEEK_API_KEY — a provider the project abandoned
        in 2026-09. The stale requirement made the check pass on an unused key
        while never validating a single credential that is really called, and it
        made a fresh GLM+Qwen-only deployment refuse to start.

        【中文】检查环境变量配置是否完整。
        · 关键变量（缺了就跑不了）：LLM_API_KEY（Qwen）、GLM_API_KEY
        · 可选变量（缺了也能跑但功能受限）：ANTHROPIC_API_KEY、REDIS_URL
        · 已废弃变量（还在 .env 里但代码不再读）：DEEPSEEK_API_KEY 等
          如果它们还被设着，给一个 warn 提醒清理。

        历史踩坑：以前要求 DEEPSEEK_API_KEY，但 DeepSeek 已在 2026-09 被弃用。
        结果是新的 GLM+Qwen 部署因为没设 DeepSeek 密钥而无法启动，
        同时真正需要的 GLM/Qwen 密钥反而没被检查。
        """
        start = time.monotonic()

        # Read from the environment (not module-level constants) so this reports
        # what a deployment actually has, and so tests can inject values.
        # 【中文】直接从环境变量读而不是从模块常量读，这样测试可以注入假值，
        # 而且能看到部署环境实际配了什么（而不是代码里的默认值）。
        critical_vars = {
            "LLM_API_KEY": os.getenv("LLM_API_KEY", ""),
            "GLM_API_KEY": os.getenv("GLM_API_KEY", ""),
        }
        optional_vars = {
            "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),
            "REDIS_URL": os.getenv("REDIS_URL", ""),
        }
        # Retired 2026-09. Present in .env but read by nothing in src/.
        # 【中文】这些变量已经在 2026-09 随 DeepSeek 一起退役了，
        # 代码里不再读取。如果 .env 里还留着，提醒用户清理。
        deprecated_vars = {
            "DEEPSEEK_API_KEY": os.getenv("DEEPSEEK_API_KEY", ""),
            "DEEPSEEK_BASE_URL": os.getenv("DEEPSEEK_BASE_URL", ""),
        }

        missing_critical = [k for k, v in critical_vars.items() if not v]
        missing_optional = [k for k, v in optional_vars.items() if not v]
        stale = [k for k, v in deprecated_vars.items() if v]

        elapsed = round((time.monotonic() - start) * 1000, 1)

        if missing_critical:
            return {
                "status": "error",
                "message": f"Missing required: {', '.join(missing_critical)}",
                "latency_ms": elapsed,
                "critical": True,
            }
        notes = []
        if missing_optional:
            notes.append(f"Missing optional: {', '.join(missing_optional)}")
        if stale:
            notes.append(
                f"Deprecated vars still set (unused since the DeepSeek removal): "
                f"{', '.join(stale)}"
            )
        if notes:
            return {
                "status": "warn",
                "message": " | ".join(notes),
                "latency_ms": elapsed,
            }
        return {"status": "ok", "message": "All required env vars present", "latency_ms": elapsed}

    def check_output_dir_writable(self) -> dict:
        """Verify the output directory exists and is writable.

        【中文】检查输出目录是否可写。
        方法是创建一个临时文件再删掉。如果连这都做不到，
        翻译成品就没地方存，属于致命错误。
        """
        start = time.monotonic()
        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            probe = OUTPUT_DIR / ".health_probe"
            probe.write_text("ok")
            probe.unlink()
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return {"status": "ok", "message": str(OUTPUT_DIR), "latency_ms": elapsed}
        except Exception as exc:
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return {"status": "error", "message": str(exc), "latency_ms": elapsed, "critical": True}

    def check_sqlite(self) -> dict:
        """Verify the SQLite checkpoint database is accessible.

        【中文】检查 SQLite 断点数据库是否能正常连接和执行查询。
        断点数据库存着每章的翻译存档，连不上就意味着无法恢复中断的翻译。
        """
        start = time.monotonic()
        try:
            import sqlite3
            db_path = CHECKPOINT_DB_PATH
            conn = sqlite3.connect(db_path)
            conn.execute("SELECT 1")
            conn.close()
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return {"status": "ok", "message": str(db_path), "latency_ms": elapsed}
        except Exception as exc:
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return {"status": "error", "message": str(exc), "latency_ms": elapsed, "critical": True}

    def check_llm_api(self) -> dict:
        """Report whether the LLM provider is *configured*, without calling it.

        Probing LLM_BASE_URL + QWEN_MODEL (the Qwen 3.8 max judging tier) with a
        real 1-token call is the same OpenAI-compatible base_url
        READBACK/REVIEW_TERMS use — but it costs money on every invocation, and
        ``check_all()`` backs an unauthenticated endpoint plus a dashboard that
        polls every 5 seconds. So the real call now requires ``live_llm=True``.

        The default path is a zero-cost configuration check: it verifies the key
        is present and the base_url is well formed, which catches the failure
        mode that actually takes production down (missing/rotated credential).

        【中文】检查大模型 API 的配置是否正确。

        ⚠️ 默认路径（live_llm=False）**不调用 API、不花钱**，只检查：
        ① LLM_API_KEY 有没有设
        ② LLM_BASE_URL 是不是合法的 URL
        这已经能抓住最常见的生产事故原因（密钥缺失或过期）。

        ⚠️ 只有 live_llm=True 时才会发一个真实的 1-token 请求去试探 API。
        这会产生真实费用。因为 /api/health 不需要认证且看板每 5 秒轮询一次，
        默认开启会导致每分钟 12 次付费调用——一天下来不少钱。
        """
        start = time.monotonic()
        try:
            if not LLM_API_KEY:
                elapsed = round((time.monotonic() - start) * 1000, 1)
                return {
                    "status": "error", "message": "LLM_API_KEY not set",
                    "latency_ms": elapsed,
                    "critical": True,
                }

            base_url = LLM_BASE_URL.rstrip("/")
            if not base_url.startswith(("http://", "https://")):
                elapsed = round((time.monotonic() - start) * 1000, 1)
                return {
                    "status": "error",
                    "message": f"LLM_BASE_URL is not a usable URL: {LLM_BASE_URL!r}",
                    "latency_ms": elapsed,
                    "critical": True,
                }

            if not self._live_llm:
                # 【中文】不做真实调用，只报配置检查结果。零成本。
                elapsed = round((time.monotonic() - start) * 1000, 1)
                return {
                    "status": "ok",
                    "message": (
                        f"configured ({QWEN_MODEL} @ {base_url}); live probe skipped "
                        "— pass live_llm=True to spend a real call"
                    ),
                    "latency_ms": elapsed,
                    "live_probe": False,
                }

            # ── ⚠️ 以下代码会产生真实 API 费用 ──
            # 【中文】真实探测：发一个最小的请求（1 个 token、temperature=0），
            # 看 API 是否正常响应。max_tokens=1 把成本压到最低。
            url = f"{base_url}/chat/completions"

            payload = {
                "model": QWEN_MODEL,
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 1,
                "temperature": 0,
            }
            headers = {
                "Authorization": f"Bearer {LLM_API_KEY}",
                "Content-Type": "application/json",
            }

            with httpx.Client(timeout=5.0) as client:
                resp = client.post(url, json=payload, headers=headers)

            elapsed = round((time.monotonic() - start) * 1000, 1)

            if resp.status_code == 200:
                return {"status": "ok", "message": "LLM API responding", "latency_ms": elapsed,
                        "live_probe": True}
            else:
                body = resp.text[:200]
                return {
                    "status": "error",
                    "message": f"HTTP {resp.status_code}: {body}",
                    "latency_ms": elapsed,
                    "live_probe": True,
                }
        except httpx.TimeoutException:
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return {
                "status": "error", "message": "LLM API timed out (5s)", "latency_ms": elapsed,
                "live_probe": True,
            }
        except Exception as exc:
            # Do NOT downgrade to "warn": a failed live probe means the judging
            # nodes cannot reach the provider. Silently reporting warn here is
            # what let /api/health pass while every real call was failing.
            #
            # 【中文】⚠️ 真实探测失败时报 error 而不是 warn。
            # 历史上曾经降级为 warn，结果是 /api/health 显示"正常"，
            # 但实际上所有 API 调用都在失败——看板一片绿，翻译全在报错。
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return {"status": "error", "message": f"{type(exc).__name__}: {exc}",
                    "latency_ms": elapsed, "live_probe": True}

    def check_chroma(self, exact_store=None,
                     semantic_store: Optional["SemanticGlossary"] = None) -> dict:
        """Verify Chroma persistence and (if a semantic store is supplied) embedding health.

        【中文】检查 Chroma 向量数据库的状态。
        · 如果传了 semantic_store 实例 → 深入检查嵌入功能是否正常
        · 没传 → 只检查持久化目录是否可写
        Chroma 用于语义术语匹配，挂了会影响术语检索质量但不影响翻译本身，
        所以异常时报 warn 而不是 error。
        """
        start = time.monotonic()
        try:
            # Check Chroma persistence directory is writable
            Path(CHROMA_PERSIST_PATH).mkdir(parents=True, exist_ok=True)

            # If a semantic store instance is available, probe its ready flag
            if semantic_store is not None:
                if semantic_store.is_healthy():
                    elapsed = round((time.monotonic() - start) * 1000, 1)
                    return {"status": "ok", "message": "Chroma ready with embeddings",
                            "latency_ms": elapsed}
                else:
                    elapsed = round((time.monotonic() - start) * 1000, 1)
                    return {"status": "warn",
                            "message": "Chroma initialised but embeddings unavailable",
                            "latency_ms": elapsed}

            # Without a live instance, just report that the path is writable
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return {
                "status": "ok",
                "message": f"Chroma persist path writable ({CHROMA_PERSIST_PATH})",
                "latency_ms": elapsed,
            }
        except Exception as exc:
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return {"status": "warn", "message": str(exc), "latency_ms": elapsed}


# ═══════════════════════════════════════════════════════════════
# Module-level resource monitoring functions
# ═══════════════════════════════════════════════════════════════
#
# 【中文】以下是模块级的资源监控函数，不属于 HealthChecker 类，
# 因为它们不依赖任何实例状态，可以直接调用。


def check_memory() -> dict:
    """Return current memory usage.

    Uses psutil if available, otherwise returns a degraded status with
    the reason the check could not run.

    【中文】检查当前内存使用情况。
    · 需要安装 psutil 库才能工作；没装就报 warn 并跳过。
    · 使用率 >95% → error（随时可能 OOM 崩溃）
    · 使用率 >80% → warn（接近危险区）
    · 其他 → ok
    """
    start = time.monotonic()
    try:
        import psutil
        mem = psutil.virtual_memory()
        elapsed = round((time.monotonic() - start) * 1000, 1)
        used_gb = round(mem.used / (1024 ** 3), 2)
        total_gb = round(mem.total / (1024 ** 3), 2)
        pct = mem.percent
        if pct > 95:
            return {
                "status": "error",
                "message": f"Memory usage critical: {used_gb}/{total_gb} GB ({pct}%)",
                "used_gb": used_gb,
                "total_gb": total_gb,
                "percent": pct,
                "latency_ms": elapsed,
            }
        elif pct > 80:
            return {
                "status": "warn",
                "message": f"Memory usage high: {used_gb}/{total_gb} GB ({pct}%)",
                "used_gb": used_gb,
                "total_gb": total_gb,
                "percent": pct,
                "latency_ms": elapsed,
            }
        return {
            "status": "ok",
            "message": f"{used_gb}/{total_gb} GB ({pct}%)",
            "used_gb": used_gb,
            "total_gb": total_gb,
            "percent": pct,
            "latency_ms": elapsed,
        }
    except ImportError:
        elapsed = round((time.monotonic() - start) * 1000, 1)
        return {"status": "warn", "message": "psutil not installed — memory check skipped",
                "latency_ms": elapsed}
    except Exception as exc:
        elapsed = round((time.monotonic() - start) * 1000, 1)
        return {"status": "warn", "message": str(exc), "latency_ms": elapsed}


def check_disk_io() -> dict:
    """Check if the output directory is on a healthy filesystem.

    Performs a small write + read + delete probe to verify the underlying
    filesystem responds within a reasonable deadline (5 seconds).

    【中文】检查磁盘 I/O 是否正常。
    方法：写一个小文件 → 读回来对比 → 删掉。
    · 读回来的内容和写入的不一致 → error（文件系统可能损坏）
    · 整个过程超过 5 秒 → warn（磁盘太慢，可能影响性能）
    · 其他 → ok
    """
    start = time.monotonic()
    try:
        probe_file = OUTPUT_DIR / ".disk_io_probe"
        # 【中文】用随机数据做探测，避免被文件系统缓存优化掉。
        data = b"health-check:" + os.urandom(64).hex().encode()
        probe_file.write_bytes(data)
        read_back = probe_file.read_bytes()
        probe_file.unlink()
        elapsed = round((time.monotonic() - start) * 1000, 1)
        if read_back != data:
            return {
                "status": "error",
                "message": "Read-back mismatch — possible filesystem corruption",
                "latency_ms": elapsed,
                "critical": True,
            }
        if elapsed > 5000:
            return {
                "status": "warn",
                "message": f"Disk I/O slow ({elapsed}ms) but functional",
                "latency_ms": elapsed,
            }
        return {"status": "ok", "message": f"Disk I/O healthy ({elapsed:.1f} ms)",
                "latency_ms": elapsed}
    except Exception as exc:
        elapsed = round((time.monotonic() - start) * 1000, 1)
        return {"status": "error", "message": str(exc), "latency_ms": elapsed, "critical": True}

"""Entry point — single FastAPI app with Web UI, API routes, auth, and logging.

【中文·这个文件是干什么的】
这是整个西渡项目的**总开关**。在终端敲 `python3.11 -m src.main` 时，
Python 就从这里开始跑，最后启动一个网址服务（http://localhost:8000），
你在浏览器打开这个地址，看到的就是西渡的网页界面。

【中文·它把哪些零件拼在一起】
一个「网址服务」（FastAPI 应用）需要三类东西才能对外工作，本文件负责把它们组装起来：
  1. 网页 —— 首页、术语审核页、段落编辑器、数据看板、用量分析页（共 5 个页面）
  2. 接口 —— 供网页和外部程序调用的 /api/* 数据通道
  3. 门禁 —— 身份校验、跨域放行、限流（防止被刷爆）

【中文·启动时会发生什么（按顺序）】
  ① 先做「开机自检」：检查数据库、缓存、密钥是否就绪。
     自检判定为"不健康"就**直接拒绝启动**并抛出错误——宁可起不来，
     也不要带着故障跑起来，因为那样会在翻译到一半时才崩，更难排查。
  ② 装门禁（跨域 → 身份校验 → 限流）。
  ③ 注册 5 个网页路由和各个接口模块。
  ④ 把服务跑起来，开始监听端口。

【中文·注意】本文件只做"组装"，不含任何翻译业务逻辑。
真正的翻译流程在 src/agent/graph.py（流水线总装）里。
"""

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

# ── 下面这些 import 就是在"取零件" ──
# 【中文】每一个 from ... import ... 都是从项目其他文件里拿来一个现成组件，
# 本文件只负责把它们摆到正确的位置上。看名字就能猜出用途：
from .api.auth import APIKeyMiddleware  # 门禁：校验请求带的 API 密钥
from .api.editor import app as editor_api_router  # 接口：段落编辑器（人工逐段改稿）
from .api.logging import logger  # 工具：统一写日志
from .api.review import app as review_api_router  # 接口：术语审核（人工确认译名）
from .api.routes import app as api_router  # 接口：主力接口（提交翻译任务等）
from .config import API_PORT, CORS_ORIGINS, HEALTH_LIVE_LLM_PROBE, HOST, VERSION
from .dashboard import DASHBOARD_PAGE, get_dashboard_data  # 页面：运行状态看板
from .editor_ui import EDITOR_PAGE  # 页面：段落编辑器界面
from .health import HealthChecker  # 开机自检器
from .usage_ui import USAGE_PAGE, get_usage_data  # 页面：用量与错误分析
from .web_ui import PAGE, REVIEW_PAGE  # 页面：首页 + 术语审核页


def create_app() -> FastAPI:
    """Assemble the full application.

    【中文】把上面 import 进来的所有零件，组装成一个可以对外提供服务的完整应用。
    这是本文件唯一的主函数，返回的对象就是"西渡服务"本身。

    【中文·为什么要包成函数，而不是直接写在文件顶层】
    因为测试代码也要建一个同样的应用来测接口。包成函数后，
    测试和生产走的是**完全相同的组装流程**，测出来的结果才可信；
    如果各写各的，测试通过也不代表真实启动没问题。
    """
    app = FastAPI(
        title="Westward Echo",
        description="AI-powered web novel translation with cultural adaptation",
        version=VERSION,
    )

    # ── Startup health checks ──
    # The live LLM probe costs money, so it is opt-in via HEALTH_LIVE_LLM_PROBE.
    # Default False keeps create_app() free of billable calls — tests build the
    # app through this same factory.
    #
    # 【中文·开机自检】启动前先把系统依赖挨个查一遍（数据库能不能连、密钥有没有配等）。
    # 这里有个花钱的坑：真去问一次大模型"你还活着吗"是要计费的最小调用。
    # 所以默认**不**做这个真实探测（HEALTH_LIVE_LLM_PROBE=false），
    # 只有你明确打开开关时才会花钱。默认关闭还有个附带好处：
    # 测试也走这个函数建应用，于是跑一遍测试一分钱不花。
    health = HealthChecker(live_llm=HEALTH_LIVE_LLM_PROBE)
    report = health.check_all()   # 逐项检查，返回一份体检报告

    # 【中文】体检结果分三档，处理方式完全不同：
    if report["status"] == "unhealthy":
        # 「不健康」= 有致命问题（比如数据库连不上）→ 拒绝启动。
        # 为什么宁可不启动：如果带病启动，服务表面正常，
        # 等翻译跑到第 300 章才崩，前面几小时的进度和钱就白费了。
        # 在门口拦住，问题当场暴露，排查成本最低。
        msg = f"Startup health check FAILED: {report['status']}"
        logger.critical(msg)
        # Log each failing check for diagnostics
        # 【中文】把每一项失败原因都打进日志，方便你一眼看出到底哪儿坏了，
        # 而不是只看到一句笼统的"启动失败"。
        for name, check in report["checks"].items():
            if check["status"] == "error":
                logger.critical("  %s: %s", name, check["message"])
        raise RuntimeError(f"System unhealthy — refusing to start. {report}")

    if report["status"] == "degraded":
        # 「降级」= 能用，但有部件不正常（比如某个可选服务没起）→ 记警告后照常启动。
        logger.warning("Startup health check DEGRADED")
        for name, check in report["checks"].items():
            if check["status"] in ("warn", "error"):
                logger.warning("  %s: %s", name, check["message"])
    elif report["status"] == "healthy":
        # 「健康」= 一切正常。
        logger.info("Startup health check HEALTHY")

    # ── 装门禁（中间件）──
    # 【中文·什么是"中间件"】可以理解为"安检通道"。
    # 每个进来的网络请求，在到达真正的业务代码之前，都要先依次穿过这几道关卡；
    # 任何一道不通过，请求就地被拦下，业务代码根本不会执行。
    app.add_middleware(
        CORSMiddleware,
        # 【中文·跨域放行】默认仅同源（本应用自己托管前端，无需跨域）。
        # 如需独立前端，在 .env 设 CORS_ORIGINS=https://frontend.example.com,...
        # 逗号分隔域名白名单。allow_credentials 关闭：鉴权走 X-API-Key 头，无需 Cookie。
        allow_origins=CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # 【中文·密钥门禁】检查请求是否带了正确的 API_KEY。
    # 如果 .env 里 API_KEY 留空，这道门禁等于关闭（本地开发模式），谁都能调。
    app.add_middleware(APIKeyMiddleware)

    # Rate limiting (token-bucket, configurable via RATE_LIMIT_RPM / RATE_LIMIT_ENABLED)
    # 【中文·限流】用"令牌桶"算法限制每分钟最多接受多少个请求（默认 30 个）。
    # 作用：防止有人（或某个写错的脚本）疯狂刷接口，把大模型账单刷爆、把服务压垮。
    # 超额的请求会被直接回绝，而不是排队等着烧钱。
    # 包在 try 里是因为这个模块是后来才加的，万一缺失也不能让整个服务起不来。
    try:
        from .api.rate_limit import RateLimitMiddleware
        app.add_middleware(RateLimitMiddleware)
    except ImportError:
        logger.warning("rate_limit module unavailable — running without rate limiting")

    from pathlib import Path

    # ── 注册网页路由 ──
    # 【中文·什么是"路由"】就是"网址 → 返回什么内容"的对照表。
    # 下面每个 @app.get("...") 都在表里加一行：访问这个地址，就返回这个页面。
    # 你在浏览器里能看到的所有页面，都在这里定义。

    # Anchor-v5 as the main page (v4 kept as fallback)
    # 【中文·首页】优先返回新版落地页 anchor-v5.html；
    # 文件不存在就退回旧版 anchor-v4.html；再没有就用代码里内置的 PAGE 兜底。
    # 三层降级是为了保证首页永远打得开，不会白屏。
    @app.get("/", response_class=HTMLResponse)
    async def index():
        docs_dir = Path(__file__).resolve().parent.parent / "docs"
        landing = docs_dir / "anchor-v5.html"
        if landing.exists():
            return landing.read_text(encoding="utf-8")
        legacy = docs_dir / "anchor-v4.html"
        if legacy.exists():
            return legacy.read_text(encoding="utf-8")
        return PAGE

    # Glossary review page at /review
    # 【中文·术语审核页】人工确认"这个名字该译成什么"的地方。
    # 大模型翻出来的术语先落到这里，你点"确认"或"拒绝"，
    # 确认过的译名之后会锁进术语库，全书统一，不再翻来覆去变。
    @app.get("/review", response_class=HTMLResponse)
    async def review_page():
        return REVIEW_PAGE

    # Editor page — human-in-the-loop paragraph editing
    # 【中文·段落编辑器】"人在环中"的逐段改稿界面，地址形如 /editor/某任务号。
    # 大模型翻完的稿子可以在这里一段一段人工润色，改动会被记录下来。
    # 注意 URL 里的 {job_id} 是占位符，下面用字符串替换填进真实任务号。
    @app.get("/editor/{job_id}", response_class=HTMLResponse)
    async def editor_page(job_id: str):
        return EDITOR_PAGE.replace("{JOB_ID}", job_id)

    # Dashboard — observability page (no auth)
    # 【中文·运行看板】实时显示翻译进度、花费、错误等运行指标。
    # 特意不设身份校验（no auth），方便随时开着看，不用每次登录。
    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard():
        return DASHBOARD_PAGE

    @app.get("/api/dashboard/metrics")
    async def dashboard_metrics():
        """JSON endpoint powering the dashboard's auto-refresh.

        【中文】看板页面每隔几秒自动刷新，就是来调这个地址取最新数据（JSON 格式）。
        页面负责"显示"，这里负责"给数据"，两者分开。
        """
        return get_dashboard_data()

    # Usage analytics page — error tracking & editor behavior
    # 【中文·用量分析页】统计报错情况和人工编辑行为，用来发现"哪类章节老出错"。
    @app.get("/usage", response_class=HTMLResponse)
    async def usage_page():
        return USAGE_PAGE

    @app.get("/api/usage/events")
    async def usage_events_api():
        """JSON endpoint powering the usage page's auto-refresh.

        【中文】同上，给用量分析页提供自动刷新所需的数据。
        """
        return get_usage_data()

    # CMS API
    # 【中文·CMS 接口】对接内容管理系统的通道：从那边取小说原文、把成品送回去。
    from .api.cms import app as cms_api

    # Mount order is load-bearing: Starlette matches mounts in registration
    # order, so the catch-all "/api" must come LAST. Registering it first —
    # as this did — swallowed every more specific sub-app beneath it, so
    # /api/review/* and /api/editor/* returned 404 and both UIs were dead:
    # the review page's three fetch calls (web_ui.py:1837/1886/1897) never
    # reached the server. That is why `status='confirmed'` matched zero rows in
    # the real checkpoints.db — the human curation loop had never run at all,
    # not merely been scoped to the wrong book.
    #
    # The directly-registered /api/dashboard/metrics and /api/usage/events
    # above are unaffected: plain routes registered earlier still win over a
    # later mount, which is why /api/cms (mounted before /api) always worked.
    #
    # 【中文·⚠️ 下面这几行的顺序不能调换，这是踩过坑的】
    # 框架是按"注册先后"来匹配网址的，谁先登记谁先被检查。
    # "/api" 是个通配前缀，能匹配所有以 /api 开头的地址。
    # 如果把它排在最前面，它就会把后面那些更具体的地址（/api/review、
    # /api/editor）全部"吞掉"——请求还没走到对应模块就被它截胡，结果是 404。
    #
    # 这个 bug 曾经真实发生过，而且极难发现：术语审核页看起来正常打开，
    # 但页面上那三个数据请求全部悄悄失败，于是人工审核功能**从来没真正生效过**
    # ——数据库里"已确认"的术语一条都没有。表面无报错，实际整条链路是断的。
    #
    # 所以规则是：越具体的地址排越前，通配的 "/api" 必须放在最后。
    app.mount("/api/cms", cms_api)
    app.mount("/api/review", review_api_router)
    app.mount("/api/editor", editor_api_router)
    app.mount("/api", api_router)   # ← 通配的必须留在最后

    logger.info(
        f"Westward Echo v{VERSION} (Celery + Redis + Auth + Logging + Health + CMS + Editor)"
    )

    return app


# 【中文】这一行很关键：文件被加载时就立刻组装出应用对象。
# 这样 uvicorn（网络服务器）才能直接用 `src.main:app` 找到它，
# 而不需要再手动调用一次 create_app()。
app = create_app()


def main():
    """【中文】真正的启动动作：把上面组装好的应用交给网络服务器跑起来。

    host 决定"谁能访问"：0.0.0.0 表示本机所有网卡都监听（局域网内其他设备也能连），
    127.0.0.1 则只有本机能连。port 是端口号，默认 8000。
    两者都可以在 .env 里改，不用动代码。
    """
    uvicorn.run(app, host=HOST, port=API_PORT)


if __name__ == "__main__":
    # 【中文】只有"直接运行这个文件"时才会执行 main()。
    # 如果是被别的代码 import 进去用（比如测试），就只拿 app 对象，不启动服务。
    main()

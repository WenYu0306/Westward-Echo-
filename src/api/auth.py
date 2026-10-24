"""API key authentication middleware for FastAPI.

【中文·这个文件是干什么的】
这是西渡服务的"门禁系统"。每个进来的网络请求，在到达真正的业务代码之前，
都要先穿过这道关卡——检查请求是否带了正确的 API 密钥（API Key）。
没带或带错的请求会被直接拦下，返回 401（未授权）状态码。

【中文·它在整个流程里的位置】
被 src/main.py 的 create_app() 函数注册为中间件（middleware）。
所谓"中间件"就是"安检通道"，每个请求进业务代码前都要依次穿过几道关卡，
本文件是其中负责"验身份"的那一道。

【中文·核心概念解释】
  · API Key —— 一串预设的密码字符串，调用方放在请求头 X-API-Key 里传过来。
    相当于进门要刷的门禁卡，有卡才能进。
  · HTTP 401 —— "未授权"状态码，意思是"你没证明自己是谁，我不让你进"。
  · 开发模式 —— 如果 .env 配置文件里 API_KEY 留空，这道门禁等于关闭，
    谁都能调。方便本机调试，但公开部署时必须设一个强密钥。

【中文·哪些地址不需要门禁】
首页 /、健康检查 /health 和 /api/health、接口文档 /docs 和 /openapi.json
这五个地址永远开放，不需要密钥。因为健康检查看板需要随时访问，
接口文档是给开发者看的，首页是给人打开的。
"""

import hmac

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..config import API_KEY


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Validate X-API-Key header on protected routes.

    If API_KEY is empty in config, auth is disabled (dev mode).
    Only the root page, /health, /api/health, /docs, and /openapi.json
    are always open. Everything else requires authentication when
    API_KEY is set.

    【中文】身份校验中间件类。
    对外提供一件事：拦截每个请求，检查它有没有带正确的 API 密钥。
    · 如果配置里 API_KEY 为空 → 跳过校验（开发模式），所有请求放行。
    · 如果请求的地址在白名单 OPEN_PATHS 里 → 跳过校验，直接放行。
    · 其他情况 → 检查请求头 X-API-Key，不匹配就返回 401 拒绝。
    """

    # Only these exact paths are open in production
    # 【中文】白名单：这几个地址永远不需要密钥就能访问。
    # 用 frozenset（冻结集合）而不是 list，是因为查找速度更快（O(1)），
    # 而且不会被意外修改。
    OPEN_PATHS = frozenset({"/", "/health", "/api/health", "/docs", "/openapi.json"})

    async def dispatch(self, request: Request, call_next):
        """Process each incoming request through the auth check.

        【中文】这是中间件的核心方法，每个请求进来都会执行一次。
        参数：
          · request —— 当前进来的网络请求对象，包含网址、请求头等信息。
          · call_next —— "放行"函数，调用它表示"这道关卡通过了，交给下一道"。
        返回：要么放行后的响应，要么是 401 拒绝响应。
        """
        path = request.url.path

        # Open paths — no auth needed
        # 【中文】如果请求的地址在白名单里，直接放行，不做任何校验。
        if path in self.OPEN_PATHS:
            return await call_next(request)

        # Skip auth if not configured (dev mode)
        # 【中文】如果 .env 里没配 API_KEY（空字符串），说明是开发模式，
        # 跳过校验让所有请求通过。⚠️ 公开部署时必须设置 API_KEY，
        # 否则任何人都能调用你的翻译接口，可能产生大量费用。
        if not API_KEY:
            return await call_next(request)

        # Everything else requires API key
        # 【中文】从请求头里取出调用方传来的密钥，和服务端预设的比对。
        # 用 hmac.compare_digest 做恒定时间比较，防止时序攻击（timing attack）。
        client_key = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(client_key, API_KEY):
            # 【中文】密钥不对或没带 → 返回 HTTP 401（未授权）。
            # 401 的含义是"你没有证明自己有权访问"，
            # 区别于 403（"我认识你但不允许你做这件事"）。
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Invalid or missing API key. Provide X-API-Key header."},
            )

        # 【中文】密钥匹配 → 放行，把请求交给后续的中间件或业务代码处理。
        return await call_next(request)

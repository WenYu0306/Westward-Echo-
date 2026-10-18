"""Token-bucket rate limiter middleware for translation API endpoints.

【中文·这个文件是干什么的】
这是西渡服务的"限流系统"。它用一种叫"令牌桶"的算法，限制每分钟最多接受
多少个请求（默认 30 个）。作用是防止有人（或某个写错的脚本）疯狂刷接口，
把大模型账单刷爆、把服务压垮。

【中文·它在整个流程里的位置】
被 src/main.py 的 create_app() 注册为中间件（middleware），
排在身份校验中间件之后。每个修改类请求（POST/PUT/DELETE/PATCH）进来时，
都要先从令牌桶里拿一个"号码牌"，拿到才能继续；拿不到就被拒绝，
返回 HTTP 429（请求过多）状态码。

【中文·什么是"令牌桶"】
想象门口有个发号员，每分钟固定发出 N 张号码牌（N = RATE_LIMIT_RPM）。
  · 每张牌只能用一次，用完就没了。
  · 牌发完了，后来的人就得等下一批。
  · 如果一段时间没人来，牌会攒起来，但最多攒 N 张（不会无限堆积）。
这样既保证了平均速率不超过 N/分钟，又允许短时间内的突发流量。

【中文·两种使用模式】
  ① HTTP 中间件模式（RateLimitMiddleware）：对外部请求生效，
     超额直接返回 429 拒绝，让调用方自己决定什么时候重试。
  ② 内部自节流模式（wait_if_needed）：翻译流水线内部调用大模型时用，
     没牌了就等一下再试，而不是直接报错。这样翻译不会因为偶尔超速而中断。

【中文·配置方式】
通过 .env 环境变量控制：
  · RATE_LIMIT_RPM —— 每分钟允许的请求数，默认 30。
  · RATE_LIMIT_ENABLED —— 是否启用限流，默认 true。设为 false 可关闭。
"""

import threading
import time
from typing import Optional

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..config import RATE_LIMIT_ENABLED, RATE_LIMIT_RPM
from .logging import logger


class TokenBucket:
    """Thread-safe token bucket rate limiter.

    Tokens are replenished continuously at the configured rate.

    【中文】令牌桶类：限流的核心数据结构。
    它是线程安全的（thread-safe），意味着多个请求同时来抢牌也不会出错。
    对外提供三件事：
      · consume()           —— 尝试拿一张牌，返回 True/False。
      · wait_if_needed()    —— 没牌就等一会儿再试（内部自节流用）。
      · remaining           —— 当前还剩多少张牌（信息展示用）。
      · seconds_until_next_token —— 预计还要等多久才有下一张牌。
    """

    def __init__(self, rate: int, capacity: Optional[int] = None):
        """Initialize the token bucket.

        【中文】初始化令牌桶。
        参数：
          · rate     —— 每分钟补充多少张牌（即允许的请求速率）。
          · capacity —— 桶的最大容量（最多攒多少张牌）。
                       不传的话默认等于 rate，即最多攒一分钟的量。
        """
        self._rate = rate  # tokens per minute
        # 【中文】capacity 决定桶的上限。如果调用方没指定，就用 rate 作为上限，
        # 意思是"最多攒一分钟的牌"，避免长时间空闲后突然涌入大量请求。
        self._capacity = capacity if capacity is not None else rate
        # 【中文】初始时桶是满的，给服务刚启动时的第一批请求留足余量。
        self._tokens: float = float(self._capacity)
        # 【中文】记录上次补牌的时间点。time.monotonic() 是单调时钟，
        # 不受系统时间调整影响（比如用户改了电脑时间也不会算错）。
        self._last_refill: float = time.monotonic()
        # 【中文】线程锁：保证同一时刻只有一个线程在修改桶的状态，
        # 防止两个请求同时拿到同一张牌。
        self._lock = threading.Lock()

    def consume(self, now: Optional[float] = None) -> bool:
        """Attempt to consume 1 token. Returns True if allowed, False if denied.

        【中文】尝试消耗一张牌。
        参数：
          · now —— 当前时间（可选），不传就用当前真实时间。
                   传入是为了测试时可以模拟时间流逝。
        返回：True = 拿到了牌，可以继续；False = 没牌了，被拒绝。

        【中文·内部逻辑】
        ① 先根据距离上次补牌的时间差，计算这段时间该补多少张牌。
        ② 把新补的牌加到桶里（但不超过上限）。
        ③ 如果桶里至少有 1 张牌，扣掉 1 张并返回 True。
        ④ 否则返回 False。
        """
        if now is None:
            now = time.monotonic()

        with self._lock:
            # Refill tokens based on elapsed time
            # 【中文】计算自上次补牌以来过了多少秒，按比例补牌。
            # 比如 rate=30（每分钟30张），过了2秒就补 30*(2/60)=1 张。
            elapsed = now - self._last_refill
            refill_amount = elapsed * (self._rate / 60.0)  # tokens per second
            # 【中文】min 确保补牌后不超过桶的上限，多余的牌丢弃。
            self._tokens = min(self._capacity, self._tokens + refill_amount)
            self._last_refill = now

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    def wait_if_needed(self, max_wait_seconds: float = 5.0) -> float:
        """If rate limited, block until a token is available (up to max_wait).

        This is the "soft" mode used internally by the translation loop to
        self-throttle.  The HTTP middleware still returns 429 for external
        clients.

        Returns actual wait time in seconds.  Returns 0 if a token was
        immediately available.

        【中文】"软限流"模式：没牌就等，等到有牌或超时为止。
        这是翻译流水线内部用的——调大模型时发现超速了，就暂停一下再试，
        而不是直接报错中断翻译。

        参数：
          · max_wait_seconds —— 最多等多久（秒），默认 5 秒。
                                超过这个时间还没牌就放弃等待。
        返回：实际等待了多少秒。如果立刻就有牌，返回 0。

        【中文·为什么不用 seconds_until_next_token 来计算等待时间】
        因为那个方法内部也要获取锁（self._lock），而这里已经在循环里了，
        如果在持锁状态下再调它会死锁（reentrant lock issue）。
        所以这里直接在锁内计算等待时间，避免重复加锁。
        """
        start = time.monotonic()
        deadline = start + max_wait_seconds

        while True:
            now = time.monotonic()
            # 【中文】每次循环先试着拿一张牌，拿到了就直接返回。
            if self.consume(now):
                return now - start

            # Calculate how long until next token is available
            # 【中文】看看离超时还有多久，超时就退出循环。
            remaining = deadline - now
            if remaining <= 0:
                break

            # Calculate required wait directly (avoid calling seconds_until_next_token
            # which acquires the lock, to prevent reentrant lock issues)
            # 【中文】在锁内直接计算还需要等多久才有下一张牌。
            # deficit = 还差多少张牌才够 1 张。
            # wait = 按补牌速率算出补齐这些牌需要多少秒。
            with self._lock:
                deficit = 1.0 - self._tokens
                wait = deficit / (self._rate / 60.0) if deficit > 0 else 0.0
            # Sleep for a fraction of the wait (or remaining time), whichever is smaller
            # 【中文】取三者最小值作为本次休眠时长：
            #   · wait      —— 理论上有牌需要等的时间
            #   · remaining —— 离超时还剩的时间
            #   · 0.5       —— 单次最多睡半秒，避免睡太久错过补牌时机
            sleep_time = min(wait, remaining, 0.5)
            if sleep_time > 0:
                time.sleep(sleep_time)

        # Timeout — one last try
        # 【中文】超时后再做最后一次尝试，万一刚好有牌了呢。
        if self.consume():
            return time.monotonic() - start
        # 【中文】实在没牌了，返回最大等待时间表示"我等满了但还是没拿到"。
        return max_wait_seconds

    @property
    def remaining(self) -> int:
        """Approximate remaining tokens (for informational headers).

        【中文】当前桶里大约还剩多少张牌（取整数）。
        用在响应头 X-RateLimit-Remaining 里，告诉调用方"你还能调几次"。
        注意这是近似值，因为在你读到这个数字的瞬间，可能已经有新牌补进来了。
        """
        with self._lock:
            return max(0, int(self._tokens))

    @property
    def seconds_until_next_token(self) -> float:
        """Estimated time in seconds until a token is available.

        【中文】预计还要等多少秒才有下一张牌可用。
        如果桶里已经有牌，返回 0。
        用在响应头 X-RateLimit-Reset 和 429 响应的 retry_after 里，
        告诉调用方"请过 N 秒再来"。
        """
        with self._lock:
            if self._tokens >= 1.0:
                return 0.0
            deficit = 1.0 - self._tokens
            # 【中文】deficit / (rate/60) = 还差的牌数 ÷ 每秒补牌数 = 需要等的秒数。
            return deficit / (self._rate / 60.0)


# Singleton bucket shared across all workers (thread-safe for single-process).
# 【中文】全局唯一的令牌桶实例。所有请求共用同一个桶，
# 这样才能准确控制整体速率。如果用多个桶，每个各管各的，限流就失效了。
_bucket: Optional[TokenBucket] = None
# 【中文】保护 _bucket 初始化的锁。双重检查锁定（double-checked locking）：
# 外层检查避免每次都加锁的性能开销，内层检查防止两个线程同时创建桶。
_bucket_lock = threading.Lock()


def _get_bucket() -> TokenBucket:
    """Lazy-init the shared token bucket.

    【中文】懒加载获取全局令牌桶。第一次调用时才创建，后续复用同一个。
    用双重检查锁定模式：先不加锁检查 _bucket 是否为 None，
    只有为 None 时才加锁再次检查并创建。这样正常路径上没有锁开销。
    """
    global _bucket
    if _bucket is None:
        with _bucket_lock:
            if _bucket is None:
                # 【中文】rate 和 capacity 都从配置读取，
                # 改 .env 里的 RATE_LIMIT_RPM 就能调整限流阈值。
                _bucket = TokenBucket(rate=RATE_LIMIT_RPM, capacity=RATE_LIMIT_RPM)
    return _bucket


# 【中文】只读方法的集合。这些方法不会对服务器产生副作用，
# 所以不限流——查进度、看健康状态这些操作随便调没关系。
# 用 frozenset 是因为查找速度 O(1) 且不可变。
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Token-bucket rate limiter for translation API endpoints.

    Only rate-limits mutating endpoints (POST, PUT, DELETE, PATCH). Read endpoints
    (GET /health, GET /, GET /api/translate/{job_id}) pass through unthrottled.

    Configurable via env vars:
    - RATE_LIMIT_RPM: max requests per minute (default: 30)
    - RATE_LIMIT_ENABLED: "true" or "false" (default: true)

    【中文】限流中间件类。
    只对"会改变服务器状态"的请求（POST/PUT/DELETE/PATCH）限流，
    纯查询请求（GET/HEAD/OPTIONS）直接放行。
    理由：查进度、看看板这些操作不花钱也不占资源，没必要限制；
    真正花钱的是提交翻译任务（POST），那才需要控速。

    【中文·被拒绝时的行为】
    返回 HTTP 429（Too Many Requests），附带：
      · 响应体里的 retry_after_seconds —— 建议调用方等多少秒再试。
      · 响应头 X-RateLimit-Remaining: 0 —— 当前剩余牌数为零。
      · 响应头 X-RateLimit-Reset —— 预计恢复时间（秒）。
    调用方看到 429 就应该停下来等一等，不要立刻重试。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        """Check rate limit before passing the request to business logic.

        【中文】每个请求进来时执行的限流检查。
        流程：
          ① 如果限流被关闭（RATE_LIMIT_ENABLED=false）→ 直接放行。
          ② 如果是只读请求（GET/HEAD/OPTIONS）→ 直接放行。
          ③ 尝试从令牌桶拿牌 → 拿到就放行，拿不到就返回 429。
          ④ 放行后，在响应头里附上剩余牌数和恢复时间，方便调用方感知。
        """
        # 【中文】限流开关关闭或只读请求 → 不做任何限制，直接交给后续处理。
        if not RATE_LIMIT_ENABLED or request.method in _READ_METHODS:
            return await call_next(request)

        bucket = _get_bucket()
        now = time.monotonic()

        if not bucket.consume(now):
            # 【中文】没拿到牌 → 计算建议等待时间并返回 429。
            # max(1, ...) 确保至少让调用方等 1 秒，避免出现 retry_after=0
            # 导致调用方立刻重试形成死循环。
            retry_after = max(1, int(bucket.seconds_until_next_token) + 1)
            logger.warning(
                "Rate limit hit — %s %s (retry after %ds, remaining=%d)",
                request.method,
                request.url.path,
                retry_after,
                bucket.remaining,
            )
            # 【中文】HTTP 429 = "Too Many Requests"，意思是"你调得太频繁了，
            # 请稍后再试"。这是标准的限流响应码，所有 HTTP 客户端都认识。
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limit_exceeded",
                    "retry_after_seconds": retry_after,
                },
                headers={
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(retry_after),
                },
            )

        # 【中文】拿到了牌 → 放行请求，让业务代码处理。
        response = await call_next(request)

        # Attach rate-limit headers to the response
        # 【中文】在响应头里附上限流信息，让调用方知道当前的配额状态：
        #   · X-RateLimit-Remaining —— 还剩多少张牌
        #   · X-RateLimit-Reset     —— 下一张牌预计多少秒后可用
        # 这是业界惯例（GitHub、Twitter 等 API 都用这套头），
        # 调用方的 SDK 可以自动读取这些信息来做智能重试。
        response.headers["X-RateLimit-Remaining"] = str(bucket.remaining)
        response.headers["X-RateLimit-Reset"] = str(
            max(0, int(bucket.seconds_until_next_token))
        )

        return response

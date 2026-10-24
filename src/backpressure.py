"""Backpressure guard — reject new work when the queue is too deep.

Prevents overload by returning HTTP 503 before the system accepts work it
cannot reasonably complete.

【中文·这个文件是干什么的】
这是系统的"水管阀门"。想象家里的下水管已经堵满了水，这时候你不应该
再往里面灌水——否则只会让所有水都溢出来，一滴也排不走。本模块做的事
情完全一样：当后台翻译队列里积压的任务太多时，**拒绝接受新任务**，
而不是硬接下来让所有任务一起变慢甚至全部超时。

【中文·它防止什么坏事发生】
如果不设这道阀门，用户可以一口气提交几千章的翻译请求。每章翻译需要
调用大模型 API（要花钱、要等响应），队列越积越长，前面的任务还没跑完，
后面的就已经超时失败了。更糟的是，大模型账单会一直涨，但产出的成品
却越来越少——钱花了，活没干完。

【中文·它在整个流程里的位置】
网页接口（src/api/routes.py）收到翻译请求后，先问这里的
``backpressure.try_accept()``："还能接吗？"能接才把任务丢进 Celery
后台队列；不能接就直接返回 HTTP 503（"服务繁忙，请稍后再试"）。
任务完成后调 ``release()`` 把名额还回来。

【中文·核心概念：背压（backpressure）】
"背压"这个词来自流体力学：下游管道压力太高时，上游必须减压或停泵，
否则管道会爆。在软件里就是"消费者忙不过来时，生产者别再喂了"。
这里的"消费者"是 Celery 后台工人，"生产者"是网页接口。
"""

import threading
from typing import Optional

try:
    import redis as _redis
except ImportError:  # pragma: no cover — redis 是 requirements.txt 已声明的依赖
    _redis = None

# 【中文】Redis 原子脚本：try_accept 的「检查上限→自增」与 release 的「>0 才减」。
# 用 Lua 保证两步在 Redis 服务端原子执行，避免并发请求把计数顶穿上限。
_ACCEPT_LUA = """
local cur = redis.call('GET', KEYS[1])
if cur and tonumber(cur) >= tonumber(ARGV[1]) then
    return 0
end
redis.call('INCR', KEYS[1])
return 1
"""

_RELEASE_LUA = """
local cur = redis.call('GET', KEYS[1])
if cur and tonumber(cur) > 0 then
    redis.call('DECR', KEYS[1])
end
return 1
"""


class BackpressureGuard:
    """Prevent overload by rejecting new work when the queue is too deep.

    【中文·这个类做什么】
    一个线程安全的计数器 + 上限检查器。每次有新任务进来，先问
    ``try_accept()``；任务做完（无论成功失败）调 ``release()`` 归还名额。
    当前排队数达到上限时，``try_accept()`` 返回 False，调用方就知道该拒绝了。

    Example::

        guard = BackpressureGuard(max_queue_depth=100)
        if not guard.try_accept():
            return HTTP_503  # "Service overloaded, try again later"
        try:
            do_work()
        finally:
            guard.release()
    """

    def __init__(self, max_queue_depth: int = 100, redis_key: Optional[str] = None):
        """【中文】初始化背压守卫。

        参数：
          · max_queue_depth：队列深度上限，默认 100。意思是同时最多允许
            100 个翻译任务在后台排队或执行中。超过这个数字，新请求会被拒绝。
            为什么是 100：这是实测出来的经验值——再多就会导致部分任务
            在大模型 API 侧超时，反而降低整体吞吐量。
        """
        self._max_queue_depth = max_queue_depth
        self._pending_count: int = 0
        self._lock = threading.Lock()
        # 【中文】跨进程共享计数器：redis_key 非空时用 Redis 计数（带进程内回退），
        # 否则退化为纯进程内计数（测试与无 Celery 的同步路径）。
        self._redis_key = redis_key
        self._redis = None
        self._redis_checked = False

    def _get_redis(self):
        """Return a cached Redis client, or None if unconfigured/unreachable.

        【中文】懒加载 Redis 连接，结果缓存。只在 redis_key 非空时尝试。
        连不上（测试环境无 Redis、或 Redis 宕机）就返回 None，调用方回退到
        进程内计数。ping 只发生一次，之后复用缓存，避免每次请求都探测。
        """
        if self._redis_key is None or _redis is None:
            return None
        if self._redis_checked:
            return self._redis
        self._redis_checked = True
        try:
            from .config import REDIS_URL
            client = _redis.Redis.from_url(
                REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5
            )
            client.ping()
            self._redis = client
        except Exception:
            self._redis = None
        return self._redis

    def try_accept(self) -> bool:
        """Return True if new work can be accepted. Increments pending count.

        Must be paired with a matching ``release()`` call when work completes,
        succeeds, or fails.

        【中文】尝试接受一个新任务。
        · 如果当前排队数 < 上限 → 计数 +1，返回 True（"可以接"）。
        · 如果已满 → 直接返回 False（"满了，别加了"），计数不变。
        ⚠️ 返回 True 之后，**必须**在任务结束时调 ``release()``，
        否则名额永远不会还回来，系统最终会卡在"永远满"的状态。
        """
        r = self._get_redis()
        if r is not None:
            try:
                return bool(r.eval(_ACCEPT_LUA, 1, self._redis_key, self._max_queue_depth))
            except Exception:
                pass  # Redis 出错 → 回退进程内计数
        with self._lock:
            if self._pending_count >= self._max_queue_depth:
                return False
            self._pending_count += 1
            return True

    def release(self):
        """Decrement pending count when work completes (success or failure).

        【中文】归还一个名额。任务做完了（不管成功还是失败）都要调一次。
        内部做了 >0 保护，多调一次也不会变成负数。
        """
        r = self._get_redis()
        if r is not None:
            try:
                r.eval(_RELEASE_LUA, 1, self._redis_key)
                return
            except Exception:
                pass  # Redis 出错 → 回退进程内计数
        with self._lock:
            if self._pending_count > 0:
                self._pending_count -= 1

    def is_backpressured(self) -> bool:
        """True when the queue is at or above the threshold.

        【中文】查询当前是否处于"背压状态"（队列已满）。
        只读检查，不改变计数。供看板等监控界面显示用。
        """
        return self.queue_depth >= self._max_queue_depth

    @property
    def queue_depth(self) -> int:
        """Current number of in-flight / pending items.

        【中文】当前正在排队或执行中的任务数量。
        """
        r = self._get_redis()
        if r is not None:
            try:
                val = r.get(self._redis_key)
                return int(val) if val is not None else 0
            except Exception:
                pass  # Redis 出错 → 回退进程内计数
        with self._lock:
            return self._pending_count

    @property
    def max_queue_depth(self) -> int:
        """【中文】队列深度上限（构造时传入的值）。"""
        return self._max_queue_depth

    def reset(self):
        """Reset the pending count to zero. For use in test teardown only.

        【中文】把计数清零。**仅供测试用**。
        生产环境不应该调这个——如果运行中清零，等于把所有"占着的名额"
        都丢了，可能导致实际并发远超上限。
        """
        r = self._get_redis()
        if r is not None:
            try:
                r.delete(self._redis_key)
            except Exception:
                pass  # Redis 出错 → 忽略，进程内计数也会清零
        with self._lock:
            self._pending_count = 0

    def snapshot(self) -> dict:
        """【中文】返回当前状态的快照字典，供看板 / 健康检查使用。

        返回字段：
          · queue_depth：当前排队数
          · max_queue_depth：上限
          · backpressured：是否已满（布尔值）
        """
        depth = self.queue_depth
        return {
            "queue_depth": depth,
            "max_queue_depth": self._max_queue_depth,
            "backpressured": depth >= self._max_queue_depth,
        }


# Module-level singleton
# 【中文】全模块唯一的实例，整个系统共用这一个守卫。
# max_queue_depth=100 与上面的默认值一致，写在这里是为了让"上限是多少"
# 只有一个地方能看到，改的时候不会漏。
backpressure = BackpressureGuard(max_queue_depth=100, redis_key="westward_echo:backpressure:pending")

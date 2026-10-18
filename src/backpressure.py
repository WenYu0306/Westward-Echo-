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

    def __init__(self, max_queue_depth: int = 100):
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
        with self._lock:
            if self._pending_count > 0:
                self._pending_count -= 1

    def is_backpressured(self) -> bool:
        """True when the queue is at or above the threshold.

        【中文】查询当前是否处于"背压状态"（队列已满）。
        只读检查，不改变计数。供看板等监控界面显示用。
        """
        with self._lock:
            return self._pending_count >= self._max_queue_depth

    @property
    def queue_depth(self) -> int:
        """Current number of in-flight / pending items.

        【中文】当前正在排队或执行中的任务数量。
        """
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
        with self._lock:
            self._pending_count = 0

    def snapshot(self) -> dict:
        """【中文】返回当前状态的快照字典，供看板 / 健康检查使用。

        返回字段：
          · queue_depth：当前排队数
          · max_queue_depth：上限
          · backpressured：是否已满（布尔值）
        """
        with self._lock:
            return {
                "queue_depth": self._pending_count,
                "max_queue_depth": self._max_queue_depth,
                "backpressured": self._pending_count >= self._max_queue_depth,
            }


# Module-level singleton
# 【中文】全模块唯一的实例，整个系统共用这一个守卫。
# max_queue_depth=100 与上面的默认值一致，写在这里是为了让"上限是多少"
# 只有一个地方能看到，改的时候不会漏。
backpressure = BackpressureGuard(max_queue_depth=100)

"""Circuit breaker for preventing cascading failures when the LLM API is unreliable.

States: CLOSED (normal) -> OPEN (tripped, fast-fail) -> HALF_OPEN (testing recovery)

One circuit breaker per language — if en-US fails, es-ES, de, and fr continue unaffected.

【中文·这个文件是干什么的】
这是系统的"空气开关"（断路器）。想象家里的电路：某条线路连续出故障，
空气开关就自动跳闸断电，防止继续烧钱和把整个系统拖垮。过一段时间后
试着合闸——如果故障还在就再跳，如果好了就恢复正常供电。

本模块做的事完全一样：当某个语言的大模型 API 连续失败时，自动"跳闸"，
后续对该语言的调用直接报错而不去调 API（省钱、省时间）。其他语言不受影响
——en-US 挂了，es-ES / de / fr 照常跑。过一段时间自动进入"试探"状态，
发一个请求试试，好了就恢复，不好就继续跳闸。

【中文·它防止什么坏事发生】
大模型 API 偶尔会抽风（限流、超时、服务器故障）。如果不设熔断器：
① 每个失败的请求都在白烧钱（API 返回错误但仍然计费）；
② 翻译流水线会卡在等待响应上，吞吐量暴跌；
③ 大量失败请求涌向已经不堪重负的 API，形成"雪崩效应"，
   把本来只是小问题的故障放大成全面瘫痪。

【中文·三种状态】
  CLOSED（闭合/正常）→ 一切正常，请求照发
  OPEN（断开/跳闸）→ 快速失败，不调 API，等恢复超时
  HALF_OPEN（半开/试探）→ 允许少量探测请求，成功则恢复，失败则重新跳闸

【中文·它在整个流程里的位置】
Celery 翻译任务在调用大模型前，先通过熔断器的 ``call()`` 方法。
如果熔断器处于 OPEN 状态，直接抛出 CircuitBreakerOpenError，
任务跳过该章节而不是傻等。每种语言有独立的熔断器实例，互不干扰。
"""

import threading
import time
from collections import deque
from typing import Callable


class CircuitBreakerOpenError(Exception):
    """Raised when a call is attempted while the circuit breaker is OPEN.

    【中文】熔断器处于 OPEN 状态时抛出的异常。
    调用方捕获这个异常就知道"该语言的 API 暂时不能用"，可以跳过或降级处理。
    """


class CircuitBreaker:
    """Prevent cascading failures when the LLM API is unreliable.

    The breaker trips after ``failure_threshold`` failures **within a rolling
    ``window_seconds``**, then fast-fails every call for ``recovery_timeout``
    seconds. After that it admits up to ``half_open_max`` probe calls
    (HALF_OPEN): a successful probe closes the circuit, a failed one re-opens it.

    Why a rolling window and not a consecutive-failure counter
    ----------------------------------------------------------
    This used to count *consecutive* failures and clear the counter on any
    success. That has a measured cost: an API failing sporadically — say 4
    failures per 5 calls, forever — never reaches the threshold, because each
    interleaved success wipes the count. The pipeline keeps paying a broken
    provider indefinitely, which is exactly what a circuit breaker exists to
    stop. Failures now age out of the window on their own; a success no longer
    erases them.

    A successful **HALF_OPEN probe** still clears the window (see
    ``record_success``) — otherwise recovery would be impossible, since the
    failures that caused the trip would still be in-window and the very next
    failure would re-trip immediately.

    Thread-safe via ``threading.Lock``. The lock is NOT reentrant, so code that
    already holds it must never touch the ``_failure_count`` property or the
    ``state`` property — use the internal ``_failure_times`` deque directly.

    【中文·这个类做什么】
    一个完整的熔断器实现。核心逻辑：
    ① 在滚动时间窗口内统计失败次数；
    ② 达到阈值就跳闸（CLOSED → OPEN）；
    ③ 等待恢复超时后进入试探状态（OPEN → HALF_OPEN）；
    ④ 试探成功就恢复（HALF_OPEN → CLOSED），失败就重新跳闸。

    【中文·为什么用"滚动窗口"而不是"连续失败计数"】
    旧版用的是"连续失败 N 次就跳闸，中间成功一次就清零"。问题是：
    如果 API 每 5 次调用里坏 4 次、好 1 次（永远如此），计数器永远到不了
    阈值——因为每次成功都把计数清零了。结果就是流水线一直在给一个
    半坏的 API 付钱，永远不跳闸。改成滚动窗口后，失败记录按时间自动过期，
    成功不再清除历史，这种"间歇性故障"也能被正确捕获。
    """

    # 【中文】三种状态的字符串常量。
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max: int = 1,
        window_seconds: float = 60.0,
    ):
        """
        Args:
            name: Human-readable name for this breaker (e.g. ``"en-US"``).
            failure_threshold: Failures within the rolling window needed to trip.
            recovery_timeout: Seconds to wait before transitioning OPEN -> HALF_OPEN.
            half_open_max: Probe calls admitted concurrently while HALF_OPEN.
                The N+1-th caller fast-fails instead of also hitting the API.
            window_seconds: Rolling window over which failures are counted.
                Old failures age out on their own; successes do not clear them.

        【中文】初始化熔断器。

        参数：
          · name：名字，通常是语言代码如 "en-US"
          · failure_threshold：窗口内失败几次就跳闸，默认 5 次
          · recovery_timeout：跳闸后等多久才尝试恢复，默认 30 秒
          · half_open_max：试探状态最多允许几个并发探测请求，默认 1 个。
            多余的探测请求直接快速失败，避免同时打多个请求给可疑的 API
          · window_seconds：滚动窗口的长度，默认 60 秒。
            超过这个时间的失败记录会自动被丢弃
        """
        self.name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max = half_open_max
        self._window_seconds = window_seconds

        self._lock = threading.Lock()
        self._state: str = self.CLOSED
        # Monotonic timestamps of failures still inside the window, oldest
        # first. Replaces a bare int counter: `len()` gives the in-window count,
        # and popleft() ages failures out instead of waiting for a success to
        # erase them. Bounded by pruning on every read/write, never by maxlen —
        # a maxlen would silently drop in-window failures.
        #
        # 【中文】用一个双端队列存失败的时间戳，代替简单的整数计数器。
        # 好处：len() 就是窗口内的失败数，popleft() 就能淘汰过期的记录。
        # ⚠️ 不用 deque(maxlen=N) 是因为 maxlen 会在满时静默丢弃最旧的元素，
        # 而那些元素可能还在窗口内——会导致少算失败次数，该跳闸时不跳。
        self._failure_times: deque = deque()
        self._opened_at: float = 0.0
        self._half_open_attempts: int = 0

        # History for observability
        # 【中文】以下四个计数器用于看板和诊断，不参与熔断判断逻辑。
        self._total_successes: int = 0
        self._total_failures: int = 0
        self._total_calls: int = 0
        self._open_transitions: int = 0

    # ── public API ──────────────────────────────────────────────

    def call(self, fn: Callable, *args, **kwargs):
        """Execute ``fn(*args, **kwargs)`` if the circuit allows it.

        Raises ``CircuitBreakerOpenError`` immediately if the circuit is OPEN,
        or if the circuit is HALF_OPEN and its ``half_open_max`` probe slots are
        already taken. Raises ``CircuitBreakerOpenError`` if the breaker opens
        *during* a HALF_OPEN probe failure.

        Returns the result of ``fn`` on success.

        【中文】通过熔断器执行一个函数调用。

        用法：``breaker.call(some_function, arg1, arg2)``
        · 如果熔断器 CLOSED → 正常执行 fn
        · 如果 OPEN 且未到恢复时间 → 立即抛 CircuitBreakerOpenError
        · 如果 OPEN 但到了恢复时间 → 切换到 HALF_OPEN，允许探测
        · 如果 HALF_OPEN 且探测名额已满 → 立即抛异常
        · fn 执行成功 → record_success()
        · fn 抛异常 → record_failure()，然后把异常继续往上抛

        ⚠️ fn 的执行在锁外面进行，避免持锁期间做耗时的网络 I/O。
        """
        with self._lock:
            self._total_calls += 1
            if self._state == self.OPEN:
                if self._should_attempt_recovery():
                    # 【中文】恢复超时已过，从 OPEN 切到 HALF_OPEN 开始试探。
                    self._state = self.HALF_OPEN
                    self._half_open_attempts = 0
                else:
                    # 【中文】还在冷却期，直接拒绝。
                    self._total_failures += 1
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker '{self.name}' is OPEN. "
                        f"{self._time_until_recovery():.0f}s until recovery attempt."
                    )

            if self._state == self.HALF_OPEN:
                # Probe throttling. _half_open_attempts counts *admitted*
                # probes, so the check happens before the increment: with
                # half_open_max=1 the first caller is admitted and every
                # concurrent caller fast-fails instead of also hitting a
                # provider we are actively suspecting. Without this, N threads
                # would all probe at once and the "max" was a config value
                # nobody read.
                #
                # 【中文】试探名额控制。half_open_max=1 时只允许一个线程去探测，
                # 其他线程直接快速失败。如果不限制，N 个线程会同时发探测请求，
                # 等于把"试探"变成了"轰炸"，对可疑的 API 雪上加霜。
                if self._half_open_attempts >= self._half_open_max:
                    self._total_failures += 1
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker '{self.name}' is HALF_OPEN and its "
                        f"{self._half_open_max} probe slot(s) are taken; "
                        f"fast-failing instead of piling onto a suspect API."
                    )
                self._half_open_attempts += 1

        # Execute the call outside the lock so we don't hold it during I/O.
        # 【中文】⚠️ 注意：fn 的执行在锁外面。如果在锁里面调 API，
        # 其他线程想记录成功/失败就得等这个 API 返回，整个熔断器变成串行瓶颈。
        try:
            result = fn(*args, **kwargs)
        except Exception:
            self.record_failure()
            raise

        self.record_success()
        return result

    def record_success(self) -> None:
        """Report a successful call — reset the circuit to CLOSED.

        Whether the failure window is cleared depends on *what* succeeded:

        * A success while CLOSED (ordinary traffic) does **not** clear the
          window. Sporadic failures interleaved with successes are the exact
          pattern a rolling window exists to catch; letting one success erase
          the history is what made such an API never trip and keep billing.
        * A success while HALF_OPEN/OPEN is a **recovery probe** proving the
          provider is back, so the window is cleared. Keeping those failures
          would re-trip on the next single failure and make recovery_timeout
          pointless.

        【中文】记录一次成功调用。
        · 如果在 CLOSED 状态下成功 → 不清除失败窗口。原因：间歇性故障
          （5 次里坏 4 次好 1 次）正是滚动窗口要抓的模式，一次成功就清零
          会让这种故障永远触发不了熔断。
        · 如果在 HALF_OPEN/OPEN 状态下成功 → 说明 API 恢复了，清除窗口。
          不清除的话，旧的失败记录还在窗口里，下一次随便一个失败就又跳闸了，
          recovery_timeout 就白等了。
        """
        with self._lock:
            recovering = self._state != self.CLOSED
            self._state = self.CLOSED
            self._half_open_attempts = 0
            self._total_successes += 1
            if recovering:
                self._failure_times.clear()

    def record_failure(self) -> None:
        """Report a failed call — may trip the breaker.

        【中文】记录一次失败调用，可能触发跳闸。
        · 如果在 HALF_OPEN 状态下失败 → 试探失败，立刻回到 OPEN
        · 如果在 CLOSED 状态下失败 → 检查窗口内失败数是否达到阈值，
          达到了就跳到 OPEN
        """
        with self._lock:
            now = time.monotonic()
            self._failure_times.append(now)
            self._prune_window(now)
            self._total_failures += 1

            if self._state == self.HALF_OPEN:
                # Probe failed — back to OPEN
                # 【中文】试探失败了，说明 API 还没好，重新跳闸。
                self._state = self.OPEN
                self._opened_at = now
                return

            in_window = len(self._failure_times)
            if in_window >= self._failure_threshold and self._state == self.CLOSED:
                # 【中文】窗口内失败数达到阈值，触发跳闸。
                self._state = self.OPEN
                self._opened_at = now
                self._open_transitions += 1

                # Record trip event for analytics. Uses the deque directly:
                # we hold the lock, and the _failure_count property takes it
                # again (threading.Lock is not reentrant).
                #
                # 【中文】把跳闸事件记到 error_tracker 里供分析。
                # ⚠️ 这里直接用 _failure_times 而不是 _failure_count 属性，
                # 因为 _failure_count 内部也要获取锁，而 threading.Lock
                # 不可重入——同一个线程获取两次会死锁。
                try:
                    from .error_tracker import record_event
                    record_event(
                        None, None, "circuit_breaker",
                        f"{self.name} breaker OPEN after {in_window} failures "
                        f"in the last {self._window_seconds:.0f}s",
                        self.name,
                    )
                except Exception:
                    pass  # Event recording must never block circuit breaker logic

    @property
    def _failure_count(self) -> int:
        """Failures still inside the rolling window.

        Read-only on purpose: it used to be a plain int that tests assigned to
        directly (``breaker._failure_count = 0``), which silently desynced it
        from the window. Use ``_reset()`` to clear state.

        【中文】窗口内当前的失败数（只读属性）。
        ⚠️ 故意做成只读属性而不是可写的 int。历史上它是普通 int，
        测试代码直接 ``breaker._failure_count = 0`` 来重置，但这不会清除
        _failure_times 队列，导致计数器和实际窗口内容不一致。
        要重置请用 _reset() 方法。
        """
        with self._lock:
            self._prune_window(time.monotonic())
            return len(self._failure_times)

    def _reset(self) -> None:
        """Return the breaker to a pristine CLOSED state.

        For test teardown: a breaker left OPEN (or holding in-window failures)
        poisons every later test that fetches the same language from the
        module-level registry. Prefer this over poking private fields.

        【中文】把熔断器恢复到初始的 CLOSED 状态。**仅供测试用**。
        如果一个测试把熔断器搞成 OPEN 就退出了，下一个测试拿到同一个
        语言的熔断器时就会莫名其妙地失败。这个方法确保测试间不互相污染。
        """
        with self._lock:
            self._state = self.CLOSED
            self._failure_times.clear()
            self._half_open_attempts = 0
            self._opened_at = 0.0

    def is_open(self) -> bool:
        """Return True if the circuit is currently OPEN (fast-fail mode).

        【中文】查询熔断器是否处于 OPEN 状态。
        注意：如果已经到了恢复时间，虽然内部状态还是 OPEN，
        但逻辑上应该视为"即将进入 HALF_OPEN"，所以返回 False。
        """
        with self._lock:
            if self._state == self.OPEN:
                if self._should_attempt_recovery():
                    return False
                return True
            return False

    @property
    def state(self) -> str:
        """Current state: ``'closed'``, ``'open'``, or ``'half_open'``.

        【中文】当前状态的字符串表示。
        如果内部是 OPEN 但恢复超时已过，对外显示为 half_open。
        """
        with self._lock:
            if self._state == self.OPEN and self._should_attempt_recovery():
                return self.HALF_OPEN
            return self._state

    def snapshot(self) -> dict:
        """Return a lightweight metrics snapshot for the dashboard.

        【中文】返回熔断器的状态快照，供看板显示。

        返回字段：
          · name / state：名字和当前状态
          · failure_count：窗口内失败数
          · total_successes / total_failures / total_calls：历史总计
          · open_transitions：累计跳闸次数
          · failure_threshold / recovery_timeout / window_seconds / half_open_max：配置参数

        ⚠️ 内部已持有锁，所以直接访问 _state 和 _failure_times，
        不能再调 self.state 或 self._failure_count（它们也会获取锁，会死锁）。
        """
        with self._lock:
            # Access _state directly — self.state acquires _lock which
            # would deadlock since we already hold it. Same for the failure
            # count: _failure_count is a property that takes the lock again.
            st = self._state
            if st == self.OPEN and self._should_attempt_recovery():
                st = self.HALF_OPEN
            self._prune_window(time.monotonic())
            return {
                "name": self.name,
                "state": st,
                "failure_count": len(self._failure_times),
                "total_successes": self._total_successes,
                "total_failures": self._total_failures,
                "total_calls": self._total_calls,
                "open_transitions": self._open_transitions,
                "failure_threshold": self._failure_threshold,
                "recovery_timeout": self._recovery_timeout,
                # Surfaced now that both are real behaviour, not dead config:
                # the dashboard previously could not show why a breaker was
                # counting failures the way it did.
                #
                # 【中文】把 window_seconds 和 half_open_max 也暴露出来，
                # 这样看板能解释"为什么这个熔断器是这样计数的"。
                # 以前这两个参数是"写了没人读的死配置"，现在它们是真实行为。
                "window_seconds": self._window_seconds,
                "half_open_max": self._half_open_max,
            }

    # ── internal helpers ───────────────────────────────────────

    def _prune_window(self, now: float) -> None:
        """Drop failures older than ``now - window_seconds``.

        Caller MUST hold ``_lock``. Entries were appended in non-decreasing
        time order (``time.monotonic`` never goes backwards), so only the left
        end can be expired and ``popleft`` in a loop is sufficient — no scan.

        【中文】淘汰窗口外的旧失败记录。
        ⚠️ 调用方必须已持有 _lock。
        因为 time.monotonic() 单调递增，队列里的时间戳一定是从小到大排列的，
        所以只需要从左边 pop 就行，不需要遍历整个队列。
        """
        cutoff = now - self._window_seconds
        while self._failure_times and self._failure_times[0] < cutoff:
            self._failure_times.popleft()

    def _should_attempt_recovery(self) -> bool:
        """Check if enough time has passed to try a recovery probe.

        Caller MUST hold ``_lock``.

        【中文】判断是否可以尝试恢复（恢复超时是否已过）。
        ⚠️ 调用方必须已持有 _lock。
        """
        return (time.monotonic() - self._opened_at) >= self._recovery_timeout

    def _time_until_recovery(self) -> float:
        """Seconds remaining before the next recovery attempt.

        Caller MUST hold ``_lock``.

        【中文】距离下次恢复尝试还有多少秒。
        ⚠️ 调用方必须已持有 _lock。
        """
        return max(0.0, self._recovery_timeout - (time.monotonic() - self._opened_at))


# ═══════════════════════════════════════════════════════════════
# Per-language singleton registry
# ═══════════════════════════════════════════════════════════════
#
# 【中文·按语言隔离的单例注册表】
# 每种语言有自己的熔断器实例，互不影响。第一次请求某种语言时自动创建。
# 这样 en-US 的 API 挂了不会影响 es-ES / de / fr 的翻译。

_breakers: dict[str, CircuitBreaker] = {}
_breakers_lock = threading.Lock()
# 【中文】默认配置常量。这些值经过实测校准：
# · 5 次失败 / 60 秒窗口 → 大约每分钟失败 5 次就跳闸
# · 30 秒恢复超时 → 跳闸后半分钟试一次
# · 1 个探测名额 → 试探时只放一个请求进去
_DEFAULT_THRESHOLD = 5
_DEFAULT_RECOVERY = 30.0
_WINDOW_SECONDS = 60.0
_DEFAULT_HALF_OPEN_MAX = 1


def get_breaker(
    language: str,
    failure_threshold: int = _DEFAULT_THRESHOLD,
    recovery_timeout: float = _DEFAULT_RECOVERY,
    window_seconds: float = _WINDOW_SECONDS,
    half_open_max: int = _DEFAULT_HALF_OPEN_MAX,
) -> CircuitBreaker:
    """Return (or create) a CircuitBreaker per language.

    >>> breaker = get_breaker("en-US")
    >>> breaker = get_breaker("es-ES")  # Different breaker — independent state

    ``half_open_max`` was accepted by the constructor but not by this factory,
    so probe throttling could not be configured through the registry — the same
    "config value nobody reads" gap the rolling window had.

    【中文】获取指定语言的熔断器实例（没有就创建一个）。
    同一种语言多次调用返回同一个实例（单例模式）。
    语言名忽略大小写（"en-US" 和 "EN-US" 是同一个）。

    参数含义同 CircuitBreaker.__init__，这里可以覆盖默认值。
    """
    key = language.lower()
    if key not in _breakers:
        with _breakers_lock:
            # 【中文】双重检查锁定：外层 if 避免每次都获取锁，
            # 内层 if 防止两个线程同时创建同一个语言的熔断器。
            if key not in _breakers:
                _breakers[key] = CircuitBreaker(
                    name=language,
                    failure_threshold=failure_threshold,
                    recovery_timeout=recovery_timeout,
                    window_seconds=window_seconds,
                    half_open_max=half_open_max,
                )
    return _breakers[key]


def get_all_breakers() -> dict:
    """Return snapshots of all registered circuit breakers.

    【中文】返回所有已注册的熔断器的状态快照。
    供看板一次性展示所有语言的熔断器状态。
    """
    with _breakers_lock:
        return {lang: cb.snapshot() for lang, cb in _breakers.items()}

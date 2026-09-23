"""Shared fixtures for Westward Echo integration tests.

Two autouse guards make "tests cost ¥0" an enforced invariant instead of a
claim in a handoff doc:

* ``_block_outbound_network`` — any TCP connect to a non-loopback address fails.
* ``_forbid_live_llm`` — every node's ``ChatOpenAI`` is replaced by a guard that
  records the attempt and raises. Violations are checked at teardown and fail
  the test, so a leak can no longer hide inside a background thread that
  swallows exceptions.

A test that genuinely needs a real provider must opt in explicitly::

    @pytest.mark.live_api
    def test_...(): ...

and be run with ``-m live_api`` after the cost has been announced.
"""
import json
import os
import socket
import tempfile
import threading
import traceback

import pytest

# ── Redirect every write path BEFORE src is imported ────────────────────
# config.DATA_DIR / OUTPUT_DIR and the DB_PATH constants derived from them are
# evaluated at import time, so these must be set here rather than in a fixture.
# Previously `pytest` appended real rows to data/token_ledger.jsonl, mutated
# data/translation_events.db, and dropped hundreds of job files into output/.
_TEST_TMP = tempfile.mkdtemp(prefix="westward-echo-tests-")
os.environ["XIDU_DATA_DIR"] = os.path.join(_TEST_TMP, "data")
os.environ["XIDU_OUTPUT_DIR"] = os.path.join(_TEST_TMP, "output")
os.environ["XIDU_TOKEN_LEDGER"] = os.path.join(_TEST_TMP, "data", "token_ledger.jsonl")
os.environ["CHECKPOINT_DB_PATH"] = os.path.join(_TEST_TMP, "data", "checkpoints.db")
# .env pins CHROMA_PERSIST_PATH to the real repo path and it does not follow
# DATA_DIR, so redirect it explicitly or tests write the real vector store.
os.environ["CHROMA_PERSIST_PATH"] = os.path.join(_TEST_TMP, "data", "chroma")
os.makedirs(os.environ["XIDU_DATA_DIR"], exist_ok=True)
os.makedirs(os.environ["XIDU_OUTPUT_DIR"], exist_ok=True)

# Poison credentials at import time, before src.config reads the environment.
# A test that forgot to mock then fails auth instead of billing the real account.
for _secret_var in (
    "DEEPSEEK_API_KEY", "GLM_API_KEY", "LLM_API_KEY", "QWEN_API_KEY",
    "DASHSCOPE_API_KEY", "OPENAI_API_KEY",
):
    os.environ[_secret_var] = "sk-FAKE-TESTS-MUST-MOCK"
# CHROMA_HOST is a hostname, not a secret; poisoning it makes chromadb dial a
# nonsense host and produces misleading failures.
os.environ.pop("CHROMA_HOST", None)

# Every module that did ``from langchain_openai import ChatOpenAI`` holds its own
# reference, so the guard has to be installed per-module.
_LLM_MODULES = (
    "src.agent.nodes.read",
    "src.agent.nodes.review_terms",
    "src.agent.nodes.write",
    "src.agent.nodes.readback",
    "src.agent.nodes.fix",
)

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "", "testserver"}
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_create_connection = socket.create_connection
_net_violations: list = []


def _is_local(addr) -> bool:
    """True for AF_UNIX paths, socketpair, and loopback targets."""
    if isinstance(addr, (bytes, str)):
        return True
    if not isinstance(addr, tuple) or not addr:
        return True
    host = addr[0]
    if isinstance(host, bytes):
        host = host.decode("utf-8", "replace")
    return host in _LOOPBACK_HOSTS


def _blocked_connect(self, addr):
    if not _is_local(addr):
        _net_violations.append(addr)
        raise RuntimeError(
            f"conftest: test tried to reach {addr!r}. Tests must not call real "
            "APIs — mock the LLM or mark the test @pytest.mark.live_api."
        )
    return _real_connect(self, addr)


def _blocked_connect_ex(self, addr):
    if not _is_local(addr):
        _net_violations.append(addr)
        raise RuntimeError(
            f"conftest: test tried to reach {addr!r}. Tests must not call real APIs."
        )
    return _real_connect_ex(self, addr)


def _blocked_create_connection(address, *args, **kwargs):
    if not _is_local(address):
        _net_violations.append(address)
        raise RuntimeError(
            f"conftest: test tried to reach {address!r}. Tests must not call real APIs."
        )
    return _real_create_connection(address, *args, **kwargs)


@pytest.fixture(autouse=True)
def _block_outbound_network():
    """Fail any attempt to reach a routable address during a test."""
    socket.socket.connect = _blocked_connect            # type: ignore[assignment]
    socket.socket.connect_ex = _blocked_connect_ex      # type: ignore[assignment]
    socket.create_connection = _blocked_create_connection  # type: ignore[assignment]
    del _net_violations[:]
    yield
    socket.socket.connect = _real_connect               # type: ignore[assignment]
    socket.socket.connect_ex = _real_connect_ex         # type: ignore[assignment]
    socket.create_connection = _real_create_connection  # type: ignore[assignment]
    attempts = list(_net_violations)
    del _net_violations[:]
    if attempts:
        pytest.fail(
            f"test made {len(attempts)} outbound connection attempt(s): "
            f"{attempts[:5]}. Tests must not spend money.",
            pytrace=False,
        )


class _LiveLLMGuard:
    """Stand-in for ChatOpenAI that refuses to exist.

    Instantiating one means some code path reached a real provider. The attempt
    is recorded (so teardown can fail the test even when the caller swallows the
    exception, as routes.py's background thread does) and then raised.
    """

    def __init__(self, *args, **kwargs):
        model = kwargs.get("model") or (args[0] if args else "?")
        stack = "".join(traceback.format_stack()[-18:-1])
        _llm_violations.append((model, stack))
        raise RuntimeError(
            f"conftest: a test constructed a real ChatOpenAI (model={model!r}). "
            "Tests must mock the LLM — use the mock_translate_invoke fixture, "
            "patch the node's ChatOpenAI, or mark the test @pytest.mark.live_api "
            "and announce the cost first."
        )


_llm_violations: list = []


def drain_background_threads(timeout: float = 10.0) -> None:
    """Wait for threads started since ``snapshot_threads`` to finish.

    routes.py launches daemon threads and returns immediately, so without this a
    straggler wakes up after the test's own mocks are torn down and builds a real
    client. Call it from a fixture teardown *while the mocks are still active*.
    """
    for t in threading.enumerate():
        if t in _known_threads or t is threading.current_thread():
            continue
        t.join(timeout=timeout)
    _known_threads.update(threading.enumerate())


_known_threads: set = set(threading.enumerate())


@pytest.fixture(autouse=True)
def _forbid_live_llm(request):
    """Replace each node's ChatOpenAI with a guard unless the test opts out."""
    if request.node.get_closest_marker("live_api"):
        yield
        return

    import importlib

    saved = {}
    for mod_name in _LLM_MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except Exception:  # pragma: no cover - module genuinely unavailable
            continue
        if hasattr(mod, "ChatOpenAI"):
            saved[(mod, mod_name)] = mod.ChatOpenAI
            mod.ChatOpenAI = _LiveLLMGuard  # type: ignore[assignment]

    del _llm_violations[:]
    yield

    # Drain stragglers BEFORE restoring the real ChatOpenAI, otherwise a late
    # thread wakes up, builds a real client, and the violation gets blamed on
    # whatever test runs next (and may be billed after this test "passed").
    drain_background_threads()

    for (mod, mod_name), original in saved.items():
        mod.ChatOpenAI = original  # type: ignore[assignment]

    violations = list(_llm_violations)
    del _llm_violations[:]
    if violations:
        models = sorted({v[0] for v in violations})
        pytest.fail(
            f"test triggered {len(violations)} real LLM construction(s) "
            f"(models={models}). This is the fake-green path: the test passed "
            "before only because the exception died in a background thread. "
            f"First call site:\n{violations[0][1]}",
            pytrace=False,
        )


@pytest.fixture(autouse=True)
def _reset_backpressure():
    """Reset the module-level backpressure singleton before each test.

    Without this, tests that use backpressure.try_accept() without a matching
    release() (e.g. due to an exception in the async path) pollute the singleton
    and cause spurious failures in downstream assertions.
    """
    from src.backpressure import backpressure
    backpressure.reset()
    yield


@pytest.fixture(autouse=True)
def _reset_circuit_breakers():
    """Reset the per-language circuit breaker registry before each test.

    The circuit breaker is a module-level singleton keyed by language; a test
    that trips "en-US" would otherwise leave it OPEN and poison downstream
    tests that hit the same breaker through get_breaker().
    """
    from src.circuit_breaker import _breakers, _breakers_lock
    with _breakers_lock:
        _breakers.clear()
    yield


@pytest.fixture
def mock_llm_response():
    """Return a valid translation JSON response that parse_llm_response can handle."""

    def _build(
        translated_text=(
            "She stepped into the grand hall, her heels clicking against the marble floor."
        ),
        new_terms_found=None,
        adaptation_notes=None,
        chapter_summary="The heroine enters the grand hall and faces the CEO.",
    ):
        return json.dumps({
            "translated_text": translated_text,
            "new_terms_found": new_terms_found or [
                {"term_cn": "大殿", "term_en": "grand hall", "category": "location"},
                {"term_cn": "霸总", "term_en": "Alpha CEO", "category": "culture"},
            ],
            "adaptation_notes": adaptation_notes or [
                "Adapted '霸总' to 'Alpha CEO' for Western romance genre context"
            ],
            "chapter_summary": chapter_summary,
        })

    return _build


@pytest.fixture
def sample_chapter():
    """Return a sample Chinese chapter for testing."""
    return {
        "title": "第一章 穿成霸总文女主",
        "content": (
            "苏念醒过来的时候，发现自己躺在一张陌生的大床上。\n\n"
            "她揉了揉眼睛，试图回忆昨晚发生了什么。脑海里突然响起一个机械的声音：\n\n"
            "【叮——恭喜宿主绑定霸总攻略系统！】\n\n"
            "苏念愣住了。什么系统？什么霸总？她只是加了个班，怎么就穿越了？\n\n"
            "她环顾四周，房间奢华得像五星级酒店。落地窗外是城市的夜景，霓虹灯闪烁。\n\n"
            "门外传来脚步声。一个高大英俊的男人走了进来，穿着剪裁考究的西装。\n\n"
            '"你就是苏念？"男人冷冷地说，"从今天起，你就是裴家的人了。"\n\n'
            "苏念心里一万匹草泥马奔腾而过。她只是一个普通社畜，怎么就变成了霸总文女主？\n\n"
            "她深吸一口气，决定先弄清楚目前的处境再说。毕竟既来之，则安之。"
        ),
        "number": 1,
    }


@pytest.fixture
def sample_chapter_2():
    """Return a second sample chapter for multi-chapter tests."""
    return {
        "title": "第二章 裴总的契约",
        "content": (
            "第二天一早，苏念被楼下传来的钢琴声吵醒了。\n\n"
            "她走下楼，看到裴衍舟正坐在客厅的钢琴前弹奏。晨光洒在他身上，画面美好得不真实。\n\n"
            '"起来了？"裴衍舟头也不抬地说，"正好，我让律师拟了一份契约，你签一下。"\n\n'
            "苏念接过文件，快速扫了一眼。契约上写着：假扮三个月情侣，对外宣称已经结婚。\n\n"
            '"这是什么意思？"她皱起眉头。\n\n'
            '"字面上的意思。"裴衍舟终于抬起头，"三个月后，一切结束，你拿钱走人。"\n\n'
            "苏念冷笑一声。这剧情她可太熟了。上一世看了不下五十本霸总文，每个女主都是这么开始的。\n\n"
            "系统提示音又响了起来：【支线任务：拒绝契约。奖励：气场+10】\n\n"
            '苏念把契约放了回去，微笑道：“不好意思，裴总，我不演。”\n\n'
            "裴衍舟眼神一暗。"
        ),
        "number": 2,
    }


@pytest.fixture
def sample_chapter_3():
    """Return a third sample chapter for multi-chapter tests."""
    return {
        "title": "第三章 父凭子贵",
        "content": (
            "苏念在裴家已经住了一周了。\n\n"
            "她发现这里每天都有各种白莲花女配来找茬，如果不是穿书，她真会以为自己在演宫斗剧。\n\n"
            '"苏小姐，裴总请你去书房。"管家恭敬地说道。\n\n'
            "苏念走进书房，看到裴衍舟正站在落地窗前。他的侧脸在逆光中格外分明。\n\n"
            '"你这周的面试表现不错，"他说，"公司那边已经准备好了，明天开始上班。"\n\n'
            "苏念点了点头。她的社畜之魂在这几天里又觉醒了。\n\n"
            '"不过——"裴衍舟转身看着她，"你之前认识楚淮？"\n\n'
            "苏念心跳漏了一拍。楚淮，原书的男二号，是裴衍舟的竞争对手。\n\n"
            '"不认识。\"她说，"只是在找工作时去过耀星集团面试。"\n\n'
            "裴衍舟的目光锐利起来。他可不信。"
        ),
        "number": 3,
    }


@pytest.fixture
def sample_glossary():
    """Return a pre-populated glossary dict for testing."""
    return {
        "苏念": "Su Nian",
        "裴衍舟": "Pei Yanzhou",
        "楚淮": "Chu Huai",
        "林婉清": "Lin Wanqing",
        "裴氏集团": "Pei Group",
        "耀星集团": "Starbright Group",
    }


@pytest.fixture
def mock_translate_invoke(mock_llm_response):
    """Patch ChatOpenAI.invoke in the translate node to return a mock response.

    Returns a context-manager-ready helper so each test can customise the
    response or model class path.
    """

    def _patcher(
        translated_text=(
            "Su Nian opened her eyes and found herself lying on a large, unfamiliar bed."
        ),
        new_terms_found=None,
        target_path="src.agent.nodes.write.ChatOpenAI",
    ):
        from unittest.mock import MagicMock, patch

        mock_response = MagicMock()
        mock_response.content = mock_llm_response(
            translated_text=translated_text,
            new_terms_found=new_terms_found,
        )

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response

        return patch(target_path, return_value=mock_llm)

    return _patcher

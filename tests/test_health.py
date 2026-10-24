"""Tests for src/health.py — the cost guard and the config check.

These lock down three defects that together produced a fake-green /api/health:

1. ``check_llm_api`` made a real, billable HTTP call on every invocation.
   ``/api/health`` is unauthenticated and un-rate-limited, and the dashboard
   polls it every 5 s — so an open dashboard spent money continuously.
2. A failed probe was downgraded to ``warn``, so tests asserted
   ``status in ("healthy", "degraded")`` and passed while every real call died.
3. ``check_config`` required ``DEEPSEEK_API_KEY`` — a provider abandoned in
   2026-09 — and never validated ``LLM_API_KEY`` or ``GLM_API_KEY``, the two
   credentials the pipeline actually uses.
"""

import time as _real_time
from pathlib import Path

import pytest

import src.health as health_mod
from src.health import HealthChecker, check_disk_io, check_memory

# ═══════════════════════════════════════════════════════════════
# Deterministic clock
#
# Every check does ``start = time.monotonic()`` and then
# ``elapsed = round((time.monotonic() - start) * 1000, 1)``. Against the real
# clock that value is noise, so the assertion ``latency_ms >= 0`` — the obvious
# thing to write — passes for *every* mutation of the arithmetic: ``* 1000``
# becoming ``* 1001`` still yields a positive number, and so does widening
# ``round(..., 1)`` to ``round(..., 2)``. Feeding a known delta makes the
# arithmetic falsifiable instead: 0.123456 s must come back as exactly 123.5 ms.
# ═══════════════════════════════════════════════════════════════

_DELTA = 0.123456
_EXPECTED_MS = 123.5  # round(0.123456 * 1000, 1); *1001 -> 123.6, round(_,2) -> 123.46


class _FakeTime:
    """Stand-in for the ``time`` module returning a scripted monotonic sequence."""

    def __init__(self, deltas):
        self._queue = list(deltas)
        self._base = 1_000_000.0

    def monotonic(self):
        if not self._queue:
            return _real_time.monotonic()
        return self._base + self._queue.pop(0)

    def __getattr__(self, name):
        # strftime / gmtime / perf_counter and everything else stay real.
        return getattr(_real_time, name)


@pytest.fixture
def fake_time(monkeypatch):
    """Install a deterministic clock; call it with the monotonic deltas to serve."""

    def _install(deltas):
        clock = _FakeTime(deltas)
        monkeypatch.setattr(health_mod, "time", clock)
        return clock

    return _install


# ═══════════════════════════════════════════════════════════════
# Cost guard: check_all() must never spend money by default
# ═══════════════════════════════════════════════════════════════

class TestNoBillableCallByDefault:
    def test_default_checker_does_not_probe_live(self):
        assert HealthChecker()._live_llm is False

    def test_check_llm_api_reports_skip_by_default(self):
        result = HealthChecker().check_llm_api()
        assert result["live_probe"] is False
        assert "skipped" in result["message"]

    def test_check_llm_api_never_touches_httpx_by_default(self, monkeypatch):
        """Strongest form of the guarantee: httpx is never reached at all.

        If the guard regressed, this would raise and the test would fail rather
        than silently billing the account.
        """
        import src.health as health_mod

        def _explode(*args, **kwargs):
            raise AssertionError(
                "check_llm_api() built an httpx client while live_llm=False — "
                "this would bill the real API on every /api/health request."
            )

        monkeypatch.setattr(health_mod.httpx, "Client", _explode)
        result = HealthChecker().check_llm_api()
        assert result["status"] == "ok"
        assert result["live_probe"] is False

    def test_check_all_does_not_probe_live(self, monkeypatch):
        import src.health as health_mod

        def _explode(*args, **kwargs):
            raise AssertionError("check_all() reached httpx while live_llm=False")

        monkeypatch.setattr(health_mod.httpx, "Client", _explode)
        report = HealthChecker().check_all()
        assert report["checks"]["llm_api"]["live_probe"] is False

    def test_live_probe_is_opt_in(self, monkeypatch):
        """live_llm=True must actually issue the call — otherwise the opt-in
        would be dead and the check would be untestable."""
        import src.health as health_mod

        calls = []

        class _FakeResp:
            status_code = 200
            text = "ok"

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def post(self, url, json=None, headers=None):
                calls.append(url)
                return _FakeResp()

        monkeypatch.setattr(health_mod.httpx, "Client", _FakeClient)
        result = HealthChecker(live_llm=True).check_llm_api()
        assert result["live_probe"] is True
        assert result["status"] == "ok"
        assert len(calls) == 1
        assert calls[0].endswith("/chat/completions")


# ═══════════════════════════════════════════════════════════════
# Fake-green fix: a failed live probe must be an ERROR, not a warn
# ═══════════════════════════════════════════════════════════════

class TestProbeFailureIsNotDowngraded:
    def test_connection_error_reports_error(self, monkeypatch):
        """This is the exact regression: exceptions used to become "warn", so
        /api/health passed while every call failed."""
        import src.health as health_mod

        class _BoomClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def post(self, *args, **kwargs):
                raise ConnectionError("network unreachable")

        monkeypatch.setattr(health_mod.httpx, "Client", _BoomClient)
        result = HealthChecker(live_llm=True).check_llm_api()
        assert result["status"] == "error"
        assert "ConnectionError" in result["message"]

    def test_timeout_reports_error(self, monkeypatch):
        import httpx

        import src.health as health_mod

        class _TimeoutClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def post(self, *args, **kwargs):
                raise httpx.TimeoutException("timed out")

        monkeypatch.setattr(health_mod.httpx, "Client", _TimeoutClient)
        result = HealthChecker(live_llm=True).check_llm_api()
        assert result["status"] == "error"

    def test_http_error_reports_error(self, monkeypatch):
        import src.health as health_mod

        class _Resp:
            status_code = 400
            text = "Arrearage: account balance exhausted"

        class _Client:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def post(self, *args, **kwargs):
                return _Resp()

        monkeypatch.setattr(health_mod.httpx, "Client", _Client)
        result = HealthChecker(live_llm=True).check_llm_api()
        assert result["status"] == "error"
        assert "400" in result["message"]

    def test_missing_key_is_critical_error(self, monkeypatch):
        import src.health as health_mod

        monkeypatch.setattr(health_mod, "LLM_API_KEY", "")
        result = HealthChecker().check_llm_api()
        assert result["status"] == "error"
        assert result.get("critical") is True

    def test_malformed_base_url_is_critical_error(self, monkeypatch):
        import src.health as health_mod

        monkeypatch.setattr(health_mod, "LLM_BASE_URL", "not-a-url")
        result = HealthChecker().check_llm_api()
        assert result["status"] == "error"
        assert result.get("critical") is True
        assert "not-a-url" in result["message"]


# ═══════════════════════════════════════════════════════════════
# check_config must validate the credentials actually in use
# ═══════════════════════════════════════════════════════════════

class TestCheckConfigValidatesRealProviders:
    def test_both_provider_keys_present_is_not_error(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-qwen")
        monkeypatch.setenv("GLM_API_KEY", "sk-glm")
        result = HealthChecker().check_config()
        assert result["status"] != "error"

    def test_missing_qwen_key_is_critical(self, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.setenv("GLM_API_KEY", "sk-glm")
        result = HealthChecker().check_config()
        assert result["status"] == "error"
        assert result.get("critical") is True
        assert "LLM_API_KEY" in result["message"]

    def test_missing_glm_key_is_critical(self, monkeypatch):
        """GLM serves WRITE and FIX — without it the pipeline cannot run, so a
        config check that ignores it gives false confidence at deploy time."""
        monkeypatch.setenv("LLM_API_KEY", "sk-qwen")
        monkeypatch.delenv("GLM_API_KEY", raising=False)
        result = HealthChecker().check_config()
        assert result["status"] == "error"
        assert result.get("critical") is True
        assert "GLM_API_KEY" in result["message"]

    def test_glm_qwen_only_deployment_is_not_critical(self, monkeypatch):
        """A fresh deploy that configures only the two live providers must not
        be refused at startup. The old check demanded DEEPSEEK_API_KEY and so
        main.py raised RuntimeError for a perfectly valid configuration."""
        monkeypatch.setenv("LLM_API_KEY", "sk-qwen")
        monkeypatch.setenv("GLM_API_KEY", "sk-glm")
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        result = HealthChecker().check_config()
        assert result["status"] == "ok", result

    def test_deprecated_deepseek_vars_are_flagged_not_required(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-qwen")
        monkeypatch.setenv("GLM_API_KEY", "sk-glm")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-old-unused")
        result = HealthChecker().check_config()
        # Present-but-unused must warn (it is misleading), never be required.
        assert result["status"] == "warn"
        assert "DEEPSEEK_API_KEY" in result["message"]
        assert "Deprecated" in result["message"]

    def test_missing_optional_vars_are_flagged(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-qwen")
        monkeypatch.setenv("GLM_API_KEY", "sk-glm")
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = HealthChecker().check_config()
        assert result["status"] == "warn"
        assert "ANTHROPIC_API_KEY" in result["message"]


# ═══════════════════════════════════════════════════════════════
# Report shape
# ═══════════════════════════════════════════════════════════════

class TestReportShape:
    def test_check_all_returns_all_checks(self):
        report = HealthChecker().check_all()
        for key in ("disk_space", "config", "output_dir", "sqlite",
                    "llm_api", "memory", "disk_io", "chroma"):
            assert key in report["checks"], f"missing check: {key}"
        assert report["status"] in ("healthy", "degraded", "unhealthy")
        assert report["summary"]["total"] == len(report["checks"])

    def test_critical_error_makes_report_unhealthy(self, monkeypatch):
        """A critical failure must surface as unhealthy so main.py refuses to
        start, rather than being averaged away into 'degraded'."""
        monkeypatch.setattr(HealthChecker, "check_config",
                            lambda self: {"status": "error", "message": "boom",
                                          "latency_ms": 0.0, "critical": True})
        report = HealthChecker().check_all()
        assert report["status"] == "unhealthy"

    def test_summary_counts_match_checks(self, monkeypatch):
        monkeypatch.setattr(HealthChecker, "check_config",
                            lambda self: {"status": "ok", "message": "fine",
                                          "latency_ms": 0.0})
        report = HealthChecker().check_all()
        statuses = [c["status"] for c in report["checks"].values()]
        assert report["summary"]["ok"] == statuses.count("ok")
        assert report["summary"]["warn"] == statuses.count("warn")
        assert report["summary"]["error"] == statuses.count("error")


# ═══════════════════════════════════════════════════════════════
# check_all status logic
#
# ``unhealthy`` is the startup gate: main.py refuses to boot on it. The rule is
# "error AND critical", not "error" — so a non-critical error must degrade
# instead of blocking startup, and a warn carrying critical=True must NOT block.
# Each test below pins one operand of that rule; without them, flipping ``and``
# to ``or`` or the ``critical`` default to True left the suite green.
# ═══════════════════════════════════════════════════════════════

def _ok(msg="fine"):
    return {"status": "ok", "message": msg, "latency_ms": 0.0}


def _warn(msg="meh", **extra):
    return {"status": "warn", "message": msg, "latency_ms": 0.0, **extra}


def _error(msg="boom", **extra):
    return {"status": "error", "message": msg, "latency_ms": 0.0, **extra}


def _stub_checks(monkeypatch, **overrides):
    """Replace every individual check so check_all's aggregation is testable.

    ``check_memory`` / ``check_disk_io`` are module-level functions, so they are
    patched on the module rather than the class.
    """
    results = {
        "disk_space": _ok("space"),
        "config": _ok("config"),
        "output_dir": _ok("outdir"),
        "sqlite": _ok("sqlite"),
        "llm_api": _ok("llm"),
        "memory": _ok("mem"),
        "disk_io": _ok("io"),
        "chroma": _ok("chroma"),
    }
    results.update(overrides)
    for report_key, method in (
        ("disk_space", "check_disk_space"),
        ("config", "check_config"),
        ("output_dir", "check_output_dir_writable"),
        ("sqlite", "check_sqlite"),
        ("llm_api", "check_llm_api"),
    ):
        monkeypatch.setattr(HealthChecker, method,
                            lambda self, _n=report_key, **kw: results[_n])
    monkeypatch.setattr(HealthChecker, "check_chroma",
                        lambda self, **kw: results["chroma"])
    monkeypatch.setattr(health_mod, "check_memory", lambda: results["memory"])
    monkeypatch.setattr(health_mod, "check_disk_io", lambda: results["disk_io"])
    return results


class TestCheckAllStatusLogic:
    def test_all_ok_is_healthy(self, monkeypatch):
        """Zero errors and zero warns must be 'healthy'.

        Kills ``error_count > 0 or warn_count > 0`` mutated to ``<=``: with no
        failures the mutated comparison is True and the report would read
        'degraded' on a perfectly fine machine.
        """
        _stub_checks(monkeypatch)
        report = HealthChecker().check_all()
        assert report["status"] == "healthy"
        assert report["summary"] == {"ok": 8, "warn": 0, "error": 0, "total": 8}

    def test_warn_only_is_degraded_not_healthy(self, monkeypatch):
        _stub_checks(monkeypatch, memory=_warn("psutil missing"))
        report = HealthChecker().check_all()
        assert report["status"] == "degraded"
        assert report["summary"]["warn"] == 1
        assert report["summary"]["error"] == 0

    def test_non_critical_error_is_degraded_not_unhealthy(self, monkeypatch):
        """An error without critical=True must not block startup.

        Kills ``or`` on the criticality test: mutated, any error at all — even a
        non-blocking one — would report 'unhealthy' and main.py would refuse to
        boot a working deployment.
        """
        _stub_checks(monkeypatch, chroma=_error("chroma down"))
        report = HealthChecker().check_all()
        assert report["status"] == "degraded"
        assert report["summary"]["error"] == 1

    def test_error_and_warn_together_is_degraded(self, monkeypatch):
        """Both counters non-zero, neither critical → still 'degraded'.

        Kills the ``>`` → ``<=`` flip on line 85: mutated, ``1<=0 or 1<=0`` is
        False and the report falls through to 'healthy' while an error is open.
        """
        _stub_checks(monkeypatch, chroma=_error("chroma down"),
                     memory=_warn("psutil missing"))
        report = HealthChecker().check_all()
        assert report["status"] == "degraded"
        assert report["summary"]["error"] == 1
        assert report["summary"]["warn"] == 1

    def test_critical_error_is_unhealthy(self, monkeypatch):
        _stub_checks(monkeypatch, config=_error("no key", critical=True))
        report = HealthChecker().check_all()
        assert report["status"] == "unhealthy"

    def test_error_without_critical_key_defaults_to_false(self, monkeypatch):
        """``c.get("critical", False)`` — the default is load-bearing.

        Mutated to ``True``, a plain error dict that never set the key would be
        treated as critical and block startup.
        """
        _stub_checks(monkeypatch, sqlite=_error("locked"))  # no "critical" key
        report = HealthChecker().check_all()
        assert report["status"] == "degraded"
        assert "critical" not in report["checks"]["sqlite"]

    def test_warn_with_critical_flag_is_still_not_unhealthy(self, monkeypatch):
        """Criticality only escalates *errors*; a warn stays non-blocking.

        Kills ``and`` → ``or``: mutated, ``status=="error" or critical`` is True
        for this warn and the report becomes 'unhealthy'.
        """
        _stub_checks(monkeypatch, disk_space=_warn("low", critical=True))
        report = HealthChecker().check_all()
        assert report["status"] == "degraded"

    def test_one_critical_among_many_ok_still_unhealthy(self, monkeypatch):
        _stub_checks(monkeypatch, disk_space=_error("full", critical=True))
        report = HealthChecker().check_all()
        assert report["status"] == "unhealthy"
        assert report["summary"]["ok"] == 7

    def test_summary_total_equals_check_count(self, monkeypatch):
        _stub_checks(monkeypatch, memory=_warn(), chroma=_error("x", critical=True))
        report = HealthChecker().check_all()
        assert report["summary"]["total"] == len(report["checks"]) == 8
        assert (report["summary"]["ok"] + report["summary"]["warn"]
                + report["summary"]["error"]) == 8

    def test_timestamp_is_present_and_utc_shaped(self, monkeypatch):
        import re
        _stub_checks(monkeypatch)
        report = HealthChecker().check_all()
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
                            report["timestamp"])


# ═══════════════════════════════════════════════════════════════
# check_disk_space
# ═══════════════════════════════════════════════════════════════

def _fake_usage(free_mb):
    class _U:
        free = free_mb * 1024 * 1024
        total = 10 ** 12
        used = 10 ** 11
    return _U()


class _FakeShutil:
    def __init__(self, free_mb=None, exc=None):
        self._free_mb, self._exc = free_mb, exc
        self.calls = []

    def disk_usage(self, path):
        self.calls.append(path)
        if self._exc:
            raise self._exc
        return _fake_usage(self._free_mb)

    def __getattr__(self, name):
        import shutil as _real
        return getattr(_real, name)


class TestCheckDiskSpace:
    def test_enough_space_is_ok(self, monkeypatch, fake_time, tmp_path):
        monkeypatch.setattr(health_mod, "CHROMA_PERSIST_PATH",
                            str(tmp_path / "data" / "chroma"))
        fake = _FakeShutil(free_mb=1000)
        monkeypatch.setattr(health_mod, "shutil", fake)
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_disk_space()
        assert result["status"] == "ok"
        assert "1000 MB free" in result["message"]
        assert result["latency_ms"] == _EXPECTED_MS
        assert "critical" not in result

    def test_threshold_boundary_exactly_500_is_ok(self, monkeypatch, fake_time, tmp_path):
        """500 MB free with a 500 MB threshold is NOT low (the test is ``<``).

        Kills both the ``<`` → ``>=`` flip and the 500 → 501 default drift: at
        exactly 500 the mutated forms report an error.
        """
        monkeypatch.setattr(health_mod, "CHROMA_PERSIST_PATH",
                            str(tmp_path / "data" / "chroma"))
        monkeypatch.setattr(health_mod, "shutil", _FakeShutil(free_mb=500))
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_disk_space()
        assert result["status"] == "ok", result
        assert result["latency_ms"] == _EXPECTED_MS

    def test_one_mb_below_threshold_is_critical_error(self, monkeypatch, fake_time, tmp_path):
        monkeypatch.setattr(health_mod, "CHROMA_PERSIST_PATH",
                            str(tmp_path / "data" / "chroma"))
        monkeypatch.setattr(health_mod, "shutil", _FakeShutil(free_mb=499))
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_disk_space()
        assert result["status"] == "error"
        assert result["critical"] is True
        assert "499 MB free" in result["message"]
        assert "500 MB" in result["message"]
        assert result["latency_ms"] == _EXPECTED_MS

    def test_mb_conversion_divides_by_1024_squared(self, monkeypatch, fake_time, tmp_path):
        """``free // (1024 * 1024)`` — the reported number must be real MiB.

        Either 1024 drifting to 1025 changes 500 MB into 499 and flips the
        verdict, so the boundary case above plus this one pin the divisor.
        """
        monkeypatch.setattr(health_mod, "CHROMA_PERSIST_PATH",
                            str(tmp_path / "data" / "chroma"))
        monkeypatch.setattr(health_mod, "shutil", _FakeShutil(free_mb=1234))
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_disk_space()
        assert "1234 MB free" in result["message"]

    def test_custom_threshold_is_honoured(self, monkeypatch, fake_time, tmp_path):
        monkeypatch.setattr(health_mod, "CHROMA_PERSIST_PATH",
                            str(tmp_path / "data" / "chroma"))
        monkeypatch.setattr(health_mod, "shutil", _FakeShutil(free_mb=500))
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_disk_space(min_free_mb=501)
        assert result["status"] == "error"
        assert result["critical"] is True

    def test_parent_dir_is_created_when_missing(self, monkeypatch, fake_time, tmp_path):
        """``mkdir(parents=True, ...)`` — a fresh clone has no data/ yet.

        Mutated to ``parents=False`` this raises FileNotFoundError, and the
        checker would report a critical disk error on a healthy machine.
        """
        deep = tmp_path / "a" / "b" / "chroma"
        assert not deep.parent.parent.exists()
        monkeypatch.setattr(health_mod, "CHROMA_PERSIST_PATH", str(deep))
        monkeypatch.setattr(health_mod, "shutil", _FakeShutil(free_mb=900))
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_disk_space()
        assert result["status"] == "ok", result
        assert deep.parent.exists()

    def test_existing_dir_does_not_raise(self, monkeypatch, fake_time, tmp_path):
        """``mkdir(..., exist_ok=True)`` — the normal steady state.

        Mutated to ``exist_ok=False`` this raises FileExistsError on every call
        after the first, i.e. on every dashboard poll.
        """
        data = tmp_path / "data"
        data.mkdir()
        monkeypatch.setattr(health_mod, "CHROMA_PERSIST_PATH", str(data / "chroma"))
        monkeypatch.setattr(health_mod, "shutil", _FakeShutil(free_mb=900))
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_disk_space()
        assert result["status"] == "ok", result

    def test_disk_usage_failure_is_critical_error(self, monkeypatch, fake_time, tmp_path):
        monkeypatch.setattr(health_mod, "CHROMA_PERSIST_PATH",
                            str(tmp_path / "data" / "chroma"))
        monkeypatch.setattr(health_mod, "shutil",
                            _FakeShutil(exc=OSError("device not configured")))
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_disk_space()
        assert result["status"] == "error"
        assert result["critical"] is True
        assert "device not configured" in result["message"]
        assert result["latency_ms"] == _EXPECTED_MS


# ═══════════════════════════════════════════════════════════════
# check_output_dir_writable
# ═══════════════════════════════════════════════════════════════

class TestCheckOutputDirWritable:
    def test_writable_dir_is_ok_and_leaves_no_probe(self, monkeypatch, fake_time, tmp_path):
        out = tmp_path / "output"
        monkeypatch.setattr(health_mod, "OUTPUT_DIR", out)
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_output_dir_writable()
        assert result["status"] == "ok"
        assert result["message"] == str(out)
        assert result["latency_ms"] == _EXPECTED_MS
        assert not (out / ".health_probe").exists(), "probe file was not cleaned up"

    def test_missing_parents_are_created(self, monkeypatch, fake_time, tmp_path):
        out = tmp_path / "a" / "b" / "output"
        monkeypatch.setattr(health_mod, "OUTPUT_DIR", out)
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_output_dir_writable()
        assert result["status"] == "ok", result
        assert out.exists()

    def test_path_occupied_by_a_file_is_critical_error(self, monkeypatch, fake_time, tmp_path):
        """OUTPUT_DIR pointing at a regular file must fail loudly.

        mkdir(exist_ok=True) on an existing *file* raises FileExistsError, which
        is the realistic misconfiguration this check exists to catch.
        """
        blocker = tmp_path / "output"
        blocker.write_text("not a directory")
        monkeypatch.setattr(health_mod, "OUTPUT_DIR", blocker)
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_output_dir_writable()
        assert result["status"] == "error"
        assert result["critical"] is True
        assert result["latency_ms"] == _EXPECTED_MS

    def test_existing_dir_does_not_raise(self, monkeypatch, fake_time, tmp_path):
        """``exist_ok=True`` — the directory normally already exists."""
        out = tmp_path / "output"
        out.mkdir()
        monkeypatch.setattr(health_mod, "OUTPUT_DIR", out)
        fake_time([0.0, _DELTA])
        assert HealthChecker().check_output_dir_writable()["status"] == "ok"

    def test_write_failure_is_critical_error(self, monkeypatch, fake_time, tmp_path):
        out = tmp_path / "output"
        out.mkdir()
        monkeypatch.setattr(health_mod, "OUTPUT_DIR", out)

        def _explode(self, *a, **k):
            raise PermissionError("read-only filesystem")

        monkeypatch.setattr(Path, "write_text", _explode)
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_output_dir_writable()
        assert result["status"] == "error"
        assert result["critical"] is True
        assert "read-only filesystem" in result["message"]


# ═══════════════════════════════════════════════════════════════
# check_sqlite
# ═══════════════════════════════════════════════════════════════

class TestCheckSqlite:
    def test_openable_db_is_ok(self, monkeypatch, fake_time, tmp_path):
        db = tmp_path / "checkpoints.db"
        monkeypatch.setattr(health_mod, "CHECKPOINT_DB_PATH", str(db))
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_sqlite()
        assert result["status"] == "ok"
        assert result["message"] == str(db)
        assert result["latency_ms"] == _EXPECTED_MS

    def test_unopenable_path_is_critical_error(self, monkeypatch, fake_time, tmp_path):
        """A directory where the DB should be → sqlite3.OperationalError.

        This is the real failure mode (wrong CHECKPOINT_DB_PATH in .env); it
        must be critical because LangGraph checkpointing cannot work without it.
        """
        monkeypatch.setattr(health_mod, "CHECKPOINT_DB_PATH", str(tmp_path))
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_sqlite()
        assert result["status"] == "error"
        assert result["critical"] is True
        assert result["latency_ms"] == _EXPECTED_MS

    def test_missing_parent_dir_is_critical_error(self, monkeypatch, fake_time, tmp_path):
        monkeypatch.setattr(health_mod, "CHECKPOINT_DB_PATH",
                            str(tmp_path / "nope" / "checkpoints.db"))
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_sqlite()
        assert result["status"] == "error"
        assert result["critical"] is True

    def test_connection_is_closed(self, monkeypatch, fake_time, tmp_path):
        """The probe must not leak a connection on the 5-second dashboard poll.

        sqlite3.Connection is a C type and rejects attribute assignment, so the
        tracker is a delegating wrapper rather than a patched instance.
        """
        import sqlite3

        db = tmp_path / "checkpoints.db"
        monkeypatch.setattr(health_mod, "CHECKPOINT_DB_PATH", str(db))
        closed = []
        real_connect = sqlite3.connect

        class _TrackingConn:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, *a, **k):
                return self._inner.execute(*a, **k)

            def close(self):
                closed.append(True)
                self._inner.close()

            def __getattr__(self, name):
                return getattr(self._inner, name)

        monkeypatch.setattr(sqlite3, "connect",
                            lambda *a, **k: _TrackingConn(real_connect(*a, **k)))
        fake_time([0.0, _DELTA])
        assert HealthChecker().check_sqlite()["status"] == "ok"
        assert closed == [True]


# ═══════════════════════════════════════════════════════════════
# check_chroma
# ═══════════════════════════════════════════════════════════════

class _FakeSemanticStore:
    def __init__(self, healthy=True, exc=None):
        self._healthy, self._exc = healthy, exc

    def is_healthy(self):
        if self._exc:
            raise self._exc
        return self._healthy


class TestCheckChroma:
    def test_no_store_reports_path_writable(self, monkeypatch, fake_time, tmp_path):
        persist = tmp_path / "chroma"
        monkeypatch.setattr(health_mod, "CHROMA_PERSIST_PATH", str(persist))
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_chroma(exact_store=None, semantic_store=None)
        assert result["status"] == "ok"
        assert "persist path writable" in result["message"]
        assert str(persist) in result["message"]
        assert result["latency_ms"] == _EXPECTED_MS

    def test_missing_parents_are_created(self, monkeypatch, fake_time, tmp_path):
        """``mkdir(parents=True, ...)`` on a fresh checkout."""
        persist = tmp_path / "a" / "b" / "chroma"
        monkeypatch.setattr(health_mod, "CHROMA_PERSIST_PATH", str(persist))
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_chroma()
        assert result["status"] == "ok", result
        assert persist.exists()

    def test_existing_dir_does_not_raise(self, monkeypatch, fake_time, tmp_path):
        """``exist_ok=True`` — Chroma's dir exists on every poll after the first."""
        persist = tmp_path / "chroma"
        persist.mkdir()
        monkeypatch.setattr(health_mod, "CHROMA_PERSIST_PATH", str(persist))
        fake_time([0.0, _DELTA])
        assert HealthChecker().check_chroma()["status"] == "ok"

    def test_healthy_store_is_ok(self, monkeypatch, fake_time, tmp_path):
        monkeypatch.setattr(health_mod, "CHROMA_PERSIST_PATH", str(tmp_path / "chroma"))
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_chroma(
            semantic_store=_FakeSemanticStore(healthy=True))
        assert result["status"] == "ok"
        assert "ready with embeddings" in result["message"]
        assert result["latency_ms"] == _EXPECTED_MS

    def test_unhealthy_store_is_warn_not_error(self, monkeypatch, fake_time, tmp_path):
        """Embeddings down degrades quality but the pipeline still runs, so this
        must be 'warn'. Escalating it to error would block startup."""
        monkeypatch.setattr(health_mod, "CHROMA_PERSIST_PATH", str(tmp_path / "chroma"))
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_chroma(
            semantic_store=_FakeSemanticStore(healthy=False))
        assert result["status"] == "warn"
        assert "embeddings unavailable" in result["message"]
        assert result["latency_ms"] == _EXPECTED_MS

    def test_store_probe_exception_is_warn(self, monkeypatch, fake_time, tmp_path):
        """The whole function degrades to warn, never error — a Chroma outage
        must not be able to refuse startup."""
        monkeypatch.setattr(health_mod, "CHROMA_PERSIST_PATH", str(tmp_path / "chroma"))
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_chroma(
            semantic_store=_FakeSemanticStore(exc=RuntimeError("chroma http down")))
        assert result["status"] == "warn"
        assert "chroma http down" in result["message"]
        assert result.get("critical") is not True
        assert result["latency_ms"] == _EXPECTED_MS

    def test_unwritable_persist_path_is_warn(self, monkeypatch, fake_time, tmp_path):
        blocker = tmp_path / "chroma"
        blocker.write_text("occupied by a file")
        monkeypatch.setattr(health_mod, "CHROMA_PERSIST_PATH", str(blocker))
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_chroma()
        assert result["status"] == "warn"
        assert result["latency_ms"] == _EXPECTED_MS

    def test_exact_store_alone_still_reports_writable(self, monkeypatch, fake_time, tmp_path):
        """Only ``semantic_store`` gates the embedding probe; passing an exact
        store must not change the branch taken."""
        monkeypatch.setattr(health_mod, "CHROMA_PERSIST_PATH", str(tmp_path / "chroma"))
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_chroma(exact_store=object(), semantic_store=None)
        assert result["status"] == "ok"
        assert "persist path writable" in result["message"]


# ═══════════════════════════════════════════════════════════════
# check_memory (module-level)
#
# psutil is NOT installed on this machine, so the ImportError branch is the one
# that actually runs in production here — the numeric branches were dead code as
# far as the suite was concerned. Injecting a fake module makes them live.
# ═══════════════════════════════════════════════════════════════

import sys  # noqa: E402  (kept next to the tests that need it)

_USED_BYTES = 10_869_488_484   # 10.12 GiB at 1024**3, 10.123 unrounded, 10.09 at 1025**3
_TOTAL_BYTES = 34_849_364_639  # 32.46 GiB at 1024**3, 32.456 unrounded, 32.36 at 1025**3


def _install_fake_psutil(monkeypatch, percent, used=_USED_BYTES, total=_TOTAL_BYTES,
                         exc=None):
    import types

    class _VM:
        pass

    vm = _VM()
    vm.used, vm.total, vm.percent = used, total, percent
    fake = types.ModuleType("psutil")

    def _virtual_memory():
        if exc:
            raise exc
        return vm

    fake.virtual_memory = _virtual_memory
    monkeypatch.setitem(sys.modules, "psutil", fake)
    return vm


class TestCheckMemory:
    def test_low_usage_is_ok(self, monkeypatch, fake_time):
        _install_fake_psutil(monkeypatch, percent=42)
        fake_time([0.0, _DELTA])
        result = check_memory()
        assert result["status"] == "ok"
        assert result["percent"] == 42
        assert result["latency_ms"] == _EXPECTED_MS

    def test_gb_conversion_uses_1024_cubed_and_two_decimals(self, monkeypatch, fake_time):
        """Pins the unit divisor and the rounding width at once.

        1024→1025 shifts used_gb to 10.09; ``**3``→``**4`` collapses it to 0.0;
        ``round(_,2)``→``round(_,3)`` yields 10.123. All three must read 10.12.
        """
        _install_fake_psutil(monkeypatch, percent=42)
        fake_time([0.0, _DELTA])
        result = check_memory()
        assert result["used_gb"] == 10.12
        assert result["total_gb"] == 32.46
        assert "10.12/32.46 GB (42%)" == result["message"]

    def test_boundary_80_is_ok(self, monkeypatch, fake_time):
        """80% exactly is not "high" (the test is ``> 80``)."""
        _install_fake_psutil(monkeypatch, percent=80)
        fake_time([0.0, _DELTA])
        result = check_memory()
        assert result["status"] == "ok", result
        assert result["percent"] == 80

    def test_81_is_warn(self, monkeypatch, fake_time):
        """Kills the 80→81 threshold drift: at 81 the mutated form says ok."""
        _install_fake_psutil(monkeypatch, percent=81)
        fake_time([0.0, _DELTA])
        result = check_memory()
        assert result["status"] == "warn"
        assert "Memory usage high" in result["message"]
        assert result["percent"] == 81
        assert result["latency_ms"] == _EXPECTED_MS

    def test_boundary_95_is_warn_not_error(self, monkeypatch, fake_time):
        """95% exactly is not critical. Kills ``>`` → ``<=`` on line 356."""
        _install_fake_psutil(monkeypatch, percent=95)
        fake_time([0.0, _DELTA])
        result = check_memory()
        assert result["status"] == "warn", result

    def test_96_is_error(self, monkeypatch, fake_time):
        """Kills the 95→96 drift: at 96 the mutated threshold reports warn."""
        _install_fake_psutil(monkeypatch, percent=96)
        fake_time([0.0, _DELTA])
        result = check_memory()
        assert result["status"] == "error"
        assert "Memory usage critical" in result["message"]
        assert result["used_gb"] == 10.12
        assert result["total_gb"] == 32.46
        assert result["latency_ms"] == _EXPECTED_MS

    def test_error_branch_carries_no_critical_flag(self, monkeypatch, fake_time):
        """Memory pressure is reported but must not block startup: check_all only
        escalates on critical=True, and this check deliberately never sets it."""
        _install_fake_psutil(monkeypatch, percent=99)
        fake_time([0.0, _DELTA])
        result = check_memory()
        assert result["status"] == "error"
        assert "critical" not in result

    def test_missing_psutil_is_warn(self, monkeypatch, fake_time):
        """The real path on this machine and in python:3.11-slim."""
        monkeypatch.setitem(sys.modules, "psutil", None)  # makes `import` raise
        fake_time([0.0, _DELTA])
        result = check_memory()
        assert result["status"] == "warn"
        assert "psutil not installed" in result["message"]
        assert result["latency_ms"] == _EXPECTED_MS

    def test_psutil_runtime_error_is_warn(self, monkeypatch, fake_time):
        _install_fake_psutil(monkeypatch, percent=0, exc=OSError("sysctl failed"))
        fake_time([0.0, _DELTA])
        result = check_memory()
        assert result["status"] == "warn"
        assert "sysctl failed" in result["message"]
        assert result["latency_ms"] == _EXPECTED_MS


# ═══════════════════════════════════════════════════════════════
# check_disk_io (module-level)
# ═══════════════════════════════════════════════════════════════

class _RecordingOs:
    """``os`` stand-in that records the urandom length and returns fixed bytes."""

    def __init__(self, requests):
        self._requests = requests

    def urandom(self, n):
        self._requests.append(n)
        return b"\xab" * n

    def __getattr__(self, name):
        import os as _real_os
        return getattr(_real_os, name)


class TestCheckDiskIo:
    def test_healthy_roundtrip_is_ok(self, monkeypatch, fake_time, tmp_path):
        out = tmp_path / "output"
        out.mkdir()
        monkeypatch.setattr(health_mod, "OUTPUT_DIR", out)
        fake_time([0.0, _DELTA])
        result = check_disk_io()
        assert result["status"] == "ok"
        assert "Disk I/O healthy" in result["message"]
        assert result["latency_ms"] == _EXPECTED_MS
        assert not (out / ".disk_io_probe").exists(), "probe file was not cleaned up"

    def test_probe_uses_64_random_bytes(self, monkeypatch, fake_time, tmp_path):
        """The payload must stay small — this runs on a 5-second poll.

        Pins ``os.urandom(64)``: drifting to 65 would silently grow every probe.
        """
        out = tmp_path / "output"
        out.mkdir()
        monkeypatch.setattr(health_mod, "OUTPUT_DIR", out)
        requests = []
        monkeypatch.setattr(health_mod, "os", _RecordingOs(requests))
        fake_time([0.0, _DELTA])
        assert check_disk_io()["status"] == "ok"
        assert requests == [64]

    def test_readback_mismatch_is_critical_error(self, monkeypatch, fake_time, tmp_path):
        """Silent corruption is the one failure this probe exists to catch."""
        out = tmp_path / "output"
        out.mkdir()
        monkeypatch.setattr(health_mod, "OUTPUT_DIR", out)
        monkeypatch.setattr(Path, "read_bytes", lambda self: b"corrupted-by-fs")
        fake_time([0.0, _DELTA])
        result = check_disk_io()
        assert result["status"] == "error"
        assert result["critical"] is True
        assert "Read-back mismatch" in result["message"]
        assert result["latency_ms"] == _EXPECTED_MS

    def test_slow_but_correct_is_warn(self, monkeypatch, fake_time, tmp_path):
        """5000.5 ms is over the 5 s deadline → warn.

        Kills the 5000→5001 drift (5000.5 > 5001 is False) and ``>`` → ``<=``.
        """
        out = tmp_path / "output"
        out.mkdir()
        monkeypatch.setattr(health_mod, "OUTPUT_DIR", out)
        fake_time([0.0, 5.0005])
        result = check_disk_io()
        assert result["status"] == "warn"
        assert "slow" in result["message"]
        assert result["latency_ms"] == 5000.5

    def test_fast_io_is_not_warn(self, monkeypatch, fake_time, tmp_path):
        """Complement of the above: a normal 123.5 ms probe must stay 'ok',
        which is what kills ``elapsed > 5000`` flipped to ``<=``."""
        out = tmp_path / "output"
        out.mkdir()
        monkeypatch.setattr(health_mod, "OUTPUT_DIR", out)
        fake_time([0.0, _DELTA])
        assert check_disk_io()["status"] == "ok"

    def test_unwritable_output_dir_is_critical_error(self, monkeypatch, fake_time, tmp_path):
        out = tmp_path / "output"  # does not exist; write_bytes raises
        monkeypatch.setattr(health_mod, "OUTPUT_DIR", out)
        fake_time([0.0, _DELTA])
        result = check_disk_io()
        assert result["status"] == "error"
        assert result["critical"] is True
        assert result["latency_ms"] == _EXPECTED_MS

    def test_unlink_failure_is_critical_error(self, monkeypatch, fake_time, tmp_path):
        out = tmp_path / "output"
        out.mkdir()
        monkeypatch.setattr(health_mod, "OUTPUT_DIR", out)

        def _explode(self, *a, **k):
            raise PermissionError("cannot delete")

        monkeypatch.setattr(Path, "unlink", _explode)
        fake_time([0.0, _DELTA])
        result = check_disk_io()
        assert result["status"] == "error"
        assert result["critical"] is True


# ═══════════════════════════════════════════════════════════════
# Live probe request shape
#
# The probe is opt-in and billable, so its cost envelope is part of its
# contract: one token, a five second ceiling, and an error body short enough to
# fit in a log line. None of that was asserted before.
# ═══════════════════════════════════════════════════════════════

class _RecordingClient:
    """httpx.Client stand-in capturing constructor kwargs and the posted payload."""

    instances = []

    def __init__(self, *args, **kwargs):
        self.init_args, self.init_kwargs = args, kwargs
        self.posts = []
        _RecordingClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None, headers=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        return self.response


def _install_recording_client(monkeypatch, response):
    _RecordingClient.instances = []
    _RecordingClient.response = response
    monkeypatch.setattr(health_mod.httpx, "Client", _RecordingClient)
    return _RecordingClient


class _Resp:
    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text


class TestLiveProbeRequestShape:
    def test_probe_requests_exactly_one_token(self, monkeypatch, fake_time):
        """``max_tokens: 1`` is the cost guard on an opt-in billable call.

        Drifting to 2 doubles what a health check spends for no diagnostic gain.
        """
        client = _install_recording_client(monkeypatch, _Resp())
        fake_time([0.0, _DELTA])
        result = HealthChecker(live_llm=True).check_llm_api()
        assert result["status"] == "ok"
        payload = client.instances[0].posts[0]["json"]
        assert payload["max_tokens"] == 1
        assert payload["temperature"] == 0
        assert payload["messages"] == [{"role": "user", "content": "Hi"}]

    def test_probe_uses_five_second_timeout(self, monkeypatch, fake_time):
        """/api/health is unauthenticated; a hung provider must not tie up a
        worker indefinitely."""
        client = _install_recording_client(monkeypatch, _Resp())
        fake_time([0.0, _DELTA])
        HealthChecker(live_llm=True).check_llm_api()
        assert client.instances[0].init_kwargs["timeout"] == 5.0

    def test_probe_sends_bearer_token_to_completions_endpoint(self, monkeypatch, fake_time):
        client = _install_recording_client(monkeypatch, _Resp())
        fake_time([0.0, _DELTA])
        HealthChecker(live_llm=True).check_llm_api()
        post = client.instances[0].posts[0]
        assert post["url"].endswith("/chat/completions")
        assert post["headers"]["Authorization"].startswith("Bearer ")
        assert post["headers"]["Content-Type"] == "application/json"
        assert health_mod.QWEN_MODEL == post["json"]["model"]

    def test_success_latency_is_measured(self, monkeypatch, fake_time):
        _install_recording_client(monkeypatch, _Resp())
        fake_time([0.0, _DELTA])
        result = HealthChecker(live_llm=True).check_llm_api()
        assert result["live_probe"] is True
        assert result["latency_ms"] == _EXPECTED_MS

    def test_http_error_body_is_truncated_to_200_chars(self, monkeypatch, fake_time):
        """A provider can return a multi-kilobyte HTML error page; the message
        goes into a JSON health response and a log line."""
        long_body = "Arrearage: " + ("x" * 400)
        _install_recording_client(monkeypatch, _Resp(status_code=400, text=long_body))
        fake_time([0.0, _DELTA])
        result = HealthChecker(live_llm=True).check_llm_api()
        assert result["status"] == "error"
        assert result["live_probe"] is True
        assert result["message"].startswith("HTTP 400: ")
        body_part = result["message"][len("HTTP 400: "):]
        assert len(body_part) == 200
        assert result["latency_ms"] == _EXPECTED_MS

    def test_http_error_is_not_treated_as_success(self, monkeypatch, fake_time):
        """Pins ``status_code == 200``: mutated to ``!=`` a 400 would report ok,
        and mutated to ``== 201`` a working provider would report an error."""
        _install_recording_client(monkeypatch, _Resp(status_code=200))
        fake_time([0.0, _DELTA])
        assert HealthChecker(live_llm=True).check_llm_api()["status"] == "ok"

        _install_recording_client(monkeypatch, _Resp(status_code=503, text="down"))
        fake_time([0.0, _DELTA])
        assert HealthChecker(live_llm=True).check_llm_api()["status"] == "error"

    def test_timeout_branch_marks_live_probe(self, monkeypatch, fake_time):
        """``live_probe: True`` on the timeout path proves a real call was
        attempted — without it a timeout is indistinguishable from the free
        configuration check."""
        import httpx

        class _TimeoutClient:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def post(self, *a, **k):
                raise httpx.TimeoutException("timed out")

        monkeypatch.setattr(health_mod.httpx, "Client", _TimeoutClient)
        fake_time([0.0, _DELTA])
        result = HealthChecker(live_llm=True).check_llm_api()
        assert result["status"] == "error"
        assert result["live_probe"] is True
        assert "timed out" in result["message"]
        assert result["latency_ms"] == _EXPECTED_MS

    def test_exception_branch_marks_live_probe(self, monkeypatch, fake_time):
        class _BoomClient:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def post(self, *a, **k):
                raise ConnectionError("network unreachable")

        monkeypatch.setattr(health_mod.httpx, "Client", _BoomClient)
        fake_time([0.0, _DELTA])
        result = HealthChecker(live_llm=True).check_llm_api()
        assert result["status"] == "error"
        assert result["live_probe"] is True
        assert result["latency_ms"] == _EXPECTED_MS

    def test_free_config_check_latency_is_measured(self, monkeypatch, fake_time):
        """The default (zero-cost) path reports latency too, so its arithmetic
        is pinned even though no network is involved."""
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_llm_api()
        assert result["live_probe"] is False
        assert result["status"] == "ok"
        assert result["latency_ms"] == _EXPECTED_MS

    def test_missing_key_latency_is_measured(self, monkeypatch, fake_time):
        monkeypatch.setattr(health_mod, "LLM_API_KEY", "")
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_llm_api()
        assert result["status"] == "error"
        assert result["critical"] is True
        assert result["latency_ms"] == _EXPECTED_MS

    def test_malformed_url_latency_is_measured(self, monkeypatch, fake_time):
        monkeypatch.setattr(health_mod, "LLM_BASE_URL", "ftp://nope")
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_llm_api()
        assert result["status"] == "error"
        assert result["critical"] is True
        assert result["latency_ms"] == _EXPECTED_MS

    def test_trailing_slash_in_base_url_is_stripped(self, monkeypatch, fake_time):
        client = _install_recording_client(monkeypatch, _Resp())
        monkeypatch.setattr(health_mod, "LLM_BASE_URL", "https://example.test/v1/")
        fake_time([0.0, _DELTA])
        HealthChecker(live_llm=True).check_llm_api()
        assert client.instances[0].posts[0]["url"] == "https://example.test/v1/chat/completions"


# ═══════════════════════════════════════════════════════════════
# check_config latency arithmetic
# ═══════════════════════════════════════════════════════════════

class TestCheckConfigLatency:
    def test_ok_path_latency_is_measured(self, monkeypatch, fake_time):
        monkeypatch.setenv("LLM_API_KEY", "sk-qwen")
        monkeypatch.setenv("GLM_API_KEY", "sk-glm")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_config()
        assert result["status"] == "ok"
        assert result["latency_ms"] == _EXPECTED_MS

    def test_error_path_latency_is_measured(self, monkeypatch, fake_time):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("GLM_API_KEY", raising=False)
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_config()
        assert result["status"] == "error"
        assert result["critical"] is True
        assert result["latency_ms"] == _EXPECTED_MS

    def test_warn_path_latency_is_measured(self, monkeypatch, fake_time):
        monkeypatch.setenv("LLM_API_KEY", "sk-qwen")
        monkeypatch.setenv("GLM_API_KEY", "sk-glm")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-stale")
        fake_time([0.0, _DELTA])
        result = HealthChecker().check_config()
        assert result["status"] == "warn"
        assert result["latency_ms"] == _EXPECTED_MS

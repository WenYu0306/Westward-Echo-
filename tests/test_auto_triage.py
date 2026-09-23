"""Tests for scripts/auto_triage.py.

The regression that matters: this script used to run at *import* time, so
importing it DELETED rows from the production glossary, made a real billable
API call, and bulk-set ``status='confirmed'``. These tests lock the safety
properties down.
"""

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "auto_triage.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("auto_triage_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def triage_db(tmp_path):
    """A glossary DB with a few pending terms and some metadata rows."""
    db = tmp_path / "checkpoints.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE exact_glossary ("
        "term_cn TEXT PRIMARY KEY, term_en TEXT, category TEXT, "
        "context TEXT, status TEXT)"
    )
    conn.executemany(
        "INSERT INTO exact_glossary VALUES (?,?,?,?,?)",
        [
            ("苏念", "Su Nian", "character", "", "pending_review"),
            ("裴衍舟", "Pei Yanzhou", "character", "", "pending_review"),
            ("霸总", "Alpha CEO", "culture", "", "pending_review"),
            ("界玉", "Jie Yu", "author_name", "", "pending_review"),
            ("大殿", "the grand hall", "location", "", "confirmed"),
        ],
    )
    conn.commit()
    conn.close()
    return db


# ═══════════════════════════════════════════════════════════════
# The core safety property: importing must have no side effects
# ═══════════════════════════════════════════════════════════════

class TestImportHasNoSideEffects:
    def test_import_does_not_open_the_database(self, monkeypatch, tmp_path):
        """Importing used to connect to data/checkpoints.db and DELETE rows."""
        opened = []
        real_connect = sqlite3.connect

        def _spy(*args, **kwargs):
            opened.append(args)
            return real_connect(*args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", _spy)
        _load_module()
        assert opened == [], f"import opened a database: {opened}"

    def test_import_does_not_touch_the_network(self, monkeypatch):
        import socket

        def _boom(*args, **kwargs):
            raise AssertionError("import attempted a network connection")

        monkeypatch.setattr(socket.socket, "connect", _boom)
        _load_module()

    def test_module_exposes_main_guard(self):
        """The script body must be inside main(), not at module level."""
        source = _SCRIPT.read_text(encoding="utf-8")
        assert 'if __name__ == "__main__":' in source
        # No top-level executable call into the LLM.
        top_level_calls = [
            line for line in source.splitlines()
            if line.startswith("resp = httpx.post") or line.startswith("conn = sqlite3")
        ]
        assert top_level_calls == [], (
            f"found top-level side-effecting statements: {top_level_calls}"
        )


# ═══════════════════════════════════════════════════════════════
# Default behaviour: dry run, no API, no writes
# ═══════════════════════════════════════════════════════════════

class TestDryRunDefault:
    def test_no_apply_flag_makes_no_api_call(self, triage_db, monkeypatch):
        mod = _load_module()

        def _boom(*args, **kwargs):
            raise AssertionError("--apply was not passed, so no LLM call is allowed")

        monkeypatch.setattr(mod, "_call_llm", _boom)
        rc = mod.main(["--db", str(triage_db)])
        assert rc == 0

    def test_dry_run_does_not_modify_rows(self, triage_db, monkeypatch, capsys):
        mod = _load_module()
        monkeypatch.setattr(mod, "_call_llm",
                            lambda batch: (_ for _ in ()).throw(AssertionError("no API")))

        before = _snapshot(triage_db)
        mod.main(["--db", str(triage_db)])
        assert _snapshot(triage_db) == before
        assert "DRY RUN" in capsys.readouterr().out

    def test_missing_database_exits_cleanly(self, tmp_path):
        mod = _load_module()
        with pytest.raises(SystemExit) as exc:
            mod.main(["--db", str(tmp_path / "nope.db")])
        assert "not found" in str(exc.value)

    def test_nothing_pending_short_circuits(self, tmp_path, monkeypatch, capsys):
        db = tmp_path / "g.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE exact_glossary (term_cn TEXT PRIMARY KEY, "
                     "term_en TEXT, category TEXT, context TEXT, status TEXT)")
        conn.execute("INSERT INTO exact_glossary VALUES "
                     "('大殿','the grand hall','location','','confirmed')")
        conn.commit()
        conn.close()

        mod = _load_module()
        monkeypatch.setattr(mod, "_call_llm",
                            lambda batch: (_ for _ in ()).throw(AssertionError("no API")))
        assert mod.main(["--db", str(db)]) == 0
        assert "nothing to triage" in capsys.readouterr().out


# ═══════════════════════════════════════════════════════════════
# --apply path: writes must follow the LLM's decisions exactly
# ═══════════════════════════════════════════════════════════════

def _snapshot(db):
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT term_cn, term_en, category, status FROM exact_glossary ORDER BY term_cn"
    ).fetchall()
    conn.close()
    return rows


class TestApplyPath:
    def _stub_llm(self, mod, monkeypatch, decisions, recorder=None):
        def _fake(batch):
            if recorder is not None:
                recorder.append(batch)
            return {"decisions": decisions}

        monkeypatch.setattr(mod, "_call_llm", _fake)

    def test_keep_confirms_the_term(self, triage_db, monkeypatch):
        mod = _load_module()
        self._stub_llm(mod, monkeypatch, [
            {"term_cn": "苏念", "decision": "KEEP"},
        ])
        mod.main(["--db", str(triage_db), "--apply"])

        conn = sqlite3.connect(triage_db)
        status = conn.execute(
            "SELECT status FROM exact_glossary WHERE term_cn='苏念'").fetchone()[0]
        conn.close()
        assert status == "confirmed"

    def test_fix_updates_rendering_and_confirms(self, triage_db, monkeypatch):
        mod = _load_module()
        self._stub_llm(mod, monkeypatch, [
            {"term_cn": "霸总", "decision": "FIX",
             "fixed_en": "the domineering tycoon"},
        ])
        mod.main(["--db", str(triage_db), "--apply"])

        conn = sqlite3.connect(triage_db)
        row = conn.execute(
            "SELECT term_en, status FROM exact_glossary WHERE term_cn='霸总'").fetchone()
        conn.close()
        assert row == ("the domineering tycoon", "confirmed")

    def test_fix_without_replacement_is_skipped(self, triage_db, monkeypatch, capsys):
        mod = _load_module()
        self._stub_llm(mod, monkeypatch, [{"term_cn": "霸总", "decision": "FIX"}])
        mod.main(["--db", str(triage_db), "--apply"])

        conn = sqlite3.connect(triage_db)
        row = conn.execute(
            "SELECT term_en, status FROM exact_glossary WHERE term_cn='霸总'").fetchone()
        conn.close()
        assert row == ("Alpha CEO", "pending_review")
        assert "skipped" in capsys.readouterr().out

    def test_reject_deletes_by_default(self, triage_db, monkeypatch):
        mod = _load_module()
        self._stub_llm(mod, monkeypatch, [{"term_cn": "界玉", "decision": "REJECT"}])
        mod.main(["--db", str(triage_db), "--apply"])

        assert "界玉" not in [r[0] for r in _snapshot(triage_db)]

    def test_no_delete_suppresses_reject(self, triage_db, monkeypatch, capsys):
        """--no-delete must make REJECT report-only — deletion is irreversible."""
        mod = _load_module()
        self._stub_llm(mod, monkeypatch, [{"term_cn": "界玉", "decision": "REJECT"}])
        mod.main(["--db", str(triage_db), "--apply", "--no-delete"])

        assert "界玉" in [r[0] for r in _snapshot(triage_db)]
        assert "would DELETE" in capsys.readouterr().out

    def test_unknown_decision_is_ignored(self, triage_db, monkeypatch):
        mod = _load_module()
        self._stub_llm(mod, monkeypatch, [
            {"term_cn": "苏念", "decision": "MAYBE"},
            {"term_cn": "", "decision": "KEEP"},
        ])
        before = _snapshot(triage_db)
        mod.main(["--db", str(triage_db), "--apply"])
        assert _snapshot(triage_db) == before

    def test_batch_is_capped(self, triage_db, monkeypatch):
        mod = _load_module()
        seen = []
        self._stub_llm(mod, monkeypatch, [], recorder=seen)
        mod.main(["--db", str(triage_db), "--apply"])
        assert len(seen) == 1
        assert len(seen[0]) <= mod.BATCH_SIZE

    def test_confirmed_terms_are_not_re_reviewed(self, triage_db, monkeypatch):
        mod = _load_module()
        seen = []
        self._stub_llm(mod, monkeypatch, [], recorder=seen)
        mod.main(["--db", str(triage_db), "--apply"])
        reviewed = {t["term_cn"] for t in seen[0]}
        assert "大殿" not in reviewed, "already-confirmed term was re-triaged"


# ═══════════════════════════════════════════════════════════════
# Provider configuration: DeepSeek is gone
# ═══════════════════════════════════════════════════════════════

class TestProviderConfig:
    def test_no_functional_deepseek_reference_remains(self):
        """The abandoned provider must not be *used*.

        Prose in the docstring that explains the history is fine and useful;
        what matters is that no code reads a DEEPSEEK_* variable or names a
        deepseek model.
        """
        import ast

        source = _SCRIPT.read_text(encoding="utf-8")
        tree = ast.parse(source)

        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                names.add(node.value)

        offenders = [
            n for n in names
            if "deepseek" in n.lower() and len(n) < 200  # ignore long prose blobs
        ]
        assert not offenders, f"code still references DeepSeek: {offenders}"

    def test_uses_configured_provider(self):
        source = _SCRIPT.read_text(encoding="utf-8")
        assert "from src.config import LLM_API_KEY, LLM_BASE_URL, QWEN_MODEL" in source

    def test_missing_key_refuses_to_call(self, monkeypatch, triage_db):
        """No credential → exit before any HTTP request is built."""
        mod = _load_module()
        import src.config as cfg

        monkeypatch.setattr(cfg, "LLM_API_KEY", "")
        with pytest.raises(SystemExit) as exc:
            mod._call_llm([{"term_cn": "苏念"}])
        assert "LLM_API_KEY" in str(exc.value)

    def test_llm_failure_raises_without_writing(self, monkeypatch, triage_db):
        mod = _load_module()

        class _Resp:
            is_success = False
            status_code = 400
            text = "Arrearage"

        import httpx
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
        import src.config as cfg
        monkeypatch.setattr(cfg, "LLM_API_KEY", "sk-fake")

        before = _snapshot(triage_db)
        with pytest.raises(RuntimeError, match="LLM triage failed"):
            mod._call_llm([{"term_cn": "苏念"}])
        assert _snapshot(triage_db) == before


class TestResponseParsing:
    """_call_llm must tolerate the ```json fence models habitually emit."""

    def _stub_post(self, monkeypatch, content):
        import httpx

        import src.config as cfg

        monkeypatch.setattr(cfg, "LLM_API_KEY", "sk-fake")

        class _Resp:
            is_success = True
            status_code = 200

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": content}}]}

        monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())

    def test_plain_json(self, monkeypatch):
        mod = _load_module()
        self._stub_post(monkeypatch, '{"decisions": [{"term_cn": "苏念", "decision": "KEEP"}]}')
        assert mod._call_llm([{"term_cn": "苏念"}])["decisions"][0]["decision"] == "KEEP"

    def test_fenced_json(self, monkeypatch):
        mod = _load_module()
        self._stub_post(monkeypatch,
                        '```json\n{"decisions": [{"term_cn": "苏念", "decision": "KEEP"}]}\n```')
        assert mod._call_llm([{"term_cn": "苏念"}])["decisions"][0]["term_cn"] == "苏念"

    def test_fenced_without_language_tag(self, monkeypatch):
        mod = _load_module()
        self._stub_post(monkeypatch,
                        '```\n{"decisions": [{"term_cn": "苏念", "decision": "FIX"}]}\n```')
        assert mod._call_llm([{"term_cn": "苏念"}])["decisions"][0]["decision"] == "FIX"

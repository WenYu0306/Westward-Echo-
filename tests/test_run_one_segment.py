"""Regression tests for scripts/run_one_segment.py.

Locks down the crash and the false-completion report that made this script
unusable as a production runner:

* ``UnboundLocalError: local variable 'glossary'`` — when every chapter is
  already checkpointed the ``while`` body never runs, yet the summary print
  referenced a variable assigned only inside it.
* ``ALL DONE: N chapters`` printed unconditionally, even when chapters had been
  skipped by ``continue`` after an error.
* Chinese chapter titles written into the English deliverable.
* Empty/stub translations written into the deliverable anyway.
"""

import importlib.util
import json
import os
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "run_one_segment.py"


def _load_module():
    """Import scripts/run_one_segment.py by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("run_one_segment_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeChapter:
    """Stands in for src.chapter_splitter's chapter object.

    ``action`` matters: main() filters ``c.action != ParagraphTag.SKIP`` before
    translating, so a fake without it crashes the filter rather than the code
    under test.
    """

    def __init__(self, index, title, content, action=None):
        self.index = index
        self.title = title
        self.content = content
        self.word_count = len(content)
        if action is None:
            from src.chapter_splitter import ParagraphTag
            action = ParagraphTag.CHAPTER
        self.action = action


@pytest.fixture
def script_env(monkeypatch, tmp_path):
    """Load the script with its novel/checkpoint paths pointed at tmp_path.

    Nothing here reaches a provider: detect_and_read and split_chapters are
    stubbed, and TranslationAgent is replaced by a recorder whose
    translate_chapter returns canned results.
    """
    mod = _load_module()

    ckpt_dir = tmp_path / "out"
    ckpt_dir.mkdir()
    monkeypatch.setattr(mod, "CKPT_DIR", str(ckpt_dir))
    monkeypatch.setattr(mod, "NOVEL_PATH", str(tmp_path / "novel.txt"))

    calls = []

    class _FakeStore:
        def __init__(self):
            self._d = {"苏念": "Su Nian"}

        def to_dict(self):
            return dict(self._d)

        def snapshot(self):
            return json.dumps(self._d)

    class _FakeAgent:
        def __init__(self, book_id=None, **kwargs):
            self.book_id = book_id
            self.exact_store = _FakeStore()
            self._results = list(getattr(_FakeAgent, "queued_results", []))

        def load_glossary_snapshot(self, snapshot):
            pass

        def translate_chapter(self, **kwargs):
            calls.append(kwargs)
            return _FakeAgent._next_result(kwargs)

    monkeypatch.setattr(mod, "TranslationAgent", _FakeAgent)
    monkeypatch.setattr(mod, "detect_and_read",
                        lambda p: ("第一章 测试\n\n苏念醒来。\n", "utf-8"))

    return {
        "mod": mod,
        "monkeypatch": monkeypatch,
        "ckpt_dir": ckpt_dir,
        "calls": calls,
        "agent_cls": _FakeAgent,
    }


def _queue_results(agent_cls, results):
    """Make the fake agent return canned translate_chapter results in order."""
    queue = list(results)

    def _next(kwargs):
        if not queue:
            return {"translated_text": "", "chapter_title_en": "", "chapter_summary": ""}
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    agent_cls._next_result = staticmethod(_next)


GOOD = ("The grey sky pressed down on the town and the wind carried dust "
        "through the empty street. ") * 6


class TestNoCrashWhenNothingToTranslate:
    def test_all_chapters_already_checkpointed(self, script_env, capsys):
        """The original bug: while-body never runs → glossary undefined → crash."""
        mod = script_env["mod"]
        # split_chapters returns nothing translatable, so `chapters` is empty.
        script_env["monkeypatch"].setattr(mod, "split_chapters", lambda text: [])

        mod.main()  # must not raise UnboundLocalError

        # Proof the pre-loop initialisation did its job: the summary prints a
        # real term count instead of dying on an unbound name. The glossary
        # *file* is only written inside the loop, so with nothing to translate
        # there is legitimately no deliverable — only the report matters here.
        out = capsys.readouterr().out
        assert "Glossary: 1 terms" in out
        assert "ALL DONE: 0/0" in out

    def test_checkpoint_says_everything_done(self, script_env):
        """Same code path reached via a resume checkpoint instead of an empty book."""
        mod = script_env["mod"]
        script_env["monkeypatch"].setattr(
            mod, "split_chapters",
            lambda text: [_FakeChapter(1, "第一章 测试", "苏念醒来。")],
        )
        # last_idx=0 means chapter 1 is done → start=1 → while never runs.
        (script_env["ckpt_dir"] / "_checkpoint.json").write_text(
            json.dumps({"last_idx": 0, "glossary_snapshot": "{}",
                        "previous_summary": ""}),
            encoding="utf-8",
        )

        mod.main()  # must not raise


class TestFailureReporting:
    def test_failed_chapter_is_not_reported_as_done(self, script_env, capsys):
        mod = script_env["mod"]
        script_env["monkeypatch"].setattr(
            mod, "split_chapters",
            lambda text: [_FakeChapter(1, "第一章 测试", "苏念醒来。")],
        )
        _queue_results(script_env["agent_cls"], [RuntimeError("provider refused")])

        with pytest.raises(SystemExit) as exc:
            mod.main()

        assert exc.value.code == 1, "incomplete run must exit non-zero"
        out = capsys.readouterr().out
        assert "INCOMPLETE" in out
        assert "0/1" in out
        assert "ALL DONE" not in out

    def test_full_success_reports_all_done(self, script_env, capsys):
        mod = script_env["mod"]
        script_env["monkeypatch"].setattr(
            mod, "split_chapters",
            lambda text: [_FakeChapter(1, "第一章 测试", "苏念醒来。" * 200)],
        )
        _queue_results(script_env["agent_cls"], [{
            "translated_text": GOOD,
            "chapter_title_en": "The Grey Sky",
            "chapter_summary": "Su Nian wakes.",
        }])

        mod.main()

        out = capsys.readouterr().out
        assert "ALL DONE: 1/1" in out


class TestDeliverableContent:
    def _read_deliverable(self, script_env):
        path = script_env["ckpt_dir"] / "limitless_horror_en.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def test_chinese_title_never_written(self, script_env):
        """ZERO-CHINESE RULE: the deliverable is for English readers."""
        mod = script_env["mod"]
        script_env["monkeypatch"].setattr(
            mod, "split_chapters",
            lambda text: [_FakeChapter(1, "第一章 钟楼街的游行", "苏念醒来。" * 200)],
        )
        _queue_results(script_env["agent_cls"], [{
            "translated_text": GOOD,
            "chapter_title_en": "The Parade on Bell Tower Street",
            "chapter_summary": "Su Nian wakes.",
        }])

        mod.main()

        body = self._read_deliverable(script_env)
        assert "钟楼街" not in body
        assert "第一章" not in body
        assert "## Chapter 1: The Parade on Bell Tower Street" in body

    def test_missing_english_title_falls_back_to_number_not_chinese(self, script_env):
        mod = script_env["mod"]
        script_env["monkeypatch"].setattr(
            mod, "split_chapters",
            lambda text: [_FakeChapter(7, "第七章 夜雨", "苏念醒来。" * 200)],
        )
        _queue_results(script_env["agent_cls"], [{
            "translated_text": GOOD,
            "chapter_title_en": "",          # pipeline produced no title
            "chapter_summary": "Su Nian wakes.",
        }])

        mod.main()

        body = self._read_deliverable(script_env)
        assert "夜雨" not in body
        assert "## Chapter 7: Chapter 7" in body

    def test_empty_translation_is_not_written(self, script_env):
        """This is how 空章/符号章 ended up mixed into finished books."""
        mod = script_env["mod"]
        script_env["monkeypatch"].setattr(
            mod, "split_chapters",
            lambda text: [_FakeChapter(1, "第一章 测试", "苏念醒来。" * 200)],
        )
        _queue_results(script_env["agent_cls"], [{
            "translated_text": "",
            "chapter_title_en": "Nothing",
            "chapter_summary": "",
        }])

        with pytest.raises(SystemExit):
            mod.main()

        body = self._read_deliverable(script_env)
        assert "## Chapter 1" not in body

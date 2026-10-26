"""Regression tests for scripts/run_novel.py — the real production runner.

This script produces the delivered corpus, so every defect here ships to
readers. Locked down here:

* progress/ALL DONE reported the *scanned* count, so a run that delivered 1 of
  2 chapters printed "100%" and "ALL DONE".
* Chinese titles landed in the English deliverable (H1 book title and the
  ``ch.title`` chapter-heading fallback) — the project's own #1 quality rule.
* ``glossary`` was bound only inside the ``while``, so a fully-checkpointed
  resume crashed with UnboundLocalError at the summary print.
* ``--limit N`` never cold-read anything: every ``sample_points_fn`` starts at
  position 10, so short validation runs had an empty sample set.
* the ``elif word_count < 50`` branch was unreachable dead code.
"""

import importlib.util
import json
import re
from pathlib import Path

import pytest

from src import delivery

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "run_novel.py"

GOOD = ("The grey sky pressed down on the town and the wind carried dust "
        "through the empty street. ") * 6


def _load_module():
    spec = importlib.util.spec_from_file_location("run_novel_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeChapter:
    def __init__(self, index, title, content):
        from src.chapter_splitter import ParagraphTag
        self.index = index
        self.title = title
        self.content = content
        self.word_count = len(content)
        self.action = ParagraphTag.CHAPTER


@pytest.fixture
def runner(monkeypatch, tmp_path):
    """Load run_novel.py with its output dir and novel source redirected to tmp.

    No provider is reachable: TranslationAgent is replaced by a recorder whose
    translate_chapter returns canned results, and split_chapters/detect_and_read
    are stubbed.
    """
    mod = _load_module()

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    monkeypatch.setattr(mod, "detect_and_read",
                        lambda p: ("第一章 测试\n\n苏念醒来。\n", "utf-8"))

    def _install(novel_cfg, chapters, results):
        monkeypatch.setitem(mod.NOVELS, "unit_test_book", novel_cfg)
        monkeypatch.setattr(mod, "split_chapters", lambda text: list(chapters))

        queue = list(results)

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

            def load_glossary_snapshot(self, snapshot, target_lang="en-US"):
                pass

            def translate_chapter(self, **kwargs):
                if not queue:
                    return {"translated_text": "", "chapter_title_en": "",
                            "chapter_summary": ""}
                item = queue.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item

        monkeypatch.setattr(mod, "TranslationAgent", _FakeAgent)

    def _run(argv):
        monkeypatch.setattr(mod.sys, "argv", ["run_novel.py", *argv])
        # token_ledger.summary hits data/token_ledger.jsonl; conftest already
        # repoints XIDU_TOKEN_LEDGER into tmp, so this is safe and real.
        return mod.main()

    return {"mod": mod, "install": _install, "run": _run, "out_dir": out_dir,
            "tmp": tmp_path}


def _cfg(out_dir, **overrides):
    cfg = {
        "name": "测试之书",
        "path": "unused.txt",
        "genre": "urban",
        "book_id": "unit_test_book",
        "output_dir": str(out_dir),
        "output_file": "unit_test_en.md",
        "expected_chapters": None,
        # Mirrors the real functions: first sample point is position 10.
        "sample_points_fn": lambda total: frozenset(range(10, total + 1, 50)),
    }
    cfg.update(overrides)
    return cfg


def _deliverable(out_dir):
    p = Path(out_dir) / "unit_test_en.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ═══════════════════════════════════════════════════════════════
# Zero-Chinese rule in the deliverable
# ═══════════════════════════════════════════════════════════════

class TestZeroChineseInDeliverable:
    def test_chinese_book_title_not_written(self, runner):
        """Real corpus shipped '# 地府叫我小先生 — English Translation'."""
        runner["install"](_cfg(runner["out_dir"]),
                          [_FakeChapter(1, "第一章 测试", "苏念醒来。" * 200)],
                          [{"translated_text": GOOD, "chapter_title_en": "The Grey Sky",
                            "chapter_summary": "Su Nian wakes."}])
        runner["run"](["unit_test_book"])

        body = _deliverable(runner["out_dir"])
        assert "测试之书" not in body
        assert body.startswith("# Unit Test Book — English Translation")

    def test_explicit_name_en_is_preferred(self, runner):
        runner["install"](_cfg(runner["out_dir"], name_en="The Book of Tests"),
                          [_FakeChapter(1, "第一章 测试", "苏念醒来。" * 200)],
                          [{"translated_text": GOOD, "chapter_title_en": "One",
                            "chapter_summary": ""}])
        runner["run"](["unit_test_book"])

        assert _deliverable(runner["out_dir"]).startswith(
            "# The Book of Tests — English Translation")

    def test_chinese_chapter_title_not_used_as_fallback(self, runner):
        """When the pipeline gives no English title, fall back to ASCII — not
        to the Chinese heading. Real corpus had '## Chapter 0: Prologue（楔子）'."""
        runner["install"](_cfg(runner["out_dir"]),
                          [_FakeChapter(7, "第七章 钟楼街的游行", "苏念醒来。" * 200)],
                          [{"translated_text": GOOD, "chapter_title_en": "",
                            "chapter_summary": ""}])
        runner["run"](["unit_test_book"])

        body = _deliverable(runner["out_dir"])
        assert "钟楼街" not in body
        assert "第七章" not in body
        assert "## Chapter 7: Chapter 7" in body

    def test_no_cjk_anywhere_in_deliverable(self, runner):
        runner["install"](_cfg(runner["out_dir"]),
                          [_FakeChapter(1, "第一章 测试", "苏念醒来。" * 200)],
                          [{"translated_text": GOOD, "chapter_title_en": "The Grey Sky",
                            "chapter_summary": ""}])
        runner["run"](["unit_test_book"])

        cjk = [c for c in _deliverable(runner["out_dir"]) if "\u4e00" <= c <= "\u9fff"]
        assert not cjk, f"deliverable contains CJK: {cjk}"


# ═══════════════════════════════════════════════════════════════
# Honest progress reporting
# ═══════════════════════════════════════════════════════════════

class TestProgressIsHonest:
    def test_failed_chapter_is_not_reported_as_done(self, runner, capsys):
        """The documented incident: 1 of 2 delivered, printed '100%' + ALL DONE."""
        runner["install"](_cfg(runner["out_dir"]),
                          [_FakeChapter(1, "第一章", "苏念醒来。" * 200),
                           _FakeChapter(2, "第二章", "天空是灰色的。" * 200)],
                          [{"translated_text": GOOD, "chapter_title_en": "One",
                            "chapter_summary": ""},
                           RuntimeError("content filter 1301")])
        rc = runner["run"](["unit_test_book"])

        assert rc == 1, "incomplete run must exit non-zero"
        out = capsys.readouterr().out
        assert "INCOMPLETE: 1/2" in out
        assert "ALL DONE" not in out

    def test_cost_json_records_the_gap(self, runner):
        runner["install"](_cfg(runner["out_dir"]),
                          [_FakeChapter(1, "第一章", "苏念醒来。" * 200),
                           _FakeChapter(2, "第二章", "天空是灰色的。" * 200)],
                          [{"translated_text": GOOD, "chapter_title_en": "One",
                            "chapter_summary": ""},
                           RuntimeError("boom")])
        runner["run"](["unit_test_book"])

        cost = json.loads((runner["out_dir"] / "_cost.json").read_text(encoding="utf-8"))
        assert cost["chapters"] == 1
        assert cost["chapters_scanned"] == 2
        assert cost["complete"] is False
        assert 2 in cost["chapters_failed"]

    def test_full_success_reports_all_done(self, runner, capsys):
        runner["install"](_cfg(runner["out_dir"]),
                          [_FakeChapter(1, "第一章", "苏念醒来。" * 200)],
                          [{"translated_text": GOOD, "chapter_title_en": "One",
                            "chapter_summary": ""}])
        rc = runner["run"](["unit_test_book"])

        assert rc == 0
        assert "ALL DONE: 1/1" in capsys.readouterr().out

    def test_truncated_chapter_counts_as_failure(self, runner, capsys):
        """A stub/truncated translation must not ship AND must not be counted
        as delivered — this is how 空章/截断章 hid in finished books."""
        runner["install"](_cfg(runner["out_dir"]),
                          [_FakeChapter(1, "第一章", "苏念醒来。" * 200)],
                          [{"translated_text": "Too short.", "chapter_title_en": "One",
                            "chapter_summary": ""}])
        rc = runner["run"](["unit_test_book"])

        assert rc == 1
        out = capsys.readouterr().out
        assert "TRUNCATED" in out or "EMPTY" in out
        assert "## Chapter 1" not in _deliverable(runner["out_dir"])


# ═══════════════════════════════════════════════════════════════
# Resume path: the UnboundLocalError crash
# ═══════════════════════════════════════════════════════════════

class TestResumeDoesNotCrash:
    def test_nothing_left_to_translate(self, runner, capsys):
        runner["install"](_cfg(runner["out_dir"]), [], [])
        rc = runner["run"](["unit_test_book"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "ALL DONE: 0/0" in out
        assert "Glossary: 1 terms" in out

    def test_checkpoint_says_everything_done(self, runner, capsys):
        runner["install"](_cfg(runner["out_dir"]),
                          [_FakeChapter(1, "第一章", "苏念醒来。")], [])
        # last_idx=0 → start=1 → while body never runs → old code crashed on
        # `glossary` in the summary print.
        (runner["out_dir"] / "_checkpoint.json").write_text(
            json.dumps({"last_idx": 0, "glossary_snapshot": "{}",
                        "previous_summary": ""}), encoding="utf-8")
        # A consistent resume: the checkpoint says chapter 1 shipped, and the
        # deliverable really contains it. written_chapters is rebuilt by parsing
        # the deliverable, so both halves must agree.
        (runner["out_dir"] / "unit_test_en.md").write_text(
            "# Unit Test Book — English Translation\n\n"
            "## Chapter 1: Already Done\n\nExisting text.\n\n---\n\n",
            encoding="utf-8")

        rc = runner["run"](["unit_test_book"])

        assert rc == 0, "a complete resume must not report INCOMPLETE"
        out = capsys.readouterr().out
        assert "ALL DONE: 1/1" in out
        assert "Glossary: 1 terms" in out
        # Resume must not rewrite or duplicate the already-delivered chapter.
        body = _deliverable(runner["out_dir"])
        assert body.count("## Chapter 1:") == 1

    def test_checkpoint_claims_done_but_deliverable_is_missing(self, runner, capsys):
        """Inconsistent state must be flagged, not silently accepted.

        The checkpoint says chapter 1 shipped; the deliverable has no such
        chapter. Trusting the checkpoint alone is exactly how a book can be
        declared finished while chapters are absent from what the reader gets.
        """
        runner["install"](_cfg(runner["out_dir"]),
                          [_FakeChapter(1, "第一章", "苏念醒来。")], [])
        (runner["out_dir"] / "_checkpoint.json").write_text(
            json.dumps({"last_idx": 0, "glossary_snapshot": "{}",
                        "previous_summary": ""}), encoding="utf-8")

        rc = runner["run"](["unit_test_book"])

        assert rc == 1
        out = capsys.readouterr().out
        assert "INCOMPLETE: 0/1" in out
        assert "Chapters absent from the deliverable: [1]" in out


# ═══════════════════════════════════════════════════════════════
# --limit N must still cold-read something
# ═══════════════════════════════════════════════════════════════

class TestLimitModeStillColdReads:
    def _positions(self, mod, monkeypatch, runner, n_chapters, limit):
        """Run with N chapters and return which positions were sampled."""
        chapters = [_FakeChapter(i + 1, f"第{i+1}章", "苏念醒来。" * 200)
                    for i in range(n_chapters)]
        results = [{"translated_text": GOOD, "chapter_title_en": f"C{i+1}",
                    "chapter_summary": "", "readback_feedback": {
                        "verdict": "PASS", "would_keep_reading": True,
                        "overall_impression": "fine",
                        "comprehension_issues": [], "engagement_gaps": []}}
                   for i in range(n_chapters)]
        runner["install"](_cfg(runner["out_dir"]), chapters, results)
        monkeypatch.setattr(mod.sys, "argv",
                            ["run_novel.py", "unit_test_book", "--limit", str(limit)])
        mod.main()
        qlog = runner["out_dir"] / "_quality.json"
        if not qlog.exists():
            return []
        return [q["ch_position"] for q in json.loads(qlog.read_text(encoding="utf-8"))]

    def test_limit_2_cold_reads_both_ends(self, runner, monkeypatch):
        """Documented defect: sample set was empty for N<10, so the 'quick
        validation mode' ran with no quality check whatsoever."""
        mod = runner["mod"]
        positions = self._positions(mod, monkeypatch, runner, n_chapters=2, limit=2)
        assert positions, "no cold read happened in --limit 2 mode"
        assert set(positions) == {1, 2}

    def test_limit_1_cold_reads_chapter_one(self, runner, monkeypatch):
        mod = runner["mod"]
        positions = self._positions(mod, monkeypatch, runner, n_chapters=1, limit=1)
        assert positions == [1]

    def test_full_run_keeps_configured_sample_points(self, runner, monkeypatch):
        """The fallback must not disturb a normal long run."""
        mod = runner["mod"]
        positions = self._positions(mod, monkeypatch, runner, n_chapters=12, limit=12)
        assert positions == [10], "configured sampling changed for a long run"


class TestScanDelivered:
    def test_splits_numbers_and_bodies(self, runner):
        text = ("## Chapter 1: One\n\nbody one\n\n---\n\n"
                "## Chapter 2: Two\n\nbody two\n\n---\n\n")
        written, bodies = delivery.scan_written(text)
        assert written == {1, 2}
        assert "body one" in bodies[1]
        assert "body two" in bodies[2]
        assert "body two" not in bodies[1]

    def test_empty_deliverable(self, runner):
        assert delivery.scan_written("") == (set(), {})


class TestRepairMissing:
    """--repair-missing exists so a gap costs one chapter, not a whole book.

    Before this, the only way to fill a missing chapter was to delete the
    checkpoint, and the loop called translate_chapter for every already-written
    chapter too — a full re-translation at full price.
    """

    def _seed_deliverable(self, out_dir, present):
        out = Path(out_dir) / "unit_test_en.md"
        blocks = [f"## Chapter {n}: Chapter {n}\n\n"
                  + ("Existing body. " * 20) + "\n\n---\n\n" for n in present]
        out.write_text("# Unit Test — English Translation\n\n" + "".join(blocks),
                       encoding="utf-8")
        return out

    def test_only_the_gap_is_translated(self, runner, monkeypatch):
        mod = runner["mod"]
        chapters = [_FakeChapter(i, f"第{i}章", "源文内容") for i in (1, 2, 3)]
        runner["install"](_cfg(runner["out_dir"], expected_chapters=3), chapters, [])

        calls = []

        def fake_translate(self, **kwargs):
            calls.append(kwargs["chapter_number"])
            return {"translated_text": GOOD, "chapter_title_en": "Filled",
                    "chapter_summary": "summary"}

        monkeypatch.setattr(mod.TranslationAgent, "translate_chapter", fake_translate)
        self._seed_deliverable(runner["out_dir"], [1, 3])
        (Path(runner["out_dir"]) / "_checkpoint.json").write_text(
            json.dumps({"last_idx": 2, "glossary_snapshot": "{}",
                        "previous_summary": "old summary"}), encoding="utf-8")

        code = runner["run"](["unit_test_book", "--repair-missing"])

        assert calls == [2], f"only the gap should hit the API, got {calls}"
        text = _deliverable(runner["out_dir"])
        for n in (1, 2, 3):
            assert text.count(f"## Chapter {n}:") == 1
        assert code == 0

        # The checkpoint must not rewind past what the interrupted run reached.
        ckpt = json.loads((Path(runner["out_dir"]) / "_checkpoint.json")
                          .read_text(encoding="utf-8"))
        assert ckpt["last_idx"] == 2

    def test_skipped_chapters_seed_continuity(self, runner, monkeypatch):
        """A gap chapter must still get a before-context, taken from the
        delivered chapter that precedes it (per-chapter summaries are not
        persisted anywhere)."""
        mod = runner["mod"]
        chapters = [_FakeChapter(i, f"第{i}章", "源文内容") for i in (1, 2)]
        runner["install"](_cfg(runner["out_dir"], expected_chapters=2), chapters, [])
        seen = {}

        def fake_translate(self, **kwargs):
            seen["previous_summary"] = kwargs["previous_summary"]
            return {"translated_text": GOOD, "chapter_title_en": "Two",
                    "chapter_summary": "s"}
        monkeypatch.setattr(mod.TranslationAgent, "translate_chapter", fake_translate)
        self._seed_deliverable(runner["out_dir"], [1])
        (Path(runner["out_dir"]) / "_checkpoint.json").write_text(
            json.dumps({"last_idx": 1, "glossary_snapshot": "{}",
                        "previous_summary": "stale"}), encoding="utf-8")

        runner["run"](["unit_test_book", "--repair-missing"])

        assert "Existing body." in seen["previous_summary"]
        assert "stale" not in seen["previous_summary"]


class TestDeliveryScanWired:
    def test_cost_json_carries_the_structural_scan(self, runner, monkeypatch):
        mod = runner["mod"]
        chapters = [_FakeChapter(1, "第1章", "源文内容")]
        result = {"translated_text": GOOD, "chapter_title_en": "One",
                  "chapter_summary": "s"}
        runner["install"](_cfg(runner["out_dir"], expected_chapters=1), chapters, [result])

        runner["run"](["unit_test_book"])

        cost = json.loads((Path(runner["out_dir"]) / "_cost.json")
                          .read_text(encoding="utf-8"))
        assert isinstance(cost["scan"], dict)
        assert "numbering_defects" in cost["scan"]
        assert cost["scan"]["glossary_conflicts"] == 0


class TestReorderDeliverable:
    def test_out_of_order_block_moves_into_place(self, runner, tmp_path):
        """A repair run appends, so the filled gap lands last; the book must
        still read in chapter order."""
        p = tmp_path / "book.md"
        p.write_text("# Title\n\n"
                     "## Chapter 1: One\n\nA.\n\n---\n\n"
                     "## Chapter 3: Three\n\nC.\n\n---\n\n"
                     "## Chapter 2: Two\n\nB.\n\n---\n\n", encoding="utf-8")

        moved = delivery.reorder(p)

        text = p.read_text(encoding="utf-8")
        assert moved > 0
        assert [int(x) for x in re.findall(r"^## Chapter (\d+):", text, re.M)] == [1, 2, 3]
        assert text.startswith("# Title")

    def test_already_ordered_file_is_not_rewritten(self, runner, tmp_path):
        p = tmp_path / "book.md"
        p.write_text("## Chapter 1: One\n\nA.\n\n---\n\n"
                     "## Chapter 2: Two\n\nB.\n\n---\n\n", encoding="utf-8")
        before = p.read_text(encoding="utf-8")
        assert delivery.reorder(p) == 0
        assert p.read_text(encoding="utf-8") == before


class TestReplaceChapters:
    def test_replaces_only_the_named_block(self, runner, tmp_path):
        p = tmp_path / "book.md"
        p.write_text("# Title\n\n"
                     "## Chapter 1: One\n\nOLD ONE\n\n---\n\n"
                     "## Chapter 2: Two\n\nKEEP ME\n\n---\n\n", encoding="utf-8")

        replaced = delivery.replace_chapters(p, {1: "NEW ONE"})

        text = p.read_text(encoding="utf-8")
        assert replaced == 1
        assert "NEW ONE" in text and "OLD ONE" not in text
        assert "KEEP ME" in text and text.startswith("# Title")

    def test_unknown_chapter_changes_nothing(self, runner, tmp_path):
        p = tmp_path / "book.md"
        p.write_text("## Chapter 1: One\n\nA.\n\n---\n\n", encoding="utf-8")
        before = p.read_text(encoding="utf-8")
        assert delivery.replace_chapters(p, {9: "X"}) == 0
        assert p.read_text(encoding="utf-8") == before


class TestRetranslateMode:
    def test_existing_chapter_is_redone_and_replaced_in_place(self, runner, monkeypatch):
        mod = runner["mod"]
        chapters = [_FakeChapter(i, f"第{i}章", "源文内容") for i in (1, 2)]
        result = {"translated_text": GOOD, "chapter_title_en": "One",
                  "chapter_summary": "s"}
        runner["install"](_cfg(runner["out_dir"], expected_chapters=2),
                          chapters, [result])

        calls = []
        original = mod.TranslationAgent.translate_chapter

        def spy(self, **kwargs):
            calls.append(kwargs["chapter_number"])
            return original(self, **kwargs)

        monkeypatch.setattr(mod.TranslationAgent, "translate_chapter", spy)

        out = Path(runner["out_dir"]) / "unit_test_en.md"
        out.write_text("# Unit Test — English Translation\n\n"
                       "## Chapter 1: One\n\n" + ("Old body. " * 20) + "\n\n---\n\n"
                       "## Chapter 2: Two\n\n" + ("Keep body. " * 20) + "\n\n---\n\n",
                       encoding="utf-8")
        (Path(runner["out_dir"]) / "_checkpoint.json").write_text(
            json.dumps({"last_idx": 1, "glossary_snapshot": "{}",
                        "previous_summary": ""}), encoding="utf-8")

        runner["run"](["unit_test_book", "--retranslate", "1"])

        assert calls == [1], f"only the named chapter may be redone, got {calls}"
        text = out.read_text(encoding="utf-8")
        assert GOOD.strip() in text
        assert "Old body." not in text
        assert "Keep body." in text
        assert [int(x) for x in re.findall(r"^## Chapter (\d+):", text, re.M)] == [1, 2]


class TestChapterBudgetIntegration:
    """A chapter that already burned its budget must not be retried again."""

    def _seed_attempts(self, out_dir, chapter, attempts, cost):
        import json as _json
        (Path(out_dir) / "_attempts.json").write_text(_json.dumps(
            {str(chapter): {"attempts": attempts, "cost_cny": cost,
                            "outcome": "not_deliverable"}}), encoding="utf-8")

    def test_over_budget_chapter_is_skipped_without_an_api_call(
            self, runner, monkeypatch):
        mod = runner["mod"]
        chapters = [_FakeChapter(1, "第1章", "源文内容")]
        runner["install"](_cfg(runner["out_dir"], expected_chapters=1), chapters, [])
        calls = []
        monkeypatch.setattr(mod.TranslationAgent, "translate_chapter",
                            lambda self, **kw: calls.append(kw) or {
                                "translated_text": GOOD, "chapter_title_en": "One",
                                "chapter_summary": "s"})
        self._seed_attempts(runner["out_dir"], 1, attempts=3, cost=4.0)

        runner["run"](["unit_test_book"])

        assert calls == [], "over-budget chapter must not reach the provider"
        assert _deliverable(runner["out_dir"]) == ""

    def test_retry_uses_fast_mode(self, runner, monkeypatch):
        """A chapter with a prior attempt must not re-run the cold reader."""
        mod = runner["mod"]
        chapters = [_FakeChapter(1, "第1章", "源文内容")]
        runner["install"](_cfg(runner["out_dir"], expected_chapters=1), chapters, [])
        seen = {}
        monkeypatch.setattr(mod.TranslationAgent, "translate_chapter",
                            lambda self, **kw: seen.update(kw) or {
                                "translated_text": GOOD, "chapter_title_en": "One",
                                "chapter_summary": "s"})
        self._seed_attempts(runner["out_dir"], 1, attempts=1, cost=0.3)

        runner["run"](["unit_test_book"])

        assert seen["skip_readback"] is True

    def test_attempts_are_recorded_for_the_next_run(self, runner, monkeypatch):
        mod = runner["mod"]
        chapters = [_FakeChapter(1, "第1章", "源文内容")]
        runner["install"](_cfg(runner["out_dir"], expected_chapters=1), chapters, [])
        monkeypatch.setattr(mod.TranslationAgent, "translate_chapter",
                            lambda self, **kw: {
                                "translated_text": GOOD, "chapter_title_en": "One",
                                "chapter_summary": "s"})

        runner["run"](["unit_test_book"])

        import json as _json
        data = _json.loads((Path(runner["out_dir"]) / "_attempts.json")
                           .read_text(encoding="utf-8"))
        assert data["1"]["attempts"] == 1
        assert data["1"]["outcome"] == "ok"


class TestTranslateBatch:
    """One code path: batch_size=1 is today's behaviour, >1 runs concurrently."""

    def _items(self, n):
        return [{"i": i, "ch": _FakeChapter(i + 1, f"第{i+1}章", "源文"),
                 "pos": i + 1, "is_sample": False, "fast": True,
                 "context": f"ctx{i}", "rows_before": 0} for i in range(n)]

    def test_sequential_batch_reuses_the_main_agent(self, runner, monkeypatch):
        mod = runner["mod"]
        created = []

        class FakeAgent:
            def __init__(self, **kw):
                created.append(kw)

            def translate_chapter(self, **kw):
                return {"translated_text": "x"}

        main_agent = FakeAgent()
        created.clear()          # ignore the agent the test itself constructed
        outcomes = mod.translate_batch(self._items(2), main_agent,
                                       {"book_id": "b", "genre": "g"}, 1, "{}")
        assert created == [], "batch_size=1 must not spawn worker agents"
        assert all("result" in o for o in outcomes)

    def test_concurrent_batch_uses_one_agent_per_chapter(self, runner, monkeypatch):
        mod = runner["mod"]
        created = []

        class FakeAgent:
            def __init__(self, **kw):
                created.append(kw.get("book_id"))

            def load_glossary_snapshot(self, *a, **k):
                pass

            def translate_chapter(self, **kw):
                return {"translated_text": f"ch{kw['chapter_number']}"}

        monkeypatch.setattr(mod, "TranslationAgent", FakeAgent)
        outcomes = mod.translate_batch(self._items(3), object(),
                                       {"book_id": "bk", "genre": "g"}, 3, "{}")
        assert len(created) == 3
        assert all(b.startswith("bk_w") for b in created)
        assert [o["result"]["translated_text"] for o in outcomes] == ["ch1", "ch2", "ch3"]

    def test_context_is_passed_through(self, runner, monkeypatch):
        mod = runner["mod"]
        seen = []

        class FakeAgent:
            def translate_chapter(self, **kw):
                seen.append(kw["previous_summary"])
                return {"translated_text": "x"}

        mod.translate_batch(self._items(2), FakeAgent(),
                            {"book_id": "b", "genre": "g"}, 1, "{}")
        assert seen == ["ctx0", "ctx1"]

    def test_circuit_open_on_one_chapter_does_not_sink_the_others(
            self, runner, monkeypatch):
        from src.circuit_breaker import CircuitBreakerOpenError
        mod = runner["mod"]

        class FakeAgent:
            def __init__(self, **kw):
                pass

            def load_glossary_snapshot(self, *a, **k):
                pass

            def translate_chapter(self, **kw):
                if kw["chapter_number"] == 2:
                    raise CircuitBreakerOpenError("open")
                return {"translated_text": "ok"}

        monkeypatch.setattr(mod, "TranslationAgent", FakeAgent)
        outcomes = mod.translate_batch(self._items(3), object(),
                                       {"book_id": "bk", "genre": "g"}, 3, "{}")
        assert outcomes[1] == {"circuit": True}
        assert outcomes[0]["result"]["translated_text"] == "ok"
        assert outcomes[2]["result"]["translated_text"] == "ok"

    def test_batch_run_still_delivers_every_chapter(self, runner, monkeypatch):
        mod = runner["mod"]
        chapters = [_FakeChapter(i, f"第{i}章", "源文内容") for i in (1, 2, 3)]
        results = [{"translated_text": GOOD, "chapter_title_en": f"Ch{n}",
                    "chapter_summary": "s"} for n in (1, 2, 3)]
        runner["install"](_cfg(runner["out_dir"], expected_chapters=3), chapters, results)

        # The fixture's fake agent ignores batch_size, so this exercises the
        # collection/flush wiring rather than real concurrency.
        runner["run"](["unit_test_book", "--batch", "3"])

        text = _deliverable(runner["out_dir"])
        assert [n for n in (1, 2, 3) if f"## Chapter {n}:" in text] == [1, 2, 3]

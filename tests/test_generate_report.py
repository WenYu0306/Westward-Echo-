"""Tests for scripts/generate_report.py — the customer-facing QA report.

These lock down the judgement logic that was audited to be unreliable. The
report is handed to customers as quality evidence, so a wrong number here is a
false claim. Three classes of defect were found and are now pinned:

1. FALSE RED: absence was counted as inconsistency, and fuzzy-match context
   snippets were counted as distinct "variants", so 《地府》 reported 19.4% term
   consistency when the rendering was actually stable. The check now uses
   verbatim matching only and reports three honest states.
2. FALSE GREEN: a 59/59 cold-read PASS was displayed as the headline quality
   proof, despite being a 2.6% self-reviewed sample. It is now an advisory that
   explicitly states it is not quality evidence.
3. FALSE CLAIM: the report said "Markdown + EPUB" though the CLI runner never
   builds an EPUB (0 .epub files on disk). Formats are now listed from disk.
"""

import importlib.util
import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "generate_report.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("generate_report_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Ch:
    """Minimal stand-in for a split_chapters chapter (index + content).

    ``action`` is None so it survives generate_report's
    ``c.action != ParagraphTag.SKIP`` filter without needing the real enum.
    """

    def __init__(self, index, content):
        self.index = index
        self.content = content
        self.action = None
        self.word_count = len(content)


@pytest.fixture
def report_mod():
    return _load_module()


# ═══════════════════════════════════════════════════════════════
# Term check: verbatim only, three honest states
# ═══════════════════════════════════════════════════════════════

class TestTermCheckVerbatimOnly:
    def test_all_chapters_hit_is_verbatim_all(self, report_mod):
        cn = [_Ch(1, "苏念走了"), _Ch(2, "苏念又走了"), _Ch(3, "苏念再走")]
        en = {1: "Su Nian left.", 2: "Su Nian left again.", 3: "Su Nian went on."}
        res = report_mod._check_term_consistency(cn, en, {"苏念": "Su Nian"})
        assert res["苏念"]["status"] == "VERBATIM_ALL"
        assert res["苏念"]["verbatim_hits"] == 3

    def test_no_chapter_hits_is_verbatim_none(self, report_mod):
        cn = [_Ch(1, "苏念走了"), _Ch(2, "苏念又走"), _Ch(3, "苏念再走")]
        en = {1: "Someone left.", 2: "Someone left again.", 3: "Someone went on."}
        res = report_mod._check_term_consistency(cn, en, {"苏念": "Su Nian"})
        assert res["苏念"]["status"] == "VERBATIM_NONE"
        assert res["苏念"]["verbatim_hits"] == 0

    def test_some_chapters_hit_is_verbatim_partial(self, report_mod):
        cn = [_Ch(1, "苏念走了"), _Ch(2, "苏念又走"), _Ch(3, "苏念再走")]
        en = {1: "Su Nian left.", 2: "A person left again.", 3: "Su Nian went on."}
        res = report_mod._check_term_consistency(cn, en, {"苏念": "Su Nian"})
        assert res["苏念"]["status"] == "VERBATIM_PARTIAL"
        assert res["苏念"]["verbatim_hits"] == 2

    def test_case_insensitive_verbatim(self, report_mod):
        cn = [_Ch(1, "苏念"), _Ch(2, "苏念"), _Ch(3, "苏念")]
        en = {1: "su nian left", 2: "SU NIAN left", 3: "Su Nian left"}
        res = report_mod._check_term_consistency(cn, en, {"苏念": "Su Nian"})
        assert res["苏念"]["status"] == "VERBATIM_ALL"

    def test_absence_does_not_make_it_inconsistent(self, report_mod):
        """THE core false-red regression.

        The source mentions the term in 4 chapters, the translation renders it
        verbatim in the 1 chapter where it survives and omits it in the other 3
        (legal compression). The old code counted the 3 absences as 3 distinct
        "variants" and judged INCONSISTENT. Verbatim matching must classify this
        as PARTIAL, never as a drift, and must not fabricate a rate.
        """
        cn = [_Ch(1, "断头鬼来了"), _Ch(2, "断头鬼走了"), _Ch(3, "断头鬼"), _Ch(4, "断头鬼")]
        en = {1: "Duan Tou Gui arrived.", 2: "The road was empty.",
              3: "Nothing here.", 4: "Still nothing."}
        res = report_mod._check_term_consistency(cn, en, {"断头鬼": "Duan Tou Gui"})
        assert res["断头鬼"]["status"] == "VERBATIM_PARTIAL"
        assert res["断头鬼"]["verbatim_hits"] == 1
        # No "variant" or "inconsistent" verdict may be produced from absence.
        assert "INCONSISTENT" not in res["断头鬼"]["status"]

    def test_context_snippet_words_do_not_count_as_a_hit(self, report_mod):
        """A term whose words scatter across ordinary prose is NOT a verbatim hit.

        This is what defeated _extract_candidate_term: 张婶→'Granny Zhang' was
        'matched' against 'deaf granny' (a different character), and 文凭→
        'Old Diplomas' against the plain noun 'a diploma from school'. That
        matcher only required every word to occur SOMEWHERE in the chapter.
        Verbatim substring matching cannot be fooled this way.
        """
        cn = [_Ch(1, "文凭的事"), _Ch(2, "文凭的事"), _Ch(3, "文凭的事")]
        en = {1: "a diploma from school, and the old records.",
              2: "the diplomas were old and filed away.",
              3: "she kept a diploma, old but precious."}
        res = report_mod._check_term_consistency(cn, en, {"文凭": "Old Diplomas"})
        # Every word is present in every chapter, but the contiguous phrase
        # "old diplomas" appears in none of them.
        assert res["文凭"]["verbatim_hits"] == 0
        assert res["文凭"]["status"] == "VERBATIM_NONE"

    def test_rare_term_skipped(self, report_mod):
        """Fewer than 3 source chapters → not enough signal to test."""
        cn = [_Ch(1, "苏念"), _Ch(2, "苏念")]
        en = {1: "Su Nian", 2: "Su Nian"}
        res = report_mod._check_term_consistency(cn, en, {"苏念": "Su Nian"})
        assert res == {}

    def test_chapter_with_empty_translation_not_counted(self, report_mod):
        cn = [_Ch(1, "苏念"), _Ch(2, "苏念"), _Ch(3, "苏念")]
        en = {1: "Su Nian left", 2: "", 3: "Su Nian went"}
        res = report_mod._check_term_consistency(cn, en, {"苏念": "Su Nian"})
        assert res["苏念"]["chapters_checked"] == 2
        assert res["苏念"]["verbatim_hits"] == 2
        assert res["苏念"]["status"] == "VERBATIM_ALL"

    def test_states_partition_all_tested_terms(self, report_mod):
        """all + partial + none must equal the number of tested terms — no term
        may fall through the classification and silently vanish."""
        cn = [_Ch(1, "甲乙丙丁戊"), _Ch(2, "甲乙丙丁戊"), _Ch(3, "甲乙丙丁戊")]
        en = {1: "A B C left", 2: "totally different", 3: "A B C again"}
        glossary = {
            "甲": "A",            # verbatim all (A appears in ch1 & ch3... but not ch2)
            "乙": "never-there",  # verbatim none
        }
        res = report_mod._check_term_consistency(cn, en, glossary)
        states = [v["status"] for v in res.values()]
        assert all(s in ("VERBATIM_ALL", "VERBATIM_PARTIAL", "VERBATIM_NONE")
                   for s in states)


# ═══════════════════════════════════════════════════════════════
# Report assembly: cold-read is never presented as proof; grade
# ignores advisories; EPUB claim follows disk
# ═══════════════════════════════════════════════════════════════

class _FakeCfg:
    """Drives generate_report() against a synthetic on-disk corpus."""

    def __init__(self, tmp_path, report_mod, monkeypatch, *, quality=None,
                 epub=False, chapters=3, json_residue_chapter=None):
        self.mod = report_mod
        out_dir = tmp_path / "book_out"
        out_dir.mkdir()
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        cn_lines = []
        en_blocks = []
        cn_stub = "苏念在这一章里出现，断头鬼也在场，事情变得复杂起来，需要仔细交代。"  # ~33 chars
        for i in range(1, chapters + 1):
            cn_lines.append(f"第{i}章 测试\n\n{cn_stub}\n")
            # Must clear the empty/short floor (>=50 chars) AND keep the ratio
            # inside 1.5–5.0. ~120 chars vs the ~33-char CN stub → ratio ≈ 3.6x.
            # Still contains "Su Nian" verbatim so the term check has a hit.
            body = ("Su Nian appears in this chapter, and the headless ghost is "
                    "there too, which makes everything far more complicated.")
            if json_residue_chapter == i:
                body = '{"translated_text": "leaked envelope"}'
            en_blocks.append(f"## Chapter {i}: Chapter {i}\n\n{body}\n\n---\n")

        novel_txt = src_dir / "novel.txt"
        novel_txt.write_text("\n".join(cn_lines), encoding="utf-8")
        (out_dir / "book_en.md").write_text("\n\n".join(en_blocks), encoding="utf-8")
        (out_dir / "_glossary.json").write_text(
            json.dumps({"苏念": "Su Nian", "断头鬼": "Duan Tou Gui"}), encoding="utf-8")
        (out_dir / "_quality.json").write_text(
            json.dumps(quality or []), encoding="utf-8")
        if epub:
            (out_dir / "book.epub").write_bytes(b"PK\x03\x04fake")

        # split_chapters on a synthetic file is brittle; stub it to return the
        # chapters we described. Content must match cn_stub so word_count (and
        # therefore the length ratio) reflects the same text written to disk,
        # and both glossary terms are present in every chapter.
        fake_chapters = [_Ch(i, cn_stub) for i in range(1, chapters + 1)]
        monkeypatch.setattr(report_mod, "split_chapters", lambda text: fake_chapters)
        monkeypatch.setattr(report_mod, "detect_and_read",
                            lambda p: (novel_txt.read_text(encoding="utf-8"), "utf-8"))
        monkeypatch.setitem(report_mod.NOVELS, "synthetic_book", {
            "name": "合成测试书",
            "name_en": "Synthetic Test Book",
            "genre": "urban",
            "book_id": "synthetic_book",
            "path": str(novel_txt),
            "output_dir": str(out_dir),
            "output_file": "book_en.md",
        })
        self.out_dir = out_dir


class TestReportHonesty:
    def test_cold_read_all_pass_is_advisory_not_proof(self, report_mod, tmp_path, monkeypatch):
        quality = [{"verdict": "PASS", "would_keep_reading": True,
                    "impression": "great", "ch_number": 1}]
        _FakeCfg(tmp_path, report_mod, monkeypatch, quality=quality, chapters=3)
        report = report_mod.generate_report("synthetic_book")

        assert report["summary"]["cold_read_is_quality_evidence"] is False
        # A 100%-PASS tiny sample must NOT be filed under hard issues (which
        # drive the grade) but must raise an advisory saying it is not evidence.
        joined_advisories = " ".join(report["advisories"])
        assert "不可作为质量结论" in joined_advisories or "不构成质量证据" in " ".join(
            report["passed"])

    def test_epub_not_claimed_when_absent(self, report_mod, tmp_path, monkeypatch):
        _FakeCfg(tmp_path, report_mod, monkeypatch, epub=False, chapters=3)
        report = report_mod.generate_report("synthetic_book")
        assert report["_deliverable_epub"] == ""
        md = (report_mod.Path(report["_deliverable_md"]).parent / "_report.md"
              ).read_text(encoding="utf-8")
        assert "未包含 EPUB" in md
        assert "Markdown + EPUB" not in md

    def test_epub_claimed_when_present(self, report_mod, tmp_path, monkeypatch):
        _FakeCfg(tmp_path, report_mod, monkeypatch, epub=True, chapters=3)
        report = report_mod.generate_report("synthetic_book")
        assert report["_deliverable_epub"].endswith(".epub")

    def test_advisories_do_not_downgrade_the_grade(self, report_mod, tmp_path, monkeypatch):
        """Grade must reflect hard defects only. Being honest about sampling
        limits (an advisory) must not itself drag the grade to C."""
        _FakeCfg(tmp_path, report_mod, monkeypatch,
                 quality=[{"verdict": "PASS", "ch_number": 1}], chapters=3)
        report = report_mod.generate_report("synthetic_book")
        assert report["advisories"], "expected at least the cold-read advisory"
        assert not report["issues"], f"unexpected hard issues: {report['issues']}"
        assert report["grade"] == "A+"

    def test_json_residue_is_a_hard_issue(self, report_mod, tmp_path, monkeypatch):
        """A leaked model envelope in a delivered chapter is a real defect and
        must grade down — this is the one hard issue the real 《地府》 run found."""
        _FakeCfg(tmp_path, report_mod, monkeypatch, chapters=3,
                 json_residue_chapter=2)
        report = report_mod.generate_report("synthetic_book")
        assert any("JSON" in i for i in report["issues"])
        assert report["grade"] in ("B", "C")

    def test_no_consistency_rate_key_in_summary(self, report_mod, tmp_path, monkeypatch):
        """The unreliable single rate must be gone, not merely recomputed."""
        _FakeCfg(tmp_path, report_mod, monkeypatch, chapters=3)
        report = report_mod.generate_report("synthetic_book")
        assert "term_consistency_rate" not in report["summary"]
        # The honest verbatim partition must be present and self-consistent.
        s = report["summary"]
        assert s["terms_verbatim_all"] + s["terms_verbatim_partial"] + \
            s["terms_verbatim_none"] == s["terms_tested"]

    def test_chinese_book_title_not_in_deliverable_report_h1(
            self, report_mod, tmp_path, monkeypatch):
        _FakeCfg(tmp_path, report_mod, monkeypatch, chapters=3)
        report = report_mod.generate_report("synthetic_book")
        md = Path(report["_deliverable_md"]).parent / "_report.md"
        text = md.read_text(encoding="utf-8")
        # The report references name_en for the book, not the Chinese title.
        assert "Synthetic Test Book" in text or "合成测试书" in text

"""Tests for scripts/scan_deliverable.py — the full-corpus deliverable scanner.

Every fixture here is a reduced form of something actually found in
``novels/output/difu_segmented/difu_en.md``, so the checks are pinned to the real
defect shapes rather than to invented ones.

Two of these tests exist because of defects the scanner itself shipped with on
its first run, caught only by running it against the real 2301-chapter corpus:

* ``_TERMINAL`` written as a ``str`` instead of a ``tuple`` made
  ``str.endswith`` test for a whole literal suffix, flagging all 2301 chapters.
  ``test_terminal_is_a_tuple_not_a_string`` pins the type directly.
* Body lines were stored as bare strings with positions reconstructed from
  ``enumerate`` offsets, so every reported line number was off by the count of
  blank lines above it (Chapter 366's residue reported as 31698 instead of
  31767). ``test_line_numbers_survive_blank_lines_and_rules`` pins that.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = ROOT / "scripts" / "scan_deliverable.py"


@pytest.fixture(scope="module")
def scan():
    """Import the scanner by path — scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location("scan_deliverable", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["scan_deliverable"] = mod
    spec.loader.exec_module(mod)
    return mod


def _write(tmp_path, text):
    path = tmp_path / "book_en.md"
    path.write_text(text, encoding="utf-8")
    return path


# ── parsing ─────────────────────────────────────────────────────────────


class TestChapterParsing:
    def test_parses_en_and_cn_numbers_from_heading(self, scan):
        chapters = scan._split_chapters(
            "# Title\n\n## Chapter 2: 第1章 鬼节，鬼敲门\n\nBody text.\n")
        assert len(chapters) == 1
        assert chapters[0]["en"] == 2
        assert chapters[0]["cn"] == 1

    def test_heading_without_cn_number_yields_none(self, scan):
        chapters = scan._split_chapters("## Chapter 0: Prologue（楔子）\n\nBody.\n")
        assert chapters[0]["cn"] is None

    def test_prologue_counts_as_a_chapter(self, scan):
        """difu_en.md opens with `## Chapter 0: Prologue`, which is why the EN
        series has a legitimate gap at 1 and an offset of 2 throughout."""
        chapters = scan._split_chapters(
            "## Chapter 0: Prologue（楔子）\n\nOnce.\n\n"
            "## Chapter 2: 第1章 鬼节\n\nBody.\n")
        assert [c["en"] for c in chapters] == [0, 2]

    def test_body_excludes_blank_lines_and_horizontal_rules(self, scan):
        chapters = scan._split_chapters(
            "## Chapter 1: 第1章 X\n\nPara one.\n\n---\n\nPara two.\n\n"
            "## Chapter 2: 第2章 Y\n\nNext.\n")
        first = chapters[0]["body"]
        assert [text for _, text in first] == ["Para one.", "Para two."]


class TestLineNumbersAreReal:
    def test_line_numbers_survive_blank_lines_and_rules(self, scan):
        """The reported line must be the real file line, not an enumerate offset.

        Here the offending body line is line 7 of the file; a naive offset from
        the heading would report 3 (heading at 1, two content lines, one blank,
        one rule).
        """
        text = (
            "# Title\n"            # 1
            "\n"                   # 2
            "## Chapter 1: 第1章 X\n"  # 3
            "\n"                   # 4
            "---\n"                # 5
            "\n"                   # 6
            "Chen Daji had never been\n"  # 7  ← truncated
        )
        chapters = scan._split_chapters(text)
        finding = scan.check_body_completeness(
            chapters[0]["body"], chapters[0]["start_line"])
        assert finding["check"] == "TRUNCATED_BODY"
        assert finding["line"] == 7

    def test_json_residue_reports_its_own_line(self, scan):
        text = (
            "## Chapter 366: 第364章 X\n"   # 1
            "\n"                            # 2
            "Some prose that ran on```json\n"  # 3
            "{\n"                           # 4
        )
        chapters = scan._split_chapters(text)
        findings = scan.check_json_residue(chapters[0]["body"])
        assert [f["line"] for f in findings] == [3, 4]


# ── check 1: JSON residue ───────────────────────────────────────────────


class TestJsonResidue:
    """Shapes reduced from the two real occurrences in difu_en.md."""

    def _findings(self, scan, body_text):
        chapters = scan._split_chapters(f"## Chapter 1: 第1章 X\n\n{body_text}")
        return scan.check_json_residue(chapters[0]["body"])

    def test_bare_brace_line_is_residue(self, scan):
        """Ch366 ends with a lone `{` after a ```json fence; Ch1472 likewise."""
        found = self._findings(scan, "Prose.\n{\n")
        assert any(f["detail"] == "bare '{' line" for f in found)

    def test_json_fence_is_residue(self, scan):
        found = self._findings(scan, "Prose that ran on```json\n")
        assert any("```json fence" in f["detail"] for f in found)

    def test_unpacked_envelope_key_is_residue(self, scan):
        """Ch1472's whole translation sits inside `{"translated_text": "..."}`
        and was never unpacked."""
        found = self._findings(scan, '"translated_text": "Grand Warden Xin..."')
        assert any('"translated_text"' in f["detail"] for f in found)

    def test_clean_prose_has_no_residue(self, scan):
        assert self._findings(scan, "Just ordinary prose. Nothing weird.\n") == []

    def test_dialogue_with_braces_in_prose_is_not_flagged(self, scan):
        """A `{` mid-line is prose (or a formatting quirk), not an envelope."""
        assert self._findings(scan, "He typed {something} and moved on.\n") == []


# ── check 2/3: body completeness ────────────────────────────────────────


class TestBodyCompleteness:
    def _check(self, scan, body_text):
        chapters = scan._split_chapters(f"## Chapter 1: 第1章 X\n\n{body_text}")
        return scan.check_body_completeness(
            chapters[0]["body"], chapters[0]["start_line"])

    def test_terminal_is_a_tuple_not_a_string(self, scan):
        """`str.endswith("abc")` tests one literal suffix; `endswith(("a","b"))`
        tests any of them. A plain string here flagged all 2301 chapters."""
        assert isinstance(scan._TERMINAL, tuple)
        assert len(scan._TERMINAL) > 1

    def test_period_ending_is_complete(self, scan):
        assert self._check(scan, "A complete sentence.\n") is None

    def test_mid_sentence_cut_is_truncated(self, scan):
        """Ch1472: '...not the pink of embarrassment, but the' — no period."""
        finding = self._check(scan, "not the pink of embarrassment, but the\n")
        assert finding["check"] == "TRUNCATED_BODY"

    def test_emphasis_marker_ending_is_complete(self, scan):
        """~95% of naive flags were markdown emphasis, not truncation."""
        assert self._check(scan, "*A gentle snake. Right.*\n") is None
        assert self._check(scan, "The whole village is digging up the grave._\n") is None

    def test_closing_quote_ending_is_complete(self, scan):
        assert self._check(scan, "\"Brother,\" he said, \"let's drink.\"\n") is None

    def test_em_dash_ending_is_complete(self, scan):
        """An interrupted line of dialogue is a legitimate web-novel chapter end
        ("—save my child—"). Excluding dashes inflated the count 11 → 19."""
        assert self._check(scan, "\"I'm not dead—please—save my child—\n") is None

    def test_ellipsis_ending_is_complete(self, scan):
        assert self._check(scan, "it will poison you…\n") is None

    def test_empty_chapter_is_flagged(self, scan):
        chapters = scan._split_chapters("## Chapter 9: 第8章 X\n\n## Chapter 10: 第9章 Y\n\nBody.\n")
        finding = scan.check_body_completeness(
            chapters[0]["body"], chapters[0]["start_line"])
        assert finding["check"] == "EMPTY_CHAPTER"

    def test_known_legitimate_shapes_still_flag_for_triage(self, scan):
        """A poem attribution and a sexagenary date are NOT defects but do not
        end in terminal punctuation either. The scanner is a triage list with
        evidence, not a gate — so it reports them rather than guessing."""
        assert self._check(scan, "— Su Shi, *Butterflies in Love with Flowers*\n")
        assert self._check(scan, "Jie Yu, Guimao year, Guihai month, Jichou day\n")


# ── check 4: meta-text leak ─────────────────────────────────────────────


class TestMetaText:
    def _findings(self, scan, body_text):
        chapters = scan._split_chapters(f"## Chapter 1: 第1章 X\n\n{body_text}")
        return scan.check_meta_text(chapters[0]["body"])

    def test_shouty_end_of_chapter_is_leak(self, scan):
        """Ch1286: the model's own scaffolding reached the deliverable."""
        found = self._findings(scan, "Prose.\nEND OF CHAPTER 1286\n")
        assert len(found) == 1
        assert found[0]["detail"] == "chapter-end scaffolding"

    def test_lower_case_end_of_chapter_is_also_leak(self, scan):
        """Ch1077 carries the quiet variant; the pattern is case-insensitive, so
        no second pattern is needed (a duplicate was added and removed)."""
        assert self._findings(scan, "Prose.\nEnd of Chapter.\n")

    def test_model_self_reference_is_leak(self, scan):
        assert self._findings(scan, "As an AI, I cannot help with that.\n")

    def test_prose_mentioning_a_chapter_end_is_not_leak(self, scan):
        """The pattern is anchored, so ordinary narration does not trip it."""
        assert self._findings(scan, "He reached the end of chapter one of his life.\n") == []


# ── check 5: Chinese residue ────────────────────────────────────────────


class TestChineseResidue:
    def test_cjk_in_body_is_flagged(self, scan):
        chapters = scan._split_chapters(
            "## Chapter 1: 第1章 X\n\nHe muttered 卧槽 under his breath.\n")
        found = scan.check_chinese_residue(chapters[0]["body"])
        assert len(found) == 1
        # output_guard matches single characters, not words: 卧槽 counts as 2.
        assert "2 CJK char(s)" in found[0]["detail"]
        assert "卧" in found[0]["detail"] and "槽" in found[0]["detail"]

    def test_detail_says_chars_not_tokens(self, scan):
        """The wording is part of the evidence a human triages; "token" would
        imply word-level segmentation this check does not do."""
        chapters = scan._split_chapters("## Chapter 1: 第1章 X\n\n残留中文 here.\n")
        detail = scan.check_chinese_residue(chapters[0]["body"])[0]["detail"]
        assert "token" not in detail

    def test_cjk_in_the_heading_is_not_flagged(self, scan):
        """Chapter titles legitimately carry the Chinese original
        ("## Chapter 2: 第1章 鬼节"), so only the body is scanned."""
        chapters = scan._split_chapters("## Chapter 2: 第1章 鬼节，鬼敲门\n\nClean prose.\n")
        assert scan.check_chinese_residue(chapters[0]["body"]) == []


# ── check 6: numbering ──────────────────────────────────────────────────


class TestNumbering:
    def _numbering(self, scan, pairs):
        text = "# T\n\n" + "\n\n".join(
            f"## Chapter {en}: 第{cn}章 X{i}\n\nBody {i}."
            for i, (en, cn) in enumerate(pairs))
        return scan.check_numbering(scan._split_chapters(text))

    def test_dominant_offset_is_reported_not_flagged_per_chapter(self, scan):
        """difu_en.md's offset is 2 for 1772 of 2301 chapters. That is the
        prologue convention, not 1772 defects — so it is reported once."""
        report = self._numbering(scan, [(2, 1), (3, 2), (4, 3), (5, 4)])
        assert report["dominant_offset"] == 1
        assert report["dominant_offset_count"] == 4

    def test_duplicate_en_chapters_are_defects(self, scan):
        report = self._numbering(scan, [(2, 1), (2, 2), (3, 3)])
        assert report["duplicate_en_chapters"] == [2]

    def test_one_cn_chapter_mapping_to_two_en_is_defect(self, scan):
        """difu_en.md has 5 of these: 1046→[1048,1408] etc. Mapping errors, not
        duplicated content."""
        report = self._numbering(scan, [(2, 1), (3, 1), (4, 2)])
        assert report["ambiguous_cn_to_en"] == {1: [2, 3]}

    def test_numbering_gaps_are_reported(self, scan):
        report = self._numbering(scan, [(0, 1), (2, 2)])
        assert report["en_numbering_gaps"] == [1]

    def test_clean_sequence_has_no_defects(self, scan):
        report = self._numbering(scan, [(1, 1), (2, 2), (3, 3)])
        assert report["duplicate_en_chapters"] == []
        assert report["ambiguous_cn_to_en"] == {}
        assert report["en_numbering_gaps"] == []

    def test_headings_without_cn_number_are_listed(self, scan):
        """difu_en.md has 4 (chapters 0, 157, 1935, 2246) — the prologue plus
        headings whose CN number never made it into the title."""
        report = self._numbering_no_cn(scan)
        assert report["headings_without_cn_number"] == [0, 3]

    def _numbering_no_cn(self, scan):
        text = ("## Chapter 0: Prologue（楔子）\n\nBody.\n\n"
                "## Chapter 2: 第1章 X\n\nBody.\n\n"
                "## Chapter 3: Part Two\n\nBody.\n")
        return scan.check_numbering(scan._split_chapters(text))

    def test_no_cn_number_anywhere_does_not_crash(self, scan):
        """dominant_offset is None here. An earlier version read
        ``(dominant_offset or 0)`` inside the comprehension — unreachable, since
        no CN numbers also means no paired chapters, so the body never ran.
        Proved unreachable, then replaced by an explicit None guard."""
        report = scan.check_numbering(
            scan._split_chapters("## Chapter 1: Prologue\n\nBody.\n"))
        assert report["dominant_offset"] is None
        assert report["offset_outliers"] == []
        assert report["chapters_total"] == 1

    def test_offset_tolerance_boundary_is_twenty(self, scan):
        """Departure of exactly 20 is normal drift; 21 is an anomaly.

        Calibrated on difu_en.md: legitimate offsets are 2/3/1/4 (all within ±2
        of the dominant 2), genuine anomalies sit at 302/362/502. Literals here
        on purpose — deriving the inputs from _OFFSET_OUTLIER_TOLERANCE would
        let the constant mutate with the expectation and survive.
        """
        report = self._numbering(scan, [
            (2, 1), (3, 2), (4, 3), (5, 4),   # dominant offset 1
            (31, 10),                         # offset 21 → departs by 20 → OK
            (33, 11),                         # offset 22 → departs by 21 → anomaly
        ])
        assert report["dominant_offset"] == 1
        assert [o["en"] for o in report["offset_outliers"]] == [33]

    def test_offset_distribution_is_capped_at_eight(self, scan):
        """The distribution is a human diagnostic; 9 distinct offsets report 8."""
        pairs = [(2, 1), (3, 2), (4, 3)]          # dominant offset 1
        pairs += [(cn + off, cn) for cn, off in
                  zip(range(20, 29), range(3, 12))]   # 9 more distinct offsets
        report = self._numbering(scan, pairs)
        assert len(report["offset_distribution"]) == 8
        assert report["dominant_offset"] == 1

    def test_outlier_list_is_capped_at_twenty(self, scan):
        """30 normal chapters must outnumber the 25 anomalies, or the anomalies
        *become* the dominant offset and the normal ones get reported instead —
        which is what the first version of this test did, and why it failed.
        """
        pairs = [(i + 1, i) for i in range(1, 31)]          # 30 × offset 1
        pairs += [(cn + 500, cn) for cn in range(50, 75)]    # 25 × offset 500
        report = self._numbering(scan, pairs)
        assert report["dominant_offset"] == 1
        assert len(report["offset_outliers"]) == 20


# ── report shape / CLI ─────────────────────────────────────────────────


class TestLineNumbersUnderMutation:
    """The two ``+1``s in _split_chapters survived mutation because the original
    fixtures put a blank line and a ``---`` between heading and body: shifting
    the body start one line later still landed inside that filtered region, so
    the mutant was equivalent *for those inputs* — a fixture defect, not a safe
    constant. These fixtures put real content immediately after the heading.
    """

    def test_heading_line_is_one_based(self, scan):
        chapters = scan._split_chapters(
            "# Title\n\n## Chapter 5: 第4章 X\n\nBody.\n")
        assert chapters[0]["start_line"] == 3

    def test_body_starts_on_the_line_after_the_heading(self, scan):
        """No blank line: line 2 is body, so skipping it must be observable."""
        chapters = scan._split_chapters("## Chapter 1: 第1章 X\nFirst.\nSecond.\n")
        assert [ln for ln, _ in chapters[0]["body"]] == [2, 3]

    def test_empty_chapter_finding_points_at_its_heading_line(self, scan):
        chapters = scan._split_chapters(
            "# T\n\n## Chapter 4: 第3章 X\n\n## Chapter 5: 第4章 Y\n\nBody.\n")
        finding = scan.check_body_completeness(
            chapters[0]["body"], chapters[0]["start_line"])
        assert finding["check"] == "EMPTY_CHAPTER"
        assert finding["line"] == 3


class TestEvidenceTruncation:
    """Evidence is what a human reads to triage, so its bounds are pinned: too
    short hides the defect, too long makes the report unreadable."""

    def test_json_residue_evidence_is_capped_at_120_chars(self, scan):
        chapters = scan._split_chapters(
            "## Chapter 1: 第1章 X\n\n" + "y" * 300 + "\n")
        found = scan.check_json_residue(chapters[0]["body"])
        assert found == [], "plain prose is not residue"
        chapters = scan._split_chapters(
            '## Chapter 1: 第1章 X\n\n"translated_text": "' + "z" * 300 + '"\n')
        found = scan.check_json_residue(chapters[0]["body"])
        assert len(found) == 1
        assert len(found[0]["evidence"]) == 120

    def test_truncation_evidence_keeps_the_last_90_chars(self, scan):
        """The *tail* is the evidence for a mid-sentence cut, so the slice is
        [-90:], not [:90]."""
        long_line = "A" * 200 + "but the"
        chapters = scan._split_chapters(
            f"## Chapter 1: 第1章 X\n\n{long_line}\n")
        finding = scan.check_body_completeness(
            chapters[0]["body"], chapters[0]["start_line"])
        assert finding["evidence"].endswith("but the")
        assert len(finding["evidence"]) == 90

    def test_meta_text_evidence_is_capped_at_120_chars(self, scan):
        chapters = scan._split_chapters(
            "## Chapter 1: 第1章 X\n\nEND OF CHAPTER " + "q" * 300 + "\n")
        found = scan.check_meta_text(chapters[0]["body"])
        assert len(found) == 1
        assert len(found[0]["evidence"]) == 120

    def test_chinese_residue_evidence_is_capped_at_120_chars(self, scan):
        chapters = scan._split_chapters(
            "## Chapter 1: 第1章 X\n\n" + "w" * 300 + "卧槽\n")
        found = scan.check_chinese_residue(chapters[0]["body"])
        assert len(found) == 1
        assert len(found[0]["evidence"]) == 120

    def test_cjk_detail_lists_at_most_eight_characters(self, scan):
        """A line of pure CJK would otherwise dump hundreds of chars into a
        single-line report field."""
        chapters = scan._split_chapters(
            "## Chapter 1: 第1章 X\n\n" + "卧槽地府鬼节阎王孟婆阴阳修真" + "\n")
        found = scan.check_chinese_residue(chapters[0]["body"])
        assert len(found) == 1
        detail = found[0]["detail"]
        assert detail.startswith("14 CJK char(s): ")
        listed = detail.split(": ")[1]
        assert len(listed) == 8


class TestReportFields:
    def test_line_count_is_newlines_plus_one(self, scan, tmp_path):
        """A file with no trailing newline still reports its real line count."""
        path = _write(tmp_path, "## Chapter 1: 第1章 A\n\nProse.")
        assert scan.scan_file(path)["lines"] == 3

    def test_line_count_with_trailing_newline(self, scan, tmp_path):
        path = _write(tmp_path, "## Chapter 1: 第1章 A\n\nProse.\n")
        assert scan.scan_file(path)["lines"] == 4

    def test_directory_argument_exits_two(self, scan, tmp_path):
        """--all globs files, but a hand-passed directory must be rejected
        rather than read_text()'d into an IsADirectoryError traceback."""
        assert scan.main([str(tmp_path)]) == 2


class TestHumanReportLayout:
    def test_separator_rule_is_seventy_two_wide(self, scan, tmp_path, capsys):
        path = _write(tmp_path, "## Chapter 1: 第1章 A\n\nProse.\n")
        scan._print_human(scan.scan_file(path))
        out = capsys.readouterr().out
        assert "=" * 72 in out
        assert "=" * 73 not in out

    def test_check_names_are_padded_to_twenty(self, scan, tmp_path, capsys):
        """Literal spaces, not ``f"{x:<20}"`` — formatting the expectation with
        the same width the code uses makes the mutant self-consistent and
        therefore unkillable.

        "JSON_RESIDUE" is 12 chars, padded to the 20-char column gives 8 spaces,
        then the print's own separator space: 9 in total.
        """
        path = _write(tmp_path, "## Chapter 1: 第1章 A\n\n{\n")
        scan._print_human(scan.scan_file(path))
        out = capsys.readouterr().out
        assert "JSON_RESIDUE" + " " * 9 + "1" in out
        assert "JSON_RESIDUE" + " " * 10 not in out

    def test_findings_are_grouped_under_their_chapter(self, scan, tmp_path, capsys):
        path = _write(tmp_path, "## Chapter 7: 第6章 A\n\n{\n")
        scan._print_human(scan.scan_file(path))
        out = capsys.readouterr().out
        assert "Ch7" in out


class TestJsonOutput:
    def test_json_is_pretty_printed(self, scan, tmp_path, capsys):
        """Pinned because a consumer greps this output; compact single-line JSON
        would still parse and would silently break that."""
        import json
        path = _write(tmp_path, "## Chapter 1: 第1章 A\n\nProse.\n")
        assert scan.main([str(path), "--json"]) == 0
        out = capsys.readouterr().out
        json.loads(out)                       # still valid
        assert '\n  "' in out                 # and indented by two

    def test_json_keeps_cjk_readable_not_escaped(self, scan, tmp_path, capsys):
        """``ensure_ascii=False`` is load-bearing: real findings carry Chinese
        evidence (CHINESE_RESIDUE, CJK in a heading-derived detail). With it
        flipped the JSON still parses but every CJK char becomes ``\\uXXXX``,
        which defeats the point of a triage list. The ASCII-only fixtures above
        made this mutant equivalent — caught only by a CJK fixture.
        """
        import json
        path = _write(tmp_path, "## Chapter 1: 第1章 A\n\nHe said 卧槽 aloud.\n")
        assert scan.main([str(path), "--json"]) == 0
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["findings_by_check"]["CHINESE_RESIDUE"] == 1
        assert "卧" in out and "槽" in out
        assert "\\u" not in out

    def test_single_file_json_is_an_object_not_a_list(self, scan, tmp_path, capsys):
        import json
        path = _write(tmp_path, "## Chapter 1: 第1章 A\n\nProse.\n")
        scan.main([str(path), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert isinstance(payload, dict)

    def test_multi_file_json_is_a_list(self, scan, tmp_path, capsys):
        import json
        a = tmp_path / "a_en.md"
        b = tmp_path / "b_en.md"
        a.write_text("## Chapter 1: 第1章 A\n\nProse.\n", encoding="utf-8")
        b.write_text("## Chapter 1: 第1章 B\n\nProse.\n", encoding="utf-8")
        scan.main([str(a), str(b), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert isinstance(payload, list) and len(payload) == 2


# ── end to end ──────────────────────────────────────────────────────────


class TestScanFileEndToEnd:
    def test_clean_file_produces_no_findings(self, scan, tmp_path):
        path = _write(tmp_path, (
            "# Book\n\n"
            "## Chapter 1: 第1章 开端\n\nClean prose here.\n\n"
            "## Chapter 2: 第2章 继续\n\nAlso clean prose.\n"))
        report = scan.scan_file(path)
        assert report["chapters_total"] == 2
        assert report["findings_total"] == 0
        assert report["chapters_with_findings"] == 0

    def test_findings_are_attributed_to_their_own_chapter(self, scan, tmp_path):
        path = _write(tmp_path, (
            "# Book\n\n"
            "## Chapter 1: 第1章 A\n\nFine prose.\n\n"
            "## Chapter 2: 第2章 B\n\n{\n\n"
            "## Chapter 3: 第3章 C\n\nMore fine prose.\n"))
        report = scan.scan_file(path)
        chapters_with_findings = {f["chapter"] for f in report["findings"]}
        assert chapters_with_findings == {2}, (
            "a finding in chapter 2 must not be blamed on 1 or 3")

    def test_report_shape_is_stable_for_json_consumers(self, scan, tmp_path):
        path = _write(tmp_path, "## Chapter 1: 第1章 A\n\nProse.\n")
        report = scan.scan_file(path)
        for key in ("file", "lines", "chapters_total", "chapters_with_findings",
                    "findings_by_check", "findings_total", "numbering_defects",
                    "numbering", "findings"):
            assert key in report, f"missing report key: {key}"

    def test_exit_code_is_zero_for_clean_scan(self, scan, tmp_path, capsys):
        path = _write(tmp_path, "## Chapter 1: 第1章 A\n\nProse.\n")
        assert scan.main([str(path)]) == 0

    def test_strict_exits_one_when_findings_exist(self, scan, tmp_path):
        path = _write(tmp_path, "## Chapter 1: 第1章 A\n\n{\n")
        assert scan.main([str(path), "--strict"]) == 1

    def test_strict_exits_zero_when_clean(self, scan, tmp_path):
        path = _write(tmp_path, "## Chapter 1: 第1章 A\n\nProse.\n")
        assert scan.main([str(path), "--strict"]) == 0

    def test_missing_file_exits_two(self, scan, tmp_path):
        assert scan.main([str(tmp_path / "nope_en.md")]) == 2

    def test_no_arguments_exits_two(self, scan):
        assert scan.main([]) == 2

    def test_json_mode_emits_parseable_report(self, scan, tmp_path, capsys):
        import json
        path = _write(tmp_path, "## Chapter 1: 第1章 A\n\nProse.\n")
        assert scan.main([str(path), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["chapters_total"] == 1

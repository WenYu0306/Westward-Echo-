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
        chapters = scan._split_chapters(
            "## Chapter 9: 第8章 X\n\n## Chapter 10: 第9章 Y\n\nBody.\n"
        )
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
                    "numbering", "quotes", "glossary", "findings"):
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


# ── 条款16：引号风格一致性 ───────────────────────────────────────────────
# 判据阈值全部在 difu_en.md 上实测标定，见 scan_deliverable.py 的 Check 7 注释。
# 这里的 fixture 是那些真实形态的缩略版，不是凭空发明的形状。

_CURLY = "\u201c"   # “
_CURLY_R = "\u201d"  # ”


def _chap(en, body, cn=None):
    """一章的最小 markdown 形态。"""
    title = f"第{cn}章 T" if cn is not None else "T"
    return f"## Chapter {en}: {title}\n\n{body}\n\n---\n"


class TestDominantQuoteStyle:
    def test_straight_wins_when_more_frequent(self, scan, tmp_path):
        text = (_chap(1, 'He said "a" and "b".')
                + _chap(2, 'More "quotes" here.'))
        chapters = scan._split_chapters(text)
        assert scan.dominant_quote_style(chapters) == "straight"

    def test_curly_wins_when_more_frequent(self, scan, tmp_path):
        text = (_chap(1, f"He said {_CURLY}a{_CURLY_R} and {_CURLY}b{_CURLY_R}.")
                + _chap(2, f"More {_CURLY}quotes{_CURLY_R} here."))
        chapters = scan._split_chapters(text)
        assert scan.dominant_quote_style(chapters) == "curly"

    def test_no_quotes_anywhere_defaults_to_straight(self, scan):
        chapters = scan._split_chapters(_chap(1, "Plain prose."))
        assert scan.dominant_quote_style(chapters) == "straight"

    def test_tie_defaults_to_straight(self, scan):
        # 1 直 + 1 弯 → 平票，不得抛错，落到 straight。
        text = _chap(1, 'He said "a".') + _chap(2, f"He said {_CURLY}b{_CURLY_R}.")
        chapters = scan._split_chapters(text)
        assert scan.dominant_quote_style(chapters) == "straight"


class TestQuoteStyleConsistency:
    def _quotes(self, scan, text, dominant="straight"):
        chapters = scan._split_chapters(text)
        return scan.check_quote_style(chapters, dominant)

    def test_all_straight_has_zero_defects(self, scan):
        text = _chap(1, 'He said "a".') + _chap(2, 'She said "b".')
        q = self._quotes(scan, text)
        assert q["dominant"] == "straight"
        assert q["defects"] == 0
        assert q["mixed_chapters"] == []
        assert q["off_dominant_chapters"] == []

    def test_mixed_chapter_is_reported_with_its_line(self, scan):
        # "混用"要求同一章内两种引号都出现；只有一种弯引号的章属于
        # off_dominant（见 test_whole_chapter_in_minority_style_is_off_dominant）。
        text = (_chap(1, 'He said "a".')
                + _chap(2, f'She said "b" then {_CURLY}c{_CURLY_R}.'))
        q = self._quotes(scan, text)
        # _chap 每章占 5 行（标题/空行/正文/空行/分隔线），第 2 章的正文在
        # 第 8 行。指向真实行号是本文件既有的硬约束（见 TestLineNumbersAreReal）。
        assert q["mixed_chapters"] == [
            {"en": 2, "line": 8, "straight": 2, "curly": 2}]
        assert q["off_dominant_chapters"] == []
        assert q["defects"] == 1

    def test_whole_chapter_in_minority_style_is_off_dominant(self, scan):
        text = (_chap(1, 'He said "a".') + _chap(2, 'She said "b".')
                + _chap(3, f"Only {_CURLY}curly{_CURLY_R} here."))
        q = self._quotes(scan, text)
        assert q["off_dominant_chapters"] == [3]
        assert q["mixed_chapters"] == []
        assert q["defects"] == 1

    def test_mixed_and_off_dominant_are_counted_separately(self, scan):
        text = (_chap(1, 'He said "a".')
                + _chap(2, f'Straight "x" plus {_CURLY}curly{_CURLY_R}.')
                + _chap(3, f"Only {_CURLY}curly{_CURLY_R}."))
        q = self._quotes(scan, text)
        assert [m["en"] for m in q["mixed_chapters"]] == [2]
        assert q["off_dominant_chapters"] == [3]
        assert q["defects"] == 2

    def test_curly_quotes_in_heading_are_not_flagged(self, scan):
        """真实回归：Ch157/Ch1345 的弯引号只在**中文原标题**里。

        实测 difu 时我一度把这两章算成"同章混用"，得到 22 章；标题携带中文
        原文是合法的（同 TestChineseResidue::test_cjk_in_the_heading_is_not_
        flagged 的口径），正确值是 20。body 排除标题行，所以不得报。
        """
        # 直接构造贴近真实形态的章节（不走 _chap，因为要精确控制标题行）。
        text = (f'## Chapter 157: 第一节是语文课，{_CURLY}知耻而后勇{_CURLY_R}的陈大计\n'
                f'\nHe said "a". Then "b".\n\n---\n')
        chapters = scan._split_chapters(text)
        q = scan.check_quote_style(chapters, "straight")
        assert q["defects"] == 0
        assert q["mixed_chapters"] == []

    def test_apostrophe_is_not_a_quote_style_defect(self, scan):
        """弯单引号 U+2019 实测 3089 次、绝大多数是英文撇号，不得计入。"""
        text = (_chap(1, 'He said "a".')
                + _chap(2, "The woman\u2019s belly. They aren\u2019t afraid."))
        q = self._quotes(scan, text)
        assert q["defects"] == 0

    def test_straight_apostrophe_is_not_counted_as_quote_style(self, scan):
        """ASCII 单引号 68,039 次全是撇号；只判双引号。"""
        text = (_chap(1, 'He said "a".')
                + _chap(2, "The woman's belly. They aren't afraid."))
        q = self._quotes(scan, text)
        assert q["defects"] == 0

    def test_dominant_style_is_not_a_defect_when_curly_dominates(self, scan):
        """判据判的是**偏离主导**，不是"必须用直引号"。"""
        text = (_chap(1, f"He said {_CURLY}a{_CURLY_R}.")
                + _chap(2, f"She said {_CURLY}b{_CURLY_R}.")
                + _chap(3, 'The odd one out "x".'))
        chapters = scan._split_chapters(text)
        dominant = scan.dominant_quote_style(chapters)
        q = scan.check_quote_style(chapters, dominant)
        assert dominant == "curly"
        assert q["off_dominant_chapters"] == [3]
        assert q["defects"] == 1

    def test_empty_book_has_zero_defects(self, scan):
        chapters = scan._split_chapters("")
        q = scan.check_quote_style(chapters, "straight")
        assert q["chapters_total"] == 0
        assert q["defects"] == 0


class TestGlossaryConflicts:
    def _g(self, scan, glossary):
        return scan.check_glossary_conflicts(glossary)

    def test_empty_glossary_reports_zero(self, scan):
        g = self._g(scan, {})
        assert g == {"terms_total": 0, "one_en_many_cn": 0,
                     "conflicts": [], "legitimate_variants": []}

    def test_one_to_one_mapping_is_clean(self, scan):
        g = self._g(scan, {"八爷": "Eighth Master", "七爷": "Seventh Master"})
        assert g["terms_total"] == 2
        assert g["one_en_many_cn"] == 0
        assert g["conflicts"] == []

    def test_homophone_clash_is_a_conflict(self, scan):
        """实测形态：白无畏/白无谓 同音异形字，必有一个是错的。"""
        g = self._g(scan, {"白无畏": "Bai Wuwei", "白无谓": "Bai Wuwei"})
        assert g["one_en_many_cn"] == 1
        assert len(g["conflicts"]) == 1
        c = g["conflicts"][0]
        assert c["kind"] == "HOMOPHONE_CLASH"
        assert c["en"] == "Bai Wuwei"
        assert c["variants"] == ["白无畏", "白无谓"]

    def test_dirty_term_is_a_conflict(self, scan):
        """实测形态：犬封恶鬼/犬尼 —— 术语值里带斜杠注释。"""
        g = self._g(scan, {"犬封恶鬼/犬尼": "Dog Ni", "犬尼": "Dog Ni"})
        assert len(g["conflicts"]) == 1
        c = g["conflicts"][0]
        assert c["kind"] == "DIRTY_TERM"
        assert c["en"] == "Dog Ni"

    def test_parenthetical_annotation_is_dirty(self, scan):
        """实测形态：少将军 (addressing Chen Daji)。"""
        g = self._g(scan, {"少将军": "Young General",
                           "少将军 (addressing Chen Daji)": "Young General"})
        assert [c["kind"] for c in g["conflicts"]] == ["DIRTY_TERM"]

    def test_full_width_paren_is_dirty_too(self, scan):
        """实测形态：洪贤（避嫌，通县）长 —— 全角括号同样算脏标记。"""
        g = self._g(scan, {"洪贤长": "County Chief Hong",
                           "洪贤（避嫌，通县）长": "County Chief Hong"})
        assert [c["kind"] for c in g["conflicts"]] == ["DIRTY_TERM"]

    def test_honorific_variant_is_not_a_conflict(self, scan):
        """实测形态：容慧/容慧禅师 是简称与全称，合法，只报不判。"""
        g = self._g(scan, {"容慧": "Chan Master Ronghui",
                           "容慧禅师": "Chan Master Ronghui"})
        assert g["conflicts"] == []
        assert g["one_en_many_cn"] == 1
        assert g["legitimate_variants"] == [
            {"en": "Chan Master Ronghui", "variants": ["容慧", "容慧禅师"]}]

    def test_suffix_variant_is_not_a_conflict(self, scan):
        """实测形态：鬼母/鬼母邪神。"""
        g = self._g(scan, {"鬼母": "Ghost Mother", "鬼母邪神": "Ghost Mother"})
        assert g["conflicts"] == []
        assert len(g["legitimate_variants"]) == 1

    def test_two_char_difference_is_not_treated_as_homophone(self, scan):
        """实测形态：灰家仙/灰老六 —— 等长但差 2 字，不是同音字冲突。"""
        g = self._g(scan, {"灰家仙": "Grey Sixth", "灰老六": "Grey Sixth"})
        assert g["conflicts"] == []
        assert len(g["legitimate_variants"]) == 1

    def test_different_lengths_are_not_homophone(self, scan):
        """不等长就不可能是"仅一字不同"，归入合法变体。"""
        g = self._g(scan, {"常二爷": "Second Master Chang",
                           "常家二爷": "Second Master Chang"})
        assert g["conflicts"] == []
        assert len(g["legitimate_variants"]) == 1

    def test_dirty_wins_over_homophone_when_both_apply(self, scan):
        """脏标记优先归类，避免同一组报两次。"""
        g = self._g(scan, {"白无畏": "Bai Wuwei",
                           "白无谓 (alias)": "Bai Wuwei"})
        assert [c["kind"] for c in g["conflicts"]] == ["DIRTY_TERM"]

    def test_many_conflicts_are_all_reported(self, scan):
        """实测 difu 有 13 组真冲突；判据必须全列，不得只报第一组。"""
        glossary = {}
        for i in range(13):
            glossary[f"白无{i}畏"] = f"Term{i}"
            glossary[f"白无{i}谓"] = f"Term{i}"
        g = self._g(scan, glossary)
        assert g["one_en_many_cn"] == 13
        assert len(g["conflicts"]) == 13
        assert all(c["kind"] == "HOMOPHONE_CLASH" for c in g["conflicts"])

    def test_conflicts_sorted_by_english_rendering(self, scan):
        glossary = {"白无畏": "Zeta", "白无谓": "Zeta",
                    "常怀元": "Alpha", "常怀远": "Alpha"}
        g = self._g(scan, glossary)
        assert [c["en"] for c in g["conflicts"]] == ["Alpha", "Zeta"]


class TestLoadGlossary:
    def test_missing_file_yields_empty_dict(self, scan, tmp_path):
        path = tmp_path / "book_en.md"
        path.write_text("## Chapter 1: 第1章 A\n\nProse.\n", encoding="utf-8")
        assert scan._load_glossary(path) == {}

    def test_valid_glossary_is_loaded(self, scan, tmp_path):
        import json
        path = tmp_path / "book_en.md"
        path.write_text("## Chapter 1: 第1章 A\n\nProse.\n", encoding="utf-8")
        (tmp_path / "_glossary.json").write_text(
            json.dumps({"八爷": "Eighth Master"}), encoding="utf-8")
        assert scan._load_glossary(path) == {"八爷": "Eighth Master"}

    def test_corrupt_json_yields_empty_dict_not_crash(self, scan, tmp_path):
        path = tmp_path / "book_en.md"
        path.write_text("## Chapter 1: 第1章 A\n\nProse.\n", encoding="utf-8")
        (tmp_path / "_glossary.json").write_text("{not json", encoding="utf-8")
        assert scan._load_glossary(path) == {}

    def test_json_that_is_not_a_mapping_yields_empty_dict(self, scan, tmp_path):
        import json
        path = tmp_path / "book_en.md"
        path.write_text("## Chapter 1: 第1章 A\n\nProse.\n", encoding="utf-8")
        (tmp_path / "_glossary.json").write_text(
            json.dumps(["not", "a", "dict"]), encoding="utf-8")
        assert scan._load_glossary(path) == {}

    def test_scan_file_without_glossary_reports_zero_terms(self, scan, tmp_path):
        path = _write(tmp_path, "## Chapter 1: 第1章 A\n\nProse.\n")
        report = scan.scan_file(path)
        assert report["glossary"]["terms_total"] == 0

    def test_scan_file_reads_sibling_glossary(self, scan, tmp_path):
        import json
        path = _write(tmp_path, "## Chapter 1: 第1章 A\n\nProse.\n")
        (tmp_path / "_glossary.json").write_text(json.dumps({
            "白无畏": "Bai Wuwei", "白无谓": "Bai Wuwei",
        }), encoding="utf-8")
        report = scan.scan_file(path)
        assert report["glossary"]["terms_total"] == 2
        assert len(report["glossary"]["conflicts"]) == 1


class TestQuoteStyleInScanFile:
    def test_quote_defects_do_not_enter_per_chapter_findings(self, scan, tmp_path):
        """引号偏离单独成节，不得混进逐章 findings 把条款14 分诊项冲掉。

        difu 实测 203 章偏离 vs 17 条逐章缺陷——混在一起就没人看得见了。
        """
        text = (_chap(1, 'He said "a".') + _chap(2, 'She said "b".')
                + _chap(3, f"Only {_CURLY}curly{_CURLY_R}."))
        path = _write(tmp_path, text)
        report = scan.scan_file(path)
        assert report["quotes"]["defects"] == 1
        assert report["findings_total"] == 0
        assert "QUOTE_STYLE_MIX" not in report["findings_by_check"]

    def test_strict_exits_one_on_quote_defects_only(self, scan, tmp_path):
        text = (_chap(1, 'He said "a".') + _chap(2, 'She said "b".')
                + _chap(3, f"Only {_CURLY}curly{_CURLY_R}."))
        path = _write(tmp_path, text)
        report = scan.scan_file(path)
        assert report["findings_total"] == 0        # 逐章缺陷为零
        assert report["numbering_defects"] == 0     # 章号异常为零
        assert report["quotes"]["defects"] == 1     # 仅引号偏离
        assert scan.main([str(path), "--strict"]) == 1

    def test_strict_exits_one_on_glossary_conflicts_only(self, scan, tmp_path):
        import json
        path = _write(tmp_path, '## Chapter 1: 第1章 A\n\nHe said "a".\n')
        (tmp_path / "_glossary.json").write_text(json.dumps({
            "白无畏": "Bai Wuwei", "白无谓": "Bai Wuwei",
        }), encoding="utf-8")
        report = scan.scan_file(path)
        assert report["findings_total"] == 0
        assert report["quotes"]["defects"] == 0
        assert len(report["glossary"]["conflicts"]) == 1
        assert scan.main([str(path), "--strict"]) == 1

    def test_strict_still_zero_when_only_legitimate_variants(self, scan, tmp_path):
        """合法变体（简称/全称）不得让 --strict 变红。"""
        import json
        path = _write(tmp_path, '## Chapter 1: 第1章 A\n\nHe said "a".\n')
        (tmp_path / "_glossary.json").write_text(json.dumps({
            "容慧": "Chan Master Ronghui", "容慧禅师": "Chan Master Ronghui",
        }), encoding="utf-8")
        report = scan.scan_file(path)
        assert report["glossary"]["conflicts"] == []
        assert len(report["glossary"]["legitimate_variants"]) == 1
        assert scan.main([str(path), "--strict"]) == 0


class TestQuoteReportLayout:
    def test_off_dominant_list_is_capped_at_twenty(self, scan, tmp_path, capsys):
        """183 章全列会淹没其余缺陷；照 _OFFSET_OUTLIER_LIMIT 的先例截断。

        主导是按**引号字符数**定的，不是按章数，所以直引号章必须够多才能压过
        40 章弯引号：每章各 2 个引号字符，60 直 vs 40 弯 → 直引号主导，
        那 40 章弯引号才是"偏离主导"。（difu 真实语料是 79,399 直 vs 8,005 弯。）
        """
        text = ""
        for en in range(1, 61):                       # 60 章直引号（主导）
            text += _chap(en, 'He said "a".')
        for en in range(61, 101):                     # 40 章通篇弯引号
            text += _chap(en, f"Only {_CURLY}curly{_CURLY_R}.")
        path = _write(tmp_path, text)
        report = scan.scan_file(path)
        assert report["quotes"]["dominant"] == "straight"
        assert len(report["quotes"]["off_dominant_chapters"]) == 40
        scan.main([str(path)])
        out = capsys.readouterr().out
        assert "通篇用少数派引号: 40 章（前 20）" in out

    def test_mixed_chapter_list_is_capped_at_twenty(self, scan, tmp_path, capsys):
        text = ""
        for en in range(1, 61):                       # 60 章纯直引号，稳住主导
            text += _chap(en, 'He said "a".')
        for en in range(61, 91):                      # 30 章混用
            text += _chap(en, f'Straight "x" plus {_CURLY}curly{_CURLY_R}.')
        path = _write(tmp_path, text)
        report = scan.scan_file(path)
        assert report["quotes"]["dominant"] == "straight"
        assert len(report["quotes"]["mixed_chapters"]) == 30
        scan.main([str(path)])
        out = capsys.readouterr().out
        assert "同章混用两套引号: 30 章（前 20）" in out

    def test_glossary_conflicts_are_all_printed(self, scan, tmp_path, capsys):
        """13 组真冲突必须全印——这是给人修的动作清单，不是分布诊断。"""
        import json
        glossary = {}
        for i in range(13):
            glossary[f"白无{i}畏"] = f"Term{i}"
            glossary[f"白无{i}谓"] = f"Term{i}"
        path = _write(tmp_path, '## Chapter 1: 第1章 A\n\nHe said "a".\n')
        (tmp_path / "_glossary.json").write_text(
            json.dumps(glossary), encoding="utf-8")
        scan.main([str(path)])
        out = capsys.readouterr().out
        for i in range(13):
            assert f"Term{i}" in out

    def test_missing_glossary_says_skipped(self, scan, tmp_path, capsys):
        path = _write(tmp_path, "## Chapter 1: 第1章 A\n\nProse.\n")
        scan.main([str(path)])
        assert "无 _glossary.json，跳过" in capsys.readouterr().out

    def test_conclusion_line_counts_all_four_defect_kinds(self, scan, tmp_path, capsys):
        import json
        text = _chap(1, 'He said "a".') + _chap(2, f"Only {_CURLY}c{_CURLY_R}.")
        path = _write(tmp_path, text)
        (tmp_path / "_glossary.json").write_text(json.dumps({
            "白无畏": "Bai Wuwei", "白无谓": "Bai Wuwei",
        }), encoding="utf-8")
        scan.main([str(path)])
        out = capsys.readouterr().out
        assert "0 条逐章缺陷" in out
        assert "1 章引号风格偏离" in out
        assert "1 组术语表冲突" in out


class TestQuoteMinorityLineIsLocated:
    """少数派引号的**行号定位**必须真的在找少数派，不能退化成"章首行"。

    变异实测：`q in line` → `not in`、`dominant == "straight"` → `!=` 两个
    变异点都存活过一轮，原因是早先的用例里该章只有一行正文，"第一处少数派
    引号所在行"与"章的第一行"是同一个值，条件怎么变都不可观测。这里让少数派
    引号落在**第二行**，两个变异都会把行号指错。
    """

    def test_minority_quote_on_second_body_line_reports_that_line(self, scan):
        # _chap 每章占 5 行（标题/空/正文/空/分隔线），拼接时不额外加行：
        #   行1 标题 / 行3 正文 → 第 2 章标题在行 6，正文首行 8、次行 9
        text = (_chap(1, 'He said "a".')
                + _chap(2, 'First line has only straight "x".\n'
                           f'Then {_CURLY}curly{_CURLY_R} shows up.'))
        chapters = scan._split_chapters(text)
        q = scan.check_quote_style(chapters, "straight")
        assert len(q["mixed_chapters"]) == 1
        assert q["mixed_chapters"][0]["line"] == 9    # 次行，不是章首行 8
        assert q["mixed_chapters"][0]["en"] == 2

    def test_first_body_line_wins_when_minority_starts_there(self, scan):
        """反向对照：少数派引号就在首行时，行号必须是首行。"""
        text = (_chap(1, 'He said "a".')
                + _chap(2, f'{_CURLY}curly{_CURLY_R} first.\n'
                           'Then straight "x" later.'))
        chapters = scan._split_chapters(text)
        q = scan.check_quote_style(chapters, "straight")
        assert q["mixed_chapters"][0]["line"] == 8    # 首行

    def test_each_mixed_chapter_points_at_its_own_line(self, scan):
        """多个混用章各自定位，不得串位或共用同一个行号。

        off_dominant 分支只存章号（183 章机械替换不需要行号），所以行号定位
        只由 mixed_chapters 承载——它必须逐章独立算对。
        """
        text = (_chap(1, 'He said "a".')
                + _chap(2, f'{_CURLY}curly{_CURLY_R} first.\nStraight "x" later.')
                + _chap(3, 'Only straight "y" here.\n'
                           f'Then {_CURLY}curly{_CURLY_R} appears.'))
        chapters = scan._split_chapters(text)
        q = scan.check_quote_style(chapters, "straight")
        assert [(m["en"], m["line"]) for m in q["mixed_chapters"]] == [
            (2, 8),     # 第 2 章：标题 6 / 空 7 / 正文首行 8
            (3, 15),    # 第 3 章：第 2 章正文占两行，故标题在 12 / 空 13
                        # / 正文首行 14 / 次行 15（少数派弯引号在次行）
        ]
        assert q["off_dominant_chapters"] == []


class TestGlossaryReportLabels:
    """报告里的缺陷类型标签必须与判据结果一致。

    变异实测：`c["kind"] == "DIRTY_TERM"` → `!=` 存活过一轮。标签印反会把
    人引向错误的修复方向（把术语值里的斜杠注释当成同音字去改），所以这是
    行为，不是布局。
    """

    def _report(self, scan, tmp_path, glossary):
        import json
        path = _write(tmp_path, '## Chapter 1: 第1章 A\n\nHe said "a".\n')
        (tmp_path / "_glossary.json").write_text(
            json.dumps(glossary), encoding="utf-8")
        scan.main([str(path)])
        return path

    def test_dirty_term_is_labelled_dirty(self, scan, tmp_path, capsys):
        self._report(scan, tmp_path,
                     {"犬封恶鬼/犬尼": "Dog Ni", "犬尼": "Dog Ni"})
        out = capsys.readouterr().out
        assert "[脏标记] Dog Ni" in out
        assert "[同音/形近字] Dog Ni" not in out

    def test_homophone_clash_is_labelled_homophone(self, scan, tmp_path, capsys):
        self._report(scan, tmp_path,
                     {"白无畏": "Bai Wuwei", "白无谓": "Bai Wuwei"})
        out = capsys.readouterr().out
        assert "[同音/形近字] Bai Wuwei" in out
        assert "[脏标记] Bai Wuwei" not in out

    def test_both_labels_appear_when_both_kinds_present(self, scan, tmp_path, capsys):
        self._report(scan, tmp_path, {
            "犬尼": "Dog Ni", "犬封恶鬼/犬尼": "Dog Ni",
            "白无畏": "Bai Wuwei", "白无谓": "Bai Wuwei",
        })
        out = capsys.readouterr().out
        assert "[脏标记] Dog Ni" in out
        assert "[同音/形近字] Bai Wuwei" in out


class TestDominantStyleLabelInReport:
    """主导风格的中文标签必须跟着实际主导风格走。

    变异实测：`q["dominant"] == "straight"` → `!=` 存活过一轮——报告会把
    直引号主导的书印成"主导: 弯双引号"，人工据此改错方向。
    """

    def test_straight_dominant_book_says_straight(self, scan, tmp_path, capsys):
        path = _write(tmp_path, '## Chapter 1: 第1章 A\n\nHe said "a".\n')
        scan.main([str(path)])
        assert "主导: 直双引号" in capsys.readouterr().out

    def test_curly_dominant_book_says_curly(self, scan, tmp_path, capsys):
        text = (_chap(1, f"He said {_CURLY}a{_CURLY_R}.")
                + _chap(2, f"She said {_CURLY}b{_CURLY_R}."))
        path = _write(tmp_path, text)
        report = scan.scan_file(path)
        assert report["quotes"]["dominant"] == "curly"
        scan.main([str(path)])
        out = capsys.readouterr().out
        assert "主导: 弯双引号" in out
        assert "主导: 直双引号" not in out

"""Tests for scripts/clean_glossary.py — conservative glossary hygiene.

The cleaner must never rewrite a term it cannot justify: a wrong rewrite would
change what every future chapter is told to use. These tests pin the three
actions (drop invented, drop redundant, report ambiguous) and the fact that the
input dict is not mutated.
"""

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "clean_glossary.py"

_spec = importlib.util.spec_from_file_location("clean_glossary_under_test", _SCRIPT)
cg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cg)

SOURCE = "犬尼来了，少将军走了，洪贤长笑了。张三丰在此。"


class TestCandidateKeys:
    def test_splits_on_slash_and_keeps_forms_present_in_source(self):
        assert cg.candidate_keys("犬封恶鬼/犬尼", SOURCE) == ["犬尼"]

    def test_both_forms_survive_when_both_are_in_the_source(self):
        assert cg.candidate_keys("洪贤长/少将军", SOURCE) == ["洪贤长", "少将军"]

    def test_parenthetical_is_stripped(self):
        assert cg.candidate_keys("少将军 (addressing Chen Daji)", SOURCE) == ["少将军"]
        assert cg.candidate_keys("洪贤（避嫌，通县）长", SOURCE) == ["洪贤长"]

    def test_nothing_survives_when_the_key_was_invented(self):
        assert cg.candidate_keys("Zhang Shen (Aunt Zhang)", SOURCE) == []


class TestClean:
    def test_invented_key_is_dropped(self):
        glossary = {"张三丰": "Zhang Sanfeng", "Zhang Shen (Aunt Zhang)": "Aunt Zhang"}
        cleaned, changed, review = cg.clean(glossary, SOURCE)
        assert "Zhang Shen (Aunt Zhang)" not in cleaned
        assert [c["why"] for c in changed] == ["源文从未出现"]
        assert review == []

    def test_redundant_composite_is_dropped(self):
        glossary = {"少将军": "Young General",
                    "少将军 (addressing Chen Daji)": "Young General"}
        cleaned, changed, _ = cg.clean(glossary, SOURCE)
        assert "少将军 (addressing Chen Daji)" not in cleaned
        assert cleaned["少将军"] == "Young General"
        assert changed[0]["why"] == "拆开后各项已存在"

    def test_ambiguous_composite_is_reported_not_rewritten(self):
        """``洪贤长/少将军`` maps two names to one rendering and ``少将军`` has no
        entry of its own — deciding which English belongs to which name is not
        derivable, so it is left alone."""
        glossary = {"洪贤长": "County Chief Hong",
                    "洪贤长/少将军": "Hong and the Young General"}
        cleaned, changed, review = cg.clean(glossary, SOURCE)
        assert cleaned == glossary
        assert changed == []
        assert [r["key"] for r in review] == ["洪贤长/少将军"]

    def test_existing_mapping_is_never_overwritten(self):
        glossary = {"少将军": "Young General", "少将军 (mock)": "Little General"}
        cleaned, changed, review = cg.clean(glossary, SOURCE)
        assert cleaned["少将军"] == "Young General"
        assert "少将军 (mock)" not in cleaned
        assert review == []

    def test_input_is_not_mutated(self):
        glossary = {"少将军 (x)": "Young General", "少将军": "Young General"}
        snapshot = dict(glossary)
        cg.clean(glossary, SOURCE)
        assert glossary == snapshot

    def test_clean_glossary_is_untouched(self):
        glossary = {"犬尼": "Dog Ni", "少将军": "Young General"}
        cleaned, changed, review = cg.clean(glossary, SOURCE)
        assert cleaned == glossary and changed == [] and review == []

"""Focused tests for TranslationAgent._post_process — the compile-quality gates.

These are the "engineering backstop" gates: they run after the LLM nodes and
decide what may be cached for the rest of the book. If a gate's condition is
flipped, tests must go red — this file exists to give the mutation harness
(``scripts/mutation_test.py graph_post_process``) something fast and sharp to
run. It deliberately avoids the full pipeline (slow) and asserts on logs,
because a gate that silently takes the wrong branch is often invisible in the
returned data alone.
"""

import logging

import pytest

from src.agent.graph import TranslationAgent
from src.config import MAX_RETRANSLATION_ATTEMPTS


@pytest.fixture
def agent():
    a = TranslationAgent(book_id="test_post_process_gates")
    a.style_memo.update_from_read_analysis = lambda *a, **k: None
    a.style_memo.update_from_feedback = lambda *a, **k: None
    a.exact_store.add_batch = lambda *a, **k: None
    a.semantic_store.add_batch = lambda *a, **k: None
    return a


def make_result(
    translated_text="Wang San lived in the village.",
    read_decisions=None,
    new_terms=None,
    readback_feedback=None,
    read_analysis=None,
    retries=0,
):
    """Build a post-graph result dict; read_analysis defaults to a normal dict."""
    if read_analysis is None:
        read_analysis = {"terminology_decisions": read_decisions or []}
    return {
        "chapter_number": 1,
        "translated_text": translated_text,
        "read_analysis": read_analysis,
        "new_terms_found": new_terms if new_terms is not None else [],
        "readback_feedback": readback_feedback if readback_feedback is not None else {},
        "quality_issues": [],
        "adaptation_notes": [],
        "chapter_summary": "",
        "retranslation_count": retries,
    }


def warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


# ────────────────────────── fidelity check (records) ──────────────────────────


class TestFidelityCheckGate:
    def test_drift_is_recorded_in_quality_issues_and_logged(self, agent, caplog):
        result = make_result(
            translated_text="Lóng Pópo lived in the village.",
            read_decisions=[{"term_cn": "聋婆婆", "proposed_en": "Deaf Granny"}],
        )
        with caplog.at_level(logging.WARNING, logger="src.agent.graph"):
            agent._post_process(result, "en-US")

        assert any("聋婆婆" in issue for issue in result["quality_issues"])
        assert any(w.startswith("FIDELITY ch1") for w in warnings(caplog))

    def test_no_drift_leaves_quality_issues_empty(self, agent, caplog):
        result = make_result(
            translated_text="Deaf Granny lived in the village.",
            read_decisions=[{"term_cn": "聋婆婆", "proposed_en": "Deaf Granny"}],
        )
        with caplog.at_level(logging.WARNING, logger="src.agent.graph"):
            agent._post_process(result, "en-US")

        assert result["quality_issues"] == []
        assert not any(w.startswith("FIDELITY") for w in warnings(caplog))

    def test_existing_quality_issues_are_preserved(self, agent):
        result = make_result(
            translated_text="Lóng Pópo lived in the village.",
            read_decisions=[{"term_cn": "聋婆婆", "proposed_en": "Deaf Granny"}],
        )
        result["quality_issues"] = ["pre-existing issue"]
        agent._post_process(result, "en-US")

        assert result["quality_issues"][0] == "pre-existing issue"
        assert len(result["quality_issues"]) == 2


# ────────────────────────── FORCED_ACCEPT audit log ──────────────────────────


class TestForcedAcceptAudit:
    def _feedback(self, verdict):
        return {
            "verdict": verdict,
            "comprehension_issues": [{"passage": "x", "issue": "y"}],
            "engagement_gaps": [],
        }

    def test_logged_at_max_retries(self, agent, caplog):
        result = make_result(retries=MAX_RETRANSLATION_ATTEMPTS,
                             readback_feedback=self._feedback("NEEDS_FIX"))
        with caplog.at_level(logging.WARNING, logger="src.agent.graph"):
            agent._post_process(result, "en-US")
        assert any(w.startswith("FORCED_ACCEPT") for w in warnings(caplog))

    def test_logged_above_max_retries(self, agent, caplog):
        result = make_result(retries=MAX_RETRANSLATION_ATTEMPTS + 1,
                             readback_feedback=self._feedback("NEEDS_FIX"))
        with caplog.at_level(logging.WARNING, logger="src.agent.graph"):
            agent._post_process(result, "en-US")
        assert any(w.startswith("FORCED_ACCEPT") for w in warnings(caplog))

    def test_not_logged_below_max_retries(self, agent, caplog):
        # FIX still has attempts left — this is not a forced accept.
        result = make_result(retries=MAX_RETRANSLATION_ATTEMPTS - 1,
                             readback_feedback=self._feedback("NEEDS_FIX"))
        with caplog.at_level(logging.WARNING, logger="src.agent.graph"):
            agent._post_process(result, "en-US")
        assert not any(w.startswith("FORCED_ACCEPT") for w in warnings(caplog))

    def test_not_logged_when_verdict_passes(self, agent, caplog):
        result = make_result(retries=MAX_RETRANSLATION_ATTEMPTS,
                             readback_feedback=self._feedback("PASS"))
        with caplog.at_level(logging.WARNING, logger="src.agent.graph"):
            agent._post_process(result, "en-US")
        assert not any(w.startswith("FORCED_ACCEPT") for w in warnings(caplog))

    def test_not_logged_without_feedback(self, agent, caplog):
        result = make_result(retries=MAX_RETRANSLATION_ATTEMPTS)
        with caplog.at_level(logging.WARNING, logger="src.agent.graph"):
            agent._post_process(result, "en-US")
        assert not any(w.startswith("FORCED_ACCEPT") for w in warnings(caplog))

    def test_only_three_fix_changes_are_reported(self, agent, caplog):
        result = make_result(retries=MAX_RETRANSLATION_ATTEMPTS,
                             readback_feedback=self._feedback("NEEDS_FIX"))
        result["adaptation_notes"] = ["note1", "note2", "note3", "note4"]
        with caplog.at_level(logging.WARNING, logger="src.agent.graph"):
            agent._post_process(result, "en-US")
        msg = [w for w in warnings(caplog) if w.startswith("FORCED_ACCEPT")][0]
        assert "note3" in msg
        assert "note4" not in msg


# ────────────────────────── style-memo side effects ──────────────────────────


class TestStyleMemoUpdates:
    def test_read_analysis_feeds_memo_every_chapter(self, agent):
        seen = {}
        agent.style_memo.update_from_read_analysis = (
            lambda analysis, num: seen.update(analysis=analysis, num=num)
        )
        result = make_result(read_analysis={"terminology_decisions": []})
        agent._post_process(result, "en-US")
        assert seen["num"] == 1

    def test_empty_read_analysis_skips_memo(self, agent):
        called = []
        agent.style_memo.update_from_read_analysis = lambda *a, **k: called.append(1)
        result = make_result(read_analysis={})
        agent._post_process(result, "en-US")
        assert called == []

    def test_feedback_feeds_memo_when_present(self, agent):
        called = []
        agent.style_memo.update_from_feedback = lambda *a, **k: called.append(1)
        result = make_result(readback_feedback={"verdict": "PASS"})
        agent._post_process(result, "en-US")
        assert called == [1]

    def test_no_feedback_skips_memo(self, agent):
        called = []
        agent.style_memo.update_from_feedback = lambda *a, **k: called.append(1)
        result = make_result()
        agent._post_process(result, "en-US")
        assert called == []


# ────────────────────────── READ notes merge ──────────────────────────


class TestNoteMerge:
    def test_read_cultural_note_fills_missing_note(self, agent):
        result = make_result(
            read_decisions=[{
                "term_cn": "聋婆婆",
                "proposed_en": "Deaf Granny",
                "cultural_note": "pun on dragon (龙)",
            }],
            new_terms=[{"term_cn": "聋婆婆", "term_en": "Deaf Granny",
                        "category": "character"}],
        )
        agent._post_process(result, "en-US")
        assert result["new_terms_found"][0]["note"] == "pun on dragon (龙)"

    def test_existing_note_is_not_clobbered(self, agent):
        # WRITE already recorded a note — READ's note must not overwrite it.
        result = make_result(
            read_decisions=[{
                "term_cn": "聋婆婆",
                "proposed_en": "Deaf Granny",
                "cultural_note": "READ's note",
            }],
            new_terms=[{"term_cn": "聋婆婆", "term_en": "Deaf Granny",
                        "category": "character", "note": "WRITE's note"}],
        )
        agent._post_process(result, "en-US")
        assert result["new_terms_found"][0]["note"] == "WRITE's note"

    def test_term_without_read_note_untouched(self, agent):
        result = make_result(
            read_decisions=[{"term_cn": "王三", "proposed_en": "Wang San"}],
            new_terms=[{"term_cn": "聋婆婆", "term_en": "Deaf Granny",
                        "category": "character"}],
        )
        agent._post_process(result, "en-US")
        assert "note" not in result["new_terms_found"][0]

    def test_missing_read_analysis_skips_both_merge_blocks(self, agent):
        # A result whose read_analysis is missing/None must not be treated as
        # "has analysis" — the guard is `new_terms and read_analysis`, so a
        # `boolop ->or` mutant would dereference None here.
        result = make_result(
            new_terms=[{"term_cn": "聋婆婆", "term_en": "Deaf Granny",
                        "category": "character"}],
        )
        result["read_analysis"] = None
        agent._post_process(result, "en-US")
        assert result["new_terms_found"][0]["term_en"] == "Deaf Granny"


# ────────────────────────── fidelity gate (WRITE drift) ──────────────────────────


class TestFidelityGateBranches:
    def test_gate_without_new_terms_is_a_noop(self, agent, caplog):
        result = make_result(
            read_decisions=[{"term_cn": "聋婆婆", "proposed_en": "Deaf Granny"}],
            new_terms=[],
        )
        with caplog.at_level(logging.WARNING, logger="src.agent.graph"):
            agent._post_process(result, "en-US")
        assert not any(w.startswith("FIDELITY GATE") for w in warnings(caplog))
        assert result["new_terms_found"] == []

    def test_gate_without_read_analysis_is_a_noop(self, agent, caplog):
        result = make_result(
            read_analysis={},
            new_terms=[{"term_cn": "聋婆婆", "term_en": "Lóng Pópo",
                        "category": "character"}],
        )
        with caplog.at_level(logging.WARNING, logger="src.agent.graph"):
            agent._post_process(result, "en-US")
        assert not any(w.startswith("FIDELITY GATE") for w in warnings(caplog))

    def test_identical_rendering_does_not_fire_the_gate(self, agent, caplog):
        # Mutating `read_en != term_en` to `==` would enter the branch here.
        result = make_result(
            translated_text="Deaf Granny lived in the village.",
            read_decisions=[{"term_cn": "聋婆婆", "proposed_en": "Deaf Granny"}],
            new_terms=[{"term_cn": "聋婆婆", "term_en": "Deaf Granny",
                        "category": "character"}],
        )
        with caplog.at_level(logging.WARNING, logger="src.agent.graph"):
            agent._post_process(result, "en-US")
        assert not any(w.startswith("FIDELITY GATE") for w in warnings(caplog))

    def test_empty_proposed_en_does_not_fire_the_gate(self, agent, caplog):
        result = make_result(
            read_decisions=[{"term_cn": "聋婆婆", "proposed_en": ""}],
            new_terms=[{"term_cn": "聋婆婆", "term_en": "Lóng Pópo",
                        "category": "character"}],
        )
        with caplog.at_level(logging.WARNING, logger="src.agent.graph"):
            agent._post_process(result, "en-US")
        assert not any(w.startswith("FIDELITY GATE") for w in warnings(caplog))

    def test_empty_term_cn_does_not_fire_the_gate(self, agent, caplog):
        result = make_result(
            read_decisions=[{"term_cn": "", "proposed_en": "Deaf Granny"}],
            new_terms=[{"term_cn": "", "term_en": "Lóng Pópo", "category": "character"}],
        )
        with caplog.at_level(logging.WARNING, logger="src.agent.graph"):
            agent._post_process(result, "en-US")
        assert not any(w.startswith("FIDELITY GATE") for w in warnings(caplog))

    def test_rendering_of_exactly_50_chars_is_accepted(self, agent):
        # Boundary: len(en) <= 50 is the gate's acceptance condition.
        read_en = "A" * 50
        result = make_result(
            read_decisions=[{"term_cn": "聋婆婆", "proposed_en": read_en}],
            new_terms=[{"term_cn": "聋婆婆", "term_en": "Lóng Pópo",
                        "category": "character"}],
        )
        agent._post_process(result, "en-US")
        assert result["new_terms_found"][0]["term_en"] == read_en

    def test_rendering_of_51_chars_is_rejected(self, agent, caplog):
        # Boundary: one char over the limit must not be cached as a term.
        read_en = "A" * 51
        result = make_result(
            read_decisions=[{"term_cn": "聋婆婆", "proposed_en": read_en}],
            new_terms=[{"term_cn": "聋婆婆", "term_en": "Lóng Pópo",
                        "category": "character"}],
        )
        with caplog.at_level(logging.WARNING, logger="src.agent.graph"):
            agent._post_process(result, "en-US")
        assert not any(w.startswith("FIDELITY GATE") for w in warnings(caplog))


# ────────────────────────── curation gate → stores ──────────────────────────


class TestCurationRoutesToStores:
    def test_character_and_location_go_to_exact_store(self, agent):
        captured = {}
        agent.exact_store.add_batch = lambda terms, **k: captured.update(exact=terms)
        agent.semantic_store.add_batch = lambda terms, **k: captured.update(semantic=terms)

        result = make_result(new_terms=[
            {"term_cn": "聋婆婆", "term_en": "Deaf Granny", "category": "character"},
            {"term_cn": "九道沟村", "term_en": "Jiudaogou Village", "category": "location"},
            {"term_cn": "鬼节", "term_en": "Ghost Festival", "category": "culture"},
        ])
        agent._post_process(result, "en-US")

        assert [t["term_cn"] for t in captured["exact"]] == ["聋婆婆", "九道沟村"]
        assert [t["term_cn"] for t in captured["semantic"]] == ["聋婆婆", "九道沟村", "鬼节"]

    def test_culture_terms_never_reach_exact_store(self, agent):
        captured = {}
        agent.exact_store.add_batch = lambda terms, **k: captured.update(exact=terms)
        agent.semantic_store.add_batch = lambda terms, **k: captured.update(semantic=terms)

        result = make_result(new_terms=[
            {"term_cn": "鬼节", "term_en": "Ghost Festival", "category": "culture"},
        ])
        agent._post_process(result, "en-US")

        # No character/location term ⇒ the exact store is never written.
        assert "exact" not in captured
        assert [t["term_cn"] for t in captured["semantic"]] == ["鬼节"]

    def test_no_valid_terms_means_no_store_writes(self, agent):
        captured = {}
        agent.exact_store.add_batch = lambda terms, **k: captured.update(exact=terms)
        agent.semantic_store.add_batch = lambda terms, **k: captured.update(semantic=terms)

        result = make_result(new_terms=[
            {"term_cn": "聋婆婆", "term_en": "Lóng Pópo", "category": "character"},
        ])
        agent._post_process(result, "en-US")

        assert "exact" not in captured
        assert "semantic" not in captured


# ────────────────────────── snapshot & context ──────────────────────────


class TestSnapshotAndContext:
    def test_glossary_snapshot_is_always_written(self, agent):
        agent.exact_store.snapshot = lambda: '{"王三": "Wang San"}'
        result = make_result()
        out = agent._post_process(result, "en-US")
        assert out["glossary_snapshot_json"] == '{"王三": "Wang San"}'

    def test_snapshot_recorded_even_with_no_terms(self, agent):
        agent.exact_store.snapshot = lambda: "{}"
        result = make_result(new_terms=[])
        agent._post_process(result, "en-US")
        assert result["glossary_snapshot_json"] == "{}"

    def test_summary_feeds_cold_reader_context(self, agent):
        result = make_result()
        result["chapter_number"] = 7
        result["chapter_summary"] = (
            "Deaf Granny waits under the locust tree for her son to come home."
        )
        agent._post_process(result, "en-US")
        assert any("[Ch7]" in entry for entry in agent._chapter_context)

    def test_short_summary_is_not_buffered(self, agent):
        result = make_result()
        result["chapter_summary"] = "Too short."
        agent._post_process(result, "en-US")
        assert agent._chapter_context == []

    def test_returns_the_same_result_object(self, agent):
        result = make_result()
        assert agent._post_process(result, "en-US") is result

    def test_context_buffer_is_capped_at_five(self, agent):
        for n in range(1, 8):
            result = make_result()
            result["chapter_number"] = n
            result["chapter_summary"] = f"Chapter {n} summary " + "x" * 40
            agent._post_process(result, "en-US")
        assert len(agent._chapter_context) == 5
        assert "[Ch3]" in agent._chapter_context[0]

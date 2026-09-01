"""Tests for the rule-based cultural-fidelity check."""

from src.agent.fidelity import check_cultural_fidelity


def _analysis(*decisions):
    return {"terminology_decisions": list(decisions)}


def _decision(term_cn, proposed_en, **extra):
    d = {"term_cn": term_cn, "proposed_en": proposed_en}
    d.update(extra)
    return d


class TestCheckCulturalFidelity:
    def test_empty_input_returns_no_failures(self):
        assert check_cultural_fidelity({}, "") == []
        assert check_cultural_fidelity({}, "some text") == []
        assert check_cultural_fidelity({"terminology_decisions": []}, "text") == []

    def test_honored_decision_passes(self):
        analysis = _analysis(_decision("聋婆婆", "Deaf Granny"))
        text = "Deaf Granny lived in the village."
        assert check_cultural_fidelity(analysis, text) == []

    def test_dropped_decision_fails(self):
        analysis = _analysis(_decision("聋婆婆", "Deaf Granny"))
        text = "Lóng Pópo lived in the village."  # pinyin drift
        failures = check_cultural_fidelity(analysis, text)
        assert len(failures) == 1
        assert "聋婆婆" in failures[0]
        assert "Deaf Granny" in failures[0]

    def test_alternative_candidate_passes(self):
        analysis = _analysis(_decision("霸总", "Alpha CEO / domineering CEO"))
        text = "The domineering CEO walked in."
        assert check_cultural_fidelity(analysis, text) == []

    def test_long_descriptive_rendering_skipped(self):
        analysis = _analysis(
            _decision(
                "出马弟子",
                "a spirit medium who serves a court of fox, weasel, and snake spirits",
            )
        )
        text = "The medium served the fox spirit."
        # v1 only checks short term-level renderings, not long descriptions
        assert check_cultural_fidelity(analysis, text) == []

    def test_multiple_decisions_reports_each_failure(self):
        analysis = _analysis(
            _decision("聋婆婆", "Deaf Granny"),
            _decision("李大爷", "Uncle Li"),
        )
        text = "Lóng Pópo and Lǐ Dàye lived in the village."
        failures = check_cultural_fidelity(analysis, text)
        assert len(failures) == 2


class TestPostProcessIntegration:
    def test_post_process_records_fidelity_failure(self):
        from src.agent.graph import TranslationAgent

        agent = TranslationAgent(book_id="test_fidelity_post_process")
        # Neutralise the file-backed memo store so the test stays hermetic.
        agent.style_memo.update_from_read_analysis = lambda *a, **k: None
        agent.style_memo.update_from_feedback = lambda *a, **k: None

        result = {
            "chapter_number": 1,
            "translated_text": "Lóng Pópo lived in the village.",
            "read_analysis": {
                "terminology_decisions": [
                    {"term_cn": "聋婆婆", "proposed_en": "Deaf Granny"},
                ],
            },
            "new_terms_found": [],
            "readback_feedback": {},
            "quality_issues": [],
            "adaptation_notes": [],
            "chapter_summary": "",
        }

        agent._post_process(result, "en-US")

        assert any("聋婆婆" in q for q in result["quality_issues"]), (
            "fidelity failure should be recorded in quality_issues"
        )

"""Tests for the fidelity gate — drifted terms are corrected before caching."""

from src.agent.graph import TranslationAgent


def _agent():
    agent = TranslationAgent(book_id="test_fidelity_gate")
    agent.style_memo.update_from_read_analysis = lambda *a, **k: None
    agent.style_memo.update_from_feedback = lambda *a, **k: None
    agent.exact_store.add_batch = lambda *a, **k: None
    agent.semantic_store.add_batch = lambda *a, **k: None
    return agent


def _result(translated_text, read_decisions, new_terms):
    return {
        "chapter_number": 1,
        "translated_text": translated_text,
        "read_analysis": {"terminology_decisions": read_decisions},
        "new_terms_found": new_terms,
        "readback_feedback": {},
        "quality_issues": [],
        "adaptation_notes": [],
        "chapter_summary": "",
    }


class TestFidelityGate:
    def test_drifted_term_corrected_to_read_decision(self):
        agent = _agent()
        result = _result(
            translated_text="Lóng Pópo lived in the village.",
            read_decisions=[{"term_cn": "聋婆婆", "proposed_en": "Deaf Granny"}],
            new_terms=[{"term_cn": "聋婆婆", "term_en": "Lóng Pópo", "category": "character"}],
        )

        agent._post_process(result, "en-US")

        assert result["new_terms_found"][0]["term_en"] == "Deaf Granny"

    def test_matching_term_untouched(self):
        agent = _agent()
        result = _result(
            translated_text="Deaf Granny lived in the village.",
            read_decisions=[{"term_cn": "聋婆婆", "proposed_en": "Deaf Granny"}],
            new_terms=[{"term_cn": "聋婆婆", "term_en": "Deaf Granny", "category": "character"}],
        )

        agent._post_process(result, "en-US")

        assert result["new_terms_found"][0]["term_en"] == "Deaf Granny"

    def test_term_without_read_decision_untouched(self):
        agent = _agent()
        result = _result(
            translated_text="Wang San lived in the village.",
            read_decisions=[],
            new_terms=[{"term_cn": "王三", "term_en": "Wang San", "category": "character"}],
        )

        agent._post_process(result, "en-US")

        assert result["new_terms_found"][0]["term_en"] == "Wang San"

    def test_long_descriptive_read_rendering_not_forced(self):
        # A long descriptive rendering is an explanation, not a cached token —
        # the gate must not overwrite WRITE's term_en with it.
        agent = _agent()
        result = _result(
            translated_text="the medium served the fox spirit.",
            read_decisions=[{
                "term_cn": "出马弟子",
                "proposed_en": "a spirit medium who serves a court of fox, weasel, and snake spirits",
            }],
            new_terms=[{"term_cn": "出马弟子", "term_en": "spirit medium", "category": "culture"}],
        )

        agent._post_process(result, "en-US")

        assert result["new_terms_found"][0]["term_en"] == "spirit medium"

    def test_gate_skips_slash_rendering(self):
        # READ's "Chuma Shaman / spirit medium" is multi-candidate — do NOT
        # overwrite WRITE's clean "Chuma shaman" with it.
        agent = _agent()
        result = _result(
            translated_text="the Chuma shaman served the spirits.",
            read_decisions=[{
                "term_cn": "出马弟子",
                "proposed_en": "Chuma Shaman / spirit medium",
            }],
            new_terms=[{"term_cn": "出马弟子", "term_en": "Chuma shaman", "category": "culture"}],
        )
        agent._post_process(result, "en-US")
        assert result["new_terms_found"][0]["term_en"] == "Chuma shaman"

    def test_gate_skips_explanatory_rendering(self):
        # READ's "Qingfeng — the spirits of..." is explanatory — do NOT
        # overwrite WRITE's clean "Qingfeng" with it.
        agent = _agent()
        result = _result(
            translated_text="Qingfeng wandered the hall.",
            read_decisions=[{
                "term_cn": "清风",
                "proposed_en": "Qingfeng — the spirits of the violently dead",
            }],
            new_terms=[{"term_cn": "清风", "term_en": "Qingfeng", "category": "character"}],
        )
        agent._post_process(result, "en-US")
        assert result["new_terms_found"][0]["term_en"] == "Qingfeng"


class TestIsValidRendering:
    def test_valid_renderings_pass(self):
        from src.agent.fidelity import is_valid_rendering
        assert is_valid_rendering("聋婆婆", "Deaf Granny") == (True, "")
        assert is_valid_rendering("王三", "Wang San") == (True, "")
        assert is_valid_rendering("996", "996 grind") == (True, "")

    def test_empty_fails(self):
        from src.agent.fidelity import is_valid_rendering
        ok, _ = is_valid_rendering("聋婆婆", "")
        assert ok is False

    def test_chinese_fails(self):
        from src.agent.fidelity import is_valid_rendering
        ok, reason = is_valid_rendering("聋婆婆", "Deaf 婆婆")
        assert ok is False
        assert "Chinese" in reason

    def test_tone_marked_pinyin_fails(self):
        from src.agent.fidelity import is_valid_rendering
        ok, reason = is_valid_rendering("聋婆婆", "Lóng Pópo")
        assert ok is False
        assert "pinyin" in reason

    def test_stray_digit_fails(self):
        from src.agent.fidelity import is_valid_rendering
        ok, reason = is_valid_rendering("王三", "M3")
        assert ok is False
        assert "digit" in reason


class TestCurationGate:
    def test_invalid_terms_dropped_before_caching(self):
        agent = _agent()
        captured = {}
        agent.exact_store.add_batch = lambda terms, **k: captured.update(exact=terms)
        agent.semantic_store.add_batch = lambda terms, **k: captured.update(semantic=terms)

        result = _result(
            translated_text="Wang San lived in the village.",
            read_decisions=[],
            new_terms=[
                {"term_cn": "聋婆婆", "term_en": "Lóng Pópo", "category": "character"},
                {"term_cn": "王三", "term_en": "Wang San", "category": "character"},
            ],
        )
        agent._post_process(result, "en-US")

        # The tone-marked pinyin rendering must be dropped, not cached.
        semantic_terms = captured.get("semantic", [])
        assert [t["term_en"] for t in semantic_terms] == ["Wang San"]

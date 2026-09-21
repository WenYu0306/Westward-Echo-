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

    def test_gate_skips_parenthetical_rendering(self):
        # Regression from the 2026-09-21 E2E run (book e2e_verify): READ proposed
        #   鬼节 → "Ghost Festival (chapter title: 'Ghost Festival')"
        # and the gate overwrote WRITE's clean "Ghost Festival", caching the
        # gloss as must-use for every later chapter.
        agent = _agent()
        result = _result(
            translated_text="It was Ghost Festival night.",
            read_decisions=[{
                "term_cn": "鬼节",
                "proposed_en": "Ghost Festival (chapter title: 'Ghost Festival')",
            }],
            new_terms=[{"term_cn": "鬼节", "term_en": "Ghost Festival", "category": "culture"}],
        )
        agent._post_process(result, "en-US")
        assert result["new_terms_found"][0]["term_en"] == "Ghost Festival"


class TestIsSingleRendering:
    """The gate's guard: only a clean token may overwrite WRITE's rendering."""

    def _is_single(self, s):
        from src.agent.fidelity import is_single_rendering
        return is_single_rendering(s)

    def test_clean_token_accepted(self):
        assert self._is_single("Deaf Granny") is True

    def test_empty_rejected(self):
        assert self._is_single("") is False
        assert self._is_single("   ") is False

    def test_alternatives_rejected(self):
        assert self._is_single("Chuma Shaman / spirit medium") is False

    def test_em_dash_gloss_rejected(self):
        assert self._is_single("Qingfeng — the spirits of the violently dead") is False

    def test_en_dash_gloss_rejected(self):
        assert self._is_single("Qingfeng – the violent dead") is False

    def test_parenthetical_gloss_rejected(self):
        # Regression: the 2026-09-21 E2E run cached
        # "Ghost Festival (chapter title: 'Ghost Festival')" over WRITE's clean
        # "Ghost Festival" because this form slipped past the guard.
        assert self._is_single("Ghost Festival (chapter title: 'Ghost Festival')") is False
        assert self._is_single("Alpha CEO (domineering president)") is False

    def test_colon_gloss_rejected(self):
        assert self._is_single("Ghost Festival: the night the dead walk") is False

    def test_hyphenated_gloss_rejected(self):
        assert self._is_single("Ghost Festival - the dead walk") is False


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

    def test_parenthetical_gloss_fails(self):
        from src.agent.fidelity import is_valid_rendering
        ok, reason = is_valid_rendering("鬼节", "Ghost Festival (chapter title: 'Ghost Festival')")
        assert ok is False
        assert "explanatory" in reason

    def test_colon_gloss_fails(self):
        from src.agent.fidelity import is_valid_rendering
        ok, _ = is_valid_rendering("鬼节", "Ghost Festival: the night the dead walk")
        assert ok is False

    def test_alternatives_fail(self):
        from src.agent.fidelity import is_valid_rendering
        ok, _ = is_valid_rendering("出马弟子", "Chuma Shaman / spirit medium")
        assert ok is False

    def test_clean_token_with_dash_inside_word_passes(self):
        # A hyphen *inside* a token is not a gloss — only " - " separates one.
        from src.agent.fidelity import is_valid_rendering
        assert is_valid_rendering("鬼节", "Ghost-Festival lantern") == (True, "")


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

    def test_dropped_terms_removed_from_result_too(self):
        # A dropped term must not leave the pipeline as a "discovered term"
        # either — callers (API response, split merge, artifacts) would
        # otherwise re-report exactly what the gate rejected.
        agent = _agent()
        result = _result(
            translated_text="Wang San lived in the village.",
            read_decisions=[],
            new_terms=[
                {"term_cn": "聋婆婆", "term_en": "Lóng Pópo", "category": "character"},
                {"term_cn": "王三", "term_en": "Wang San", "category": "character"},
            ],
        )
        agent._post_process(result, "en-US")

        assert [t["term_en"] for t in result["new_terms_found"]] == ["Wang San"]

    def test_curated_result_keeps_valid_terms_intact(self):
        agent = _agent()
        result = _result(
            translated_text="Deaf Granny and Wang San lived in the village.",
            read_decisions=[],
            new_terms=[
                {"term_cn": "聋婆婆", "term_en": "Deaf Granny", "category": "character"},
                {"term_cn": "王三", "term_en": "Wang San", "category": "character"},
            ],
        )
        agent._post_process(result, "en-US")

        assert [t["term_cn"] for t in result["new_terms_found"]] == ["聋婆婆", "王三"]

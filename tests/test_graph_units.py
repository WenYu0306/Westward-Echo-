"""Unit tests for the graph's non-LLM machinery.

Covers the routing predicates, state assembly, cold-reader briefing, and the
auto-split merge — everything in ``src/agent/graph.py`` that can be tested
without a network call. These exist to give ``scripts/mutation_test.py
graph_post_process`` something fast and boundary-sensitive to run: several
mutants (numeric constants like ``[:300]``, ``top_k=15``, ``> 5``) are only
killed by a test that sits exactly on the boundary.
"""

from langgraph.graph import END

from src.agent.graph import TranslationAgent, _needs_fix, _should_readback, build_graph
from src.config import MAX_RETRANSLATION_ATTEMPTS

# ────────────────────────────── fakes ──────────────────────────────


class FakeExact:
    def __init__(self, terms=None, matches=None, formatted="EXACT TEXTS"):
        self._dict = dict(terms or {})
        self._matches = dict(matches or {})
        self.formatted = formatted
        self.match_calls = []
        self.added = []
        self.restored = None
        self.snapshot_value = "{}"

    def match_in_text(self, text, target_lang=None):
        self.match_calls.append(text)
        self.match_langs = getattr(self, "match_langs", [])
        self.match_langs.append(target_lang)
        return dict(self._matches)

    def to_dict(self, target_lang=None):
        self.dict_langs = getattr(self, "dict_langs", [])
        self.dict_langs.append(target_lang)
        return dict(self._dict)

    def to_formatted_text(self, matched_terms=None, target_lang="en-US"):
        self.plain_calls = getattr(self, "plain_calls", [])
        self.plain_calls.append({"matched": matched_terms, "lang": target_lang})
        return f"PLAIN::{self.formatted}"

    def to_formatted_text_with_notes(self, matched_terms=None, target_lang="en-US"):
        self.notes_calls = getattr(self, "notes_calls", [])
        self.notes_calls.append({"matched": matched_terms, "lang": target_lang})
        return f"NOTES::{self.formatted}"

    def add_batch(self, terms, **kwargs):
        self.added.append(list(terms))

    def snapshot(self, target_lang=None):
        self.snapshot_langs = getattr(self, "snapshot_langs", [])
        self.snapshot_langs.append(target_lang)
        return self.snapshot_value

    def restore_snapshot(self, snapshot_json, target_lang=None):
        self.restored = snapshot_json
        self.restored_lang = target_lang


class FakeSemantic:
    def __init__(self, hits=None):
        self.hits = list(hits or [])
        self.search_calls = []
        self.added = []

    def search(self, query_text, top_k=15, target_lang="en-US"):
        self.search_calls.append({"query": query_text, "top_k": top_k, "lang": target_lang})
        return [dict(h) for h in self.hits]

    def add_batch(self, terms, **kwargs):
        self.added.append(list(terms))


class FakeMemo:
    def __init__(self, all_text="", relevant=""):
        self.all_text = all_text
        self.relevant = relevant
        self.read_relevant_calls = []

    def read_all(self):
        return self.all_text

    def read_relevant(self, *, max_chars=5000, per_drawer_recent=5):
        # Keyword-only, mirroring the real signature: a caller passing chapter
        # context positionally would raise TypeError here instead of being
        # silently ignored (the failure mode the real one had for 7 weeks).
        self.read_relevant_calls.append(
            {"max_chars": max_chars, "per_drawer_recent": per_drawer_recent})
        return self.relevant

    def update_from_read_analysis(self, *a, **k):
        pass

    def update_from_feedback(self, *a, **k):
        pass


class FakeGraph:
    def __init__(self, result=None):
        self.result = result or {}
        self.invocations = []

    def invoke(self, state, config=None):
        self.invocations.append({"state": state, "config": config})
        merged = dict(state)
        merged.update(self.result)
        return merged


def bare_agent(exact=None, semantic=None, memo=None, context=None,
               prefetched_exact=None, prefetched_semantic=None, api_key=""):
    """A TranslationAgent without chroma/sqlite side effects."""
    a = object.__new__(TranslationAgent)
    a.exact_store = exact if exact is not None else FakeExact()
    a.semantic_store = semantic if semantic is not None else FakeSemantic()
    a.style_memo = memo if memo is not None else FakeMemo()
    a._chapter_context = list(context or [])
    a._prefetched_exact = prefetched_exact
    a._prefetched_semantic = prefetched_semantic
    a.api_key = api_key
    a.graph = FakeGraph()
    return a


def make_state_kwargs(**over):
    kwargs = dict(
        title="第一章", content="苏念穿越了。", number=1,
        prev_summary="", lang="en-US", genre="romance_ceo",
    )
    kwargs.update(over)
    return kwargs


# ────────────────────────────── routing ──────────────────────────────


class TestShouldReadback:
    def test_default_routes_to_cold_reader(self):
        # Fast mode must be opt-in: the default state has no skip_readback key.
        assert _should_readback({}) == "readback_node"

    def test_explicit_false_routes_to_cold_reader(self):
        assert _should_readback({"skip_readback": False}) == "readback_node"

    def test_true_skips_to_end(self):
        assert _should_readback({"skip_readback": True}) == END


class TestNeedsFix:
    def test_needs_fix_with_budget_left_routes_to_fix(self):
        state = {"readback_feedback": {"verdict": "NEEDS_FIX"}, "retranslation_count": 0}
        assert _needs_fix(state) == "fix_node"

    def test_pass_routes_to_end(self):
        state = {"readback_feedback": {"verdict": "PASS"}, "retranslation_count": 0}
        assert _needs_fix(state) == END

    def test_needs_fix_at_attempt_limit_routes_to_end(self):
        state = {
            "readback_feedback": {"verdict": "NEEDS_FIX"},
            "retranslation_count": MAX_RETRANSLATION_ATTEMPTS,
        }
        assert _needs_fix(state) == END

    def test_needs_fix_over_attempt_limit_routes_to_end(self):
        state = {
            "readback_feedback": {"verdict": "NEEDS_FIX"},
            "retranslation_count": MAX_RETRANSLATION_ATTEMPTS + 3,
        }
        assert _needs_fix(state) == END

    def test_pass_with_budget_left_still_ends(self):
        # Guard against the verdict check being weakened to `or`.
        state = {"readback_feedback": {"verdict": "PASS"}, "retranslation_count": 0}
        assert _needs_fix(state) == END

    def test_missing_feedback_defaults_to_end(self):
        assert _needs_fix({}) == END

    def test_empty_feedback_defaults_to_end(self):
        assert _needs_fix({"readback_feedback": {}}) == END


class TestBuildGraph:
    def test_build_graph_returns_a_compiled_graph(self):
        graph = build_graph(FakeExact(), FakeSemantic())
        assert graph is not None
        assert hasattr(graph, "invoke")


# ────────────────────── cold-reader context accumulation ──────────────────────


class TestUpdateContext:
    def test_summary_longer_than_20_chars_is_buffered(self, agent=None):
        a = bare_agent()
        a._update_context(1, "A" * 21)
        assert len(a._chapter_context) == 1

    def test_summary_of_exactly_20_chars_is_dropped(self):
        a = bare_agent()
        a._update_context(1, "A" * 20)
        assert a._chapter_context == []

    def test_empty_summary_is_dropped(self):
        a = bare_agent()
        a._update_context(1, "")
        assert a._chapter_context == []

    def test_entry_is_prefixed_with_chapter_number(self):
        a = bare_agent()
        a._update_context(7, "D" * 30)
        assert a._chapter_context[0].startswith("[Ch7] ")

    def test_summary_is_truncated_to_300_chars(self):
        a = bare_agent()
        a._update_context(1, "D" * 500)
        assert len(a._chapter_context[0]) == len("[Ch1] ") + 300

    def test_six_chapters_keep_only_five(self):
        a = bare_agent()
        for n in range(1, 7):
            a._update_context(n, f"Chapter {n} " + "x" * 40)
        assert len(a._chapter_context) == 5

    def test_oldest_summary_is_evicted(self):
        a = bare_agent()
        for n in range(1, 7):
            a._update_context(n, f"Chapter {n} " + "x" * 40)
        assert a._chapter_context[0].startswith("[Ch2] ")


# ────────────────────── cold-reader briefing ──────────────────────


class TestBuildColdReadContext:
    def test_empty_agent_yields_empty_briefing(self):
        a = bare_agent()
        assert a._build_cold_read_context(chapter_number=1) == ""

    def test_recent_summaries_are_included(self):
        a = bare_agent(context=["[Ch1] " + "x" * 40])
        ctx = a._build_cold_read_context(chapter_number=2)
        assert "## PREVIOUSLY (recent chapter summaries)" in ctx
        assert "[Ch1]" in ctx

    def test_character_roster_is_included(self):
        a = bare_agent(exact=FakeExact(terms={"聋婆婆": "Deaf Granny"}))
        ctx = a._build_cold_read_context(chapter_number=1)
        assert "## CHARACTERS" in ctx
        assert "**Deaf Granny**" in ctx

    def test_character_roster_omits_the_chinese_source_name(self):
        """Clause 10: the cold reader is blind, so the roster must not leak the
        Chinese source name. It used to render `- **Deaf Granny** (聋婆婆)`.
        """
        a = bare_agent(exact=FakeExact(terms={"聋婆婆": "Deaf Granny"}))
        ctx = a._build_cold_read_context(chapter_number=1)
        assert "聋婆婆" not in ctx
        assert "**Deaf Granny**" in ctx

    def test_single_char_terms_are_not_a_roster(self):
        a = bare_agent(exact=FakeExact(terms={"鬼": "Ghost"}))
        assert "## CHARACTERS" not in a._build_cold_read_context(chapter_number=1)

    def test_short_english_rendering_is_not_a_roster(self):
        a = bare_agent(exact=FakeExact(terms={"鬼节": "X"}))
        assert "## CHARACTERS" not in a._build_cold_read_context(chapter_number=1)

    def test_two_char_term_is_a_roster(self):
        a = bare_agent(exact=FakeExact(terms={"鬼节": "GF"}))
        assert "## CHARACTERS" in a._build_cold_read_context(chapter_number=1)

    def test_roster_is_capped_at_fifteen(self):
        terms = {f"词{i:02d}": f"Term{i}" for i in range(20)}
        a = bare_agent(exact=FakeExact(terms=terms))
        ctx = a._build_cold_read_context(chapter_number=1)
        assert ctx.count("  - **") == 15

    def test_early_chapter_note_joins_a_nonempty_briefing(self):
        # With no summaries but a roster, chapter > 1 gets the "early chapter" line.
        a = bare_agent(exact=FakeExact(terms={"聋婆婆": "Deaf Granny"}))
        ctx = a._build_cold_read_context(chapter_number=2)
        assert "You are reading Chapter 2" in ctx

    def test_early_chapter_note_stands_alone(self):
        # Regression: the `<= 1` guard used to discard this one-section
        # briefing entirely, so a chapter-2 cold reader with an empty glossary
        # was told nothing at all.
        a = bare_agent()
        ctx = a._build_cold_read_context(chapter_number=2)
        assert "You are reading Chapter 2" in ctx

    def test_roster_alone_is_a_valid_briefing(self):
        a = bare_agent(exact=FakeExact(terms={"聋婆婆": "Deaf Granny"}))
        ctx = a._build_cold_read_context(chapter_number=1)
        assert "## CHARACTERS" in ctx

    def test_early_chapter_note_absent_for_chapter_one(self):
        a = bare_agent(exact=FakeExact(terms={"聋婆婆": "Deaf Granny"}))
        ctx = a._build_cold_read_context(chapter_number=1)
        assert "You are reading Chapter" not in ctx

    def test_early_chapter_note_absent_when_summaries_exist(self):
        a = bare_agent(exact=FakeExact(terms={"聋婆婆": "Deaf Granny"}),
                       context=["[Ch1] " + "x" * 40])
        ctx = a._build_cold_read_context(chapter_number=2)
        assert "You are reading Chapter" not in ctx

    def test_terminology_section_from_style_memo(self):
        memo = FakeMemo(all_text="[聋婆婆] → Deaf Granny\nnot a term line\n")
        a = bare_agent(exact=FakeExact(terms={"聋婆婆": "Deaf Granny"}), memo=memo)
        ctx = a._build_cold_read_context(chapter_number=1)
        assert "## ESTABLISHED TERMINOLOGY" in ctx
        assert "- [聋婆婆] → Deaf Granny" in ctx

    def test_non_term_memo_lines_are_skipped(self):
        memo = FakeMemo(all_text="just prose\n[聋婆婆] → Deaf Granny\n")
        a = bare_agent(memo=memo)
        ctx = a._build_cold_read_context(chapter_number=1)
        assert "just prose" not in ctx

    def test_memo_line_requiring_arrow_is_skipped(self):
        memo = FakeMemo(all_text="[聋婆婆] Deaf Granny\n")
        a = bare_agent(memo=memo)
        assert "聋婆婆" not in a._build_cold_read_context(chapter_number=1)

    def test_memo_lines_capped_at_eight(self):
        memo = FakeMemo(all_text="\n".join(f"[词{i}] → Term{i}" for i in range(12)))
        a = bare_agent(memo=memo)
        ctx = a._build_cold_read_context(chapter_number=1)
        assert ctx.count("- [词") == 8

    def test_memo_line_truncated_to_120_chars(self):
        memo = FakeMemo(all_text="[" + "长" * 200 + "] → " + "x" * 50 + "\n")
        a = bare_agent(memo=memo)
        ctx = a._build_cold_read_context(chapter_number=1)
        line = [ln for ln in ctx.split("\n") if ln.startswith("- [")][0]
        assert len(line) == len("- ") + 120

    def test_briefing_is_a_string(self):
        a = bare_agent(exact=FakeExact(terms={"聋婆婆": "Deaf Granny"}))
        assert isinstance(a._build_cold_read_context(chapter_number=1), str)


# ────────────────────── glossary snapshot loading ──────────────────────


class TestLoadGlossarySnapshot:
    def test_empty_string_is_not_restored(self):
        a = bare_agent()
        a.load_glossary_snapshot("")
        assert a.exact_store.restored is None

    def test_brace_pair_is_not_restored(self):
        a = bare_agent()
        a.load_glossary_snapshot("{}")
        assert a.exact_store.restored is None

    def test_real_snapshot_is_restored(self):
        a = bare_agent()
        a.load_glossary_snapshot('{"王三": "Wang San"}')
        assert a.exact_store.restored == '{"王三": "Wang San"}'


# ────────────────────────────── state assembly ──────────────────────────────


class TestMakeState:
    def test_returns_a_dict_with_chapter_fields(self):
        a = bare_agent()
        state = a._make_state(**make_state_kwargs())
        assert state["chapter_title"] == "第一章"
        assert state["chapter_number"] == 1
        assert state["target_lang"] == "en-US"
        assert state["genre"] == "romance_ceo"
        assert state["content_type"] == "novel"

    def test_quality_score_starts_unmeasured(self):
        """0.0 means "nobody has read this chapter yet".

        This used to assert 5.0 — i.e. the fake-green default was locked in as
        expected behaviour. A fast-mode chapter skips READBACK, so it kept the
        5.0 initial value and reported a perfect score no cold reader ever gave
        it; downstream averaging then measured nothing at all.
        """
        a = bare_agent()
        assert a._make_state(**make_state_kwargs())["quality_score"] == 0.0

    def test_readback_skipped_flag_tracks_skip_readback(self):
        """The flag is how downstream tells "cold read passed" from "never read"."""
        a = bare_agent()
        assert a._make_state(**make_state_kwargs())["readback_skipped"] is False
        kwargs = {**make_state_kwargs(), "skip_readback": True}
        assert a._make_state(**kwargs)["readback_skipped"] is True

    def test_quality_issues_start_empty(self):
        a = bare_agent()
        state = a._make_state(**make_state_kwargs())
        assert state["quality_issues"] == []
        assert state["retranslation_count"] == 0

    def test_skip_readback_defaults_to_false(self):
        a = bare_agent()
        assert a._make_state(**make_state_kwargs())["skip_readback"] is False

    def test_skip_readback_can_be_requested(self):
        a = bare_agent()
        state = a._make_state(**make_state_kwargs(skip_readback=True))
        assert state["skip_readback"] is True

    def test_api_key_is_passed_through(self):
        a = bare_agent(api_key="sk-user")
        assert a._make_state(**make_state_kwargs())["api_key"] == "sk-user"

    def test_empty_api_key_stays_empty(self):
        a = bare_agent(api_key="")
        assert a._make_state(**make_state_kwargs())["api_key"] == ""

    def test_store_lookup_happens_without_prefetch(self):
        exact = FakeExact(matches={"王三": "Wang San"})
        a = bare_agent(exact=exact)
        a._make_state(**make_state_kwargs())
        assert len(exact.match_calls) == 1

    def test_prefetched_exact_matches_skip_the_lookup(self):
        exact = FakeExact(matches={"王三": "Wang San"})
        a = bare_agent(exact=exact, prefetched_exact={"聋婆婆": "Deaf Granny"})
        a._make_state(**make_state_kwargs())
        assert exact.match_calls == []

    def test_prefetched_exact_matches_are_consumed_once(self):
        a = bare_agent(prefetched_exact={"聋婆婆": "Deaf Granny"})
        a._make_state(**make_state_kwargs())
        assert a._prefetched_exact is None

    def test_prefetched_semantic_hits_skip_the_search(self):
        semantic = FakeSemantic(hits=[{"term_cn": "鬼节", "term_en": "Ghost Festival"}])
        a = bare_agent(semantic=semantic, prefetched_semantic=[])
        a._make_state(**make_state_kwargs())
        assert semantic.search_calls == []

    def test_semantic_search_uses_top_k_15(self):
        semantic = FakeSemantic()
        a = bare_agent(semantic=semantic)
        a._make_state(**make_state_kwargs())
        assert semantic.search_calls[0]["top_k"] == 15

    def test_semantic_search_receives_the_target_lang(self):
        semantic = FakeSemantic()
        a = bare_agent(semantic=semantic)
        a._make_state(**make_state_kwargs(lang="es-ES"))
        assert semantic.search_calls[0]["lang"] == "es-ES"

    def test_semantic_hits_already_in_exact_matches_are_removed(self):
        exact = FakeExact(matches={"王三": "Wang San"})
        semantic = FakeSemantic(hits=[
            {"term_cn": "王三", "term_en": "Wang San", "category": "character"},
            {"term_cn": "鬼节", "term_en": "Ghost Festival", "category": "culture"},
        ])
        a = bare_agent(exact=exact, semantic=semantic)
        state = a._make_state(**make_state_kwargs())
        assert [t["term_cn"] for t in state["semantic_terms"]] == ["鬼节"]

    def test_exact_matches_use_the_notes_format(self):
        a = bare_agent(exact=FakeExact(matches={"王三": "Wang San"}))
        state = a._make_state(**make_state_kwargs())
        assert state["exact_matches_text"].startswith("NOTES::")

    def test_no_exact_matches_use_the_plain_format(self):
        a = bare_agent(exact=FakeExact(matches={}))
        state = a._make_state(**make_state_kwargs())
        assert state["exact_matches_text"].startswith("PLAIN::")

    def test_style_memo_content_is_used_when_available(self):
        memo = FakeMemo(relevant="MEMO CONTENT")
        a = bare_agent(memo=memo)
        assert a._make_state(**make_state_kwargs())["style_memo"] == "MEMO CONTENT"

    def test_fallback_memo_is_used_when_memo_is_empty(self):
        a = bare_agent(memo=FakeMemo(relevant=""))
        state = a._make_state(**make_state_kwargs())
        assert "No translation memory yet" in state["style_memo"]

    def test_memo_receives_no_chapter_context(self):
        """`read_relevant()` is recency-based and must NOT be handed the chapter.

        This test used to assert the opposite — that _make_state passed
        (content, exact_matches) through. That assertion pinned the *call site*
        of two parameters the body never read (dead since bdccc85, 2026-08-04),
        which is the exact fake-green the project keeps hunting: a test that
        passes while proving nothing about behaviour. The chapter's terms still
        reach the prompt, but through their own channels — asserted separately
        below so the guarantee is real rather than implied.
        """
        memo = FakeMemo(relevant="M")
        exact = FakeExact(matches={"王三": "Wang San"})
        a = bare_agent(exact=exact, memo=memo)
        state = a._make_state(**make_state_kwargs(content="王三在这里。"))

        # Memo retrieval got no chapter context — defaults only.
        assert memo.read_relevant_calls == [
            {"max_chars": 5000, "per_drawer_recent": 5}]
        # The terms the memo no longer sees are still delivered through their own
        # channels. FakeExact returns a fixed stub string, so assert on what was
        # *fed into* the glossary channel (it records its inputs) rather than on
        # the stub's text.
        assert exact.notes_calls == [{"matched": {"王三": "Wang San"}, "lang": "en-US"}]
        assert state["exact_matches_text"] == "NOTES::EXACT TEXTS"
        assert state["chapter_content"] == "王三在这里。"
        assert state["style_memo"] == "M"


# ────────────────────────────── chapter entry points ──────────────────────────────


class TestTranslateChapter:
    def test_returns_the_pipeline_result(self):
        a = bare_agent()
        a.graph = FakeGraph({"translated_text": "Deaf Granny waited."})
        out = a.translate_chapter(chapter_title="第一章", chapter_content="聋婆婆等着。",
                                 chapter_number=1)
        assert out["translated_text"] == "Deaf Granny waited."

    def test_graph_is_invoked_with_the_recursion_limit(self):
        a = bare_agent()
        a.graph = FakeGraph()
        a.translate_chapter(chapter_title="第一章", chapter_content="聋婆婆等着。",
                            chapter_number=1)
        assert a.graph.invocations[0]["config"] == {"recursion_limit": 100}

    def test_graph_receives_the_chapter_state(self):
        a = bare_agent()
        a.graph = FakeGraph()
        a.translate_chapter(chapter_title="第一章", chapter_content="聋婆婆等着。",
                            chapter_number=1, genre="folk_religion")
        state = a.graph.invocations[0]["state"]
        assert state["chapter_title"] == "第一章"
        assert state["genre"] == "folk_religion"

    def test_fast_mode_defaults_to_off(self):
        # skip_readback gates the cold reader: a default of True would silently
        # skip READBACK for every chapter.
        a = bare_agent()
        a.graph = FakeGraph()
        a.translate_chapter(chapter_title="第一章", chapter_content="聋婆婆等着。",
                            chapter_number=1)
        state = a.graph.invocations[0]["state"]
        assert state["skip_readback"] is False

    def test_fast_mode_reaches_the_state(self):
        a = bare_agent()
        a.graph = FakeGraph()
        a.translate_chapter(chapter_title="第一章", chapter_content="聋婆婆等着。",
                            chapter_number=1, skip_readback=True)
        state = a.graph.invocations[0]["state"]
        assert state["skip_readback"] is True

    def test_result_carries_a_glossary_snapshot(self):
        a = bare_agent()
        a.exact_store.snapshot_value = '{"王三": "Wang San"}'
        a.graph = FakeGraph()
        out = a.translate_chapter(chapter_title="第一章", chapter_content="王三。",
                                 chapter_number=1)
        assert out["glossary_snapshot_json"] == '{"王三": "Wang San"}'

    def test_graph_is_asked_for_the_result(self):
        # _translate_once must return _post_process's output, not None.
        a = bare_agent()
        a.graph = FakeGraph({"translated_text": "Out."})
        out = a._translate_once("第一章", "聋婆婆等着。", 1, "", "en-US", "folk_religion")
        assert out["translated_text"] == "Out."

    def test_translate_once_defaults_fast_mode_off(self):
        a = bare_agent()
        a.graph = FakeGraph()
        a._translate_once("第一章", "聋婆婆等着。", 1, "", "en-US", "folk_religion")
        state = a.graph.invocations[0]["state"]
        assert state["skip_readback"] is False


class TestTranslateSplit:
    def _split_agent(self, translations):
        from src.chapter_slicer import split_chapter

        agent = bare_agent()
        segments = split_chapter(translations["content"])
        calls = []

        def fake_once(title, content, number, prev_summary, lang, genre,
                      skip_readback=False, use_flash_writer=False, content_type="novel"):
            calls.append({"title": title, "prev": prev_summary})
            return dict(translations["results"][len(calls) - 1])

        agent._translate_once = fake_once
        return agent, segments, calls

    def test_split_merges_segment_text(self):
        content = "\n\n".join(f"段落{i}" + "字" * 900 for i in range(7))
        agent = bare_agent()
        seen = []

        def fake_once(title, content, number, prev_summary, lang, genre,
                      skip_readback=False, use_flash_writer=False, content_type="novel"):
            seen.append(prev_summary)
            return {
                "translated_text": f"Part {len(seen)} " + "word " * 20,
                "new_terms_found": [{"term_cn": f"词{len(seen)}", "term_en": f"T{len(seen)}",
                                     "category": "culture"}],
                "adaptation_notes": [], "chapter_summary": "A summary long enough.",
                "quality_score": 4.0, "quality_issues": [],
                "glossary_snapshot_json": "{}",
            }

        agent._translate_once = fake_once
        out = agent.translate_chapter(
            chapter_title="长章", chapter_content=content, chapter_number=1,
        )
        assert len(seen) > 1, "long content should auto-split"
        assert out["translated_text"].count("Part ") == len(seen)
        assert len(out["new_terms_found"]) == len(seen)
        assert out["quality_score"] == 4.0
        assert len(out["new_terms_found"]) == len(seen)

    def test_bridging_context_passed_to_later_segments(self):
        content = "\n\n".join(f"段落{i}" + "字" * 900 for i in range(7))
        agent = bare_agent()
        prevs = []

        def fake_once(title, content, number, prev_summary, lang, genre,
                      skip_readback=False, use_flash_writer=False, content_type="novel"):
            prevs.append(prev_summary)
            paras = "\n\n".join("A" * 80 + f" {i}" for i in range(4))
            return {
                "translated_text": paras,
                "new_terms_found": [], "adaptation_notes": [],
                "chapter_summary": "A summary long enough to buffer.",
                "quality_score": 5.0, "quality_issues": [],
                "glossary_snapshot_json": "{}",
            }

        agent._translate_once = fake_once
        agent.translate_chapter(chapter_title="长章", chapter_content=content,
                                chapter_number=1)
        assert len(prevs) > 1
        assert prevs[0] == ""
        assert "Continuing from previous segment." in prevs[1]
        assert "Previous segment ended with:" in prevs[1]

    def test_short_paragraphs_are_not_used_as_bridge(self):
        content = "\n\n".join(f"段落{i}" + "字" * 900 for i in range(7))
        agent = bare_agent()
        prevs = []

        def fake_once(title, content, number, prev_summary, lang, genre,
                      skip_readback=False, use_flash_writer=False, content_type="novel"):
            prevs.append(prev_summary)
            return {
                "translated_text": "short\n\nbits\n\nhere",  # all < 50 chars
                "new_terms_found": [], "adaptation_notes": [],
                "chapter_summary": "A summary long enough to buffer.",
                "quality_score": 5.0, "quality_issues": [],
                "glossary_snapshot_json": "{}",
            }

        agent._translate_once = fake_once
        agent.translate_chapter(chapter_title="长章", chapter_content=content,
                                chapter_number=1)
        # No usable bridge → the next segment inherits the previous context
        # unchanged (empty), not a bogus "Continuing from" block.
        assert all("Continuing from" not in p for p in prevs[1:])

    # ── bridge boundary conditions (a bridge that is wrong or empty silently
    #    de-contextualises every later segment) ──

    def _run_split_with_paragraphs(self, paragraphs, flag_sink=None):
        content = "\n\n".join(f"段落{i}" + "字" * 900 for i in range(7))
        agent = bare_agent()
        prevs = []

        def fake_once(title, content, number, prev_summary, lang, genre,
                      skip_readback=False, use_flash_writer=False, content_type="novel"):
            prevs.append(prev_summary)
            if flag_sink is not None:
                flag_sink.append((skip_readback, use_flash_writer))
            return {
                "translated_text": "\n\n".join(paragraphs),
                "new_terms_found": [], "adaptation_notes": [],
                "chapter_summary": "A summary long enough to buffer.",
                "quality_score": 5.0, "quality_issues": [],
                "glossary_snapshot_json": "{}",
            }

        agent._translate_once = fake_once
        agent.translate_chapter(chapter_title="长章", chapter_content=content,
                                chapter_number=1)
        return prevs

    def test_paragraph_of_exactly_51_chars_is_a_bridge_candidate(self):
        paras = [f"P{i}" + "p" * 49 for i in range(3)]  # each exactly 51 chars
        prevs = self._run_split_with_paragraphs(paras)
        assert "Continuing from previous segment." in prevs[1]
        assert "P1" in prevs[1]

    def test_paragraph_of_exactly_50_chars_is_not_a_bridge_candidate(self):
        paras = [f"P{i}" + "p" * 48 for i in range(3)]  # each exactly 50 chars
        prevs = self._run_split_with_paragraphs(paras)
        assert all("Continuing from" not in p for p in prevs[1:])

    def test_bridge_uses_the_last_two_paragraphs_only(self):
        paras = ["AAA" + "a" * 60, "BBB" + "b" * 60, "CCC" + "c" * 60]
        prevs = self._run_split_with_paragraphs(paras)
        assert "BBB" in prevs[1]
        assert "CCC" in prevs[1]
        assert "AAA" not in prevs[1]

    def test_bridge_uses_the_only_paragraph_when_there_is_one(self):
        prevs = self._run_split_with_paragraphs(["SOLO" + "s" * 60])
        assert "Continuing from previous segment." in prevs[1]
        assert "SOLO" in prevs[1]

    def test_bridge_with_exactly_two_paragraphs_uses_both(self):
        # Boundary for `if len(paras) >= 2` — a mutant `>= 3` would fall through
        # to the single-paragraph branch and drop the earlier paragraph.
        paras = ["FIRST" + "f" * 60, "SECOND" + "s" * 60]
        prevs = self._run_split_with_paragraphs(paras)
        assert "FIRST" in prevs[1]
        assert "SECOND" in prevs[1]

    def test_translate_split_defaults_fast_mode_off(self):
        # A direct _translate_split call (the other entry point) must also
        # default to the full-quality path.
        flags = []
        content = "\n\n".join(f"段落{i}" + "字" * 900 for i in range(7))
        agent = bare_agent()

        def fake_once(title, content, number, prev_summary, lang, genre,
                      skip_readback=False, use_flash_writer=False, content_type="novel"):
            flags.append((skip_readback, use_flash_writer))
            return {"translated_text": "Out.", "new_terms_found": [],
                    "adaptation_notes": [], "chapter_summary": "s",
                    "quality_score": 4.0, "quality_issues": [],
                    "glossary_snapshot_json": "{}"}

        agent._translate_once = fake_once
        agent._translate_split("长章", content, 1, "", "en-US", "folk_religion")
        assert flags
        assert all(skip is False and flash is False for skip, flash in flags)

    def test_split_passes_fast_mode_flags_through(self):
        flags = []
        self._run_split_with_paragraphs(["AAAA" + "a" * 60], flag_sink=flags)
        assert flags, "split should have run"
        assert all(skip is False and flash is False for skip, flash in flags)

    def test_split_quality_score_stays_unmeasured_when_absent(self):
        """A segment result with no quality_score must merge to 0.0, not 5.0.

        5.0 was the fake-green default: it reported "cold reader gave this a
        perfect score" for a chapter no cold reader ever saw. The merge must
        stay honest — absent means unmeasured.
        """
        content = "\n\n".join(f"段落{i}" + "字" * 900 for i in range(7))
        agent = bare_agent()

        def fake_once(title, content, number, prev_summary, lang, genre,
                      skip_readback=False, use_flash_writer=False, content_type="novel"):
            return {"translated_text": "Out.", "new_terms_found": [],
                    "adaptation_notes": [], "chapter_summary": "",
                    "glossary_snapshot_json": "{}"}  # no quality_score key

        agent._translate_once = fake_once
        out = agent.translate_chapter(chapter_title="长章", chapter_content=content,
                                      chapter_number=1)
        assert out["quality_score"] == 0.0
        # And the flag must say the score carries no information.
        assert out["readback_skipped"] is True

    def test_split_returns_a_merged_dict(self):
        out = None
        content = "\n\n".join(f"段落{i}" + "字" * 900 for i in range(7))
        agent = bare_agent()

        def fake_once(title, content, number, prev_summary, lang, genre,
                      skip_readback=False, use_flash_writer=False, content_type="novel"):
            return {"translated_text": "Out.", "new_terms_found": [],
                    "adaptation_notes": ["note"], "chapter_summary": "s",
                    "quality_score": 4.0, "quality_issues": [],
                    "glossary_snapshot_json": "{}"}

        agent._translate_once = fake_once
        out = agent._translate_split("长章", content, 1, "", "en-US", "folk_religion")
        assert isinstance(out, dict)
        # Segments are merged in order, blank-line separated.
        assert out["translated_text"] == "Out.\n\nOut.\n\nOut."
        assert out["adaptation_notes"] == ["note"]


class TestFormatSemantic:
    def test_no_terms_yields_placeholder(self):
        assert TranslationAgent._format_semantic([]) == "(No semantic matches.)"

    def test_table_header_is_rendered(self):
        out = TranslationAgent._format_semantic([
            {"term_cn": "鬼节", "term_en": "Ghost Festival", "category": "culture"},
        ])
        assert out.startswith("| Chinese | English | Category |")
        assert "| 鬼节 | Ghost Festival | culture |" in out

    def test_missing_category_defaults_to_culture(self):
        out = TranslationAgent._format_semantic([{"term_cn": "鬼节", "term_en": "GF"}])
        assert "| 鬼节 | GF | culture |" in out

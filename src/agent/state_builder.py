"""Build the initial TranslatorState for a chapter translation.

【中文·这个文件是干什么的】
把「翻译一章」开跑前要填的共享白板（state）拼出来：查好本章术语、
读好翻译经验本、生成冷读者须知，然后组装成一张字典交给流水线。
原来这些逻辑挤在 graph.py 的 TranslationAgent._make_state 里（186 行），
抽到这里让 graph.py 只留「调度」不扛「拼装」。

【中文·为什么两个 store / memo 都是参数】
术语库、经验本都是 TranslationAgent 的实例字段。把拼装逻辑抽成自由函数后，
这些依赖必须由调用方（agent）显式传进来，而不是再回头访问 self——
否则就谈不上解耦。prefetch 缓存的「读后清空」也留在 agent 里，本函数
只消费已经读好的结果。
"""
from __future__ import annotations

from ..config import target_language_label
from .state import TranslatorState


def format_semantic(terms: list[dict], target_lang: str = "en-US") -> str:
    """Format semantic results as a markdown table.

    The target column is labelled from ``target_lang``; it used to be
    hard-coded "English" (see exact_store.to_formatted_text for the same
    defect on the exact-glossary path).

    【中文·把语义术语拼成一张 Markdown 表格，塞进提示词给 AI 看】
    为什么用表格：模型对表格结构的理解比一长串文字稳定得多。
    """
    if not terms:
        return "(No semantic matches.)"
    target = target_language_label(target_lang)
    lines = [f"| Chinese | {target} | Category |",
             "|----------|---------|----------|"]
    for t in terms:
        lines.append(
            f"| {t['term_cn']} | {t['term_en']} | "
            f"{t.get('category', 'culture')} |"
        )
    return "\n".join(lines)


def build_state(
    *,
    title: str,
    content: str,
    number: int,
    prev_summary: str,
    lang: str,
    genre: str,
    skip_readback: bool,
    content_type: str,
    exact_store,
    semantic_store,
    style_memo,
    api_key: str,
    book_id: str,
    prefetched_exact,
    prefetched_semantic,
    cold_read_context: str,
) -> TranslatorState:
    """Build the initial state for a chapter translation.

    Populates exact_matches_text and semantic_matches_text from the glossary
    stores — this is the only glossary lookup step (no tool calls).

    【中文·填写"共享白板"，这是流水线开跑前的准备工作】
    READ/WRITE/READBACK/FIX 四个角色不直接对话，全靠这块白板传递信息。
    本函数在发车前把白板擦干净、写上初始内容：原文、第几章、翻成什么
    语言、有哪些术语可用等。术语是「提前算好塞进去」的（不让 AI 自己查）。
    """
    # Exact matches: terms that appear in this chapter's text.
    if prefetched_exact is not None:
        exact_matches = prefetched_exact
    else:
        exact_matches = exact_store.match_in_text(content, target_lang=lang)
    exact_text = (
        exact_store.to_formatted_text_with_notes(exact_matches, target_lang=lang)
        if exact_matches
        else exact_store.to_formatted_text(exact_matches, target_lang=lang)
    )

    # Semantic matches: culturally related terms for context
    if prefetched_semantic is not None:
        semantic_hits = prefetched_semantic
    else:
        semantic_hits = semantic_store.search(content, top_k=15, target_lang=lang)
    # Filter out terms already covered by exact matches
    semantic_hits = [t for t in semantic_hits if t["term_cn"] not in exact_matches]
    semantic_text = format_semantic(semantic_hits, target_lang=lang)

    return {
        # Input
        "chapter_title": title,
        "chapter_content": content,
        "chapter_number": number,
        "book_id": book_id,
        "target_lang": lang,
        "genre": genre,
        "content_type": content_type,
        # Glossary
        "exact_glossary": exact_store.to_dict(target_lang=lang),
        "semantic_terms": semantic_hits,
        "exact_matches_text": exact_text,
        "semantic_matches_text": semantic_text,
        # Output (populated by nodes)
        "translated_text": "",
        "new_terms_found": [],
        "adaptation_notes": [],
        "fix_lessons": [],
        "chapter_summary": "",
        # Context
        "previous_chapter_summary": prev_summary,
        # Quality / feedback
        "quality_score": 0.0,
        "quality_issues": [],
        "retranslation_count": 0,
        "readback_skipped": skip_readback,
        "glossary_snapshot_json": "",
        # v0.15 reader-centric fields
        "read_analysis": {},
        "readback_feedback": {},
        "context_signals": "",
        "image_gaps": [],
        "style_memo": style_memo.read_relevant() or (
            "(No translation memory yet. This is the first chapter. "
            "Stay close to the source text — do not invent sensory details "
            "or interior monologue that the original doesn't contain. "
            "Your creative authority grows as the memo accumulates. "
            "For now: translate faithfully.)"
        ),
        "skip_readback": skip_readback,
        "api_key": api_key or "",
        # Cold reader briefing
        "cold_read_context": cold_read_context,
        "dialect_context": "",
    }

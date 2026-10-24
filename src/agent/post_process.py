"""Post-chapter persistence and gates — run after the graph finishes a chapter.

【中文·这个文件是干什么的】
一章翻完后，把这一章的收获「沉淀」下来，供后续章节复用，同时做几道
不需要 AI 参与的规则检查（不花钱）。原来这些逻辑挤在 graph.py 的
TranslationAgent._post_process 里（300 行），抽到这里让 graph.py 只留「调度」。

依次做 7 件事：
  ① 文化忠实度检查 —— WRITE 有没有把有含义的名字偷懒音译成拼音
  ② 强制接受审计日志 —— 修满轮次仍不合格时记下来备查
  ③ 存 READ 的分析进经验本（每一章都存）
  ④ 存冷读者的反馈进经验本（只有抽检章节才有）
  ⑤ 存 FIX 的修补经验进经验本（闭环关键）
  ⑥ 术语入库前的三道关卡（忠实度关、有效性关、分类入库）
  ⑦ 生成术语快照 + 更新前情提要

【中文·为什么依赖都是参数】
style_memo / exact_store / semantic_store 都是 TranslationAgent 的实例字段，
抽成自由函数后由调用方显式传入；record_context 是「更新前情提要」的回调
（指向 agent 的 _update_context），避免本模块反向依赖 agent。
"""
from __future__ import annotations

import logging

from ..config import MAX_RETRANSLATION_ATTEMPTS
from .fidelity import check_cultural_fidelity, is_single_rendering, is_valid_rendering

logger = logging.getLogger(__name__)

# 每章最多把 FIX 的几条「修补经验」存进翻译记忆。存经验只去重不限额，
# 而 bridges.md 在真实书上已 31818 行，经验本每次塞进提示词，膨胀即每章变贵变慢。
_MAX_FIX_LESSONS_PER_CHAPTER = 5


def post_process_chapter(
    result: dict,
    target_lang: str,
    *,
    style_memo,
    exact_store,
    semantic_store,
    record_context,
) -> dict:
    """After graph execution: persist new terms and update style memo.

    Enriches new terms with cultural notes from the READ agent's
    terminology_decisions so future chapters inherit not just the
    translation but the cultural reasoning behind it.

    【中文·收尾工序：一章翻完后的"沉淀"与"把关"】
    流水线跑完只是拿到了英文稿。这个函数负责把这一章的收获**存下来**，
    让后面的章节能受益，同时做几道规则检查（不花钱）。
    """
    # 【中文·从白板上把各角色的产出取出来】
    new_terms = result.get("new_terms_found", [])        # READ 发现的新术语
    read_analysis = result.get("read_analysis", {})      # READ 的完整分析
    readback_feedback = result.get("readback_feedback", {})  # 冷读者的评审
    chapter_number = result.get("chapter_number", 0)
    retries = result.get("retranslation_count", 0)       # 已经重修几轮

    # ── Cultural fidelity check (rule-based, no LLM) ──
    # READBACK is blind to the source, so it cannot catch a meaningful
    # name being flattened to pinyin. Verify WRITE honored READ's
    # terminology decisions; record failures for diagnosis.
    fidelity_failures = check_cultural_fidelity(
        result.get("read_analysis", {}),
        result.get("translated_text", ""),
    )
    if fidelity_failures:
        for failure in fidelity_failures:
            logger.warning("FIDELITY ch%d: %s", chapter_number, failure)
        result["quality_issues"] = (
            list(result.get("quality_issues", [])) + fidelity_failures
        )

    # ── Audit log: FORCED_ACCEPT when cold reader still says NEEDS_FIX ──
    # after max retries. Without this log, we cannot diagnose whether the
    # chapter was too hard, the cold reader was too strict, or FIX ineffective.
    if readback_feedback:
        verdict = readback_feedback.get("verdict", "PASS") if readback_feedback else "PASS"
        if verdict == "NEEDS_FIX" and retries >= MAX_RETRANSLATION_ATTEMPTS:
            logger.warning(
                "FORCED_ACCEPT ch%d: verdict=NEEDS_FIX after %d retries. "
                "Cold reader issues: comprehension=%d, engagement=%d. "
                "FIX changes: %s",
                chapter_number, retries,
                len(readback_feedback.get("comprehension_issues", [])),
                len(readback_feedback.get("engagement_gaps", [])),
                result.get("adaptation_notes", [])[:3],
            )

    # ── Update style memo from READ analysis (EVERY chapter) ──
    if read_analysis:
        style_memo.update_from_read_analysis(read_analysis, chapter_number)

    # ── Supplement with cold-reader feedback (sample chapters only) ──
    if readback_feedback:
        style_memo.update_from_feedback(
            readback_feedback, read_analysis, chapter_number,
        )

    # ── Bank FIX's repairs (closes the knowledge loop) ──
    fix_lessons = result.get("fix_lessons", [])
    if fix_lessons:
        for lesson in fix_lessons[:_MAX_FIX_LESSONS_PER_CHAPTER]:
            style_memo.record_lesson(
                "bridges", f"FIX that worked: {lesson[:200]}", chapter_number,
            )
        logger.info(
            "FIX LESSONS ch%d: banked %d of %d repair(s) into bridges.md",
            chapter_number, min(len(fix_lessons), _MAX_FIX_LESSONS_PER_CHAPTER),
            len(fix_lessons),
        )

    # ── Merge READ's cultural notes into new terms ──────────
    if new_terms and read_analysis:
        term_notes = {}
        for td in read_analysis.get("terminology_decisions", []):
            note = td.get("cultural_note", "")
            if note:
                term_notes[td.get("term_cn", "")] = note
        for t in new_terms:
            cn = t.get("term_cn", "")
            if cn in term_notes and not t.get("note"):
                t["note"] = term_notes[cn]

    # ── Fidelity gate: prefer READ's rendering over WRITE's drift ──
    if new_terms and read_analysis:
        read_en_by_cn = {}
        for td in read_analysis.get("terminology_decisions", []):
            cn = td.get("term_cn", "")
            en = td.get("proposed_en", "")
            if cn and en and len(en) <= 50:
                read_en_by_cn[cn] = en
        for t in new_terms:
            cn = t.get("term_cn", "")
            read_en = read_en_by_cn.get(cn, "")
            if (
                read_en
                and read_en != t.get("term_en", "")
                and is_single_rendering(read_en)
            ):
                logger.warning(
                    "FIDELITY GATE ch%d: '%s' WRITE='%s' READ='%s' — using READ",
                    chapter_number, cn, t.get("term_en", ""), read_en,
                )
                t["term_en"] = read_en

    # ── Hard curation gate: drop obviously-invalid renderings ──
    if new_terms:
        kept = []
        for t in new_terms:
            ok, reason = is_valid_rendering(t.get("term_cn", ""), t.get("term_en", ""))
            if ok:
                kept.append(t)
            else:
                logger.warning(
                    "CURATION GATE ch%d: dropping '%s' → '%s' (%s)",
                    chapter_number, t.get("term_cn", ""), t.get("term_en", ""), reason,
                )
        new_terms = kept
        result["new_terms_found"] = kept

    if new_terms:
        # Write character/location terms to exact store
        exact_terms = [
            t for t in new_terms
            if t.get("category") in ("character", "location")
        ]
        if exact_terms:
            exact_store.add_batch(
                exact_terms, chapter=result.get("chapter_number", 0),
                target_lang=target_lang,
            )

        # Write ALL terms to semantic store
        semantic_store.add_batch(new_terms, target_lang=target_lang)

    # Snapshot exact glossary for checkpoint (scoped to this chapter's language)
    result["glossary_snapshot_json"] = exact_store.snapshot(
        target_lang=target_lang
    )

    # ── Feed the cold reader's context buffer ──
    chapter_summary = result.get("chapter_summary", "")
    chapter_number = result.get("chapter_number", 0)
    record_context(chapter_number, chapter_summary)

    return result

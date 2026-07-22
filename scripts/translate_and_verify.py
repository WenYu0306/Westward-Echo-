"""Full pipeline: translate 50 chapters + automated quality verification."""

import json, time, sys, re, os
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv; load_dotenv()

from src.chapter_splitter import split_chapters, ParagraphTag
from src.agent.graph import TranslationAgent

FIXTURE = Path(__file__).parent.parent / "tests" / "fixtures" / "test_novel_50ch.txt"
OUT_DIR = Path(__file__).parent.parent / "tests" / "fixtures"


# ═══════════════════════════════════════════════════════════
# PHASE 1: Translation
# ═══════════════════════════════════════════════════════════

def translate_all():
    text = FIXTURE.read_text(encoding="utf-8")
    chapters = split_chapters(text)
    translatable = [c for c in chapters if c.action != ParagraphTag.SKIP]
    total = len(translatable)

    print(f"\n{'═'*60}")
    print(f"PHASE 1: Translating {total} chapters")
    print(f"{'═'*60}\n")

    agent = TranslationAgent()
    all_results = []
    prev_summary = ""

    for i, ch in enumerate(translatable):
        chapter_num = ch.index
        print(f"[{i+1}/{total}] 第{chapter_num}章「{ch.title[:30]}」({ch.word_count}字) ... ", end="", flush=True)

        result = agent.translate_chapter(
            chapter_title=ch.title,
            chapter_content=ch.content,
            chapter_number=chapter_num,
            previous_summary=prev_summary,
            target_lang="en-US",
        )

        tt = result["translated_text"]
        score = result.get("quality_score", "N/A")
        new_terms = len(result.get("new_terms_found", []))

        # Check for JSON residue
        has_json = tt.strip().startswith("{") or '"translated_text"' in tt[:200]

        status = "⚠️ JSON" if has_json else "✅"
        print(f"{len(tt)}字 | +{new_terms}词 | QA:{score} {status}")

        all_results.append({
            "chapter": chapter_num,
            "title": ch.title,
            "word_count_cn": ch.word_count,
            "word_count_en": len(tt),
            "new_terms": new_terms,
            "quality_score": score,
            "has_json_residue": has_json,
            "translated_text": tt,
        })

        prev_summary = result.get("chapter_summary", "")
        time.sleep(0.5)

    # Save results
    results_path = OUT_DIR / "test_novel_50ch_results.json"
    # Strip full translations to keep JSON manageable
    results_light = [{k: v for k, v in r.items() if k != "translated_text"} for r in all_results]
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(results_light, f, ensure_ascii=False, indent=2)

    # Save full translation
    full_en = "\n\n---\n\n".join(r["translated_text"] for r in all_results)
    en_path = OUT_DIR / "test_novel_50ch_en.md"
    en_path.write_text(full_en, encoding="utf-8")

    # Save glossary
    glossary = agent.exact_store.to_dict()
    gloss_path = OUT_DIR / "test_novel_50ch_glossary.json"
    gloss_path.write_text(json.dumps(glossary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n📄 译文: {en_path}")
    print(f"📄 术语: {gloss_path} ({len(glossary)} 条)")
    print(f"📄 指标: {results_path}")

    return all_results, glossary, full_en


# ═══════════════════════════════════════════════════════════
# Term consistency helpers
# ═══════════════════════════════════════════════════════════

def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            # Insertion, deletion, substitution
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (0 if c1 == c2 else 1)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


def _is_term_consistent(expected: str, translated_text: str) -> tuple:
    """
    Check if an expected English term appears consistently in translated text.

    Returns (is_consistent: bool, matched_variant_or_reason: str).

    Three-tier matching:
    1. Case-insensitive substring match (fast path)
    2. Token overlap check for multi-word terms (>=70% words within 5-word window)
    3. Levenshtein-distance check for short terms (<=5 chars, edit distance <= 1)
    """
    expected_lower = expected.lower()
    tt_lower = translated_text.lower()

    # Tier 1: case-insensitive substring match (fast path)
    if expected_lower in tt_lower:
        return (True, expected)

    # Tier 2: token overlap for multi-word terms
    expected_words = expected_lower.split()
    if len(expected_words) >= 2:
        tt_words = tt_lower.split()
        required = max(1, int(len(expected_words) * 0.7))
        window = 5

        # Slide a window over the translation words
        for i in range(len(tt_words) - window + 1):
            window_words = set(tt_words[i:i + window])
            overlap = sum(1 for ew in expected_words if ew in window_words)
            if overlap >= required:
                # Extract the matching span (±2 words around the window)
                start = max(0, i - 2)
                end = min(len(tt_words), i + window + 2)
                matched = " ".join(tt_words[start:end])
                return (True, f"[variant: {matched}]")

        # Try sliding with a smaller window if the full window didn't match
        if window > len(expected_words) + 2:
            tight_window = len(expected_words) + 2
            for i in range(len(tt_words) - tight_window + 1):
                window_words = set(tt_words[i:i + tight_window])
                overlap = sum(1 for ew in expected_words if ew in window_words)
                if overlap >= required:
                    start = max(0, i - 2)
                    end = min(len(tt_words), i + tight_window + 2)
                    matched = " ".join(tt_words[start:end])
                    return (True, f"[variant: {matched}]")

    # Tier 3: Levenshtein for short terms (<= 5 chars)
    if len(expected) <= 5:
        tt_words = tt_lower.split()
        # Check each word and also 2-word phrases
        candidates = list(tt_words)
        for i in range(len(tt_words) - 1):
            candidates.append(f"{tt_words[i]} {tt_words[i+1]}")

        for candidate in candidates:
            # Only compare candidates of similar length (±2 chars)
            if abs(len(candidate) - len(expected_lower)) <= 2:
                dist = _levenshtein_distance(expected_lower, candidate)
                if dist <= 1:
                    return (True, f"[variant: {candidate}]")

    return (False, "[NOT FOUND]")


def _extract_candidate_term(expected: str, translated_text: str) -> str:
    """
    When a term is NOT FOUND, try to extract what the LLM actually used
    by looking for words near where the expected term might be.
    """
    expected_words = expected.lower().split()
    tt_words = translated_text.lower().split()

    if len(expected_words) == 0:
        return "[NOT FOUND]"

    # Look for any word from the expected term in the translation
    # and extract a window around it
    for i, tw in enumerate(tt_words):
        if tw in [ew.rstrip("s") for ew in expected_words] or any(
            ew.rstrip("s")[:4] == tw[:4] and len(ew.rstrip("s")) >= 4
            for ew in expected_words
        ):
            start = max(0, i - 3)
            end = min(len(tt_words), i + 4)
            snippet = " ".join(tt_words[start:end])
            return f"[maybe: ...{snippet}...]"

    return "[NOT FOUND]"


# ═══════════════════════════════════════════════════════════
# PHASE 2: Automated Quality Verification
# ═══════════════════════════════════════════════════════════

def verify(all_results, glossary, full_en):
    print(f"\n{'═'*60}")
    print(f"PHASE 2: Automated Quality Verification")
    print(f"{'═'*60}")

    issues = []
    passed = []

    # ── Check 1: Chapter count ──
    expected = 50
    actual = len(all_results)
    if actual >= expected:
        passed.append(f"章节数量: {actual} (预期 ≥ {expected})")
    else:
        issues.append(f"❌ 章节数量: {actual} (预期 ≥ {expected})")

    # ── Check 2: No empty translations ──
    empty = [r for r in all_results if len(r["translated_text"]) < 50]
    if empty:
        issues.append(f"❌ {len(empty)} 章译文为空或过短")
    else:
        passed.append("空译文检测: 0 章")

    # ── Check 3: JSON residue detection ──
    json_chapters = [r for r in all_results if r["has_json_residue"]]
    if json_chapters:
        issues.append(f"❌ JSON 残留: 第{', '.join(str(r['chapter']) for r in json_chapters)}章")
    else:
        passed.append("JSON 残留检测: 0 章")

    # ── Check 4: Quality score distribution ──
    scores = [r["quality_score"] for r in all_results]
    avg_score = sum(scores) / len(scores) if scores else 0
    low = [r for r in all_results if r["quality_score"] < 3.0]
    if low:
        issues.append(f"⚠️ 低分章节 (＜3.0): {len(low)} 章 - {[(r['chapter'], r['quality_score']) for r in low]}")
    passed.append(f"平均质量评分: {avg_score:.1f}/5.0 (最低: {min(scores):.1f}, 最高: {max(scores):.1f})")

    # ── Check 5: Term consistency (the KEY metric) ──
    print(f"\n  分析术语一致性...")

    # Extract Chinese terms from the glossary and check if they appear in translations
    cn_terms = list(glossary.keys())
    term_consistency_results = {}

    for cn_term in cn_terms:
        expected_en = glossary[cn_term]
        # Find all chapters where this term should appear (check Chinese original text)
        text = FIXTURE.read_text(encoding="utf-8")
        chs = split_chapters(text)
        chapters_with_term = []

        for ch in chs:
            if cn_term in ch.content:
                chapters_with_term.append(ch.index)

        if len(chapters_with_term) < 3:
            continue  # Term doesn't appear enough to test consistency

        # Now check the translated chapters for consistent English
        seen_variants = set()
        for r in all_results:
            if r["chapter"] in chapters_with_term:
                tt = r["translated_text"]
                is_consistent, matched = _is_term_consistent(expected_en, tt)
                if is_consistent:
                    seen_variants.add(matched)
                else:
                    # Try to extract what the LLM actually used
                    candidate = _extract_candidate_term(expected_en, tt)
                    seen_variants.add(candidate)

        if len(seen_variants) > 1:
            term_consistency_results[cn_term] = {
                "status": "INCONSISTENT",
                "expected": expected_en,
                "variants": list(seen_variants),
                "chapters": chapters_with_term,
            }
        else:
            term_consistency_results[cn_term] = {
                "status": "CONSISTENT",
                "expected": expected_en,
                "chapters": chapters_with_term,
            }

    # Summarize term consistency
    inconsistent = {k: v for k, v in term_consistency_results.items() if v["status"] == "INCONSISTENT"}
    consistent = {k: v for k, v in term_consistency_results.items() if v["status"] == "CONSISTENT"}

    term_rate = len(consistent) / len(term_consistency_results) * 100 if term_consistency_results else 0
    passed.append(f"术语一致性: {len(consistent)}/{len(term_consistency_results)} 一致 ({term_rate:.0f}%)")

    if inconsistent:
        for term, detail in list(inconsistent.items())[:8]:
            issues.append(f"❌ '{term}' 不一致: 预期'{detail['expected']}', 出现{detail['variants']}")

    # ── Check 6: Translation length ratio ──
    ratios = []
    for r in all_results:
        cn_len = r["word_count_cn"]
        en_len = r["word_count_en"]
        if cn_len > 0:
            ratios.append(en_len / cn_len)
    avg_ratio = sum(ratios) / len(ratios) if ratios else 0
    passed.append(f"中英字数比: {avg_ratio:.1f}x (预期 2.0-4.0x)")

    if avg_ratio < 1.5 or avg_ratio > 5.0:
        issues.append(f"⚠️ 中英字数比异常: {avg_ratio:.1f}x")

    # ── Check 7: New term accumulation curve ──
    term_accum = []
    running_new = 0
    for r in all_results:
        running_new += r["new_terms"]
        term_accum.append(running_new)

    # New terms should accumulate fast at first, then plateau
    first_10 = term_accum[9] if len(term_accum) >= 10 else term_accum[-1]
    last_10_new = term_accum[-1] - (term_accum[-11] if len(term_accum) >= 11 else 0)
    passed.append(f"术语积累: 前10章{term_accum[9] if len(term_accum) >= 10 else term_accum[-1]}条, 最后10章新增{last_10_new}条")

    if last_10_new > first_10 * 0.5 and len(term_accum) >= 30:
        issues.append(f"⚠️ 术语持续大量新增，可能未收敛: 最后10章+{last_10_new}条")

    # ── FINAL REPORT ──
    print(f"\n{'═'*60}")
    print(f"VERIFICATION REPORT")
    print(f"{'═'*60}")

    print(f"\n✅ PASSED ({len(passed)}):")
    for p in passed:
        print(f"  ✓ {p}")

    if issues:
        print(f"\n❌ ISSUES ({len(issues)}):")
        for i in issues:
            print(f"  ✗ {i}")
    else:
        print(f"\n🎉 ALL CHECKS PASSED")

    # Grade
    grade = "A+" if not issues else ("B" if len(issues) <= 3 else "C")
    print(f"\n📊 OVERALL GRADE: {grade}")

    # Save report
    report = {
        "grade": grade,
        "passed": passed,
        "issues": issues,
        "summary": {
            "chapters": actual,
            "avg_quality_score": round(avg_score, 1),
            "term_consistency_rate": round(term_rate, 0),
            "glossary_size": len(glossary),
            "json_residue_chapters": len(json_chapters),
            "zh_en_ratio": round(avg_ratio, 1),
        },
        "term_details": {
            "consistent_count": len(consistent),
            "inconsistent_count": len(inconsistent),
            "inconsistent_terms": [
                {
                    "cn_term": cn_term,
                    "expected": detail["expected"],
                    "variants": detail["variants"],
                    "chapters": detail["chapters"],
                }
                for cn_term, detail in list(inconsistent.items())[:10]
            ],
        },
        "score_distribution": {
            "5.0": len([s for s in scores if s >= 5.0]),
            "4.0-4.9": len([s for s in scores if 4.0 <= s < 5.0]),
            "3.0-3.9": len([s for s in scores if 3.0 <= s < 4.0]),
            "<3.0": len([s for s in scores if s < 3.0]),
        },
    }

    report_path = OUT_DIR / "test_novel_50ch_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n📄 完整报告: {report_path}")

    return report


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    if not FIXTURE.exists():
        print(f"❌ 小说文件不存在: {FIXTURE}")
        print("   请等待 agent 写完 50 章小说后再运行")
        sys.exit(1)

    print("=" * 60)
    print("WESTWARD ECHO — 50-Chapter Automated Test")
    print("=" * 60)

    # Phase 1
    results, glossary, full_en = translate_all()

    # Phase 2
    report = verify(results, glossary, full_en)

    print("\n" + "=" * 60)
    print(f"GRADE: {report['grade']} | 术语一致率: {report['summary']['term_consistency_rate']:.0f}% | 均分: {report['summary']['avg_quality_score']}/5.0")
    print("=" * 60)

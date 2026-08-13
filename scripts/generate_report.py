"""Generate a customer-facing QA report from a completed translation run.

Reads the four artifacts produced by ``run_novel.py`` (translation md,
glossary json, quality json, source text) and produces:
  - ``_report.json``       machine-readable report
  - ``_report.md``         human-readable report (customer-facing)
  - ``_glossary_table.md`` CN↔EN bilingual glossary table (customer-facing)

The term-consistency and length-ratio checks are reused from
``translate_and_verify.py`` — the QA logic already exists; this script
just generalizes it to any book instead of the hardcoded 50-chapter fixture.

Usage:
    python3 scripts/generate_report.py quanzhi_fanwai
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

load_dotenv()

from src.chapter_splitter import split_chapters, ParagraphTag  # noqa: E402
from src.encoding import detect_and_read  # noqa: E402
from scripts.run_novel import NOVELS  # noqa: E402
from scripts.translate_and_verify import (  # noqa: E402
    _is_term_consistent,
    _extract_candidate_term,
)

_CHAPTER_RE = re.compile(r"^#{1,2}\s+Chapter\s+(\d+):?\s*(.*)$", re.IGNORECASE)


def _parse_en_chapters(md_text: str) -> dict[int, str]:
    """Parse ``## Chapter N: title`` blocks into {chapter_num: body}."""
    chapters: dict[int, list[str]] = {}
    current = None
    for line in md_text.split("\n"):
        m = _CHAPTER_RE.match(line.strip())
        if m:
            current = int(m.group(1))
            chapters.setdefault(current, [])
        elif current is not None:
            chapters[current].append(line)
    return {k: "\n".join(v).strip() for k, v in chapters.items()}


def _has_json_residue(text: str) -> bool:
    t = text.strip()
    return t.startswith("{") or '"translated_text"' in t[:200]


def _check_term_consistency(cn_chapters, en_chapters, glossary) -> dict:
    """Core metric: does each term's English rendering stay consistent."""
    results = {}
    for cn_term, expected_en in glossary.items():
        chapters_with_term = [c.index for c in cn_chapters if cn_term in c.content]
        if len(chapters_with_term) < 3:
            continue  # term appears too rarely to test consistency

        seen_variants = set()
        for cn_ch in cn_chapters:
            if cn_ch.index not in chapters_with_term:
                continue
            body = en_chapters.get(cn_ch.index, "")
            if not body:
                continue
            consistent, matched = _is_term_consistent(expected_en, body)
            if consistent:
                seen_variants.add(matched)
            else:
                seen_variants.add(_extract_candidate_term(expected_en, body))

        results[cn_term] = {
            "status": "CONSISTENT" if len(seen_variants) <= 1 else "INCONSISTENT",
            "expected": expected_en,
            "variants": sorted(seen_variants),
            "chapters": chapters_with_term,
        }
    return results


def generate_report(novel_key: str) -> dict:
    cfg = NOVELS[novel_key]
    out_dir = Path(cfg["output_dir"])

    # ── Load artifacts ──
    text, _ = detect_and_read(cfg["path"])
    cn_chapters = [c for c in split_chapters(text) if c.action != ParagraphTag.SKIP]

    md_path = out_dir / cfg["output_file"]
    md_text = md_path.read_text(encoding="utf-8")
    en_chapters = _parse_en_chapters(md_text)

    glossary_path = out_dir / "_glossary.json"
    glossary = json.loads(glossary_path.read_text(encoding="utf-8")) if glossary_path.exists() else {}

    quality_path = out_dir / "_quality.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8")) if quality_path.exists() else []

    # ── Checks ──
    passed = []
    issues = []

    # 1. Chapter completeness
    missing = [c.index for c in cn_chapters if c.index not in en_chapters]
    if missing:
        issues.append(f"缺译章节: {missing}")
    else:
        passed.append(f"章节完整: {len(en_chapters)}/{len(cn_chapters)}")

    # 2. Empty / short translations
    empty = [num for num, body in en_chapters.items() if len(body) < 50]
    if empty:
        issues.append(f"空译/过短章节: {empty}")
    else:
        passed.append("空译检测: 0 章")

    # 3. JSON residue
    json_ch = [num for num, body in en_chapters.items() if _has_json_residue(body)]
    if json_ch:
        issues.append(f"JSON 残留: 第{json_ch}章")
    else:
        passed.append("JSON 残留: 0 章")

    # 4. Term consistency (core metric)
    term_results = _check_term_consistency(cn_chapters, en_chapters, glossary)
    inconsistent = {k: v for k, v in term_results.items() if v["status"] == "INCONSISTENT"}
    consistent = {k: v for k, v in term_results.items() if v["status"] == "CONSISTENT"}
    rate = (len(consistent) / len(term_results) * 100) if term_results else 100.0
    passed.append(f"术语一致性: {len(consistent)}/{len(term_results)} ({rate:.0f}%)")
    for cn, detail in list(inconsistent.items())[:8]:
        issues.append(f"术语不一致 '{cn}': 预期 '{detail['expected']}', 变体 {detail['variants']}")

    # 5. Length ratio (EN character count / CN character count, matching
    #    translate_and_verify.py's original metric)
    ratios = []
    for cn_ch in cn_chapters:
        body = en_chapters.get(cn_ch.index, "")
        if cn_ch.word_count > 0 and body:
            ratios.append(len(body) / cn_ch.word_count)
    avg_ratio = sum(ratios) / len(ratios) if ratios else 0
    passed.append(f"中英字数比: {avg_ratio:.2f}x")
    if avg_ratio and (avg_ratio < 1.5 or avg_ratio > 5.0):
        issues.append(f"字数比异常: {avg_ratio:.2f}x")

    # 6. Cold-read sampling
    if quality:
        verdicts = [q.get("verdict", "?") for q in quality]
        n_pass = verdicts.count("PASS")
        n_fix = verdicts.count("NEEDS_FIX")
        passed.append(f"冷读抽检: {len(quality)} 章 — {n_pass} PASS / {n_fix} NEEDS_FIX")
    else:
        passed.append("冷读抽检: 未执行")

    # ── Grade ──
    grade = "A+" if not issues else ("B" if len(issues) <= 3 else "C")

    report = {
        "book": cfg["name"],
        "genre": cfg["genre"],
        "grade": grade,
        "passed": passed,
        "issues": issues,
        "_quality": quality,
        "summary": {
            "chapters_total": len(cn_chapters),
            "chapters_translated": len(en_chapters),
            "glossary_size": len(glossary),
            "term_consistency_rate": round(rate, 1),
            "zh_en_ratio": round(avg_ratio, 2),
            "cold_read_samples": len(quality),
            "cold_read_pass": sum(1 for q in quality if q.get("verdict") == "PASS"),
            "cold_read_needs_fix": sum(1 for q in quality if q.get("verdict") == "NEEDS_FIX"),
        },
        "term_details": {
            "consistent_count": len(consistent),
            "inconsistent_count": len(inconsistent),
            "inconsistent_terms": [
                {"cn_term": k, "expected": v["expected"],
                 "variants": v["variants"], "chapters": v["chapters"]}
                for k, v in list(inconsistent.items())[:10]
            ],
        },
    }

    # ── Write machine-readable report ──
    (out_dir / "_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ── Write human-readable report ──
    _write_report_md(report, out_dir / "_report.md")

    # ── Write bilingual glossary table ──
    _write_glossary_table(glossary, out_dir / "_glossary_table.md")

    return report


def _write_report_md(report: dict, path: Path):
    s = report["summary"]
    lines = [
        f"# 西渡编译成果报告",
        "",
        f"**作品**：《{report['book']}》",
        f"**体裁**：{report['genre']}",
        "",
        "---",
        "",
        "## 编译质量（核心）",
        "",
    ]
    # Cold-read impressions are the star — a native English reader's
    # verbatim reaction is the strongest evidence the compilation worked.
    quality = report.get("_quality", [])
    if quality:
        for q in quality:
            verdict = q.get("verdict", "?")
            keep = q.get("would_keep_reading", False)
            imp = (q.get("impression") or "").strip()
            ch = q.get("ch_number", "?")
            if verdict == "PASS" and imp:
                lines.append(f"### 第 {ch} 章 · 冷读通过")
                lines.append("")
                lines.append(f"> {imp}")
                lines.append("")
                if keep:
                    lines.append("**读者表态：愿意继续追读。**")
                    lines.append("")
            elif verdict == "NEEDS_FIX":
                lines.append(f"### 第 {ch} 章 · 需返修")
                lines.append("")
                lines.append(f"> {imp}")
                lines.append("")
    else:
        lines.append("（未执行冷读抽检）")
        lines.append("")

    lines += [
        "---",
        "",
        "## 交付内容",
        "",
        f"- 英文译稿：{s['chapters_translated']} 章完整编译（Markdown + EPUB）",
        f"- 术语表：{s['glossary_size']} 条中英对照",
        f"- 本报告",
        "",
        "---",
        "",
        "## 专业指标（工程背书）",
        "",
        f"- 术语一致率：{s['term_consistency_rate']}%",
        f"- 中英字数比：{s['zh_en_ratio']}x（健康区间 2.0–4.0x）",
        f"- 章节完整度：{s['chapters_translated']}/{s['chapters_total']}",
        "",
    ]
    if report["issues"]:
        lines.append("## 待改进项")
        lines.append("")
        for i in report["issues"]:
            lines.append(f"- {i}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_glossary_table(glossary: dict, path: Path):
    lines = ["# 术语表（中英对照）", "", "| 中文 | English |", "|------|---------|"]
    for cn, en in sorted(glossary.items()):
        lines.append(f"| {cn} | {en} |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/generate_report.py <novel_key>")
        print("Available:", ", ".join(NOVELS.keys()))
        sys.exit(1)
    novel_key = sys.argv[1]
    if novel_key not in NOVELS:
        print(f"Unknown novel '{novel_key}'. Available: {', '.join(NOVELS.keys())}")
        sys.exit(1)
    report = generate_report(novel_key)
    print(f"\nGRADE: {report['grade']} | 术语一致率: {report['summary']['term_consistency_rate']}% | "
          f"字数比: {report['summary']['zh_en_ratio']}x")
    print(f"报告已生成: {NOVELS[novel_key]['output_dir']}/_report.md")


if __name__ == "__main__":
    main()

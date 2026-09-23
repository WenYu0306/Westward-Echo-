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
from src.text_io import detect_and_read  # noqa: E402
from scripts.run_novel import NOVELS  # noqa: E402

# NOTE: _is_term_consistent / _extract_candidate_term were imported here until the
# term check was rewritten to use verbatim matching only. They are fuzzy matchers
# whose signals spot-checking proved unreliable (see _check_term_consistency), so
# they are deliberately no longer used. They still exist in translate_and_verify.py
# for that script's own 50-chapter fixture run; do not re-import them here.

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


def _find_epub(out_dir: Path):
    """Return the EPUB in a book's output dir, or None if there is none.

    Only this directory is searched. The CLI compile path does not build EPUBs —
    that happens on demand in the API layer — so most corpora legitimately have
    none, and the report must not claim a format that is not on disk.
    """
    matches = sorted(out_dir.glob("*.epub"))
    return matches[0] if matches else None


def _check_term_consistency(cn_chapters, en_chapters, glossary) -> dict:
    """Locate each glossary rendering in the translation, verbatim only.

    SCOPE LIMIT — read before trusting any number from this function.

    A reliable "term consistency rate" needs to know, for each chapter where the
    source mentions a term, what the translation actually called it. That
    requires sentence alignment. This project does not have one, so the only
    certain signal available is: does the recorded rendering appear verbatim?

    The fuzzy matchers that were used here before are not usable as evidence.
    Measured on the 2301-chapter 《地府》 corpus:

    * ``_is_term_consistent`` tiers 2/3 return ``[variant: <window ±2 words>]`` —
      a CONTEXT SNIPPET, not a rendering. Snippets differ chapter to chapter even
      when the rendering is identical, so every term needing a fuzzy match looked
      inconsistent. 乱坟岗 was flagged with "at the potter's field, the common
      graves stretched across" vs "reached the potter's field — the common graves
      at" — the SAME rendering, two snippets.
    * ``_extract_candidate_term`` only requires each word of the term to occur
      ANYWHERE in the chapter, not adjacently. Spot-checking its top hits found
      they were not variants at all: 张婶→"Granny Zhang" was "matched" against
      `deaf granny` (a DIFFERENT character), 文凭→"Old Diplomas" against the
      ordinary noun `a diploma from school`, 小石头→"Little Stone" against
      `a stone dropped into still water`. Any threshold tuned on this signal
      would be fitting noise.

    So the three verdicts below are stated as what they are: two certain counts
    and one explicitly undetermined count. No single "consistency rate" is
    claimed, because none of the candidate rates (19.4%, 62.2%, 81.8% were all
    computed at various points) survives inspection.
    """
    results = {}
    for cn_term, expected_en in glossary.items():
        chapters_with_term = [c.index for c in cn_chapters if cn_term in c.content]
        if len(chapters_with_term) < 3:
            continue  # term appears too rarely to test consistency

        expected_lower = expected_en.lower()
        verbatim_hits = 0
        chapters_checked = 0
        for cn_ch in cn_chapters:
            if cn_ch.index not in chapters_with_term:
                continue
            body = en_chapters.get(cn_ch.index, "")
            if not body:
                continue
            chapters_checked += 1
            if expected_lower in body.lower():
                verbatim_hits += 1

        if verbatim_hits == chapters_checked and chapters_checked:
            status = "VERBATIM_ALL"
        elif verbatim_hits == 0:
            status = "VERBATIM_NONE"
        else:
            status = "VERBATIM_PARTIAL"

        results[cn_term] = {
            "status": status,
            "expected": expected_en,
            "chapters": chapters_with_term,
            "chapters_checked": chapters_checked,
            "verbatim_hits": verbatim_hits,
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
    issues = []        # hard defects — these determine the grade
    advisories = []    # needs human judgement — reported, never graded

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

    # 4. Term rendering location (verbatim-only; see _check_term_consistency for
    #    why no fuzzy-based "consistency rate" is reported)
    term_results = _check_term_consistency(cn_chapters, en_chapters, glossary)
    t_all = {k: v for k, v in term_results.items() if v["status"] == "VERBATIM_ALL"}
    t_partial = {k: v for k, v in term_results.items() if v["status"] == "VERBATIM_PARTIAL"}
    t_none = {k: v for k, v in term_results.items() if v["status"] == "VERBATIM_NONE"}
    tested = len(term_results)
    passed.append(
        f"术语逐字命中: {len(t_all)}/{tested} 条在所有出现章节均逐字命中"
    )
    if t_partial:
        advisories.append(
            f"术语部分命中: {len(t_partial)} 条（例："
            f"{', '.join(list(t_partial)[:5])}）— 逐字命中不完整，可能是合法改写、"
            f"压缩省略或译法漂移；现有匹配器无法区分，需人工抽查"
        )
    if t_none:
        advisories.append(
            f"术语从未逐字出现: {len(t_none)} 条（例："
            f"{', '.join(list(t_none)[:5])}）— 可能是词表译法未被采用，或术语被"
            f"合法压缩省略；需人工判断是过度压缩还是漏译"
        )

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

    # 6. Cold-read sampling — reported, but NEVER as a pass criterion.
    if quality:
        verdicts = [q.get("verdict", "?") for q in quality]
        n_pass = verdicts.count("PASS")
        n_fix = verdicts.count("NEEDS_FIX")
        coverage = 100 * len(quality) / len(cn_chapters) if cn_chapters else 0
        # These records are not quality evidence. See HANDOFF_20260921_audit_untrusted.md
        # §1.2: they were produced by the same model family that wrote the
        # translation (self-review), the sampled chapters took a *different*
        # production path than the other 97.4% (skip_readback=not is_sample), and
        # at a measured 0.46% defect rate a 59-chapter sample misses every defect
        # with probability 79%. A re-audit of 6 non-sampled chapters returned
        # 0/6 PASS. So "59/59 PASS" is the expected result of that sampling
        # design and carries no information.
        note = (
            f"冷读抽检: {len(quality)} 章 ({coverage:.1f}% 覆盖) — "
            f"{n_pass} PASS / {n_fix} NEEDS_FIX"
        )
        if n_fix == 0:
            note += "（全 PASS 不构成质量证据，见下方警告）"
            advisories.append(
                f"冷读样本不可作为质量结论: 覆盖率仅 {coverage:.1f}%，且抽样章与"
                f"其余章节走不同生产路径（抽样章才跑冷读+修复循环）。"
                f"全 PASS 是该抽样设计下的预期结果，不携带质量信息。"
                f"需改用全量零成本扫描（completeness/output_guard）作为门禁。"
            )
        passed.append(note)
    else:
        passed.append("冷读抽检: 未执行")

    # ── Grade ──
    # Grade on hard defects only. `advisories` are findings that need human
    # judgement (term absence may be legitimate compression; the cold-read
    # caveat is about the evidence, not the translation). Counting them as
    # defects would let a report grade C purely for being honest about its own
    # sampling limits.
    grade = "A+" if not issues else ("B" if len(issues) <= 3 else "C")

    report = {
        "book": cfg["name"],
        "genre": cfg["genre"],
        "grade": grade,
        "passed": passed,
        "issues": issues,
        "advisories": advisories,
        "_quality": quality,
        # Actual on-disk deliverables, so the customer-facing report lists only
        # formats that exist. The CLI runner never builds an EPUB.
        "_deliverable_md": str(md_path),
        "_deliverable_epub": str(epub_path) if (epub_path := _find_epub(out_dir)) else "",
        "summary": {
            "chapters_total": len(cn_chapters),
            "chapters_translated": len(en_chapters),
            "glossary_size": len(glossary),
            # No term_consistency_rate: see _check_term_consistency. Every
            # candidate rate computed so far (19.4%, 62.2%, 81.8%) depended on a
            # matcher signal that spot-checking proved to be noise. Only the
            # verbatim counts below are defensible.
            "terms_tested": tested,
            "terms_verbatim_all": len(t_all),
            "terms_verbatim_partial": len(t_partial),
            "terms_verbatim_none": len(t_none),
            "zh_en_ratio": round(avg_ratio, 2),
            "cold_read_samples": len(quality),
            "cold_read_coverage_pct": round(
                100 * len(quality) / len(cn_chapters), 2) if cn_chapters else 0.0,
            "cold_read_pass": sum(1 for q in quality if q.get("verdict") == "PASS"),
            "cold_read_needs_fix": sum(1 for q in quality if q.get("verdict") == "NEEDS_FIX"),
            "cold_read_is_quality_evidence": False,
        },
        "term_details": {
            "verbatim_all_count": len(t_all),
            "verbatim_partial_count": len(t_partial),
            "verbatim_none_count": len(t_none),
            # Partial hits are the actionable queue for human spot-checking,
            # biggest gaps first: a term that should appear in 300 chapters and
            # verbatim-hits 40 deserves attention before one that misses once.
            "partial_hit_terms": [
                {"cn_term": k, "expected": v["expected"],
                 "chapters_checked": v["chapters_checked"],
                 "verbatim_hits": v["verbatim_hits"],
                 "verbatim_misses": v["chapters_checked"] - v["verbatim_hits"]}
                for k, v in sorted(
                    t_partial.items(),
                    key=lambda kv: kv[1]["chapters_checked"] - kv[1]["verbatim_hits"],
                    reverse=True,
                )[:20]
            ],
            "verbatim_none_terms": [
                {"cn_term": k, "expected": v["expected"],
                 "chapters_with_term": len(v["chapters"])}
                for k, v in list(t_none.items())[:20]
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
    # Cold-read impressions are the most vivid material in this report, so they
    # stay prominent — but they are SAMPLED and were produced by the same model
    # family that wrote the translation. Presenting them as the headline proof of
    # quality is exactly the fake-green failure this project was audited for: the
    # sampled chapters took a different production path (they alone ran the cold
    # read + fix loop), and at a measured 0.46% defect rate a 59-chapter sample
    # misses every defect with 79% probability. Label them as samples.
    quality = report.get("_quality", [])
    if quality:
        coverage = s.get("cold_read_coverage_pct", 0)
        lines.append(
            f"## 冷读样本（{len(quality)} 章 / 覆盖 {coverage}%）"
        )
        lines.append("")
        lines.append(
            "> 说明：以下为抽样章节的目标语读者即时反应，**不构成全书质量结论**。"
            "抽样章与其余章节走不同生产路径（仅抽样章执行冷读与修复循环），"
            "且评审模型与写作模型同源。全书质量以下方「全量零成本指标」为准。"
        )
        lines.append("")
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

    # Deliverables must be listed from what actually exists on disk. The report
    # used to claim "Markdown + EPUB" unconditionally, but the CLI runner never
    # builds an EPUB (only the API path does, on demand) — a corpus with zero
    # .epub files was being handed to a customer as a two-format delivery.
    md_path_actual = report.get("_deliverable_md")
    epub_present = bool(report.get("_deliverable_epub"))
    format_bits = ["Markdown"] + (["EPUB"] if epub_present else [])

    lines += [
        "---",
        "",
        "## 交付内容",
        "",
        f"- 译稿：{s['chapters_translated']} 个章节块（{' + '.join(format_bits)}）",
        f"- 术语表：{s['glossary_size']} 条中英对照",
        "- 本报告",
        "",
    ]
    if not epub_present:
        lines += [
            "> 注：本次交付未包含 EPUB。EPUB 由 API 路径按需生成，",
            "> 命令行编译流程不产出该格式。",
            "",
        ]

    lines += [
        "---",
        "",
        "## 全量零成本指标（工程背书）",
        "",
        "以下指标对**全部章节**计算，不依赖抽样，也不需要调用模型：",
        "",
        f"- 术语逐字命中：{s['terms_verbatim_all']}/{s['terms_tested']} 条在所有出现章节均逐字命中",
        f"  - 部分命中：{s['terms_verbatim_partial']} 条；从未逐字出现：{s['terms_verbatim_none']} 条"
        "（均见下方「需人工判断项」）",
        "- 说明：不提供单一「术语一致率」。可靠的比率需逐句对齐原文与译文以判断"
        "每处实际译法，本项目尚无对齐器；现有模糊匹配经抽查证明会把不同角色、"
        "普通名词误判为译法变体，任何基于它的比率都是在拟合噪声。",
        f"- 中英字数比：{s['zh_en_ratio']}x（健康区间 2.0–4.0x）",
        f"- 章节完整度：{s['chapters_translated']}/{s['chapters_total']}"
        "（按章节标题解析计数，不等于内容已逐章质检）",
        "",
    ]
    if report["issues"]:
        lines.append("## 待改进项")
        lines.append("")
        for i in report["issues"]:
            lines.append(f"- {i}")
        lines.append("")
    if report.get("advisories"):
        lines.append("## 需人工判断项")
        lines.append("")
        lines.append(
            "以下发现不计入评级，但需要人来判断是设计允许的行为，还是真实缺陷："
        )
        lines.append("")
        for a in report["advisories"]:
            lines.append(f"- {a}")
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
    s = report["summary"]
    print(f"\nGRADE: {report['grade']} | "
          f"术语逐字命中: {s['terms_verbatim_all']}/{s['terms_tested']} | "
          f"字数比: {s['zh_en_ratio']}x")
    if report.get("advisories"):
        print(f"需人工判断项: {len(report['advisories'])} 项（见 _report.md）")
    print(f"报告已生成: {NOVELS[novel_key]['output_dir']}/_report.md")


if __name__ == "__main__":
    main()

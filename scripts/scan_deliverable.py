#!/usr/bin/env python3.11
"""Full-corpus deliverable scan — the zero-cost replacement for 2.6% sampling.

Why this exists
---------------
``novels/output/difu_segmented/_report.json`` claims
``chapters_translated: 2301`` and ``grade: B``, backed by ``cold_read_coverage_pct:
2.56`` — i.e. 59 chapters were actually read. The completeness check behind that
number counts *chapter headings*, not whether each chapter's body is intact. So
Chapter 1472, whose entire translation is still sealed inside an unpacked
``{"translated_text": "..."}`` envelope and cut off mid-sentence, counts as
delivered.

This script scans 100% of chapters with no LLM call. On the real 2301-chapter
corpus it takes ~2 s and finds what sampling could not.

Calibration (measured, not assumed)
-----------------------------------
Every check below was calibrated against ``difu_en.md`` before being promoted to
a defect. The lesson came from ``completeness.py``'s ``glossary_missing`` metric,
which looks like a defect signal and is not: the 11 known-truncated chapters had
8-24 missing terms, and so did all 8 healthy controls (5-16). Overlapping ranges
cannot separate defect from normal, so it is reported as a metric only.

Naive "does the chapter end with terminal punctuation" fails the same way and
worse: 81 / 2301 chapters (3.5%) look truncated, but ~95% of those end in a
markdown emphasis marker (``*`` / ``_``) or a closing quote. Stripping emphasis
and quote characters — but **not** em dashes, which legitimately end an
interrupted line of dialogue — brings it to 11 (0.48%), of which 9 are real
defects and 2 are legitimate (a poem's attribution line, a sexagenary date).

That residual 18% false-positive rate is why the output is a *triage list with
evidence*, not a pass/fail gate. A human reads 11 lines; nobody reads 81.

Checks
------
1. ``JSON_RESIDUE``     — model envelope leaked into prose (``{`` alone on a
                          line, ``"translated_text":``, a ```json fence)
2. ``TRUNCATED_BODY``   — body does not end on terminal punctuation after
                          markdown stripping
3. ``EMPTY_CHAPTER``    — heading with no body
4. ``META_TEXT_LEAK``   — LLM scaffolding in prose (``END OF CHAPTER``, ``As an
                          AI``, ``I cannot``)
5. ``CHINESE_RESIDUE``  — untranslated CJK in the body, via output_guard
6. ``CHAPTER_NUMBERING`` — EN/CN heading map is not monotonic, or one CN chapter
                          maps to several EN chapters
7. ``QUOTE_STYLE``（条款16）— 双引号风格偏离全书主导：同章混用两套，或整章
                          通篇用少数派
8. ``GLOSSARY_CONFLICTS``（条款16）— 术语表内部冲突：同一英文渲染对应互相矛盾
                          的中文源词（值含脏标记 / 同音形近字）

7 和 8 是**整份交付物**级别的判据，单独成节报告、不进逐章 findings：difu 实测
203 章引号偏离，混进逐章清单会把 1–6 那 17 条高价值分诊项冲掉。但两者都计入
``--strict`` 与结论行，口径与 6（章号异常）一致。

Zero cost: this script never imports an LLM client and makes no network call, so
it needs no cost gate (contrast the 20 bare scripts in HANDOFF round5 §四-C).

Usage
-----
    python3.11 scripts/scan_deliverable.py novels/output/difu_segmented/difu_en.md
    python3.11 scripts/scan_deliverable.py --all          # every *_en.md found
    python3.11 scripts/scan_deliverable.py FILE --json    # machine-readable
    python3.11 scripts/scan_deliverable.py FILE --strict  # exit 1 on any finding

Exit codes: 0 = scanned clean (or findings present without --strict),
1 = --strict and findings present, 2 = usage / file error.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.output_guard import (  # noqa: E402
    find_untranslated_chinese,
    json_residue_on_line,
    meta_text_on_line,
)

# ── Human-report layout ───────────────────────────────────────────────
# Named rather than inlined at each print site: two copies of the same width
# are two mutants that can never be killed (changing one still passes), and a
# layout constant is not behaviour worth testing in itself.
_RULE_WIDTH = 72          # width of the '=' separator rules
_CHECK_COL = 20           # column width for check-name fields
# 引号偏离章列表的显示上限。difu 实测通篇弯引号 183 章，全列出来会把逐章
# 缺陷明细挤出视野；照 _OFFSET_OUTLIER_LIMIT 的做法截断，总数仍然印全。
_QUOTE_LIST_LIMIT = 20

# ── Parsing ───────────────────────────────────────────────────────────
# Deliverable headings look like:  ## Chapter 2: 第1章 鬼节，鬼敲门
# Chapter 0 is a prologue, so the EN series legitimately has no Chapter 1.
_HEADING = re.compile(r"^## Chapter (\d+):\s*(.*)$")
_CN_CHAPTER = re.compile(r"第(\d+)章")
_HRULE = "---"


def _split_chapters(text: str) -> list[dict]:
    """Return [{en, cn, title, start_line, body}] in file order.

    ``body`` is a list of ``(line_number, text)`` pairs with blanks and ``---``
    rules dropped. Keeping the original line number matters: an earlier version
    stored bare strings and reconstructed positions with ``enumerate`` offsets,
    which silently mis-numbered every finding by however many blank lines the
    chapter contained (Chapter 366's JSON residue reported as line 31698 when it
    is actually 31767). Line numbers are the primary evidence in a triage list,
    so they must point at the real line.
    """
    lines = text.split("\n")
    heads = []
    for i, line in enumerate(lines):
        m = _HEADING.match(line)
        if m:
            title = m.group(2).strip()
            cn_m = _CN_CHAPTER.search(title)
            heads.append({
                "en": int(m.group(1)),
                "cn": int(cn_m.group(1)) if cn_m else None,
                "title": title,
                "start_line": i + 1,          # 1-based heading line
                "_idx": i,                    # 0-based, for slicing
            })
    chapters = []
    for k, head in enumerate(heads):
        end = heads[k + 1]["_idx"] if k + 1 < len(heads) else len(lines)
        body = [(i + 1, s.strip())
                for i in range(head["_idx"] + 1, end)
                for s in (lines[i],) if s.strip() and s.strip() != _HRULE]
        chapters.append({**head, "body": body})
    return chapters


# ── Check 1: JSON residue ───────────────────────────────────────────────
# The judgement lives in output_guard.json_residue_on_line so the production
# WRITE guard and this scanner share one rule and cannot drift (§八's repeated
# "two copies of one rule diverge" failure). This function only adds the
# line-number / evidence packaging the corpus scan needs.
def check_json_residue(body: list[tuple[int, str]]) -> list[dict]:
    """A bare ``{``/``}`` line or a JSON key means the model's envelope was
    written to the deliverable instead of being unpacked.

    Measured on difu_en.md: Chapter 366 ends with a ```json fence and a lone
    ``{``; Chapter 1472's whole translation sits inside ``{"translated_text":
    "..."}`` (a 4,705-char single line) and is cut off mid-sentence.
    """
    findings = []
    for line_no, line in body:
        hit = json_residue_on_line(line)
        if hit:
            findings.append({
                "check": "JSON_RESIDUE",
                "line": line_no,
                "evidence": line.strip()[:120],
                "detail": hit,
            })
    return findings


# ── Check 2/3: body completeness ────────────────────────────────────────
# Terminal punctuation that legitimately ends a chapter. Em/en dashes are
# INCLUDED: an interrupted line of dialogue is a normal web-novel chapter end
# ("—save my child—"). Excluding them inflated the count from 11 to 19.
#
# This MUST be a tuple: ``str.endswith("abc")`` tests for the whole literal
# suffix, while ``str.endswith(("a","b","c"))`` tests for any of them. Written
# as a plain string this check flagged all 2301 chapters — caught by running
# against the real corpus, which the calibration pass (which did use a tuple)
# could not have caught.
_TERMINAL = tuple(".!?\u2026\u2014\u2013-")

# Markdown emphasis and quote characters to strip before testing the ending.
# Deliberately excludes dashes (see above) — stripping them first is what made
# the earlier 19-chapter result wrong.
_MD_TAIL = re.compile(r"[\*_\u201c\u201d\u2018\u2019\"')\]\s]+$")


def check_body_completeness(body: list[tuple[int, str]],
                            heading_line: int) -> dict | None:
    """EMPTY_CHAPTER if there is no body, else TRUNCATED_BODY if the last line
    does not end on terminal punctuation after markdown stripping.

    Calibrated: 11 / 2301 flagged on the real corpus, 9 genuine (0.39% FP).
    The two legitimate hits are a poem attribution ("— Su Shi, *Butterflies in
    Love with Flowers: Spring Scene") and a sexagenary date line. Both are
    reported with evidence for a human to dismiss in seconds.

    The finding points at the offending body line, not the heading: that is
    where a reader has to look.
    """
    if not body:
        return {"check": "EMPTY_CHAPTER", "line": heading_line,
                "evidence": "", "detail": "heading has no body"}
    last_no, last_text = body[-1]
    stripped = _MD_TAIL.sub("", last_text)
    if not stripped.endswith(_TERMINAL):
        return {"check": "TRUNCATED_BODY", "line": last_no,
                "evidence": stripped[-90:],
                "detail": "body does not end on terminal punctuation"}
    return None


# ── Check 4: meta-text leak ─────────────────────────────────────────────
# Judgement lives in output_guard.meta_text_on_line (single source, shared with
# the production WRITE guard). This only adds line-number / evidence packaging.
def check_meta_text(body: list[tuple[int, str]]) -> list[dict]:
    findings = []
    for line_no, line in body:
        label = meta_text_on_line(line)
        if label:
            findings.append({
                "check": "META_TEXT_LEAK", "line": line_no,
                "evidence": line.strip()[:120], "detail": label,
            })
    return findings


# ── Check 5: Chinese residue ────────────────────────────────────────────
def check_chinese_residue(body: list[tuple[int, str]]) -> list[dict]:
    """Untranslated CJK in the delivered English body.

    Reuses output_guard.find_untranslated_chinese rather than re-deriving the
    pattern. That helper matches single characters (``[一-鿿]``), not words, so
    the count below is a character count — 卧槽 reads as 2.

    NOTE: chapter *titles* legitimately carry the Chinese original
    ("## Chapter 2: 第1章 鬼节"), so only the body is scanned.
    """
    findings = []
    for line_no, line in body:
        found = find_untranslated_chinese(line)
        if found:
            findings.append({
                "check": "CHINESE_RESIDUE", "line": line_no,
                "evidence": line.strip()[:120],
                "detail": (f"{len(found)} CJK char(s): "
                           f"{''.join(sorted(found)[:8])}"),
            })
    return findings


# ── Check 6: chapter numbering ──────────────────────────────────────────
# An offset that departs from the dominant one by more than this is reported as
# an anomaly. Calibrated on difu_en.md, where the offsets are 2 (1772), 3 (310),
# 1 (155), 4 (55) — all within ±2 of the dominant 2 and all legitimate drift from
# the prologue convention — and the genuine anomalies sit at 302, 362 and 502.
# A tolerance of 20 separates those cleanly; 2 would report the 465 normal
# chapters as defects and 600 would miss nothing real but say nothing either.
_OFFSET_OUTLIER_TOLERANCE = 20

# Report bounds. The distributions are diagnostics for a human, so they are
# capped rather than dumped: the full offset table on a 2301-chapter book is
# noise next to the 5 outliers that matter.
_OFFSET_DISTRIBUTION_TOP = 8
_OFFSET_OUTLIER_LIMIT = 20


def check_numbering(chapters: list[dict]) -> dict:
    """EN/CN heading map must be monotonic and one-to-one.

    Measured on difu_en.md (2301 chapters):
      - offset distribution: 2→1772, 3→310, 1→155, 4→55, then stragglers
      - EN numbers have 0 duplicates and exactly one gap (Chapter 1, because
        Chapter 0 is the prologue and the body starts at 2) — legitimate
      - 5 CN chapters map to two different EN chapters each:
        1046→[1048,1408] 1047→[1049,1409] 1048→[1050,1410]
        1093→[1095,1395] 1227→[1229,1729]
        These are mapping errors, not duplicated content.

    So the dominant offset is not itself a defect — a *break* in it is. This
    check reports the distribution and the exceptions rather than flagging the
    normal case 2,292 times.
    """
    en_nums = [c["en"] for c in chapters]
    paired = [(c["en"], c["cn"]) for c in chapters if c["cn"] is not None]

    offsets = Counter(en - cn for en, cn in paired)
    # Written without an argument to most_common(): ``most_common(1)[0]`` and
    # ``most_common(2)[0]`` are the same value, so that literal was a permanent
    # equivalent mutant. Ranking the whole counter costs nothing at these
    # cardinalities (7 distinct offsets on a 2301-chapter book).
    ranked = offsets.most_common()
    if ranked:
        dominant_offset, dominant_count = ranked[0]
    else:
        dominant_offset, dominant_count = None, 0

    dup_en = [n for n, k in Counter(en_nums).items() if k > 1]
    gaps = sorted(set(range(min(en_nums), max(en_nums) + 1)) - set(en_nums)) \
        if en_nums else []

    cn_to_en = defaultdict(list)
    for en, cn in paired:
        cn_to_en[cn].append(en)
    ambiguous_cn = {cn: sorted(ens) for cn, ens in cn_to_en.items() if len(ens) > 1}

    # Chapters whose offset departs from the dominant one. The dominant offset
    # is the prologue convention (2 for 1772 of 2301 chapters on difu_en.md), so
    # *departure* is the signal, not the offset itself — otherwise this would
    # report 1772 non-defects.
    #
    # No `or 0` fallback here: dominant_offset is None exactly when no heading
    # carries a CN number, which also means `paired` is empty, so the old
    # `(dominant_offset or 0)` was unreachable — proved before deleting, per the
    # rule that dead code gets eliminated rather than exempted.
    offset_outliers: list[dict] = []
    if dominant_offset is not None:
        offset_outliers = [
            {"en": en, "cn": cn, "offset": en - cn}
            for en, cn in paired
            if abs((en - cn) - dominant_offset) > _OFFSET_OUTLIER_TOLERANCE
        ]

    no_cn_title = [c["en"] for c in chapters if c["cn"] is None]

    return {
        "chapters_total": len(chapters),
        "en_range": [min(en_nums), max(en_nums)] if en_nums else [],
        "dominant_offset": dominant_offset,
        "dominant_offset_count": dominant_count,
        "offset_distribution": dict(offsets.most_common(_OFFSET_DISTRIBUTION_TOP)),
        "duplicate_en_chapters": sorted(dup_en),
        "en_numbering_gaps": gaps,
        "ambiguous_cn_to_en": ambiguous_cn,
        "offset_outliers": offset_outliers[:_OFFSET_OUTLIER_LIMIT],
        "headings_without_cn_number": no_cn_title,
    }


# ── Check 7: 引号风格一致性（条款16）───────────────────────────────────
# 实测标定（difu_en.md，2301 章）：
#   直双引号 "     79,399 次
#   弯双引号 “ ”    4,010 / 3,995 次   → 直引号占 90.8%，是全书主导
#   同章混用两套     20 章
#   通篇只用弯引号  183 章
#   合计偏离主导    203 章（8.8%），分散在 171 个连续段、最长段仅 4 章
#                   → 散点式，不是某个时段/模型的配置问题
#
# ⚠️ 混用章数按**正文**算是 20，不是 22。差的 Ch157/Ch1345 弯引号只出现在
# 标题行（中文原标题："第一节是语文课，“知耻而后勇”的陈大计…"、
# "第1343章 “鬼”的警告"），正文全是直引号。标题携带中文原文是合法的
# （同 check_chinese_residue 的既有口径），扫标题会把这两章误报成不一致。
#
# 判据形态照 check_numbering 的 dominant_offset：**主导风格本身不是缺陷，
# 偏离主导才是**。否则会把 2,098 章正常章节报成缺陷。
#
# ⚠️ 只判**双**引号。弯单引号 ’ (U+2019) 实测 3,089 次，绝大多数是英文撇号
# （woman's / aren't / wasn't），纳入判据会大规模误报。这是 round9 §八
# "裸判据 95% 误报"的同类陷阱，实测后排除。
_QUOTE_STRAIGHT = '"'
_QUOTE_CURLY = ("\u201c", "\u201d")      # “ ”


def dominant_quote_style(chapters: list[dict]) -> str:
    """全书主导的双引号风格："straight" 或 "curly"。

    并列或全书无引号时返回 "straight"——此时不会有任何章被判偏离。
    """
    straight = curly = 0
    for ch in chapters:
        for _, line in ch["body"]:
            straight += line.count(_QUOTE_STRAIGHT)
            curly += sum(line.count(q) for q in _QUOTE_CURLY)
    return "curly" if curly > straight else "straight"


def check_quote_style(chapters: list[dict], dominant: str) -> dict:
    """全书双引号风格一致性汇总（条款16）。

    照 check_numbering 的形态单独成节，不进逐章 findings：difu 实测有 205 章
    偏离主导，塞进分诊清单会把条款14 那 12 条高价值缺陷冲掉；而且"把弯引号
    统一成直引号"是一次性机械修，不需要逐章人工判读。
    """
    mixed: list[dict] = []      # 同章混用两套
    off_dominant: list[int] = []  # 通篇用少数派
    for ch in chapters:
        body = ch["body"]
        straight = sum(line.count(_QUOTE_STRAIGHT) for _, line in body)
        curly = sum(sum(line.count(q) for q in _QUOTE_CURLY) for _, line in body)
        minority = curly if dominant == "straight" else straight
        if not minority:
            continue
        first_line = next(
            (line_no for line_no, line in body
             if any(q in line for q in (_QUOTE_CURLY if dominant == "straight"
                                        else (_QUOTE_STRAIGHT,)))),
            body[0][0] if body else 0,
        )
        entry = {"en": ch["en"], "line": first_line,
                 "straight": straight, "curly": curly}
        if straight and curly:
            mixed.append(entry)
        else:
            off_dominant.append(ch["en"])
    return {
        "dominant": dominant,
        "chapters_total": len(chapters),
        "mixed_chapters": mixed,
        "off_dominant_chapters": off_dominant,
        # 缺陷数 = 混用章 + 通篇少数派章。主导风格自身的章节不算。
        "defects": len(mixed) + len(off_dominant),
    }


# ── Check 8: 术语表内部冲突（条款16）───────────────────────────────────
# 实测标定（difu _glossary.json，1467 条）：同一英文渲染对应多个中文源词
# 共 29 组，实测分成三类，**只有前两类是缺陷**：
#
#   A 值含脏标记（斜杠/括号）3 组 —— 术语表自身脏数据：
#       'Dog Ni'            <- ['犬封恶鬼/犬尼', '犬尼']
#       'County Chief Hong' <- ['洪贤长', '洪贤（避嫌，通县）长']
#       'Young General'     <- ['少将军', '少将军 (addressing Chen Daji)', ...]
#   B 等长且恰好一个字不同 10 组 —— 同音字/形近字，必有一个是错的：
#       '白无畏'/'白无谓'、'常怀元'/'常怀远'、'法庆'/'法清'、
#       '空禅'/'空蝉'、'幽荧'/'幽莹'、'女妭'/'女魃' …
#   C 其余 16 组是**合法变体**（简称/全称、敬称、限定语），不报：
#       '容慧'/'容慧禅师'、'鬼母'/'鬼母邪神'、'常二爷'/'常家二爷'、
#       '大将军庙'/'将军庙' …
#
# C 类与 B 类判据不重叠：实测 16 组无一满足"等长且仅一字不同"（最短的
# '灰家仙'/'灰老六'、'绝圣女帝'/'绝圣帝君' 都差 2 个字）。故 A+B 合计
# 13 组全部为真缺陷、零误报。C 类作为度量另报，供人工核对而不判缺陷。
#
# ⚠️ 这里不做"术语在源文出现却没在译文出现"的判据：completeness.py:187-194
# 已实测否决该判据（11 个截断章缺 8-24 术语、8 个健康对照缺 5-16，区间重叠，
# WRITE 本就有权压缩次要提及），重造会得到同一个不可分离的信号。
_GLOSSARY_DIRTY = re.compile(r"[/()（）]")


def check_glossary_conflicts(glossary: dict) -> dict:
    """术语表内部一致性：同一英文渲染不得对应互相矛盾的中文源词。"""
    by_en: dict[str, set] = defaultdict(set)
    for cn, en in glossary.items():
        by_en[en].add(cn)

    conflicts: list[dict] = []
    variants_only: list[dict] = []
    for en, cns in sorted(by_en.items()):
        if len(cns) < 2:
            continue
        forms = sorted(cns)
        dirty = [c for c in forms if _GLOSSARY_DIRTY.search(c)]
        if dirty:
            conflicts.append({
                "kind": "DIRTY_TERM", "en": en, "variants": forms,
                "detail": f"值含脏标记（斜杠/括号）: {dirty}",
            })
            continue
        near = [(a, b) for a, b in itertools.combinations(forms, 2)
                if len(a) == len(b)
                and sum(1 for x, y in zip(a, b) if x != y) == 1]
        if near:
            conflicts.append({
                "kind": "HOMOPHONE_CLASH", "en": en, "variants": forms,
                "detail": "等长且仅一字不同（同音/形近字）: "
                          + ", ".join(f"{a}/{b}" for a, b in near),
            })
            continue
        # 剩下的都是简称/全称/敬称变体：合法，只报出来供人工核对。
        variants_only.append({"en": en, "variants": forms})

    return {
        "terms_total": len(glossary),
        "one_en_many_cn": sum(1 for cns in by_en.values() if len(cns) > 1),
        "conflicts": conflicts,
        "legitimate_variants": variants_only,
    }


# ── Driver ──────────────────────────────────────────────────────────────
def scan_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    chapters = _split_chapters(text)
    findings: list[dict] = []

    # 主导引号风格是**全书**属性，必须先算一次再比对——不能在章循环里逐章
    # 各自判断，否则每章都自认主导、永远查不出混用。
    quote_dominant = dominant_quote_style(chapters)

    for ch in chapters:
        body, heading_line, en = ch["body"], ch["start_line"], ch["en"]

        # Attribution happens here, not by re-deriving it from line numbers
        # afterwards: the loop already knows which chapter owns each finding,
        # and a bounds walk can only be right by accident of ordering.
        chapter_findings: list[dict] = []
        chapter_findings.extend(check_json_residue(body))
        incomplete = check_body_completeness(body, heading_line)
        if incomplete:
            chapter_findings.append(incomplete)
        chapter_findings.extend(check_meta_text(body))
        chapter_findings.extend(check_chinese_residue(body))
        for finding in chapter_findings:
            finding["chapter"] = en
        findings.extend(chapter_findings)

    by_check = Counter(f["check"] for f in findings)
    numbering = check_numbering(chapters)
    quotes = check_quote_style(chapters, quote_dominant)

    # Numbering anomalies are defects too, surfaced separately from the
    # per-chapter list so the counts stay readable.
    numbering_defects = (
        len(numbering["duplicate_en_chapters"])
        + len(numbering["ambiguous_cn_to_en"])
        + len(numbering["offset_outliers"])
    )

    # 术语表是交付物同目录的 _glossary.json。缺失或损坏时给 terms_total=0
    # 而不是抛错：不是每本交付物都带术语表，"没有表"不等于"扫描失败"。
    glossary_report = check_glossary_conflicts(_load_glossary(path))

    return {
        "file": str(path),
        "lines": text.count("\n") + 1,
        "chapters_total": len(chapters),
        "chapters_with_findings": len({f["chapter"] for f in findings}),
        "findings_by_check": dict(by_check),
        "findings_total": len(findings),
        "numbering_defects": numbering_defects,
        "numbering": numbering,
        "quotes": quotes,
        "glossary": glossary_report,
        "findings": sorted(findings, key=lambda f: f["line"]),
    }


def _load_glossary(deliverable: Path) -> dict:
    """读取交付物同目录的 _glossary.json；缺失/损坏/非映射 → {}。"""
    path = deliverable.parent / "_glossary.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _print_human(report: dict) -> None:
    n = report["numbering"]
    print(f"\n{'=' * _RULE_WIDTH}")
    print(f"扫描: {report['file']}")
    print(f"  {report['lines']} 行 / {report['chapters_total']} 章")
    print(f"  受影响章节: {report['chapters_with_findings']}")
    print(f"{'=' * _RULE_WIDTH}")

    if report["findings_by_check"]:
        print("\n按检查项:")
        for check, count in sorted(report["findings_by_check"].items()):
            print(f"  {check:<{_CHECK_COL}} {count}")
    else:
        print("\n  ✓ 无逐章缺陷")

    print("\n章号映射:")
    print(f"  主导偏移 EN-CN = {n['dominant_offset']}"
          f"（{n['dominant_offset_count']}/{n['chapters_total']} 章，属正常）")
    print(f"  分布: {n['offset_distribution']}")
    if n["duplicate_en_chapters"]:
        print(f"  ✗ 重复 EN 章号: {n['duplicate_en_chapters']}")
    if n["en_numbering_gaps"]:
        print(f"  ⚠ EN 章号缺口: {n['en_numbering_gaps']}"
              f"（Chapter 0 为楔子时缺 1 属正常）")
    if n["ambiguous_cn_to_en"]:
        print(f"  ✗ 一个中文章号映射到多个英文章号: {n['ambiguous_cn_to_en']}")
    if n["offset_outliers"]:
        print(f"  ⚠ 偏移异常章（前 {len(n['offset_outliers'])}）: "
              f"{[o['en'] for o in n['offset_outliers']]}")
    if n["headings_without_cn_number"]:
        print(f"  ⚠ 标题无中文章号: {n['headings_without_cn_number']}")

    _print_quotes(report["quotes"])
    _print_glossary(report["glossary"])

    if report["findings"]:
        print(f"\n逐章缺陷明细（{report['findings_total']} 条）:")
        for f in report["findings"]:
            # No `else "Ch?"` fallback: scan_file attributes every finding to a
            # chapter inside the loop, so an unattributed one is impossible.
            # Proved by mutation before deletion (the branch was unreachable and
            # the surviving mutant was permanent noise).
            print(f"  Ch{f['chapter']:<6} 行 {f['line']:<8} "
                  f"{f['check']:<{_CHECK_COL}} {f['detail']}")
            if f["evidence"]:
                print(f"           └ {f['evidence']!r}")

    g = report["glossary"]
    print(f"\n结论: {report['findings_total']} 条逐章缺陷 "
          f"+ {report['numbering_defects']} 条章号异常 "
          f"+ {report['quotes']['defects']} 章引号风格偏离 "
          f"+ {len(g['conflicts'])} 组术语表冲突")
    print("  这是**分诊清单**而非通过/失败门禁——TRUNCATED_BODY 经标定"
          "仍有 ~18% 误报（诗词署名、干支纪年），需人工判读。")


def _print_quotes(q: dict) -> None:
    """引号风格节。长列表按 _QUOTE_LIST_LIMIT 截断（183 章全列会淹没其余缺陷）。"""
    label = "直" if q["dominant"] == "straight" else "弯"
    print("\n引号风格（条款16）:")
    print(f"  主导: {label}双引号"
          f"（{q['chapters_total'] - q['defects']}/{q['chapters_total']} 章一致，属正常）")
    if q["mixed_chapters"]:
        shown = q["mixed_chapters"][:_QUOTE_LIST_LIMIT]
        print(f"  ✗ 同章混用两套引号: {len(q['mixed_chapters'])} 章"
              f"（前 {len(shown)}）: {[x['en'] for x in shown]}")
    if q["off_dominant_chapters"]:
        shown = q["off_dominant_chapters"][:_QUOTE_LIST_LIMIT]
        print(f"  ✗ 通篇用少数派引号: {len(q['off_dominant_chapters'])} 章"
              f"（前 {len(shown)}）: {shown}")
    if not q["defects"]:
        print("  ✓ 全书引号风格一致")


def _print_glossary(g: dict) -> None:
    """术语表一致性节。只判真冲突；合法变体报数量供人工核对。"""
    print("\n术语表一致性（条款16）:")
    if not g["terms_total"]:
        print("  （同目录无 _glossary.json，跳过）")
        return
    print(f"  条目 {g['terms_total']} | 同一英文对应多个中文 {g['one_en_many_cn']} 组")
    for c in g["conflicts"]:
        kind = "脏标记" if c["kind"] == "DIRTY_TERM" else "同音/形近字"
        print(f"  ✗ [{kind}] {c['en']} <- {c['variants']}")
    if g["legitimate_variants"]:
        print(f"  ⚠ 简称/全称/敬称变体 {len(g['legitimate_variants'])} 组"
              f"（合法，不判缺陷，供人工核对）")
    if not g["conflicts"]:
        print("  ✓ 无内部冲突")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Full-corpus deliverable scan (zero cost, no LLM call).")
    parser.add_argument("paths", nargs="*", help="deliverable .md file(s)")
    parser.add_argument("--all", action="store_true",
                        help="scan every novels/output/*/*_en.md")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 when any finding is present")
    args = parser.parse_args(argv)

    targets: list[Path] = []
    if args.all:
        targets = sorted((ROOT / "novels" / "output").glob("*/*_en.md"))
    for p in args.paths:
        targets.append(Path(p) if Path(p).is_absolute() else ROOT / p)

    if not targets:
        parser.print_usage()
        print("\nerror: no target file given (pass a path or --all)",
              file=sys.stderr)
        return 2

    reports = []
    for target in targets:
        if not target.exists():
            print(f"error: not found: {target}", file=sys.stderr)
            return 2
        if not target.is_file():
            print(f"error: not a file: {target}", file=sys.stderr)
            return 2
        reports.append(scan_file(target))

    if args.json:
        print(json.dumps(reports[0] if len(reports) == 1 else reports,
                         ensure_ascii=False, indent=2))
    else:
        for report in reports:
            _print_human(report)

    if args.strict:
        # 全书级缺陷计数（章号异常 / 引号风格偏离 / 术语表冲突）与逐章
        # findings 一并算脏。numbering_defects 本来就在里面，另两项是同类的
        # "整份交付物的缺陷计数、单独成节"，漏掉它们会让 --strict 自相矛盾：
        # 报告印着 203 章引号偏离，退出码却是 0。
        dirty = [r for r in reports
                 if (r["findings_total"]
                     or r["numbering_defects"]
                     or r["quotes"]["defects"]
                     or r["glossary"]["conflicts"])]
        return 1 if dirty else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

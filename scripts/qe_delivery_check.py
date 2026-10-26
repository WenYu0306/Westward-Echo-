#!/usr/bin/env python3.11
"""Delivery-time quality guard for the READ tier downshift.

Why this exists
---------------
READ moved from qwen3.8-max to qwen3.8-flash on 2026-10-26 on the strength of a
6-chapter A/B: −76.5% cost, −22.7% time, MetricX-24 delta −0.389 with a 95% CI
of [−1.175, +0.347]. That CI crossing zero means the experiment did **not**
prove equivalence — it only failed to prove the expensive tier is better. The
downshift is therefore conditional, and this script is the condition.

It scores an already-delivered book with MetricX-24 (reference-free, lower is
better, zero API cost) on a deterministic chapter sample and compares, segment
by segment, against a committed baseline captured from the pre-downshift
delivery. Same chapters and same segment split on both sides, so source
difficulty cancels out.

Verdicts
--------
``ok``           point delta within ``--max-delta`` (default +0.5 points).
``inconclusive`` worse by more than the threshold, but the bootstrap CI still
                 includes 0 — not enough segments to call it degradation.
``worse``        worse by more than the threshold with a CI excluding 0.
                 Exit code 2: roll back READ to qwen3.8-max and re-check.
``no_overlap``   no (chapter, segment) pair shared with the baseline.
``insufficient`` only one paired segment — cannot compute a CI.

A guard that could not compare anything exits 3, never 0: a truncated MetricX
output, a changed chapter split, or stale cached scores must not read as a pass.

Usage::

    # one-time, from the pre-downshift delivery
    python3.11 scripts/qe_delivery_check.py --book difu --write-baseline

    # after re-compiling a book
    python3.11 scripts/qe_delivery_check.py --book difu

    # re-score an existing run without paying for inference again
    python3.11 scripts/qe_delivery_check.py --book difu --aggregate-only

Requires the MetricX checkout described in ``scripts/qe_metricx.py``.

【中文·这个脚本干什么】
READ 降档后的交付护栏：对已交付的书抽固定几章，用 MetricX-24 本地打分
（零 API 费用），逐段和"降档前那版"的基线比。只有"明显更差且统计显著"
才判 worse（退出码 2），此时应把 READ 改回 qwen3.8-max。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import qe_metricx  # noqa: E402
from scripts.run_novel import NOVELS  # noqa: E402

_CHAPTER_RE = re.compile(r"^#{1,2}\s+Chapter\s+(\d+):?\s*(.*)$", re.IGNORECASE)

ROOT = Path(__file__).resolve().parent.parent
BASELINE_DIR = ROOT / "archive" / "qe_baseline"
WORSE_EXIT_CODE = 2
# Could not produce a comparison at all (truncated MetricX output, no shared
# segments). Distinct from 2: "worse" is a verdict, this is a broken guard.
UNUSABLE_EXIT_CODE = 3


def parse_en_chapters(md_text: str) -> tuple[dict[int, str], Counter]:
    """Parse ``## Chapter N: title`` blocks, plus a count of each heading.

    The count matters: 《无限恐怖》 has two chapters numbered 301–308, and a
    naive dict merge would score two chapters concatenated against one source
    chapter. Duplicated numbers are excluded from the sample by the caller.
    """
    bodies: dict[int, list[str]] = {}
    counts: Counter = Counter()
    current: int | None = None
    for line in md_text.split("\n"):
        match = _CHAPTER_RE.match(line.strip())
        if match:
            current = int(match.group(1))
            counts[current] += 1
            bodies.setdefault(current, [])
        elif current is not None:
            bodies[current].append(line)
    return {k: "\n".join(v).strip() for k, v in bodies.items()}, counts


def sample_chapters(available: set[int], n: int) -> list[int]:
    """Evenly spaced across the book, deterministic, always incl. first/last."""
    ordered = sorted(available)
    if n <= 0 or not ordered:
        return []
    if n >= len(ordered):
        return ordered
    if n == 1:
        return [ordered[len(ordered) // 2]]
    picks = [round(i * (len(ordered) - 1) / (n - 1)) for i in range(n)]
    return [ordered[i] for i in picks]


def scores_by_chapter(rows: list[dict]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for row in rows:
        out.setdefault(str(row["ch"]), {})[str(row["seg"])] = row["prediction"]
    return out


def compare_to_baseline(scored_rows: list[dict], baseline: dict,
                        max_delta: float) -> dict:
    """Paired ``current − baseline`` deltas; positive means the new output is worse.

    ``scored_rows`` must be MetricX *output* rows (they carry ``prediction``).
    Passing the input rows built by :func:`build_rows` raises instead of
    KeyError-ing, because that mistake once made the guard untestable-by-its-own-
    tests: the unit tests supplied their own rows and the real call site was
    never exercised.
    """
    base_scores = baseline.get("scores", {})
    diffs: list[float] = []
    per_chapter: dict[int, list[float]] = {}
    unmatched = 0
    for row in scored_rows:
        if "prediction" not in row:
            raise ValueError(
                "compare_to_baseline needs MetricX output rows (with "
                "'prediction'), not pipeline input rows")
        base = base_scores.get(str(row["ch"]), {}).get(str(row["seg"]))
        if base is None:
            unmatched += 1
            continue
        delta = row["prediction"] - base
        diffs.append(delta)
        per_chapter.setdefault(row["ch"], []).append(delta)

    raw_delta = statistics.mean(diffs) if diffs else None
    delta = round(raw_delta, 3) if raw_delta is not None else None
    ci = qe_metricx.bootstrap_ci(diffs)
    # Compare the unrounded mean: rounding first would let a true 0.5004 slip
    # under a 0.5 threshold.
    if raw_delta is None:
        verdict = "no_overlap"
    elif raw_delta <= max_delta:
        verdict = "ok"
    elif len(diffs) < 2:
        verdict = "insufficient"
    elif ci is not None and ci[0] > 0:
        verdict = "worse"
    else:
        verdict = "inconclusive"

    return {
        "verdict": verdict,
        "max_delta": max_delta,
        "mean_delta_new_minus_baseline": delta,
        "bootstrap_95ci": ci,
        "paired_segments": len(diffs),
        "unmatched_segments": unmatched,
        "per_chapter_delta": {
            str(ch): round(statistics.mean(v), 3)
            for ch, v in sorted(per_chapter.items())
        },
        "baseline_created_utc": baseline.get("created_utc"),
        "baseline_note": baseline.get("note"),
    }


def _load_book(book: str):
    cfg = NOVELS[book]
    sources = qe_metricx._load_sources(book)
    md_path = Path(cfg["output_dir"]) / cfg["output_file"]
    if not md_path.exists():
        raise SystemExit(f"delivered markdown not found: {md_path}")
    translations, counts = parse_en_chapters(md_path.read_text(encoding="utf-8"))
    if not translations:
        raise SystemExit(f"no '## Chapter N' headings in {md_path}")
    return cfg, sources, translations, counts


def build_rows(sources: dict[int, str], translations: dict[int, str],
               chapters: list[int], segments: int) -> list[dict]:
    items = [{"ch": ch, "ok": True, "text": translations[ch]} for ch in chapters]
    return qe_metricx.prepare_rows({"A": items}, sources, segments)


def _print_report(summary: dict, comparison: dict | None, chapters: list[int]) -> None:
    print("\nMetricX-24 delivery check (0–25, LOWER IS BETTER)")
    print(f"  chapters: {chapters}")
    print(f"  mean {summary['mean']['A']}  median {summary['median']['A']}  "
          f"segments {summary['segments']['A']}")
    if comparison is None:
        return
    print(f"  delta vs baseline: {comparison['mean_delta_new_minus_baseline']}  "
          f"95% CI {comparison['bootstrap_95ci']}  "
          f"(paired {comparison['paired_segments']})")
    for ch, delta in comparison["per_chapter_delta"].items():
        print(f"    ch{ch}: {delta:+.3f}")
    # An incomplete comparison must not print a reassuring "OK": main() exits 3
    # for it, and the report has to say the same thing.
    if comparison["unmatched_segments"] or comparison["paired_segments"] < 2:
        print(f"  verdict: UNUSABLE — {comparison['unmatched_segments']} "
              f"unmatched, {comparison['paired_segments']} paired")
    else:
        print(f"  verdict: {comparison['verdict'].upper()}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--book", required=True, choices=sorted(NOVELS))
    parser.add_argument("--chapters", type=int, default=6,
                        help="how many chapters to sample (evenly spaced)")
    parser.add_argument("--segments", type=int, default=6,
                        help="segments per chapter handed to MetricX")
    parser.add_argument("--baseline", type=Path, default=None,
                        help="baseline JSON (default archive/qe_baseline/<book>.json)")
    parser.add_argument("--work-dir", type=Path, default=None,
                        help="where qe_input/qe_output land (default: temp under baseline dir)")
    parser.add_argument("--write-baseline", action="store_true",
                        help="overwrite the baseline with this run's scores")
    parser.add_argument("--aggregate-only", action="store_true",
                        help="reuse an existing qe_output.jsonl in the work dir")
    parser.add_argument("--max-delta", type=float, default=0.5,
                        help="tolerated mean degradation in MetricX points")
    parser.add_argument("--note", default="",
                        help="provenance note stored in the baseline (e.g. which "
                             "model compiled the delivered book)")
    args = parser.parse_args(argv)

    if args.aggregate_only and args.write_baseline:
        raise SystemExit(
            "--aggregate-only reuses a previous run's scores, so it must not "
            "overwrite a baseline. Drop --aggregate-only to re-score.")

    baseline_path = args.baseline or BASELINE_DIR / f"{args.book}.json"
    work_dir = args.work_dir or BASELINE_DIR / "_work" / args.book
    work_dir.mkdir(parents=True, exist_ok=True)
    output_path = work_dir / "qe_output.jsonl"
    meta_path = work_dir / "qe_run_meta.json"

    if not args.aggregate_only and not args.write_baseline and not baseline_path.exists():
        raise SystemExit(
            f"no baseline at {baseline_path}. Capture one from a known-good "
            f"delivery first: --write-baseline (or pass --baseline).")

    cfg, sources, translations, counts = _load_book(args.book)
    unique = {ch for ch, n in counts.items() if n == 1}
    usable = sorted(unique & set(sources) & set(translations))
    chapters = sample_chapters(set(usable), args.chapters)
    if not chapters:
        raise SystemExit("no chapter is both parsable and uniquely numbered")
    rows = build_rows(sources, translations, chapters, args.segments)

    if not args.aggregate_only:
        input_path = work_dir / "qe_input.jsonl"
        input_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8")
        meta_path.write_text(json.dumps(
            {"book": args.book, "chapters": chapters,
             "segments": args.segments}), encoding="utf-8")
        print(f"wrote {input_path} ({len(rows)} segments)")
        qe_metricx.run_metricx(input_path, output_path)
    else:
        if not meta_path.exists():
            raise SystemExit(
                f"--aggregate-only needs {meta_path} to prove the cached scores "
                f"belong to this run; rerun without it")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("chapters") != chapters or meta.get("segments") != args.segments:
            raise SystemExit(
                f"stale scores: {meta_path} describes chapters "
                f"{meta.get('chapters')} / {meta.get('segments')} segments, "
                f"this run is {chapters} / {args.segments}. Rerun without "
                f"--aggregate-only.")

    scored = [json.loads(line) for line in
              output_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(scored) != len(rows):
        raise SystemExit(
            f"MetricX returned {len(scored)} scores for {len(rows)} segments; "
            f"a truncated run cannot support a verdict")
    summary = qe_metricx.aggregate(scored)

    if args.write_baseline:
        if summary["mean"]["A"] is None:
            raise SystemExit("refusing to write a baseline with no scores")
        baseline = {
            "book": args.book,
            "book_name": cfg["name"],
            "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "note": args.note,
            "chapters": chapters,
            "segments": args.segments,
            "mean": summary["mean"]["A"],
            "median": summary["median"]["A"],
            "scores": scores_by_chapter(scored),
        }
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = baseline_path.with_name(baseline_path.name + ".tmp")
        tmp_path.write_text(
            json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, baseline_path)
        _print_report(summary, None, chapters)
        print(f"wrote baseline {baseline_path}")
        return 0

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if baseline.get("segments") != args.segments:
        raise SystemExit(
            f"segment mismatch: baseline used {baseline.get('segments')}, "
            f"this run {args.segments} — paired deltas would be meaningless")
    if baseline.get("chapters") != chapters:
        raise SystemExit(
            f"chapter mismatch: baseline {baseline.get('chapters')} vs "
            f"this run {chapters}; the delivered corpus changed shape")

    comparison = compare_to_baseline(scored, baseline, args.max_delta)
    _print_report(summary, comparison, chapters)
    (work_dir / "qe_delivery_result.json").write_text(
        json.dumps({"summary": summary, "comparison": comparison},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    if (comparison["unmatched_segments"] or comparison["paired_segments"] < 2
            or comparison["verdict"] in ("no_overlap", "insufficient")):
        print(f"\nGuard could not compare all segments "
              f"({comparison['unmatched_segments']} unmatched of "
              f"{len(scored)}); no verdict is possible.",
              file=sys.stderr)
        return UNUSABLE_EXIT_CODE
    if comparison["verdict"] == "worse":
        print(f"\nREAD degraded by more than {args.max_delta} points with a CI "
              f"excluding 0. Roll back: XIDU_NODE_MODEL_READ=qwen3.8-max",
              file=sys.stderr)
        return WORSE_EXIT_CODE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

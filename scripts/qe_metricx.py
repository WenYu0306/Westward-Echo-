#!/usr/bin/env python3.11
"""Reference-free quality estimation for the READ A/B, using MetricX-24.

Why this exists
---------------
The project's quality evidence used to be "cold-read 59/59 PASS" — a 2.6%
sample judged by the same model family that wrote the translation. That is not
evidence. MetricX-24 (WMT'24 metric, hybrid = reference-free QE mode) scores a
translation from source + hypothesis only, in [0, 25], LOWER IS BETTER.

This script feeds MetricX the same A/B outputs that the blind judge sees, so a
conclusion has two independent legs: a pairwise LLM judge and a published metric.

Segmentation
------------
MetricX was trained at 1536 tokens, so a whole chapter (~2k CJK source, ~6k
English words) cannot be scored in one piece. Both source and hypothesis are
split proportionally into N contiguous segments and paired by position. This is
a heuristic: paragraph boundaries are respected where possible, but source and
hypothesis are not sentence-aligned. Both arms are split the same way, so the
A/B delta is comparable even though absolute scores are approximate.

External environment (not part of the project venv)
---------------------------------------------------
MetricX is run from a separate checkout so its older transformers cannot disturb
the project's stack. One-time setup, verified on 2026-10-26:

    python3.11 -m venv --system-site-packages /tmp/metricx-venv
    /tmp/metricx-venv/bin/pip install "transformers==4.46.3" "datasets==3.6.0" \
        "accelerate>=0.26.0" sentencepiece
    git clone --depth 1 https://github.com/google-research/metricx /tmp/metricx-src

transformers 5.x breaks MetricX in two places in predict.py; patch the clone:
  * `AutoTokenizer.from_pretrained(..., use_fast=False)` — mT5 fast tokenizer
    conversion crashes on tiktoken.
  * `TrainingArguments(..., use_cpu=True)` — avoids an MPS placeholder error on
    Apple GPUs.

HuggingFace is unreachable from here; the mirror works:
    HF_ENDPOINT=https://hf-mirror.com

Usage::

    python3.11 scripts/qe_metricx.py archive/ab_read_tier_20261026 --book difu
    python3.11 scripts/qe_metricx.py archive/... --aggregate-only

Env overrides: METRICX_REPO, METRICX_PYTHON, METRICX_MODEL, METRICX_TOKENIZER.

【中文·这个脚本干什么】
用 MetricX-24（WMT'24 官方指标）给 A/B 两臂的译文做**无参考**质量打分：
只要源文和译文，不需要人工参考译文，分数 0–25，**越低越好**。
长章按比例切成若干段分别打分。结果与盲评并列，作为第二份独立证据。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.run_novel import NOVELS  # noqa: E402
from src.chapter_splitter import ParagraphTag, split_chapters  # noqa: E402
from src.text_io import detect_and_read  # noqa: E402

DEFAULT_MODEL = "google/metricx-24-hybrid-large-v2p6-bfloat16"
DEFAULT_TOKENIZER = "google/mt5-large"
DEFAULT_REPO = "/tmp/metricx-src-20261026"
DEFAULT_PYTHON = "/tmp/metricx-venv/bin/python"


def split_proportional(text: str, n: int) -> list[str]:
    """Split into exactly ``n`` contiguous pieces of roughly equal length.

    Cuts prefer a newline within ±200 chars, so paragraphs are usually kept
    whole. If newline snapping cannot produce exactly ``n`` non-empty pieces
    (short text, or two cuts landing on the same newline), it falls back to
    even character cuts. Exactness matters: an unequal split would make
    ``zip()`` silently drop segments from one arm.
    """
    text = text.strip()
    if n <= 1 or not text:
        return [text] if text else []
    if len(text) < n:
        return [text]

    bounds = [0]
    for i in range(1, n):
        ideal = round(len(text) * i / n)
        low = max(bounds[-1] + 1, ideal - 200)
        high = min(len(text) - 1, ideal + 200)
        newline = text.find("\n", low, high + 1)
        bounds.append(newline if newline != -1 else ideal)
    bounds.append(len(text))

    boundaries = sorted(set(bounds))
    pieces = [text[boundaries[i]:boundaries[i + 1]].strip()
              for i in range(len(boundaries) - 1)]
    pieces = [piece for piece in pieces if piece]
    if len(pieces) == n:
        return pieces

    even = [round(len(text) * i / n) for i in range(n + 1)]
    return [text[even[i]:even[i + 1]].strip() for i in range(n)]


def prepare_rows(outputs: dict, sources: dict[int, str], segments: int) -> list[dict]:
    """Build MetricX input rows for every successful arm output."""
    rows: list[dict] = []
    for arm in ("A", "B"):
        for item in outputs.get(arm, []):
            if not item.get("ok"):
                continue
            chapter = item["ch"]
            source = sources[chapter]
            source_parts = split_proportional(source, segments)
            hypothesis_parts = split_proportional(item["text"], segments)
            if len(source_parts) != len(hypothesis_parts):
                raise ValueError(
                    f"Ch{chapter} arm {arm}: split mismatch "
                    f"{len(source_parts)} source vs {len(hypothesis_parts)} hypothesis"
                )
            for index, (src, hyp) in enumerate(zip(source_parts, hypothesis_parts)):
                rows.append({
                    "source": src, "hypothesis": hyp, "reference": "",
                    "arm": arm, "ch": chapter, "seg": index,
                })
    return rows


def aggregate(rows: list[dict]) -> dict:
    """Per-arm means and a paired comparison over (chapter, segment) pairs."""
    by_arm: dict[str, list[float]] = {"A": [], "B": []}
    per_pair: dict[tuple[int, int], dict[str, float]] = {}
    for row in rows:
        by_arm[row["arm"]].append(row["prediction"])
        per_pair.setdefault((row["ch"], row["seg"]), {})[row["arm"]] = row["prediction"]

    diffs = [p["B"] - p["A"] for p in per_pair.values() if "A" in p and "B" in p]
    return {
        "segments": {arm: len(values) for arm, values in by_arm.items()},
        "mean": {arm: round(statistics.mean(v), 3) if v else None
                 for arm, v in by_arm.items()},
        "median": {arm: round(statistics.median(v), 3) if v else None
                   for arm, v in by_arm.items()},
        # Positive delta = arm B (flash READ) worse, because lower is better.
        "mean_delta_B_minus_A": round(statistics.mean(diffs), 3) if diffs else None,
        "bootstrap_95ci": bootstrap_ci(diffs),
        "A_better_segments": sum(1 for d in diffs if d > 0),
        "B_better_segments": sum(1 for d in diffs if d < 0),
        "tied_segments": sum(1 for d in diffs if d == 0),
        "paired_segments": len(diffs),
    }


def bootstrap_ci(diffs: list[float], n_resamples: int = 4000,
                 seed: int = 0) -> list[float] | None:
    """95% bootstrap CI for the mean of paired differences."""
    if len(diffs) < 2:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(n_resamples):
        sample = [diffs[rng.randrange(len(diffs))] for _ in diffs]
        means.append(statistics.mean(sample))
    means.sort()
    low = means[int(0.025 * len(means))]
    high = means[int(0.975 * len(means)) - 1]
    return [round(low, 3), round(high, 3)]


def run_metricx(input_path: Path, output_path: Path) -> None:
    repo = Path(os.getenv("METRICX_REPO", DEFAULT_REPO))
    python_bin = os.getenv("METRICX_PYTHON", DEFAULT_PYTHON)
    if not repo.exists():
        raise SystemExit(f"MetricX checkout not found: {repo} "
                         f"(set METRICX_REPO; see this file's docstring)")
    if not Path(python_bin).exists():
        raise SystemExit(f"MetricX python not found: {python_bin}")
    env = dict(os.environ)
    env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    env.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    command = [
        python_bin, "-m", "metricx24.predict",
        "--tokenizer", os.getenv("METRICX_TOKENIZER", DEFAULT_TOKENIZER),
        "--model_name_or_path", os.getenv("METRICX_MODEL", DEFAULT_MODEL),
        "--max_input_length", "1536",
        "--batch_size", "1",
        # Absolute: the subprocess runs with cwd=METRICX_REPO, so a relative
        # path would resolve against the checkout, not the project.
        "--input_file", str(input_path.resolve()),
        "--output_file", str(output_path.resolve()),
        "--qe",
    ]
    proc = subprocess.run(command, cwd=str(repo), env=env,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"MetricX failed (rc={proc.returncode}):\n"
                         f"{proc.stderr[-3000:]}")


def _load_sources(book: str) -> dict[int, str]:
    cfg = NOVELS[book]
    text, _ = detect_and_read(cfg["path"])
    return {c.index: c.content for c in split_chapters(text)
            if c.action != ParagraphTag.SKIP}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("out_dir", help="directory containing outputs.json")
    parser.add_argument("--book", default="difu", choices=sorted(NOVELS))
    parser.add_argument("--segments", type=int, default=6)
    parser.add_argument("--aggregate-only", action="store_true")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    input_path = out_dir / "qe_input.jsonl"
    output_path = out_dir / "qe_output.jsonl"

    if not args.aggregate_only:
        outputs = json.loads((out_dir / "outputs.json").read_text(encoding="utf-8"))
        rows = prepare_rows(outputs, _load_sources(args.book), args.segments)
        input_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8")
        print(f"wrote {input_path} ({len(rows)} segments)")
        run_metricx(input_path, output_path)

    scored = [json.loads(line) for line in
              output_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    summary = aggregate(scored)
    (out_dir / "qe_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nMetricX-24 QE (0–25, LOWER IS BETTER)")
    print(f"  A (max READ)   mean {summary['mean']['A']}  "
          f"median {summary['median']['A']}")
    print(f"  B (flash READ) mean {summary['mean']['B']}  "
          f"median {summary['median']['B']}")
    print(f"  B−A = {summary['mean_delta_B_minus_A']}  "
          f"95% CI {summary['bootstrap_95ci']}")
    print(f"  segments: A better {summary['A_better_segments']} / "
          f"B better {summary['B_better_segments']} / "
          f"tied {summary['tied_segments']}")
    print(f"wrote {out_dir / 'qe_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

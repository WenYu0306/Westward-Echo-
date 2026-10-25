#!/usr/bin/env python3.11
"""Translate the untranslated chapter titles in a delivered .md file.

Why this exists
---------------
The zero-cost structural scan found that both delivered books ship Chinese
chapter titles to English readers:

    地府:  ## Chapter 2: 第1章 鬼节，鬼敲门     (title never translated)
    无限:  ## Chapter 2: 第一章                 (real title parked in the body)
           [醒来 — Part 1]

The cold-read sample never caught this because it judges body comprehension,
not headings, and covers 2.6% of chapters.

What it does
------------
1. Parse every ``## Chapter N: <title>`` heading. For 无限恐怖, the real title
   is the bracketed Chinese line that follows the heading; that line is used
   and deleted on rewrite.
2. Normalise away redundant bits (``第1章`` prefixes, Chinese parentheticals
   that follow an English title, bare ``第N章`` titles) and deduplicate.
3. Translate the unique titles with GLM flash, in batches, recording tokens and
   wall-clock time in ``token_ledger`` (book_id ``<book>_titles``).
4. Write ``_title_map.json`` incrementally, so a failed run resumes instead of
   re-paying for work already done.
5. With ``--apply``, back up the .md and rewrite headings; bracketed body-title
   lines are removed.

Usage::

    python3.11 scripts/translate_titles.py difu            # dry run + estimate
    python3.11 scripts/translate_titles.py difu --apply    # translate + rewrite

【中文·这个脚本干什么】
把已交付译稿里没翻译的章节标题补成英文。地府的中文标题直接留在标题行里；
无限恐怖把真标题塞在正文的 `[...]` 行里、标题行只有"第一章"。
默认只做 dry-run（统计 + 预估花费），`--apply` 才会调模型并改写文件。
改写前会备份原文件；翻译结果逐批存盘，中断后可续跑。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402

from scripts.run_novel import NOVELS  # noqa: E402
from src import token_ledger  # noqa: E402
from src.agent.parse_utils import parse_llm_json  # noqa: E402
from src.config import MODEL_PRICES_CNY_PER_M, node_llm_kwargs  # noqa: E402

CJK = re.compile(r"[一-鿿]")
HEAD_RE = re.compile(r"^(##\s*Chapter\s+\d+)\s*[:：]\s*(.*)$")
BRACKET_RE = re.compile(r"^\[(.+?)\]\s*$")
# 第一章 / 第12回 / 第两百章 …
CN_NUMBER = "0-9一二三四五六七八九十百千零〇两"
# A *bare* chapter label (nothing after it) is pure scaffolding.
CN_PURE_CHAPTER = re.compile(rf"^第\s*[{CN_NUMBER}]+\s*[章回]\s*$")
# A prefix is only scaffolding when a separator follows it: `第1章 鬼节`.
# `第一回轮班的是郑吒` is a real title whose first word happens to look like
# a chapter label, so it must survive (no separator after 回).
CN_CHAPTER_PREFIX = re.compile(
    rf"^第\s*[{CN_NUMBER}]+\s*[章回]\s*[：:、.．\-—\s]+")
CN_PAREN = re.compile(r"[（(][^）)]*[）)]")

_SYSTEM = """You translate Chinese web-novel chapter titles into English.

Rules:
- Output ONLY a JSON object: {"<Chinese input>": "<English title>"}.
- Translate EVERY key. Never merge, skip, or rename a key.
- Use Title Case, keeping articles/prepositions lowercase.
- Keep "Part 1/2/3", Roman numerals, and " — " as they appear.
- Use the glossary mappings for names and terms when they appear in a title.
- Romanize proper names in pinyin (苏念 -> Su Nian).
- A value must never contain Chinese characters.
- No explanations and no markdown fences.
"""


def normalize_title(raw: str) -> str:
    """Strip redundant chapter-number scaffolding from a title.

    【中文】去掉 `第1章 ` 前缀、以及英文标题后面的中文括注。
    纯章节号（如 `第一章`）会变成空字符串——标题行本身已经有章节号了。
    """
    text = CN_PAREN.sub("", raw).strip()
    if CN_PURE_CHAPTER.match(text):
        return ""
    text = CN_CHAPTER_PREFIX.sub("", text).strip()
    return text


def collect_entries(lines: list[str], mode: str) -> list[dict]:
    """Return rewrite entries: {line, prefix, key, delete}.

    ``mode`` is ``"heading"`` (地府) or ``"bracket"`` (无限恐怖).
    Entries with no Chinese anywhere are skipped, so a second run is a no-op.
    """
    entries: list[dict] = []
    for i, line in enumerate(lines):
        match = HEAD_RE.match(line)
        if not match:
            continue
        prefix, raw = match.group(1), match.group(2)
        key = normalize_title(raw)
        delete = None
        if mode == "bracket":
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                bracket = BRACKET_RE.match(lines[j].strip())
                if bracket and CJK.search(bracket.group(0)):
                    key = bracket.group(1).strip()
                    delete = j
        if not CJK.search(raw) and delete is None:
            continue                      # already translated
        entries.append({"line": i, "prefix": prefix, "key": key, "delete": delete})
    return entries


def apply_map(lines: list[str], entries: list[dict], mapping: dict[str, str]) -> list[str]:
    """Return new lines with titles replaced and bracket lines removed."""
    out: list[str | None] = list(lines)
    for entry in entries:
        key = entry["key"]
        if key:
            if key not in mapping:
                raise KeyError(f"no translation for {key!r}")
            out[entry["line"]] = f"{entry['prefix']}: {mapping[key]}"
        else:
            out[entry["line"]] = entry["prefix"] + ":"
    for entry in entries:
        if entry["delete"] is not None:
            out[entry["delete"]] = None
    return [line for line in out if line is not None]


def invalid_titles(mapping: dict[str, str], keys: list[str]) -> tuple[list[str], list[str]]:
    """Return (missing, leaks) — keys without a value, values still in Chinese."""
    missing = [k for k in keys if not str(mapping.get(k, "")).strip()]
    leaks = [k for k in keys if CJK.search(str(mapping.get(k, "")))]
    return missing, leaks


def estimate_cny(count: int, model: str, batch_size: int = 40) -> float:
    """Rough upper-bound cost for translating ``count`` unique titles."""
    price = MODEL_PRICES_CNY_PER_M.get(model)
    if price is None:
        return 0.0
    price_in, price_out = price
    batches = (count + batch_size - 1) // batch_size
    input_tokens = count * 25 + batches * 400
    output_tokens = count * 22
    return input_tokens / 1e6 * price_in + output_tokens / 1e6 * price_out


def _glossary_hint(glossary: dict, titles: list[str], limit: int = 80) -> str:
    picked = []
    for cn, en in glossary.items():
        if cn and any(cn in title for title in titles):
            picked.append(f"{cn} = {en}")
            if len(picked) >= limit:
                break
    return "; ".join(picked)


def _build_llm(provider_kwargs: dict) -> ChatOpenAI:
    return ChatOpenAI(  # type: ignore[call-arg]
        temperature=0.1,
        max_tokens=4096,
        request_timeout=180,
        max_retries=2,
        model_kwargs={"response_format": {"type": "json_object"}},
        **provider_kwargs,
    )


def _translate_batch(llm, titles, glossary, book_id, model,
                     strict: bool = False) -> dict[str, str]:
    hint = _glossary_hint(glossary, titles)
    user = (
        (f"Glossary (Chinese = English): {hint}\n\n" if hint else "")
        + "Titles (JSON array):\n"
        + json.dumps(titles, ensure_ascii=False)
    )
    system = _SYSTEM
    if strict:
        system += (
            "\nCRITICAL: a previous attempt copied the Chinese source into a "
            "value. A value containing ANY Chinese character is invalid. "
            "Transliterate unknown proper names into pinyin; never copy source."
        )
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    response, elapsed = token_ledger.timed_call(llm.invoke, messages)
    token_ledger.record_response(
        response, "title_translation", model,
        book_id=book_id, elapsed_s=elapsed,
    )
    content = response.content if isinstance(response.content, str) else str(response.content)
    parsed, _ = parse_llm_json(content, {})
    return {
        str(k): str(v).strip()
        for k, v in parsed.items()
        if isinstance(k, str) and isinstance(v, (str, int, float))
    }


def translate_all(llm, titles, glossary, book_id, model, batch_size, map_path,
                  max_batches: int | None = None) -> dict:
    """Translate missing titles in batches, persisting after every batch."""
    mapping: dict[str, str] = {}
    if map_path.exists():
        mapping = json.loads(map_path.read_text(encoding="utf-8"))
    todo = [t for t in titles if not str(mapping.get(t, "")).strip()]
    total = len(todo)
    spent = 0.0
    for index, start in enumerate(range(0, total, batch_size)):
        if max_batches is not None and index >= max_batches:
            print(f"  stopped after {max_batches} batch(es); resume by re-running --apply")
            break
        batch = todo[start:start + batch_size]
        before = token_ledger.summary(book_id)["cost_cny"]
        mapping.update(_translate_batch(llm, batch, glossary, book_id, model))
        spent += token_ledger.summary(book_id)["cost_cny"] - before
        map_path.write_text(
            json.dumps(mapping, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
        )
        print(f"  [{min(start + batch_size, total)}/{total}] "
              f"累计 ¥{spent:.4f}")
    return mapping


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("book", choices=sorted(NOVELS))
    parser.add_argument("--apply", action="store_true",
                        help="call the model and rewrite the .md (default: dry run)")
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing .bak backup")
    parser.add_argument("--max-batches", type=int, default=None,
                        help="stop after N batches (smoke test; resume by re-running)")
    args = parser.parse_args(argv)

    cfg = NOVELS[args.book]
    out_dir = Path(cfg["output_dir"])
    md_path = out_dir / cfg["output_file"]
    lines = md_path.read_text(encoding="utf-8").splitlines()
    mode = "bracket" if "limitless_horror_segmented" in cfg["output_dir"] else "heading"

    entries = collect_entries(lines, mode)
    keys = sorted({e["key"] for e in entries if e["key"]})
    provider = node_llm_kwargs("write")
    model = provider["model"]

    print(f"{cfg['name']}: {len(entries)} headings to rewrite, "
          f"{len(keys)} unique titles (mode={mode}, model={model})")
    print(f"estimated cost upper bound: ¥{estimate_cny(len(keys), model, args.batch_size):.4f}")
    if not args.apply:
        for key in keys[:10]:
            print(f"  e.g. {key!r}")
        print("dry run — pass --apply to translate and rewrite")
        return 0

    glossary_path = out_dir / "_glossary.json"
    glossary = (json.loads(glossary_path.read_text(encoding="utf-8"))
                if glossary_path.exists() else {})
    map_path = out_dir / "_title_map.json"
    llm = _build_llm(provider)
    mapping = translate_all(llm, keys, glossary, f"{cfg['book_id']}_titles",
                            model, args.batch_size, map_path, args.max_batches)

    for key in keys[:5]:
        print(f"  {key!r} -> {mapping.get(key)!r}")

    missing, leaks = invalid_titles(mapping, keys)
    if leaks:
        print(f"retrying {len(leaks)} title(s) that still contained Chinese")
        retried = _translate_batch(llm, leaks, glossary,
                                   f"{cfg['book_id']}_titles", model, strict=True)
        for key in leaks:
            value = str(retried.get(key, "")).strip()
            if value and not CJK.search(value):
                mapping[key] = value
        map_path.write_text(
            json.dumps(mapping, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
        )
        missing, leaks = invalid_titles(mapping, keys)
    if missing or leaks:
        print(f"REFUSING to rewrite: {len(missing)} missing, {len(leaks)} still Chinese")
        for key in (missing + leaks)[:10]:
            print(f"  {key!r} -> {mapping.get(key)!r}")
        return 1

    backup = md_path.with_suffix(md_path.suffix + ".bak")
    if backup.exists() and not args.force:
        print(f"error: {backup} exists; pass --force to overwrite")
        return 1
    backup.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")

    rewritten = apply_map(lines, entries, mapping)
    md_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    print(f"rewrote {md_path} ({len(entries)} headings); backup: {backup}")

    cost = token_ledger.summary_from_file(f"{cfg['book_id']}_titles")
    timing = cost["timing"]
    print(f"actual (all runs): {cost['calls']} calls, ¥{cost['cost_cny']:.4f}, "
          f"{timing['total_s']:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

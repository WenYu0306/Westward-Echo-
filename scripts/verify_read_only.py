"""只跑 READ 节点，验证 DeepSeek 对「王三」「聋婆婆」的术语决策是否正确。

用法：python3.11 -u scripts/verify_read_only.py
（只调 DeepSeek 的 READ，不碰 Qwen 的 WRITE，可在沙箱/无 Qwen 网络的环境跑）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent.nodes.read import read_node  # noqa: E402
from src.glossary.exact_store import ExactGlossary  # noqa: E402
from src.glossary.semantic_store import SemanticGlossary  # noqa: E402

TEXT_PATH = "/Users/wenyudemac/Documents/实验文本/《地府叫我小先生（产品演示）》.txt"


def main():
    with open(TEXT_PATH, encoding="utf-8") as f:
        text = f.read()

    state = {
        "chapter_title": "第1章 鬼节，鬼敲门",
        "chapter_content": text,
        "chapter_number": 1,
        "target_lang": "en-US",
        "genre": "folk_religion",
        "content_type": "novel",
        "style_memo": "(no memo)",
        "previous_chapter_summary": "(first chapter)",
        "exact_matches_text": "(no glossary)",
        "semantic_matches_text": "(no semantic)",
        "exact_glossary": {},
        "api_key": "",
        "image_gaps": [],
    }

    print("跑 READ 节点（DeepSeek）...\n")
    result = read_node(state, ExactGlossary(), SemanticGlossary())
    analysis = result.get("read_analysis", {})

    print("=" * 50)
    print("READ 术语决策 (terminology_decisions)：")
    for td in analysis.get("terminology_decisions", []):
        cn = td.get("term_cn", "")
        en = td.get("proposed_en", "")
        if cn and en:
            print(f"    {cn} → {en}")

    print("\n关键检查：")
    decisions = {td.get("term_cn"): td.get("proposed_en", "")
                 for td in analysis.get("terminology_decisions", [])}
    for cn, expect in [("聋婆婆", "Deaf Granny"), ("王三", "Wang San")]:
        got = decisions.get(cn, "(未决策)")
        ok = expect.lower() in got.lower() if got != "(未决策)" else False
        mark = "✅" if ok else "❌"
        print(f"    {mark} {cn} → {got}  (期望含 {expect})")


if __name__ == "__main__":
    main()

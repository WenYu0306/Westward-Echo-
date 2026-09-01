"""端到端验证文化保真度规则是否生效。

跑地府第 1 章（含「聋婆婆」谐音梗），观察加了 fidelity 规则后：
1. READ 是否决策「聋婆婆 → Deaf Granny」（意译 + 抓谐音梗），而非音译；
2. WRITE 译文里出现的是 Deaf Granny 还是 Lóng Pópo；
3. 回检（fidelity check）有没有触发。

用法（在你的终端跑，终端能连 Qwen）：
    cd "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）"
    python3 -u -m scripts.verify_fidelity
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent.graph import TranslationAgent  # noqa: E402

TEXT_PATH = "/Users/wenyudemac/Documents/实验文本/《地府叫我小先生（产品演示）》.txt"


def main():
    with open(TEXT_PATH, encoding="utf-8") as f:
        text = f.read()

    print(f"输入文本：{TEXT_PATH}（{len(text)} 字）")
    print("开始编译（会调 Qwen，需几十秒）...\n")

    agent = TranslationAgent(book_id="fidelity_verify_difu")
    result = agent.translate_chapter(
        chapter_title="第1章 鬼节，鬼敲门",
        chapter_content=text,
        chapter_number=1,
        target_lang="en-US",
        genre="folk_religion",
    )

    read_analysis = result.get("read_analysis", {})
    translated = result.get("translated_text", "")
    quality_issues = result.get("quality_issues", [])

    print("=" * 60)
    print("① READ 的术语决策 (terminology_decisions)：")
    for td in read_analysis.get("terminology_decisions", []):
        cn = td.get("term_cn", "")
        en = td.get("proposed_en", "")
        if cn and en:
            print(f"    {cn} → {en}")

    print("\n② 译文里人名怎么处理的：")
    for marker in ["Deaf Granny", "Lóng Pópo", "Uncle Li", "Lǐ Dàye"]:
        if marker in translated:
            print(f"    ✓ 出现「{marker}」")

    print("\n③ 回检是否触发（fidelity failures）：")
    fidelity_issues = [q for q in quality_issues if "READ decided" in q]
    if fidelity_issues:
        for q in fidelity_issues:
            print(f"    ⚠️ {q}")
    else:
        print("    （无）—— WRITE 执行了 READ 的决策")

    print("\n④ 结论：")
    if "Deaf Granny" in translated and "Lóng Pópo" not in translated:
        print("    ✅ PASS：谐音人名被正确意译（Deaf Granny），规则生效")
    elif "Lóng Pópo" in translated:
        print("    ❌ FAIL：仍是拼音音译（Lóng Pópo），规则未生效")
    else:
        print("    ⚠️ 未出现两个候选名，需人工看译文判断")

    print("\n⑤ 译文开头 600 字：")
    print(translated[:600])

    print("\n⑥ 南茅北马相关段落（验证文化概念翻译质量）：")
    found = False
    for kw in ["Talisman", "Spirit-Horse", "spirit-horse", "Maoshan", "shaman"]:
        idx = translated.lower().find(kw.lower())
        if idx != -1:
            start = max(0, idx - 120)
            end = min(len(translated), idx + 220)
            print(f"  【{kw}】...{translated[start:end].strip()}...")
            found = True
            break
    if not found:
        print("  （未找到关键词，需人工看完整译文）")


if __name__ == "__main__":
    main()

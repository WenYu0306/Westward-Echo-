"""端到端验证：跑一章中文网文，走完整管线，输出冷读结果。

管线：READ(GLM 5.3) → REVIEW_TERMS(Qwen) → WRITE(GLM flash) → READBACK(Qwen) → (FIX if NEEDS_FIX)

用法（在能联网的环境，已配好 .env 的 GLM/Qwen key）：
  python3 scripts/e2e_verify.py

看冷读 verdict 是不是 PASS，以及 would_keep_reading 是不是 True。
这是模型切换后必须过的一关——编译质量不降才算换模型成功。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent.graph import TranslationAgent

CHAPTER = """第一章 鬼节

九道沟村的老槐树下，聋婆婆点了一盏纸灯。

村里人都说，鬼节这天晚上，活人不能出门。可聋婆婆偏要坐在树下，守着那盏灯。

她在等一个人。

很多年前，她的儿子也是在这天晚上出了门，就再没回来。

风起了，纸灯晃了晃。聋婆婆听见身后有脚步声，很轻，像踩在棉花上。

她没有回头，只是说：你回来了。
"""


def main():
    agent = TranslationAgent(book_id="e2e_verify")
    result = agent.translate_chapter(
        chapter_title="第一章 鬼节",
        chapter_content=CHAPTER,
        chapter_number=1,
        target_lang="en-US",
        genre="folk_religion",
        skip_readback=False,  # 完整管线，含冷读
    )

    fb = result.get("readback_feedback", {})
    print("=" * 60)
    print(f"冷读 verdict:        {fb.get('verdict', 'N/A')}")
    print(f"would_keep_reading:  {fb.get('would_keep_reading', 'N/A')}")
    print(f"冷读印象:            {fb.get('overall_impression', 'N/A')}")
    print(f"质量分:              {result.get('quality_score', 'N/A')}")
    print(f"新术语数:            {len(result.get('new_terms_found', []))}")
    comp_issues = fb.get("comprehension_issues", [])
    engage_gaps = fb.get("engagement_gaps", [])
    print(f"理解问题数:          {len(comp_issues)}")
    print(f"弃读点数量:          {len(engage_gaps)}")
    print("=" * 60)
    print("译文前 600 字:")
    print(result.get("translated_text", "")[:600])

    # 每个节点的中间产物分别落盘，供交叉验证「每个节点都参与输出了」
    out_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "output",
    )
    os.makedirs(out_dir, exist_ok=True)

    def _dump(name, data):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    _dump("e2e_read_analysis.json", result.get("read_analysis", {}))
    _dump("e2e_translated.json", {
        "translated_text": result.get("translated_text", ""),
        "new_terms_found": result.get("new_terms_found", []),
        "chapter_summary": result.get("chapter_summary", ""),
        "adaptation_notes": result.get("adaptation_notes", []),
    })
    _dump("e2e_readback.json", fb)
    _dump("e2e_result.json", {
        "verdict": fb.get("verdict"),
        "would_keep_reading": fb.get("would_keep_reading"),
        "overall_impression": fb.get("overall_impression"),
        "quality_score": result.get("quality_score"),
        "comprehension_issues": fb.get("comprehension_issues", []),
        "engagement_gaps": fb.get("engagement_gaps", []),
        "retranslation_count": result.get("retranslation_count", 0),
    })
    print(f"\n中间产物已写入 {out_dir}/ 下 4 个文件")


if __name__ == "__main__":
    main()

"""Probe the GLM content-filter risk on the genre that originally tripped it.

Background: when the pipeline moved READ/FIX onto GLM, the very first E2E
attempt died with ``code 1301 — 系统检测到输入或生成内容可能包含不安全或敏感内容``
on a "穿成霸总文女主" chapter containing 草泥马. The follow-up E2E passed, but
only on a clean 鬼节 chapter. So the open question handed over was:

    does GLM's content filter make READ-on-GLM unworkable for the
    tropes Chinese web novels are actually full of?

This script answers it by running the ORIGINAL failing text — the same
cangfu/chuanshu chapter with 草泥马 — through the full pipeline, and reporting
whether any node is rejected by the content filter.

Usage:
  /opt/homebrew/bin/python3.11 scripts/probe_glm_filter.py

Exit code 0 = no content-filter rejection (the selection holds).
Exit code 1 = a node was rejected (GLM filtering is a real constraint).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent.graph import TranslationAgent

# Verbatim the chapter that triggered code 1301 during the model switch:
# 霸总 / 穿书 tropes plus the 草泥马 profanity-pun.
CHAPTER = """第一章 穿成霸总文女主

苏念醒过来的时候，发现自己躺在一张陌生的大床上。

她揉了揉眼睛，试图回忆昨晚发生了什么。脑海里突然响起一个机械的声音：

【叮——恭喜宿主绑定霸总攻略系统！】

苏念愣住了。什么系统？什么霸总？她只是加了个班，怎么就穿越了？

她环顾四周，房间奢华得像五星级酒店。落地窗外是城市的夜景，霓虹灯闪烁。

门外传来脚步声。一个高大英俊的男人走了进来，穿着剪裁考究的西装。

"你就是苏念？"男人冷冷地说，"从今天起，你就是裴家的人了。"

苏念心里一万匹草泥马奔腾而过。她只是一个普通社畜，怎么就变成了霸总文女主？

她深吸一口气，决定先弄清楚目前的处境再说。毕竟既来之，则安之。
"""


def _content_filter_rejected(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}"
    return "1301" in text or "contentFilter" in text or "不安全" in text


VARIANTS = {
    # The original failing text, verbatim.
    "full": CHAPTER,
    # Same chapter with the 草泥马 profanity-pun swapped for a neutral animal.
    # If this passes, the filter is reacting to the profanity pun rather than
    # to the 霸总/穿书/系统 tropes themselves.
    "no-profanity": CHAPTER.replace("一万匹草泥马", "一万头羊驼"),
    # The tropes without the profanity pun AND without the system-notification
    # brackets, to see whether the LitRPG brackets matter.
    "plain-tropes": (
        CHAPTER.replace("一万匹草泥马", "一万头羊驼")
        .replace("【叮——恭喜宿主绑定霸总攻略系统！】", "一个机械的声音恭喜她绑定了系统。")
    ),
}


def main():
    variant = sys.argv[1] if len(sys.argv) > 1 else "full"
    if variant not in VARIANTS:
        print(f"unknown variant {variant!r}; choose from {sorted(VARIANTS)}")
        return 2
    chapter = VARIANTS[variant]

    agent = TranslationAgent(book_id=f"probe_glm_filter_{variant}")
    try:
        result = agent.translate_chapter(
            chapter_title="第一章 穿成霸总文女主",
            chapter_content=chapter,
            chapter_number=1,
            target_lang="en-US",
            genre="romance_ceo",
            skip_readback=False,
        )
    except Exception as exc:  # noqa: BLE001 - we are classifying the failure
        if _content_filter_rejected(exc):
            print(f"[{variant}] CONTENT FILTER REJECTED a node:")
            print(f"  {type(exc).__name__}: {str(exc)[:400]}")
            return 1
        print(f"[{variant}] failed for another reason: {type(exc).__name__}: {exc}")
        return 2

    fb = result.get("readback_feedback", {})
    print(f"[{variant}] verdict:            ", fb.get("verdict", "N/A"))
    print(f"[{variant}] would_keep_reading: ", fb.get("would_keep_reading", "N/A"))
    print(f"[{variant}] new terms:          ", len(result.get("new_terms_found", [])))
    print(f"[{variant}] translated chars:   ", len(result.get("translated_text", "")))
    print(f"\n=> [{variant}] no content-filter rejection.")

    out_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output"
    )
    with open(os.path.join(out_dir, f"glm_filter_probe_{variant}.json"), "w",
              encoding="utf-8") as fh:
        json.dump({
            "variant": variant,
            "verdict": fb.get("verdict"),
            "would_keep_reading": fb.get("would_keep_reading"),
            "new_terms": result.get("new_terms_found", []),
            "translated_text": result.get("translated_text", ""),
        }, fh, ensure_ascii=False, indent=2)
    print(f"Result written to {out_dir}/glm_filter_probe_{variant}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

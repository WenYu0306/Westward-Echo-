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

【中文·这个脚本干什么】
探测智谱 GLM 的内容过滤机制会不会拦截真实网文。它用一段曾经触发过
1301 错误的"霸总穿书 + 草泥马"章节跑完整翻译流水线，看哪个节点被拦。

【中文·背景：为什么 READ 节点从 GLM 移到了 Qwen】
项目早期把 READ/FIX 节点放在 GLM 上，但第一次端到端测试就遇到了
1301 错误（"系统检测到输入或生成内容可能包含不安全或敏感内容"）。
中国网文充满了"草泥马""霸总""穿书"这类在 GLM 看来敏感的词汇和套路，
如果 READ 节点用 GLM，大量正常章节都会被误拦。所以后来把 READ 移到
Qwen（通义千问），只让 WRITE 和 FIX 继续用 GLM——因为 WRITE 的输出是英文，
不太会触发中文内容过滤；FIX 只做局部修补，输入量小。
详见 src/config.py 中 DEFAULT_NODE_MODELS 的注释。

【中文·怎么运行】
```bash
python3.11 scripts/probe_glm_filter.py          # 用原始触发文本
python3.11 scripts/probe_glm_filter.py no-profanity  # 去掉脏话的版本
python3.11 scripts/probe_glm_filter.py plain-tropes  # 去掉脏话+系统提示框
```

【中文·⚠️ 会不会花真钱、会不会写生产数据库】
⚠️ **会花真钱**：调用完整的翻译流水线（READ → WRITE → READBACK），
涉及多次大模型 API 调用。费用取决于当前配置的模型档位。
不会写生产数据库（TranslationAgent 使用独立的 book_id）。

【中文·会产生/修改什么文件】
会在 output/ 目录下写入 glm_filter_probe_{variant}.json，
记录该变体的翻译结果和冷读反馈。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent.graph import TranslationAgent

# 【中文】这就是当初触发 GLM 1301 错误的原始章节文本。
# 包含"霸总""穿书""系统"等网文套路 + "草泥马"谐音脏话，
# 是测试内容过滤的理想样本。
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
    """判断异常是否是 GLM 内容过滤导致的。

    【中文】通过检查异常文本中是否包含 "1301"（GLM 的内容过滤错误码）、
    "contentFilter" 或 "不安全" 来判断。这三个关键词覆盖了已知的
    GLM 内容过滤报错格式。
    """
    text = f"{type(exc).__name__}: {exc}"
    return "1301" in text or "contentFilter" in text or "不安全" in text


# 【中文】三个测试变体，逐步剥离敏感元素，定位到底是哪个触发了过滤：
VARIANTS = {
    # The original failing text, verbatim.
    # 【中文】原始触发文本，包含所有敏感元素。
    "full": CHAPTER,
    # Same chapter with the 草泥马 profanity-pun swapped for a neutral animal.
    # If this passes, the filter is reacting to the profanity pun rather than
    # to the 霸总/穿书/系统 tropes themselves.
    # 【中文】把"草泥马"换成"羊驼"。如果这个版本通过了，
    # 说明过滤器针对的是脏话谐音，不是霸总/穿书套路本身。
    "no-profanity": CHAPTER.replace("一万匹草泥马", "一万头羊驼"),
    # The tropes without the profanity pun AND without the system-notification
    # brackets, to see whether the LitRPG brackets matter.
    # 【中文】再去掉【】系统提示框。如果这个也通过了，
    # 说明 LitRPG 风格的方括号也可能触发过滤。
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

    # 【中文】用独立的 book_id，避免污染生产术语库。
    agent = TranslationAgent(book_id=f"probe_glm_filter_{variant}")
    try:
        # 【中文】跑完整翻译流水线（含冷读），看哪个节点被内容过滤拦截。
        result = agent.translate_chapter(
            chapter_title="第一章 穿成霸总文女主",
            chapter_content=chapter,
            chapter_number=1,
            target_lang="en-US",
            genre="romance_ceo",
            skip_readback=False,
        )
    except Exception as exc:  # noqa: BLE001 - we are classifying the failure
        # 【中文】区分"内容过滤拒绝"和"其他原因失败"：
        # 前者是本脚本要探测的目标，后者可能是网络/API 配置问题。
        if _content_filter_rejected(exc):
            print(f"[{variant}] CONTENT FILTER REJECTED a node:")
            print(f"  {type(exc).__name__}: {str(exc)[:400]}")
            return 1
        print(f"[{variant}] failed for another reason: {type(exc).__name__}: {exc}")
        return 2

    # 【中文】没有被过滤 → 打印冷读反馈和翻译结果摘要。
    fb = result.get("readback_feedback", {})
    print(f"[{variant}] verdict:            ", fb.get("verdict", "N/A"))
    print(f"[{variant}] would_keep_reading: ", fb.get("would_keep_reading", "N/A"))
    print(f"[{variant}] new terms:          ", len(result.get("new_terms_found", [])))
    print(f"[{variant}] translated chars:   ", len(result.get("translated_text", "")))
    print(f"\n=> [{variant}] no content-filter rejection.")

    # 【中文】把结果写入 output/ 目录，方便事后对比三个变体的差异。
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

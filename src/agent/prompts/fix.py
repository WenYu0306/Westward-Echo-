"""Prompt for the FIX agent — an editor who repairs based on cold reader feedback.

Fixes specific problems identified by a naive reader. It targets only what's broken.

The output language and audience wording is parameterised via
``build_prompt_context(genre, target_lang)`` — see
``src/agent/prompts/personas.py``. The JSON output example uses doubled braces
so ``str.format()`` leaves it literal.

【中文·这个文件是干什么的】
本文件提供翻译流水线中 **FIX 角色**（编辑）的两段提示词模板。
FIX 是一个精准的修补匠：它只修复冷读者（READBACK）明确指出的问题，
绝不整章重写，也不碰冷读者没抱怨的部分。

【中文·FIX 角色在流水线里的位置】
  START → READ → REVIEW_TERMS → WRITE → READBACK →(NEEDS_FIX?)→ **FIX**
                                                      ↑              ↓
                                                      └── READBACK ←─┘
当 READBACK 判定 "NEEDS_FIX" 时，流水线进入 FIX → READBACK 循环。
FIX 拿到冷读者的具体投诉 + 中文原文 + 当前英文译文，做定点修补后
交回 READBACK 再审。最多循环 _MAX_FIX_ATTEMPTS 次（定义在 graph.py）。

【中文·为什么不让 WRITE 直接重写】
因为 WRITE 重跑一整章的成本很高（token 多、时间长），而且重写可能
引入新问题。FIX 只做外科手术式的修补：冷读者说"第三段看不懂"，
FIX 就只改第三段，其他地方一字不动。这样既省钱又安全。

【中文·FIX 的三类修复优先级】
① Critical —— 读者完全看不懂发生了什么（缺上下文、文化概念无锚点）。
② Major —— 读者失去了兴趣（设定倾倒、节奏失调、语气断裂）。
③ Minor —— 读者绊了一下但恢复了（措辞别扭、代词不清、小矛盾）。

【中文·本文件的两个常量】
· FIX_SYSTEM —— 系统提示词：定义编辑的身份、修复规则、输出格式。
· FIX_USER —— 用户提示词：拼入中文原文、当前英文译文、冷读者反馈、
  术语表。
"""

# ──────────────────────────────────────────────────────────────────────
# 【中文·FIX_SYSTEM 是什么】
# 这是 FIX 角色的"身份说明书"。作为 system message 发给大模型，
# 告诉 AI："你是一个编辑。作家已经把一章中文网文改编成了目标语言，
# 测试读者标记了具体问题。你的工作是修好这些问题，其他一律不动。"
#
# ⚠️ 下面的英文是喂给大模型的提示词原文，属于业务数据，不能改动。
#
# 【中文·占位符说明】
# · {output_language} —— 目标语言名称（如 "English"），
#   来自 personas.build_prompt_context()。
# · {readers} —— 目标读者群体的复数形式（如 "American readers"），同上。
#
# 【中文·zero-Chinese rule 在 FIX 中的意义】
# FIX 的输出 polished_text 是最终交付给读者的成品，所以同样遵守
# "零中文规则"：输出里不能出现任何 CJK 字符。这条规则写在
# "How to fix" 一节里，确保编辑在修补时也不会意外引入中文。
# ──────────────────────────────────────────────────────────────────────
FIX_SYSTEM = """\
You are an editor. A writer has adapted a Chinese web novel chapter into \
{output_language}, and a test reader has flagged specific problems.

Your job: fix the problems the reader identified. Leave everything else alone.

## RULES

### 1. Fix what the reader flagged — nothing else
The reader's feedback is your ONLY mandate. If the reader didn't complain \
about it, don't touch it.

### 2. Use the Chinese original for accuracy
You have access to the source text. When fixing a confusion, check what the \
original actually says. Don't guess.

### 3. Fix categories (priority order)

**Critical — reader couldn't understand what was happening:**
- Missing context that makes the scene nonsensical
- Cultural concepts used without any reader-accessible anchor
- Character actions that seem unmotivated without cultural knowledge

**Major — reader lost engagement:**
- Exposition dumps that killed pacing
- Passages where the reader skimmed or wanted to skip
- Tone shifts that broke immersion

**Minor — reader stumbled but recovered:**
- Awkward phrasing
- Unclear pronoun references
- Minor inconsistencies

### 4. How to fix

- Add context by weaving it into action or dialogue, NEVER as a narrator \
aside or footnote
- Fix pacing by compressing exposition into a telling detail
- Bridge cultural gaps with a sensory detail, a character's reaction, or \
a one-sentence analogy to something {readers} know
- If a passage is unfixable in place, you may restructure — but preserve \
the story function
- Write every fix in {output_language}. Keep the zero-Chinese rule: no CJK \
character may enter the polished text.

### 5. Output format

Return a single JSON object — no preamble, no markdown fences:

{{
  "polished_text": "The complete fixed chapter",
  "changes_made": [
    "What you changed and why — one line per change"
  ]
}}

The polished_text must contain the COMPLETE chapter with fixes applied. \
Do not abbreviate or skip passages.
"""

# ──────────────────────────────────────────────────────────────────────
# 【中文·FIX_USER 是什么】
# 这是 FIX 角色的"本次任务单"。节点代码（src/agent/nodes/fix.py
# 的 fix_node 函数）把中文原文、当前英文译文、冷读者反馈、术语表
# 填进下面的占位符，然后作为 user message 发给大模型。
#
# ⚠️ 下面的英文是提示词原文，不能改动。
#
# 【中文·各占位符的数据来源】
# · {original_cn} —— 本章中文原文（作为修复时的真相参照），
#   来自 state["chapter_content"]。
# · {current_en} —— 当前的英文译文（待修补的版本），
#   来自 state["translated_text"]。如果是第二轮 FIX，这里就是
#   上一轮 FIX 修补后的版本。
# · {reader_feedback} —— 冷读者的结构化反馈（格式化后的文本），
#   由 nodes/fix.py 的 _format_readback_feedback() 从
#   state["readback_feedback"] 生成。包含理解障碍、兴趣断点、
#   角色辨识度评价、世界观理解等。
# · {glossary_text} —— 已确立的术语表（用于校验修复后的用词一致性），
#   来自 state["exact_matches_text"]。
# · {output_language} —— 目标语言名称（如 "English"），
#   来自 personas.build_prompt_context()。
# ──────────────────────────────────────────────────────────────────────
FIX_USER = """\
## ORIGINAL CHINESE (source of truth)
{original_cn}

## CURRENT {output_language} VERSION (to be fixed)
{current_en}

## READER FEEDBACK (fix THESE specific problems)
{reader_feedback}

## GLOSSARY (for terminology verification)
{glossary_text}

## TASK
Fix the chapter based on the reader's feedback. Return the complete fixed \
text as JSON with polished_text and changes_made.
"""

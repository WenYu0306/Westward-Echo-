# 【中文·这个文件的作用】
# 这是 src/agent/prompts/ 目录的"门牌"（Python 包标识文件）。
# 让 Python 把 prompts/ 当作一个可导入的包。
#
# 本包管理喂给 AI 的所有"角色说明书和任务单"（提示词 / prompt），包含：
#   · personas.py    —— 各角色的身份设定（按目标语言和题材分别定制）
#   · registry.py    —— 提示词注册表（按内容类型选取对应的提示词集合）
#   · translation.py —— 各目标语言的区域风格约束和语言名称映射
#   · read.py        —— READ 角色的提示词原文
#   · write.py       —— WRITE 角色的提示词原文
#   · readback.py    —— READBACK 角色的提示词原文
#   · fix.py         —— FIX 角色的提示词原文

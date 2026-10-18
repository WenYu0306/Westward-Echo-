# 【中文·这个文件的作用】
# 这是 src/agent/nodes/ 目录的"门牌"（Python 包标识文件）。
# 让 Python 把 nodes/ 当作一个可导入的包。
#
# 本包放着翻译流水线 5 个角色节点的具体实现，每个文件对应一个角色：
#   · read.py          —— READ 节点：中文网文老书虫，分析原文和画面缺口
#   · review_terms.py  —— REVIEW_TERMS 节点：术语复核
#   · write.py         —— WRITE 节点：英文类型小说作家，用目标语言重讲章节
#   · readback.py      —— READBACK 节点：毫不知情的外国读者，冷读打分
#   · fix.py           —— FIX 节点：编辑，靶向修补冷读者指出的问题

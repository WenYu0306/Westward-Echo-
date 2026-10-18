# 【中文·这个文件的作用】
# 这是 src/agent/ 目录的"门牌"（Python 包标识文件）。
# Python 只有看到 __init__.py，才会把这个目录当作一个可导入的"包"（package），
# 允许外部代码写 from src.agent import ... 来使用里面的模块。
#
# 本包是翻译流水线的核心，包含三个子部分：
#   · graph.py       —— 流水线总装（把角色连成轨道、跑列车）
#   · nodes/         —— 5 个角色节点的具体实现
#   · prompts/       —— 喂给 AI 的角色说明书和任务单（提示词）
#   · state.py       —— 所有角色共用的白板（状态定义）
#   · fidelity.py    —— 文化忠实度检查
#   · parse_utils.py —— 解析 AI 返回结果的工具函数

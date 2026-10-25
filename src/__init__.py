"""Westward Echo source package.

【中文】这是 src 包的入口文件。Python 看到目录下有这个文件，
才会把整个目录当作一个"包"（package），允许别的代码用
from src.xxx import ... 的方式引用里面的模块。

src 下按职责分成以下子模块：
  · agent   —— 翻译流水线核心（4 个 AI 角色的编排、术语库、提示词）
  · api     —— 网页接口（FastAPI 路由、身份校验、限流）
  · glossary—— 术语库两层存储（精确匹配 + 语义匹配）
  · 其余文件 —— 章节切分、EPUB 打包、CMS 对接、敏感词检测等工具模块

本文件本身不包含任何业务逻辑，仅作为包的标记存在。
"""

import sys

# 【中文】项目要求 Python 3.11+（README §Quick start、Dockerfile、CI 都是 3.11）。
# 但 macOS 自带的 `python3` 是 3.9，代码里的 `X | None` 写法在 3.9 上会抛
# "unsupported operand type(s) for |"——报错位置在语法使用点，完全看不出是版本问题。
# 在包入口先检查版本，把晦涩的 TypeError 换成一句能照做的提示。
MIN_PYTHON = (3, 11)


def check_python_version(version_info=None) -> None:
    """Raise a clear error when the interpreter is older than 3.11.

    【中文】版本守卫。传 None 时检查当前解释器；测试可传入伪造的版本元组。
    """
    info = sys.version_info if version_info is None else version_info
    if tuple(info[:2]) < MIN_PYTHON:
        running = ".".join(str(x) for x in tuple(info[:3]))
        raise RuntimeError(
            f"Westward Echo requires Python 3.11+; this is {running} "
            f"({sys.executable}). Use `python3.11` — see README §Quick start."
        )


check_python_version()

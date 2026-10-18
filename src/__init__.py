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

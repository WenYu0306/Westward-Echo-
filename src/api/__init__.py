"""HTTP API package for Westward Echo.

【中文】这个包（package）放着所有 HTTP 接口模块。
所谓"HTTP 接口"就是程序之间对话的窗口——网页上点一下按钮，
其实就是往某个网址发一条请求，这些模块负责接收请求、返回结果。

包含的模块：
  · routes.py   —— 主力接口（提交翻译任务、查进度、取结果等）
  · auth.py     —— 身份校验中间件（检查 API 密钥）
  · rate_limit.py —— 限流中间件（防止被刷爆）
  · logging.py  —— 统一日志配置
  · cms.py      —— 内容管理系统对接接口
  · review.py   —— 术语审核接口（人工确认译名）
  · editor.py   —— 段落编辑器接口（人工逐段改稿）
"""

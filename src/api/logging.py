"""Structured logging for the translation service.

【中文·这个文件是干什么的】
这是西渡服务的"日志系统"配置。所谓"日志"就是程序运行时自动写下的记录，
像飞机的黑匣子一样，出了问题可以回溯当时发生了什么。

【中文·它在整个流程里的位置】
被 src/main.py 和其他几乎所有模块 import 使用。
其他文件里写的 logger.info(...)、logger.warning(...) 等调用，
输出的格式和去向都由本文件决定。

【中文·核心概念解释】
  · 结构化日志 —— 每条日志不是随意的一段文字，而是一个 JSON 对象，
    包含时间戳（ts）、级别（level）、来源（logger）、消息（msg）等字段。
    好处是可以用程序自动分析、过滤、统计，而不是靠人眼一行行看。
  · JSON Lines —— 每行一个 JSON 对象的文件格式，方便逐行读取和处理。
  · 双通道输出 —— 同时写到两个地方：
    ① 文件（data/app.log）—— JSON 格式，给机器分析和事后排查用。
    ② 控制台（终端屏幕）—— 人类可读格式，开发时实时看。
  · 日志级别 —— 从低到高：DEBUG < INFO < WARNING < ERROR < CRITICAL。
    控制台只显示 INFO 及以上，文件记录全部（包括 DEBUG）。
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ..config import LOG_FILE, LOG_LEVEL


def setup_logging():
    """Configure structured JSON-line logging to file + human-readable to console.

    【中文】初始化日志系统：创建两个输出通道（文件 + 控制台），设置格式和级别。
    返回值：根日志器对象（root logger），其他模块通过它来写日志。

    【中文·为什么要在函数里做，而不是写在文件顶层】
    因为需要在导入时就执行一次初始化，但又要保证只执行一次。
    包成函数后，下面的 logger = setup_logging() 在模块加载时调用一次，
    后续 import 这个模块时不会重复初始化。
    """

    # 【中文】确保日志文件所在的目录存在，不存在就自动创建。
    # parents=True 表示连父目录也一起创建；exist_ok=True 表示已存在也不报错。
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)

    # 【中文】获取名为 "westward_echo" 的日志器作为根日志器。
    # Python 的日志系统是按名字分层的，所有以 "westward_echo." 开头的子日志器
    # （比如 "westward_echo.api.routes"）都会继承这里的配置。
    root = logging.getLogger("westward_echo")
    # 【中文】设置最低记录级别。getattr 把字符串 "INFO" 转成 logging.INFO 常量，
    # 如果 .env 里写了个无效级别名，就退回到 INFO。
    root.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    # File handler: JSON Lines
    # 【中文·文件通道】把所有级别的日志（包括 DEBUG）以 JSON 格式写入文件。
    # encoding="utf-8" 确保中文内容不会乱码。
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.DEBUG)  # 文件记录全部级别，不遗漏任何细节
    fh.setFormatter(_JSONFormatter())  # 使用自定义的 JSON 格式化器
    root.addHandler(fh)

    # Console handler: human-readable
    # 【中文·控制台通道】只把 INFO 及以上的日志显示在终端屏幕上，
    # 格式是人类可读的时间+级别+来源+消息，方便开发时实时观察。
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)  # 控制台只显示重要信息，DEBUG 太吵就不显示了
    ch.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(ch)

    return root


class _JSONFormatter(logging.Formatter):
    """Custom formatter that outputs each log record as a single JSON line.

    【中文】自定义格式化器：把每条日志记录转成一个 JSON 字符串。
    输出的 JSON 包含四个固定字段：
      · ts     —— UTC 时间的 ISO 8601 格式时间戳
      · level  —— 日志级别（INFO / WARNING / ERROR 等）
      · logger —— 日志器名称（标识是哪个模块写的）
      · msg    —— 日志消息正文
    如果日志附带了异常信息（比如 try/except 捕获的错误），
    还会多一个 error 字段记录错误描述。
    """

    def format(self, record):
        """Format a log record as a JSON string.

        【中文】把一条日志记录格式化为 JSON 字符串。
        参数 record 是 Python 日志系统传入的记录对象，
        包含级别、消息、异常信息等所有字段。
        """
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # 【中文】如果这条日志附带了异常信息（exc_info），
        # 把错误描述加到 JSON 里，方便事后排查。
        if record.exc_info and record.exc_info[1]:
            entry["error"] = str(record.exc_info[1])
        # ensure_ascii=False 让中文直接输出而不是转成 \uXXXX 编码，
        # 日志文件更易读。
        return json.dumps(entry, ensure_ascii=False)


# 【中文】模块加载时立即执行初始化，生成全局唯一的日志器实例。
# 其他文件 from ..api.logging import logger 拿到的就是这个对象，
# 全项目共享同一个日志配置，不会出现格式不一致的问题。
logger = setup_logging()

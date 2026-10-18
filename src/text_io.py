"""Encoding-tolerant reading of Chinese novel text.

Chinese web-novel files in the wild are UTF-8, GBK/GB2312 (older sites),
GB18030 (the national standard), and occasionally UTF-16 or Big5. Two callers
need to turn such bytes into text:

  * the API upload path (``src/api/routes.py``), which receives bytes;
  * the development scripts in ``scripts/``, which receive a file path.

This module is the single decode ladder for both — an earlier version of the
ladder was inlined in ``routes.py`` while ``scripts/`` imported a second copy,
and the two drifted apart until the scripts broke outright.

【中文·编码容错的中文小说文本读取工具】
一、这个文件是干什么的？
    把各种编码的中文小说文件正确读成 Python 字符串。
    中文网文来源五花八门，编码格式各不相同（UTF-8、GBK、GB18030 等），
    直接用一种编码去读很可能乱码。本模块提供一套"逐个尝试"的解码梯子，
    确保不管什么编码都能正确读入。

二、它在整个流程里的位置：
    · API 上传路径（routes.py）收到用户上传的文件字节后，调这里的函数解码
    · scripts/ 目录下的开发脚本也调这里读文件
    这两处曾经各自维护一份解码逻辑，后来漂移导致脚本坏掉，
    所以现在统一收归到这一个文件里。

三、核心概念：
    · 编码（encoding）：计算机把文字变成字节存储的规则。
      同一段中文，用 UTF-8 存是一串字节，用 GBK 存是另一串。
      读文件时必须用对编码，否则出现乱码。
    · 解码梯子（decode ladder）：按可能性从高到低依次尝试多种编码，
      第一个能成功解码且不产生乱码的就是正确答案。
    · BOM（Byte Order Mark）：文件开头的特殊标记字节，
      能明确指示文件用了哪种编码，比猜测更可靠。

四、本文件三个主要函数：
    · decode_bytes()     —— 纯字节解码，按编码列表逐个试
    · looks_like_chinese()—— 判断解码结果是否真的是中文
    · detect_and_read()  —— 完整的文件读取，含 BOM 检测 + 中文验证
"""

# Required: pyproject.toml pins the runtime to Python 3.9, where PEP 604 unions
# (`str | None`) are evaluated at import time and raise TypeError. Every other
# module using that syntax carries this import; this one was missing it, so
# importing src.text_io — and therefore src.api.routes and the whole API — crashed
# under 3.9 while tests happened to run on 3.11 and hid it.
#
# 【中文】这行导入是为了兼容 Python 3.9。
# Python 3.9 不支持 ``str | None`` 这种新式类型写法（会在导入时报错），
# 加了 ``from __future__ import annotations`` 就能让所有模块都用新写法。
# 本文件曾经漏了这行，导致在 3.9 环境下整个 API 启动就崩，
# 但因为测试跑在 3.11 上所以一直没发现。
from __future__ import annotations

from pathlib import Path

# Ordered by likelihood for Chinese web novels.
# 【中文】默认编码尝试顺序：按中文网文实际出现的频率排列。
# UTF-8 最常见排第一；GB18030 是国标，覆盖所有汉字；
# GBK/GB2312 是老网站常用编码。
DEFAULT_ENCODINGS = ("utf-8", "gb18030", "gbk", "gb2312")

# The wider ladder used when reading a file whose encoding we know nothing about.
# 【中文】更宽的编码梯子：用于完全不知道编码的情况。
# 比默认列表多了 Big5（繁体中文）、UTF-16、latin-1（兜底，什么字节都能解）。
SCRIPT_ENCODINGS = ("utf-8", "gb18030", "gbk", "gb2312", "big5", "utf-16", "latin-1")

# A Chinese novel sample is expected to be mostly Chinese characters.
# 【中文】中文检测参数：取前 10000 个字符做样本，
# 其中至少 5% 是中文字符才算"这是中文文本"。
_CHINESE_SAMPLE = 10000
_CHINESE_RATIO = 0.05


def decode_bytes(raw: bytes, encodings=DEFAULT_ENCODINGS) -> str | None:
    """Decode ``raw`` with the first encoding in ``encodings`` that works.

    Returns None when none of them can decode the bytes — the caller decides
    what error to surface. No byte-order-mark handling and no content
    validation happen here; use :func:`detect_and_read` for that.

    【中文】用编码列表里的编码逐个尝试解码原始字节。
    · 参数 raw：要解码的原始字节数据
    · 参数 encodings：编码尝试列表，默认用 DEFAULT_ENCODINGS
    · 返回：解码成功的字符串，全部失败则返回 None
    · 注意：这个函数不做 BOM 检测也不验证内容是否是中文，
      只做纯粹的编码尝试。需要完整功能请用 detect_and_read()。
    """
    # 逐个编码尝试，第一个成功就返回
    for enc in encodings:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            # UnicodeDecodeError = 这个编码解不了这段字节
            # LookupError = 这个编码名 Python 不认识
            continue
    return None


def looks_like_chinese(text: str) -> bool:
    """Heuristic: is this decoded text mostly Chinese characters?

    A wrong encoding usually "succeeds" (latin-1 decodes anything), so a
    successful decode is not evidence of the right encoding. This is.

    【中文】判断解码后的文本是否真的是中文。
    为什么需要这个检查：有些编码（比如 latin-1）什么字节都能"解码成功"，
    但解出来的是一堆乱码。只有确认文本里确实有大量中文字符，
    才能相信选对了编码。
    · 做法：取前 _CHINESE_SAMPLE 个字符，统计中文字符占比
    · 中文字符的 Unicode 范围是 \\u4e00 ~ \\u9fff（CJK 统一汉字基本区）
    · 占比超过 _CHINESE_RATIO（5%）就判定为中文文本
    """
    sample = text[:_CHINESE_SAMPLE]
    if not sample:
        return False
    # 统计样本中落在 CJK 统一汉字基本区的字符数
    chinese_chars = sum(1 for c in sample if "\u4e00" <= c <= "\u9fff")
    return chinese_chars > len(sample) * _CHINESE_RATIO


def detect_and_read(file_path: str | Path) -> tuple[str, str]:
    """Read a Chinese text file, trying common encodings.

    Returns ``(text, detected_encoding)``. Raises ValueError if nothing
    decodes, or OSError if the file cannot be read.

    【中文】读取中文文本文件的完整函数，自动检测编码。
    · 参数 file_path：文件路径（字符串或 Path 对象）
    · 返回：(解码后的文本, 检测到的编码名) 元组
    · 异常：所有编码都失败时抛 ValueError；文件读不了时抛 OSError

    工作流程：
      ① 先检查 BOM（文件头特殊标记），有就直接用对应编码，最可靠
      ② 没有 BOM 就用 SCRIPT_ENCODINGS 逐个尝试，
         每个编码解完后用 looks_like_chinese() 验证是不是真中文
      ③ 如果都不行，最后用 errors="replace" 强制解码（乱码用  替代），
         总比直接报错好
    """
    raw = Path(file_path).read_bytes()

    # ── BOM 检测：文件头有特殊标记就直接用，比猜测可靠 ──
    # UTF-8 BOM 是三个字节 EF BB BF
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8"), "utf-8-bom"
    # UTF-16 BOM 是 FF FE（小端）或 FE FF（大端）
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw[2:].decode("utf-16"), "utf-16-bom"

    # ── 逐个编码尝试 + 中文验证 ──
    for enc in SCRIPT_ENCODINGS:
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        # 解码成功后还要验证确实是中文，防止 latin-1 之类的假成功
        if looks_like_chinese(text):
            return text, enc

    # ── 最后兜底：用 replace 模式强制解码，乱码显示为  ──
    # 比起直接报错，返回一个部分可读的结果更实用
    for enc in SCRIPT_ENCODINGS:
        try:
            return raw.decode(enc, errors="replace"), f"{enc} (with replacements)"
        except (UnicodeDecodeError, LookupError):
            continue

    raise ValueError("Cannot decode file with any known encoding.")

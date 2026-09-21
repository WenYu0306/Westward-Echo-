"""Encoding-tolerant reading of Chinese novel text.

Chinese web-novel files in the wild are UTF-8, GBK/GB2312 (older sites),
GB18030 (the national standard), and occasionally UTF-16 or Big5. Two callers
need to turn such bytes into text:

  * the API upload path (``src/api/routes.py``), which receives bytes;
  * the development scripts in ``scripts/``, which receive a file path.

This module is the single decode ladder for both — an earlier version of the
ladder was inlined in ``routes.py`` while ``scripts/`` imported a second copy,
and the two drifted apart until the scripts broke outright.
"""

from pathlib import Path

# Ordered by likelihood for Chinese web novels.
DEFAULT_ENCODINGS = ("utf-8", "gb18030", "gbk", "gb2312")

# The wider ladder used when reading a file whose encoding we know nothing about.
SCRIPT_ENCODINGS = ("utf-8", "gb18030", "gbk", "gb2312", "big5", "utf-16", "latin-1")

# A Chinese novel sample is expected to be mostly Chinese characters.
_CHINESE_SAMPLE = 10000
_CHINESE_RATIO = 0.05


def decode_bytes(raw: bytes, encodings=DEFAULT_ENCODINGS) -> str | None:
    """Decode ``raw`` with the first encoding in ``encodings`` that works.

    Returns None when none of them can decode the bytes — the caller decides
    what error to surface. No byte-order-mark handling and no content
    validation happen here; use :func:`detect_and_read` for that.
    """
    for enc in encodings:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def looks_like_chinese(text: str) -> bool:
    """Heuristic: is this decoded text mostly Chinese characters?

    A wrong encoding usually "succeeds" (latin-1 decodes anything), so a
    successful decode is not evidence of the right encoding. This is.
    """
    sample = text[:_CHINESE_SAMPLE]
    if not sample:
        return False
    chinese_chars = sum(1 for c in sample if "\u4e00" <= c <= "\u9fff")
    return chinese_chars > len(sample) * _CHINESE_RATIO


def detect_and_read(file_path: str | Path) -> tuple[str, str]:
    """Read a Chinese text file, trying common encodings.

    Returns ``(text, detected_encoding)``. Raises ValueError if nothing
    decodes, or OSError if the file cannot be read.
    """
    raw = Path(file_path).read_bytes()

    # Byte-order marks are unambiguous — trust them over any ladder.
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8"), "utf-8-bom"
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw[2:].decode("utf-16"), "utf-16-bom"

    for enc in SCRIPT_ENCODINGS:
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if looks_like_chinese(text):
            return text, enc

    # Last resort: keep as much as we can rather than failing outright.
    for enc in SCRIPT_ENCODINGS:
        try:
            return raw.decode(enc, errors="replace"), f"{enc} (with replacements)"
        except (UnicodeDecodeError, LookupError):
            continue

    raise ValueError("Cannot decode file with any known encoding.")

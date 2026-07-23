"""Encoding detection for uploaded novel files.

Chinese web novels downloaded from various sources use different encodings:
UTF-8 (most modern sites), GBK/GB2312 (older Chinese sites), GB18030 (national standard).
"""

import codecs


# Ordered by likelihood for Chinese web novels
_ENCODINGS_TO_TRY = ["utf-8", "gbk", "gb2312", "gb18030", "utf-16", "big5", "latin-1"]


def detect_and_read(file_path: str) -> tuple[str, str]:
    """Try common Chinese encodings and return (text, detected_encoding).

    Raises ValueError if no encoding works.
    """
    with open(file_path, "rb") as f:
        raw = f.read()

    # Quick check: UTF-8 BOM
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8"), "utf-8-bom"

    # Quick check: UTF-16 BOM
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw[2:].decode("utf-16"), "utf-16-bom"

    errors_list = []
    for enc in _ENCODINGS_TO_TRY:
        try:
            text = raw.decode(enc)
            # Sanity check: if decoded text contains common Chinese chars, it's likely correct
            if _looks_like_chinese(text):
                return text, enc
        except (UnicodeDecodeError, LookupError) as e:
            errors_list.append(f"{enc}: {e}")
            continue

    # Last resort: return the first successful decode even if it looks wrong
    for enc in _ENCODINGS_TO_TRY:
        try:
            return raw.decode(enc, errors="replace"), f"{enc} (with replacements)"
        except (UnicodeDecodeError, LookupError):
            continue

    raise ValueError(f"Cannot decode file. Errors: {'; '.join(errors_list)}")


def _looks_like_chinese(text: str) -> bool:
    """Heuristic: does the text contain actual Chinese characters?"""
    sample = text[:10000]
    chinese_chars = sum(1 for c in sample if "一" <= c <= "鿿")
    # If more than 5% of the sample is Chinese characters, it's Chinese text
    return chinese_chars > len(sample) * 0.05

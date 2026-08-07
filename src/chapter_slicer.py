"""Adaptive chapter splitting for chapters that would exceed output token limits.

The adversarial test proved that chapters outputting >12K English characters
(~4000 output tokens) combined with growing prompt overhead (~5000 input tokens)
can exhaust DeepSeek V4's context window, producing empty translations with
Chinese character residue.

This module auto-detects chapters that would exceed the safe threshold and
splits them at paragraph boundaries. Each segment is translated independently
with bridging context from the previous segment, then merged.
"""

import re

# ── Thresholds ────────────────────────────────────────────────

# Max Chinese chars per segment — empirically, 3000 CN chars → ~9000 EN chars
# → ~3000 output tokens. With ~5000 prompt overhead → ~8000 total, well within
# the 16384 limit with margin for noise.
MAX_CHARS_PER_SEGMENT = 3000

# If a chapter is below this, it doesn't need splitting (even with overhead,
# 4000 CN chars × 3x EN ratio = 12000 EN chars ~= 4000 tokens, plus overhead
# ~5000 tokens = 9000 total — safe.)
SPLIT_THRESHOLD_CHARS = 4500


def should_split(chapter_content: str) -> bool:
    """Return True if this chapter would risk exhausting the output token limit."""
    cn_chars = len(chapter_content.replace("\n", "").replace(" ", ""))
    return cn_chars > SPLIT_THRESHOLD_CHARS


def split_chapter(
    chapter_content: str, max_segment_chars: int = MAX_CHARS_PER_SEGMENT
) -> list[dict]:
    """Split a long chapter into segments at natural boundaries.

    Priority: paragraph break (\\n\\n) → sentence break (。！？) → character limit.
    When a single paragraph exceeds the limit, it is split at sentence boundaries.
    When a single sentence exceeds the limit, it is split at the character limit.

    Returns a list of dicts: [{index, total, content, is_first, is_last}].
    Returns a single segment for empty/whitespace content.
    """
    if not chapter_content or not chapter_content.strip():
        return [{
            "index": 1, "total": 1, "content": chapter_content or "",
            "is_first": True, "is_last": True,
        }]

    blocks = [b.strip() for b in chapter_content.split("\n\n") if b.strip()]
    segments = []
    current_blocks: list[str] = []  # Full paragraphs accumulated so far
    current_chars = 0

    def _flush_segment():
        nonlocal current_blocks, current_chars
        if current_blocks:
            segments.append("\n\n".join(current_blocks))
            current_blocks = []
            current_chars = 0

    def _split_block_at_sentences(block: str) -> list[str]:
        """Split a single oversized block at sentence boundaries."""
        sentences = re.split(r'(?<=[。！？])', block)
        result = []
        buf = ""
        buf_chars = 0
        for s in sentences:
            sc = len(s.replace("\n", "").replace(" ", ""))
            if buf_chars + sc > max_segment_chars and buf:
                result.append(buf.strip())
                buf = s
                buf_chars = sc
            else:
                buf += s
                buf_chars += sc
        if buf.strip():
            result.append(buf.strip())
        return result

    for block in blocks:
        block_chars = len(block.replace("\n", "").replace(" ", ""))

        # A single block is too large → split at sentence boundaries
        if block_chars > max_segment_chars:
            # Flush whatever we've accumulated
            _flush_segment()
            # Split this massive block into sentence-level chunks
            for sb in _split_block_at_sentences(block):
                segments.append(sb)
            continue

        # Normal case: block fits, check if adding it exceeds the limit
        if current_chars + block_chars > max_segment_chars:
            _flush_segment()

        current_blocks.append(block)
        current_chars += block_chars

    _flush_segment()

    total = len(segments)
    return [
        {
            "index": i + 1,
            "total": total,
            "content": seg,
            "is_first": i == 0,
            "is_last": i == total - 1,
        }
        for i, seg in enumerate(segments)
    ]


def build_segment_title(original_title: str, segment: dict) -> str:
    """Generate a chapter title for a segment."""
    return f"{original_title} [Part {segment['index']}/{segment['total']}]"

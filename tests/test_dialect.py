"""Tests for dialect detection and mapping."""

from src.dialect import (
    DIALECT_MAPPING,
    DIALECT_MARKERS,
    build_dialect_context,
    detect_dialects,
    get_dialect_hint,
)


class TestDetectDialects:

    def test_dongbei_detection(self):
        text = "你整啥呢？咋地了？俺也不知道啊，老鼻子人了。"
        result = detect_dialects(text)
        assert "dongbei" in result
        assert result["dongbei"] >= 4

    def test_sichuan_detection(self):
        text = "啥子嘛，你咋子晓得的？这个地方巴适得很，要得要得。"
        result = detect_dialects(text)
        assert "sichuan" in result
        assert result["sichuan"] >= 4

    def test_beijing_detection(self):
        text = "您甭跟这儿侃大山了，这事儿门儿清，倍儿简单。"
        result = detect_dialects(text)
        assert "beijing" in result
        assert result["beijing"] >= 4

    def test_shanghai_detection(self):
        text = "侬好伐？阿拉晓得的，侬勿要拎不清。"
        result = detect_dialects(text)
        assert "shanghai" in result

    def test_cantonese_detection(self):
        text = "冇问题啦，佢哋嘅嘢食好正，靓仔！"
        result = detect_dialects(text)
        assert "cantonese" in result

    def test_no_dialect_in_standard_mandarin(self):
        text = "她走进房间，看着窗外的风景，心中涌起一阵温暖的感觉。"
        result = detect_dialects(text)
        assert len(result) == 0

    def test_single_marker_is_filtered(self):
        """A single marker is not enough — needs >= 2 to reduce false positives."""
        text = "这件事整得我有点不舒服。"  # Only "整" appears
        result = detect_dialects(text)
        assert len(result) == 0  # Single marker filtered out

    def test_mixed_dialects(self):
        text = "你整啥呢？这个地方巴适得很哦。"
        result = detect_dialects(text)
        # dongbei: 整, 啥 (2 markers). sichuan: 巴适 (1 marker, filtered)
        assert "dongbei" in result


class TestDialectHint:

    def test_dongbei_hint(self):
        hint = get_dialect_hint("dongbei")
        assert "Southern American English" in hint
        assert "y'all" in hint

    def test_unknown_dialect_returns_none(self):
        assert get_dialect_hint("alienese") is None

    def test_all_dialects_have_hints(self):
        for name in DIALECT_MARKERS:
            assert get_dialect_hint(name) is not None, f"Missing hint for {name}"

    def test_all_dialects_have_mappings(self):
        for name in DIALECT_MARKERS:
            assert name in DIALECT_MAPPING, f"Missing mapping for {name}"


class TestDialectContext:

    def test_empty_for_no_dialect(self):
        ctx = build_dialect_context("标准普通话文本，没有任何方言标记。")
        assert ctx == ""

    def test_returns_context_for_dongbei(self):
        ctx = build_dialect_context("你整啥呢？咋地了？俺不知道啊，老鼻子人了。")
        assert "DIALECT CONTEXT" in ctx
        assert "东北话" in ctx
        assert "Southern American English" in ctx

    def test_returns_context_for_sichuan(self):
        ctx = build_dialect_context("啥子嘛，巴适得很，要得要得。")
        assert "四川话" in ctx
        assert "Texas" in ctx

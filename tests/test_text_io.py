"""Tests for encoding-tolerant text reading.

These pin the behaviour the dev scripts depend on: real fixture files in this
repo are GBK/Big5/UTF-16, not just UTF-8.
"""

import pytest

from src.text_io import DEFAULT_ENCODINGS, decode_bytes, detect_and_read, looks_like_chinese


class TestDecodeBytes:
    def test_utf8_text_decodes(self):
        assert decode_bytes("聋婆婆".encode()) == "聋婆婆"

    def test_gbk_text_decodes_via_the_ladder(self):
        assert decode_bytes("聋婆婆".encode("gbk")) == "聋婆婆"

    def test_utf8_wins_over_gbk_for_utf8_input(self):
        # A GBK decode of UTF-8 bytes would silently succeed with mojibake,
        # so ordering matters: utf-8 must be tried first.
        assert decode_bytes("聋婆婆".encode()) == "聋婆婆"

    def test_undecodable_bytes_return_none(self):
        assert decode_bytes(b"\xff\xfe\x00\x01\x02") is None

    def test_empty_bytes_decode_to_empty_string(self):
        assert decode_bytes(b"") == ""

    def test_custom_ladder_is_respected(self):
        raw = "聋婆婆".encode("gb18030")
        assert decode_bytes(raw, encodings=("gb18030",)) == "聋婆婆"
        assert decode_bytes(raw, encodings=("utf-8",)) is None

    def test_big5_bytes_decode_through_a_big5_ladder(self):
        raw = "聾婆婆".encode("big5")
        assert decode_bytes(raw, encodings=("big5",)) == "聾婆婆"

    def test_default_ladder_contents(self):
        assert DEFAULT_ENCODINGS == ("utf-8", "gb18030", "gbk", "gb2312")


class TestLooksLikeChinese:
    def test_pure_chinese_text_is_chinese(self):
        assert looks_like_chinese("聋婆婆坐在老槐树下等他回来。") is True

    def test_english_text_is_not_chinese(self):
        assert looks_like_chinese("Deaf Granny sat under the locust tree.") is False

    def test_empty_text_is_not_chinese(self):
        assert looks_like_chinese("") is False

    def test_ninety_five_percent_english_is_not_chinese(self):
        text = "聋" + "a" * 95  # 1/96 ≈ 1% Chinese, under the 5% floor
        assert looks_like_chinese(text) is False

    def test_ten_percent_chinese_is_chinese(self):
        text = "聋" + "a" * 9  # 10% Chinese, over the 5% floor
        assert looks_like_chinese(text) is True

    def test_three_percent_chinese_is_not_chinese(self):
        # 3% sits between the floor (5%) and double the floor (10%): if the
        # per-character count were doubled, this would wrongly read as Chinese.
        text = "聋" * 3 + "a" * 97
        assert looks_like_chinese(text) is False

    def test_five_percent_chinese_is_not_over_the_floor(self):
        # The comparison is strictly greater-than: exactly 5% does not pass.
        text = "聋" * 5 + "a" * 95
        assert looks_like_chinese(text) is False

    def test_six_percent_chinese_is_chinese(self):
        text = "聋" * 6 + "a" * 94
        assert looks_like_chinese(text) is True

    def test_sample_is_limited_to_the_first_ten_thousand_chars(self):
        # Chinese only beyond the sample window must not count.
        text = "a" * 10000 + "聋" * 500
        assert looks_like_chinese(text) is False

    def test_one_chinese_char_just_past_the_window_does_not_tip_the_verdict(self):
        # Exact window boundary: the first 10000 chars hold exactly 5.00%
        # Chinese (not "over" the floor), so the 10001st character — Chinese —
        # must not change the answer.
        text = "聋" * 500 + "a" * 9500 + "聋"
        assert len(text) == 10001
        assert looks_like_chinese(text) is False

    def test_chinese_inside_the_sample_window_counts(self):
        text = "聋" * 501 + "a" * 9499  # 5.01% inside the window
        assert looks_like_chinese(text) is True


class TestDetectAndRead:
    def test_utf8_file(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_text("聋婆婆点了一盏纸灯。", encoding="utf-8")
        text, enc = detect_and_read(p)
        assert text == "聋婆婆点了一盏纸灯。"
        assert enc == "utf-8"

    def test_utf8_bom_file(self, tmp_path):
        p = tmp_path / "bom.txt"
        p.write_bytes("\ufeff聋婆婆点了一盏纸灯。".encode("utf-8"))
        text, enc = detect_and_read(p)
        assert text == "聋婆婆点了一盏纸灯。"
        assert enc == "utf-8-bom"

    def test_utf16_bom_file(self, tmp_path):
        p = tmp_path / "u16.txt"
        p.write_bytes("聋婆婆点了一盏纸灯。".encode("utf-16"))
        text, enc = detect_and_read(p)
        assert text == "聋婆婆点了一盏纸灯。"
        assert enc == "utf-16-bom"

    def test_gbk_file_is_detected_as_gbk_family(self, tmp_path):
        p = tmp_path / "gbk.txt"
        p.write_bytes("聋婆婆点了一盏纸灯。".encode("gbk"))
        text, enc = detect_and_read(p)
        assert text == "聋婆婆点了一盏纸灯。"
        assert enc in ("gbk", "gb18030")

    def test_big5_is_shadowed_by_gb18030(self, tmp_path):
        # Known limitation, documented rather than pretended away: GB18030 is a
        # superset that decodes Big5 bytes into CJK mojibake which still passes
        # the "looks Chinese" heuristic, so a real Big5 file is reported as
        # gb18030. Every non-UTF-8 fixture in this repo is GBK-family, so the
        # ladder is ordered for that case; proper Big5 support would need a
        # statistical detector rather than a try-and-look ladder.
        p = tmp_path / "big5.txt"
        p.write_bytes("聾婆婆點了一盞紙燈。".encode("big5"))
        text, enc = detect_and_read(p)
        assert enc == "gb18030"
        assert text, "bytes must still decode to something, not raise"

    def test_gbk_fixture_style_text_is_decoded_faithfully(self, tmp_path):
        p = tmp_path / "gbk.txt"
        p.write_bytes("《间客》\r\n内容简介：\r\n　　世界上有两件东西。".encode("gbk"))
        text, enc = detect_and_read(p)
        assert enc in ("gbk", "gb18030")
        assert "内容简介" in text

    def test_ascii_only_file_falls_back_with_replacements(self, tmp_path):
        # No encoding "looks Chinese" here, so the replacement pass runs.
        p = tmp_path / "ascii.txt"
        p.write_bytes(b"just english text")
        text, enc = detect_and_read(p)
        assert text == "just english text"
        assert "replacements" in enc

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(OSError):
            detect_and_read(tmp_path / "nope.txt")

    def test_accepts_a_string_path(self, tmp_path):
        p = tmp_path / "c.txt"
        p.write_text("聋婆婆。", encoding="utf-8")
        text, _ = detect_and_read(str(p))
        assert text == "聋婆婆。"

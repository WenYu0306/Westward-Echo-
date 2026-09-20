"""Unit tests for src/measurements.py — Chinese unit detection and hint building."""

from src.measurements import (
    _format_number,
    _parse_chinese_number,
    _parse_chinese_simple,
    build_measurements_hint,
    detect_measurements,
)


class TestDetectMeasurements:
    """Tests for detect_measurements()."""

    def test_detects_li_and_jin_in_mixed_text(self):
        """'三万里外有一斤肉' should return keys for 里 and 斤."""
        result = detect_measurements("三万里外有一斤肉")
        assert "里" in result
        assert "斤" in result

    def test_returns_empty_dict_for_text_with_no_units(self):
        """Plain text with no measurement units returns an empty dict."""
        result = detect_measurements("普通文本，没有单位。")
        assert result == {}

    def test_detects_wan_in_numeric_context(self):
        """'三万大军' should detect 万."""
        result = detect_measurements("三万大军")
        assert "万" in result

    def test_detects_yi_in_numeric_context(self):
        """'十亿人口' should detect 亿."""
        result = detect_measurements("十亿人口")
        assert "亿" in result

    def test_handles_mixed_arabic_chinese_still_catches_unit(self):
        """'3万里' uses Arabic digit but should still catch the 里 unit."""
        result = detect_measurements("3万里")
        assert "里" in result


class TestBuildMeasurementsHint:
    """Tests for build_measurements_hint()."""

    def test_returns_empty_string_when_no_measurements(self):
        """No units found should produce an empty string."""
        result = build_measurements_hint("普通文本，没有单位。")
        assert result == ""

    def test_returns_non_empty_string_when_measurements_present(self):
        """Text containing units should produce a non-empty hint string."""
        result = build_measurements_hint("三万里外有一斤肉")
        assert result != ""
        assert len(result) > 0

    def test_hint_contains_both_measurement_mentions(self):
        """The hint should reference both 里 and 斤 from the source text."""
        result = build_measurements_hint("三万里外有一斤肉")
        # The output mentions each matched phrase
        assert "三万里" in result
        assert "一斤" in result


class TestChineseNumberParsing:
    """Chinese-numeral parsing — the core the mutation test showed was uncovered."""

    def test_pure_arabic(self):
        assert _parse_chinese_number("30000") == 30000
        assert _parse_chinese_number("0") == 0

    def test_mixed_arabic_wan(self):
        assert _parse_chinese_number("3万") == 30000

    def test_mixed_arabic_yi_decimal(self):
        assert _parse_chinese_number("3.5亿") == 350000000

    def test_pure_chinese_wan(self):
        assert _parse_chinese_number("三万") == 30000

    def test_wan_with_remainder(self):
        assert _parse_chinese_number("三万五千") == 35000

    def test_nested_wan_remainder(self):
        assert _parse_chinese_number("二十万三千") == 203000

    def test_yi_plain(self):
        assert _parse_chinese_number("三亿") == 300000000

    def test_simple_thousands(self):
        assert _parse_chinese_simple("三千五百") == 3500

    def test_ten_variants(self):
        assert _parse_chinese_simple("十") == 10
        assert _parse_chinese_simple("十五") == 15

    def test_zero(self):
        assert _parse_chinese_simple("零") == 0
        assert _parse_chinese_simple("〇") == 0

    def test_liang_as_two(self):
        assert _parse_chinese_number("两万") == 20000

    def test_empty_returns_none(self):
        assert _parse_chinese_number("") is None

    def test_invalid_returns_none(self):
        assert _parse_chinese_number("abc") is None
        assert _parse_chinese_number("三abc") is None

    def test_wan_without_prefix_defaults_to_one(self):
        assert _parse_chinese_number("万") == 10000

    def test_yi_without_prefix_defaults_to_one(self):
        assert _parse_chinese_number("亿") == 100000000

    def test_simple_arabic_digits(self):
        assert _parse_chinese_simple("123") == 123

    def test_wan_invalid_remainder_returns_none(self):
        assert _parse_chinese_number("万abc") is None


class TestFormatNumber:
    """Number formatting boundaries (B/M/thousands-separator/int-vs-float)."""

    def test_billions(self):
        assert _format_number(1_000_000_000) == "1.0B"
        assert _format_number(2_500_000_000) == "2.5B"

    def test_millions(self):
        assert _format_number(1_500_000) == "1.5M"

    def test_thousands_integer(self):
        assert _format_number(2000) == "2,000"
        assert _format_number(1500) == "1,500"

    def test_below_thousand(self):
        assert _format_number(999) == "999"
        assert _format_number(100) == "100"

    def test_fractional(self):
        assert _format_number(0.5) == "0.5"


class TestConversionAccuracy:
    """Unit-conversion coefficients — a wrong factor must fail these tests."""

    def test_li_to_km_miles(self):
        out = build_measurements_hint("三万里")
        assert "15,000 km" in out       # scale 0.5
        assert "9,320.6 miles" in out   # 0.621371 miles-per-km

    def test_jin_to_kg_lbs(self):
        out = build_measurements_hint("一斤")
        assert "0.5 kg" in out          # scale 0.5
        assert "1.1 lbs" in out         # 2.20462 lbs-per-kg

    def test_zhang_to_m_ft(self):
        out = build_measurements_hint("两丈")
        assert "6.7 m" in out           # scale 3.33
        assert "21.9 ft" in out         # /0.3048 m-per-ft

    def test_chi_to_m_in(self):
        out = build_measurements_hint("三尺")
        assert "1.0 m" in out           # scale 0.33
        assert "39.0 in" in out         # 39.3701 in-per-m

    def test_mu_to_hectare_acre(self):
        out = build_measurements_hint("十亩")
        assert "0.7 hectares" in out    # scale 0.067
        assert "0.1 acres" in out       # 0.1647 acres-per-hectare

    def test_wan_under_100_no_m_abbrev(self):
        out = build_measurements_hint("十万")
        assert "~100,000" in out        # digits=10 < 100 → no M conversion
        assert "1.0M" not in out

    def test_wan_over_100_uses_m(self):
        out = build_measurements_hint("百万")
        assert " / 1M" in out           # 万 >=100 分支特有：million 转换

    def test_yi_to_billions(self):
        out = build_measurements_hint("三亿")
        assert "0.3B" in out            # 300M = 0.3B

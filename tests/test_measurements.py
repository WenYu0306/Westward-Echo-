"""Unit tests for src/measurements.py — Chinese unit detection and hint building."""

from src.measurements import build_measurements_hint, detect_measurements


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

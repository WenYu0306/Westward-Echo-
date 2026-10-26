"""Tests for scripts/normalize_quotes.py — curly to straight mapping."""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "normalize_quotes.py"
_spec = importlib.util.spec_from_file_location("normalize_quotes_under_test", _SCRIPT)
nq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nq)


class TestNormalize:
    def test_maps_all_four_curly_characters(self):
        assert nq.normalize("“a” ‘b’") == '"a" \'b\''

    def test_is_idempotent(self):
        once = nq.normalize("“x”")
        assert nq.normalize(once) == once

    def test_leaves_other_punctuation_alone(self):
        text = "He said — “stop”…"
        assert nq.normalize(text) == 'He said — "stop"…'

    def test_count_curly(self):
        assert nq.count_curly("“a”b") == 2
        assert nq.count_curly("plain") == 0

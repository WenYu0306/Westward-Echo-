"""Tests for the per-chapter token ledger.

The bug this module fixes: the project could not answer "how many tokens did
chapter N actually use?" — usage lived in a process-global in-memory counter,
was never attributed to a node/model/chapter, was never written by the
production path (scripts/run_novel.py), and vanished behind `except Exception:
pass`. These tests pin the properties that make the question answerable.
"""
import json
import threading

import pytest

from src import token_ledger as T
from src.config import MODEL_PRICES_CNY_PER_M


class _Response:
    """Stand-in for a LangChain AIMessage."""

    def __init__(self, **payload):
        for key, value in payload.items():
            setattr(self, key, value)


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path, monkeypatch):
    """Repoint the ledger file at a temp path and clear in-memory rows."""
    monkeypatch.setenv("XIDU_TOKEN_LEDGER", str(tmp_path / "ledger.jsonl"))
    T.ledger._path = None
    T.reset()
    yield
    T.ledger._path = None
    T.reset()


# ── usage extraction ────────────────────────────────────────────────

class TestExtractUsage:
    def test_openai_response_metadata(self):
        r = _Response(response_metadata={
            "token_usage": {"prompt_tokens": 6446, "completion_tokens": 7791}})
        assert T.extract_usage(r) == (6446, 7791)

    def test_newer_usage_metadata_shape(self):
        r = _Response(usage_metadata={"input_tokens": 100, "output_tokens": 50})
        assert T.extract_usage(r) == (100, 50)

    def test_zero_tokens_are_metered_not_missing(self):
        """A real 0 must not be confused with 'no usage payload'."""
        r = _Response(response_metadata={
            "token_usage": {"prompt_tokens": 0, "completion_tokens": 0}})
        assert T.extract_usage(r) == (0, 0)

    def test_partial_payload_keeps_the_half_it_got(self):
        """Only one of the two counters present: the value we did receive must
        survive extraction (the call is still billed as unmetered)."""
        r = _Response(response_metadata={"token_usage": {"prompt_tokens": 100}})
        assert T.extract_usage(r) == (100, None)
        r2 = _Response(response_metadata={"token_usage": {"completion_tokens": 9}})
        assert T.extract_usage(r2) == (None, 9)

    def test_absent_usage_returns_none_pair(self):
        assert T.extract_usage(_Response(response_metadata={})) == (None, None)

    def test_response_without_metadata_attribute(self):
        assert T.extract_usage(object()) == (None, None)

    def test_metadata_is_not_a_dict(self):
        assert T.extract_usage(_Response(response_metadata="nope")) == (None, None)

    def test_blank_metadata_wins_over_absent_usage_metadata(self):
        """An empty token_usage dict must fall through to usage_metadata."""
        r = _Response(response_metadata={"token_usage": {}},
                      usage_metadata={"input_tokens": 7, "output_tokens": 8})
        assert T.extract_usage(r) == (7, 8)

    @pytest.mark.parametrize("value,expected", [("12", 12), (3.9, 3), (None, None),
                                                ("", None), ("abc", None)])
    def test_as_int_coercion(self, value, expected):
        assert T._as_int(value) == expected


# ── cost ────────────────────────────────────────────────────────────

class TestCost:
    def test_matches_hand_calculation(self):
        # qwen3.8-max is 12 CNY/M in, 36 CNY/M out.
        # abs=1e-12 so an off-by-one in the 1_000_000 divisor cannot hide here.
        expected = (6446 / 1e6) * 12.0 + (7791 / 1e6) * 36.0
        assert T.cost_cny("qwen3.8-max", 6446, 7791) == pytest.approx(expected, abs=1e-12)

    def test_divisor_is_exactly_one_million(self):
        """1,000,000 tokens at 12/M must be exactly 12.0 CNY."""
        assert T.cost_cny("qwen3.8-max", 1_000_000, 0) == pytest.approx(12.0, abs=1e-12)

    def test_every_routed_model_has_a_price(self):
        from src.config import NODE_MODELS
        for node, model in NODE_MODELS.items():
            assert model in MODEL_PRICES_CNY_PER_M, f"{node} -> {model} unpriced"

    def test_unknown_model_is_none_not_zero(self):
        assert T.cost_cny("no-such-model", 1000, 1000) is None

    def test_none_tokens_give_none_cost(self):
        assert T.cost_cny("qwen3.8-max", None, 10) is None
        assert T.cost_cny("qwen3.8-max", 10, None) is None

    def test_zero_tokens_cost_zero(self):
        assert T.cost_cny("glm-5.3", 0, 0) == 0.0


# ── recording ───────────────────────────────────────────────────────

class TestRecording:
    def test_record_response_attributes_node_model_chapter(self):
        r = _Response(response_metadata={
            "token_usage": {"prompt_tokens": 100, "completion_tokens": 200}})
        row = T.record_response(r, "read", "qwen3.8-max", book_id="difu", chapter=173)
        assert (row["node"], row["model"], row["chapter"], row["book_id"]) == \
            ("read", "qwen3.8-max", 173, "difu")
        assert row["metered"] and row["priced"]
        assert row["cost_cny"] > 0

    def test_unmetered_response_still_recorded(self):
        """Silence must be countable: no usage payload -> a visible row."""
        T.record_response(_Response(response_metadata={}), "readback", "qwen3.8-max",
                          book_id="difu", chapter=5)
        s = T.summary("difu")
        assert s["calls"] == 1
        assert s["unmetered_calls"] == 1
        assert s["metered_calls"] == 0
        assert s["cost_cny"] == 0.0

    def test_partial_usage_is_unmetered(self):
        """Half a payload is not trustworthy enough to bill."""
        row = T.record("read", "qwen3.8-max", 500, None, book_id="b", chapter=1)
        assert row["metered"] is False
        assert T.summary("b")["unmetered_calls"] == 1

    def test_unpriced_model_counted_not_silently_free(self):
        T.record("fix", "mystery-1", 1000, 500, book_id="b", chapter=1)
        s = T.summary("b")
        assert s["unpriced_calls"] == 1
        assert s["cost_cny"] == 0.0
        assert s["per_model"]["mystery-1"]["unpriced_calls"] == 1
        assert s["per_model"]["mystery-1"]["output_tokens"] == 500

    def test_persisted_rows_are_valid_jsonl(self, tmp_path):
        T.record("read", "qwen3.8-max", 1, 2, book_id="b", chapter=1)
        T.record("write", "glm-5.3-flash", 3, 4, book_id="b", chapter=2)
        lines = (tmp_path / "ledger.jsonl").read_text().strip().splitlines()
        assert len(lines) == 2
        rows = [json.loads(x) for x in lines]
        assert [r["node"] for r in rows] == ["read", "write"]
        assert rows[1]["input_tokens"] == 3

    def test_unwritable_path_does_not_lose_the_session(self, tmp_path, monkeypatch):
        """A ledger that cannot be written must still report the session — and
        must say so, because an unwritable ledger persists nothing."""
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")           # parent is a FILE
        monkeypatch.setenv("XIDU_TOKEN_LEDGER", str(blocker / "x.jsonl"))
        T.ledger._path = None
        row = T.record("read", "qwen3.8-max", 10, 20, book_id="b", chapter=1)
        s = T.summary("b")
        assert row["metered"] is True                   # the record itself is fine
        assert s["input_tokens"] == 10                  # session total survives
        assert s["write_failures"] == 1                 # ...and the gap is visible

    def test_nested_ledger_directory_is_created(self, tmp_path, monkeypatch):
        """Configuring a path under a directory that does not exist yet must
        still persist — otherwise the on-disk ledger silently stays empty."""
        target = tmp_path / "deep" / "deeper" / "ledger.jsonl"
        monkeypatch.setenv("XIDU_TOKEN_LEDGER", str(target))
        T.ledger._path = None
        T.record("read", "qwen3.8-max", 1, 2, book_id="b", chapter=1)
        assert target.exists()
        assert len(target.read_text().strip().splitlines()) == 1

    def test_explicit_path_overrides_the_environment(self, tmp_path, monkeypatch):
        """The constructor argument wins over the env var."""
        monkeypatch.setenv("XIDU_TOKEN_LEDGER", str(tmp_path / "from_env.jsonl"))
        explicit = tmp_path / "explicit.jsonl"
        T.ledger._path = explicit
        assert T.ledger.path == explicit
        T.record("read", "qwen3.8-max", 1, 2, book_id="b", chapter=1)
        assert explicit.exists()
        assert not (tmp_path / "from_env.jsonl").exists()

    def test_non_ascii_ids_are_stored_unescaped(self, tmp_path):
        """ensure_ascii=False keeps the ledger human-readable for Chinese ids."""
        T.record("read", "qwen3.8-max", 1, 2, book_id="地府叫我小先生", chapter=1)
        raw = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8")
        assert "地府叫我小先生" in raw
        assert "\\u" not in raw

    def test_concurrent_records_are_not_lost(self):
        def worker(i):
            for j in range(25):
                T.record("write", "glm-5.3-flash", 1, 1, book_id="b", chapter=i * 100 + j)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        s = T.summary("b")
        assert s["calls"] == 200
        assert s["input_tokens"] == 200


# ── summary ─────────────────────────────────────────────────────────

class TestSummary:
    def _seed(self):
        T.record("read", "qwen3.8-max", 6446, 7791, book_id="difu", chapter=1)
        T.record("write", "glm-5.3-flash", 8526, 2752, book_id="difu", chapter=1)
        T.record("readback", "qwen3.8-max", 2473, 2031, book_id="difu", chapter=1)
        T.record("read", "qwen3.8-max", 100, 100, book_id="other", chapter=1)

    def test_totals_across_nodes(self):
        self._seed()
        s = T.summary("difu")
        assert s["input_tokens"] == 6446 + 8526 + 2473
        assert s["output_tokens"] == 7791 + 2752 + 2031
        assert s["calls"] == 3

    def test_book_filter_excludes_other_books(self):
        self._seed()
        assert T.summary("other")["calls"] == 1
        assert T.summary()["calls"] == 4

    def test_per_node_and_per_model_aggregation(self):
        self._seed()
        s = T.summary("difu")
        assert s["per_node"]["read"]["calls"] == 1
        assert s["per_node"]["write"]["cost_cny"] > 0
        assert s["per_model"]["qwen3.8-max"]["calls"] == 2
        qwen_cost = (6446 / 1e6) * 12 + (7791 / 1e6) * 36 + (2473 / 1e6) * 12 + (2031 / 1e6) * 36
        assert s["per_model"]["qwen3.8-max"]["cost_cny"] == pytest.approx(qwen_cost)
        assert s["cost_cny"] == pytest.approx(
            qwen_cost + (8526 / 1e6) * 0.8 + (2752 / 1e6) * 2.8)

    def test_summary_of_unknown_book_is_empty_not_error(self):
        s = T.summary("never-seen")
        assert s["calls"] == 0 and s["cost_cny"] == 0.0

    def test_cost_is_rounded_to_six_decimals(self):
        """Keeps the persisted report readable rather than a float tail.

        One input token on qwen3.7-flash (0.2 CNY/M) costs 2e-7 CNY — seven
        decimal places — so a looser rounding would show up right here.
        """
        T.record("read", "qwen3.7-flash", 1, 0, book_id="b", chapter=1)
        cost = T.summary("b")["cost_cny"]
        assert cost == round(cost, 6)
        assert cost == 0.0

    def test_a_visible_cost_keeps_at_most_six_decimals(self):
        T.record("read", "qwen3.8-max", 6446, 7791, book_id="b", chapter=1)
        cost = T.summary("b")["cost_cny"]
        assert cost > 0
        assert len(str(cost).split(".")[1]) <= 6

    def test_write_failures_default_to_zero(self):
        assert T.summary("b")["write_failures"] == 0

    def test_reset_clears_in_memory_rows(self):
        self._seed()
        T.reset()
        assert T.summary("difu")["calls"] == 0

"""Per-chapter, per-node, per-model token accounting that actually persists.

Why this module exists
----------------------
Before it, the project's only token numbers were *process-global in-memory*
counters, logged once when a web job finished
(``src/api/routes.py`` -> ``TranslationStats.token_snapshot``).  Consequences:

* ``scripts/run_novel.py`` — the path that actually produced the delivered
  corpus (地府 2301 ch, 无限恐怖 772 ch) — recorded **nothing at all**;
* nothing was attributed to a node, a model or a chapter, so "what did chapter
  1238 cost" was unanswerable without re-running the pipeline with a hook;
* every capture helper was wrapped in ``except Exception: pass``, so a change in
  the provider's usage payload would silently stop all accounting;
* the tier label ("flash"/"pro") came from DeepSeek, and the prices in
  ``stats.py`` were DeepSeek USD prices, so the one cost figure the project did
  print was wrong for the GLM/Qwen stack.

What this does instead
----------------------
One append-only JSONL row per API response, carrying book / chapter / node /
model / input / output tokens.  Cost is derived from real current prices in
``config.MODEL_PRICES_CNY_PER_M``.  A response whose usage cannot be read is
recorded as **unmetered** rather than dropped, so missing accounting is visible
instead of silent.  An unknown model is reported as **unpriced**, never priced
at zero.

Reading the ledger::

    from src.token_ledger import summary
    summary(book_id="difu_xiao_xiansheng")   # tokens + CNY per node/model
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .config import MODEL_PRICES_CNY_PER_M

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER_PATH = _REPO_ROOT / "data" / "token_ledger.jsonl"

# Usage payloads we understand.  LangChain's OpenAI-compatible wrapper fills
# ``response_metadata["token_usage"]``; newer AIMessage objects also expose
# ``usage_metadata``.  Both are checked so accounting survives either shape.
_METADATA_KEYS = (
    ("response_metadata", "token_usage", "prompt_tokens", "completion_tokens"),
    ("usage_metadata", None, "input_tokens", "output_tokens"),
)

UNMETERED = "UNMETERED"


class _Ledger:
    """Append-only token ledger with in-memory aggregation."""

    def __init__(self, path: Path | None = None):
        self._lock = threading.Lock()
        self._path = path
        self._rows: list[dict] = []
        self._write_failures = 0

    # ── path handling (lazy so tests can repoint via env) ──
    @property
    def path(self) -> Path:
        if self._path is not None:
            return self._path
        env = os.getenv("XIDU_TOKEN_LEDGER")
        return Path(env) if env else DEFAULT_LEDGER_PATH

    def reset(self) -> None:
        with self._lock:
            self._rows = []
            self._write_failures = 0

    # ── recording ──
    def record(
        self,
        node: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        *,
        book_id: str = "",
        chapter: int = 0,
    ) -> dict:
        """Record one API response's usage. Returns the row that was written."""
        metered = input_tokens is not None and output_tokens is not None
        row = {
            "book_id": book_id,
            "chapter": chapter,
            "node": node,
            "model": model,
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "metered": metered,
            "priced": metered and model in MODEL_PRICES_CNY_PER_M,
            "cost_cny": cost_cny(model, input_tokens, output_tokens) if metered else None,
        }
        with self._lock:
            self._rows.append(row)
            self._append(row)
        return row

    def record_response(
        self,
        response,
        node: str,
        model: str,
        *,
        book_id: str = "",
        chapter: int = 0,
    ) -> dict:
        """Extract usage from a provider response and record it.

        A response with no readable usage is recorded as unmetered — never
        dropped, so the gap is countable.
        """
        usage_in, usage_out = extract_usage(response)
        return self.record(
            node, model, usage_in, usage_out, book_id=book_id, chapter=chapter
        )

    def _append(self, row: dict) -> None:
        path = self.path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            # Losing the file must not lose the run: the in-memory rows still
            # let the caller report this session. But a failure here means the
            # on-disk ledger is incomplete, so it is counted rather than
            # swallowed — a silently unwritable ledger would make the cost
            # report look complete while persisting nothing.
            self._write_failures += 1

    # ── reading ──
    def rows(self, book_id: str | None = None) -> list[dict]:
        with self._lock:
            rows = list(self._rows)
        if book_id is None:
            return rows
        return [r for r in rows if r.get("book_id") == book_id]

    def summary(self, book_id: str | None = None) -> dict:
        """Aggregate tokens and cost per node and per model."""
        rows = self.rows(book_id)
        per_node: dict[str, dict] = {}
        per_model: dict[str, dict] = {}
        unmetered = 0
        unpriced = 0
        total_cny = 0.0
        for r in rows:
            if not r.get("metered"):
                unmetered += 1
                continue
            if not r.get("priced"):
                unpriced += 1
            for bucket, key in ((per_node, r["node"]), (per_model, r["model"])):
                agg = bucket.setdefault(
                    key, {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                          "cost_cny": 0.0, "unpriced_calls": 0}
                )
                agg["calls"] += 1
                agg["input_tokens"] += r["input_tokens"]
                agg["output_tokens"] += r["output_tokens"]
                if r.get("priced"):
                    agg["cost_cny"] += r["cost_cny"] or 0.0
                else:
                    agg["unpriced_calls"] += 1
            if r.get("priced"):
                total_cny += r["cost_cny"] or 0.0
        return {
            "book_id": book_id,
            "calls": len(rows),
            "metered_calls": len(rows) - unmetered,
            "unmetered_calls": unmetered,
            "unpriced_calls": unpriced,
            "write_failures": self._write_failures,
            "input_tokens": sum(r["input_tokens"] for r in rows if r.get("metered")),
            "output_tokens": sum(r["output_tokens"] for r in rows if r.get("metered")),
            "cost_cny": round(total_cny, 6),
            "per_node": per_node,
            "per_model": per_model,
        }


def cost_cny(model: str, input_tokens: int | None, output_tokens: int | None):
    """Cost in CNY for one call. ``None`` if the model has no known price."""
    price = MODEL_PRICES_CNY_PER_M.get(model)
    if price is None or input_tokens is None or output_tokens is None:
        return None
    price_in, price_out = price
    return (input_tokens / 1_000_000) * price_in + (output_tokens / 1_000_000) * price_out


def extract_usage(response) -> tuple[int | None, int | None]:
    """Return ``(input_tokens, output_tokens)`` from a provider response.

    ``(None, None)`` means the payload carried no usage at all — the caller
    records that as unmetered instead of silently counting zero.
    """
    for container, nested, in_key, out_key in _METADATA_KEYS:
        holder = getattr(response, container, None)
        if not isinstance(holder, dict):
            continue
        usage = holder.get(nested) if nested else holder
        if not isinstance(usage, dict):
            continue
        if in_key in usage or out_key in usage:
            return _as_int(usage.get(in_key)), _as_int(usage.get(out_key))
    return None, None


def _as_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


ledger = _Ledger()


def record(node, model, input_tokens, output_tokens, **kw) -> dict:
    """Module-level shorthand for :meth:`_Ledger.record`."""
    return ledger.record(node, model, input_tokens, output_tokens, **kw)


def record_response(response, node, model, **kw) -> dict:
    """Module-level shorthand for :meth:`_Ledger.record_response`."""
    return ledger.record_response(response, node, model, **kw)


def summary(book_id: str | None = None) -> dict:
    """Module-level shorthand for :meth:`_Ledger.summary`."""
    return ledger.summary(book_id)


def reset() -> None:
    """Drop in-memory rows (tests; the JSONL file is append-only by design)."""
    ledger.reset()

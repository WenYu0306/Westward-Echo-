"""Persistent per-chapter attempt and spend accounting for a book.

Why this exists
---------------
The quality loop had only one brake: ``MAX_RETRANSLATION_ATTEMPTS`` rounds
*inside one graph invocation*. Everything else was forgotten:

* the checkpoint stores only successful chapters, so a chapter that timed out
  left no trace and every later ``--repair-missing`` run retried it from
  scratch, re-paying the two most expensive nodes (READ, WRITE);
* nothing watched spend per chapter, so one sample-point chapter reached
  37 API calls / ¥3.96 (12× the book median) and still was not delivered.

Measured on 2026-10-26: chapter 660 of 《无限恐怖》 was fully translated six
times across three runs and never written.

This module keeps that memory in ``_attempts.json`` next to the checkpoint, and
answers one question before each chapter: *is another attempt worth it?*
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_COST_CNY = 2.0


class ChapterBudget:
    """Attempts and spend per chapter, persisted between runs."""

    def __init__(self, path, max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                 max_cost_cny: float = DEFAULT_MAX_COST_CNY):
        self.path = Path(path)
        self.max_attempts = max_attempts
        self.max_cost_cny = max_cost_cny
        self._data: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raw = {}
            if isinstance(raw, dict):
                self._data = {str(k): dict(v) for k, v in raw.items()
                              if isinstance(v, dict)}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=1),
            encoding="utf-8")

    def get(self, chapter: int) -> dict:
        return self._data.get(str(chapter), {"attempts": 0, "cost_cny": 0.0})

    def should_attempt(self, chapter: int) -> tuple[bool, str]:
        """Return ``(allowed, reason)``. Reason is empty when allowed."""
        entry = self.get(chapter)
        attempts = entry.get("attempts", 0)
        cost = entry.get("cost_cny", 0.0)
        if attempts >= self.max_attempts:
            return False, (f"已尝试 {attempts} 次（上限 {self.max_attempts}），"
                           f"累计 ¥{cost:.2f}")
        if cost >= self.max_cost_cny:
            return False, (f"累计花费 ¥{cost:.2f} 已达上限 ¥{self.max_cost_cny:.2f}，"
                           f"尝试 {attempts} 次")
        return True, ""

    def needs_fast_mode(self, chapter: int) -> bool:
        """A retry must not re-pay the full quality loop.

        The first attempt may run the cold reader; every later attempt is a
        repair of something already attempted, so READ+WRITE only.
        """
        return self.get(chapter).get("attempts", 0) > 0

    def record(self, chapter: int, *, cost: float = 0.0,
               outcome: str = "ok") -> None:
        entry = self.get(chapter)
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        entry["cost_cny"] = round(float(entry.get("cost_cny", 0.0)) + cost, 4)
        entry["outcome"] = outcome
        self._data[str(chapter)] = entry
        self.save()

    def summary(self) -> dict:
        """Chapters that consumed attempts or money, newest bookkeeping only."""
        return {
            "chapters": len(self._data),
            "over_budget": sorted(
                int(ch) for ch, e in self._data.items()
                if not self.should_attempt(int(ch))[0]),
            "total_cost_cny": round(
                sum(float(e.get("cost_cny", 0.0)) for e in self._data.values()), 4),
        }

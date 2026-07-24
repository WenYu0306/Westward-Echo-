"""Translation Style Memo — a 6-drawer knowledge base accumulating lessons
from each translated chapter.

Each book gets its own directory under data/translation_memory/<book_id>/.
Six markdown files track different categories of accumulated translation
experience. At ~50 lines per file, the full memo is ~2500-4000 tokens —
readable by an LLM in a single context window.

Drawers:
  characters.md — dialogue registers, names, voice, character traits
  pacing.md    — exposition limits, scene rhythm, information density rules
  bridges.md   — cultural bridge patterns that worked (and didn't)
  prose.md     — sentence/paragraph rhythm, show-vs-tell enforcement
  terms.md     — key terminology decisions with cultural reasoning
  MEMO.md      — index pointing to the 5 content files
"""

import os
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from .config import DATA_DIR


class StyleMemoStore:
    """Manages the 6-drawer translation style memo for one book.

    Thread-safe for single-process use (one TranslationAgent per book).
    Updates are append-only with periodic pruning of obsolete entries.
    """

    def __init__(self, book_id: str):
        self.book_id = book_id
        self.root = Path(DATA_DIR) / "translation_memory" / book_id
        self.root.mkdir(parents=True, exist_ok=True)
        self._ensure_files()

    def _ensure_files(self):
        """Create empty drawer files if they don't exist."""
        files = {
            "MEMO.md": (
                "# Translation Style Memo — Index\n"
                "- [Character Voices](characters.md) — dialogue registers, names, traits\n"
                "- [Pacing & Density](pacing.md) — exposition limits, scene rhythm rules\n"
                "- [Cultural Bridges](bridges.md) — working analogies and bridge patterns\n"
                "- [Prose Rhythm](prose.md) — sentence and paragraph patterns\n"
                "- [Terminology](terms.md) — key terms with cultural reasoning\n"
            ),
            "characters.md": "# Character Voices\n",
            "pacing.md": "# Information Density & Pacing Rules\n",
            "bridges.md": "# Cultural Bridge Patterns\n",
            "prose.md": "# Prose Rhythm Rules\n",
            "terms.md": "# Terminology Decisions with Cultural Reasoning\n",
        }
        for filename, header in files.items():
            path = self.root / filename
            if not path.exists():
                path.write_text(header, encoding="utf-8")

    # ── Public API ──────────────────────────────────────────────

    def read_all(self) -> str:
        """Return the full memo text for injection into the agent prompt.

        Reads MEMO.md + all linked files, concatenated.  Each file is
        truncated to ~60 lines (oldest-first) to keep total tokens in check.
        """
        parts = []
        for filename in ["MEMO.md"] + self._content_files():
            path = self.root / filename
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            lines = content.strip().split("\n")
            # Truncate to newest ~60 lines if the file grows too large
            if len(lines) > 70:
                header = lines[0] if lines[0].startswith("#") else ""
                keep = lines[-60:] if header else lines[-60:]
                content = (header + "\n" if header else "") + "\n".join(keep)
            parts.append(content)
        return "\n\n---\n\n".join(parts)

    def record_lesson(
        self,
        drawer: str,
        lesson: str,
        chapter_number: int = 0,
    ):
        """Append a lesson to one of the 5 content drawers.

        Args:
            drawer: one of 'characters', 'pacing', 'bridges', 'prose', 'terms'
            lesson: a single-line or multi-line lesson entry
            chapter_number: which chapter this lesson came from
        """
        filename = f"{drawer}.md"
        if filename not in self._content_files():
            raise ValueError(f"Unknown drawer: {drawer}")
        path = self.root / filename
        tag = f"[ch{chapter_number}]" if chapter_number else ""
        entry = f"\n{tag} {lesson}".rstrip() + "\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(entry)

    def update_from_feedback(
        self,
        readback_feedback: dict,
        read_analysis: dict,
        chapter_number: int,
    ):
        """Extract concrete lessons from READBACK + READ analysis.

        This is a lightweight post-processing step that runs AFTER the
        pipeline.  It reads the cold reader's complaints and converts
        them into memo entries.

        Rules are simple and deterministic — no LLM needed for extraction.
        """
        fb = readback_feedback or {}
        ra = read_analysis or {}

        # ── Pacing: if reader was bored or wanted to skip ──────
        for eg in fb.get("engagement_gaps", []):
            passage = eg.get("passage", "")[:80]
            issue = eg.get("issue", "")
            if "exposition" in issue.lower() or "info" in issue.lower() or "explain" in issue.lower():
                self.record_lesson(
                    "pacing",
                    f"Exposition drag: '{passage}' — {issue}. Keep "
                    f"explanatory passages ≤3 paragraphs, broken by action.",
                    chapter_number,
                )

        # ── Pacing: exposition-length rules from image_gaps ────
        ig_count = len(ra.get("image_gaps", []))
        if ig_count == 0:
            self.record_lesson(
                "pacing",
                f"No image gaps detected — chapter may be too abstract. "
                f"Check if cultural concepts were explained rather than shown.",
                chapter_number,
            )

        # ── Comprehension: cultural concepts that confused ─────
        for ci in fb.get("comprehension_issues", []):
            passage = ci.get("passage", "")[:80]
            issue = ci.get("issue", "")
            self.record_lesson(
                "bridges",
                f"Reader confused by '{passage}': {issue}. "
                f"Test: would a one-sentence analogy to Western equivalent fix this?",
                chapter_number,
            )

        # ── Prose: translation-ish patterns found ───────────────
        if fb.get("overall_impression", ""):
            imp = fb["overall_impression"]
            if "translation" in imp.lower() or "reads like" in imp.lower():
                self.record_lesson(
                    "prose",
                    f"Cold reader detected translation feel: {imp[:200]}. "
                    f"Reduce 'show-then-explain' pattern.",
                    chapter_number,
                )

    # ── Internal ────────────────────────────────────────────────

    @staticmethod
    def _content_files() -> list[str]:
        return ["characters.md", "pacing.md", "bridges.md", "prose.md", "terms.md"]

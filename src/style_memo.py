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

from pathlib import Path

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

        Prefer ``read_relevant()`` for the main agent prompts — it retrieves
        only entries relevant to the current chapter, keeping prompt size
        constant regardless of book length.
        """
        parts = []
        for filename in ["MEMO.md"] + self._content_files():
            path = self.root / filename
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            lines = content.strip().split("\n")
            if len(lines) > 70:
                header = lines[0] if lines[0].startswith("#") else ""
                keep = lines[-60:] if header else lines[-60:]
                content = (header + "\n" if header else "") + "\n".join(keep)
            parts.append(content)
        return "\n\n---\n\n".join(parts)

    def read_relevant(
        self,
        chapter_content: str = "",
        exact_matches=None,
        *,
        max_chars: int = 5000,
        per_drawer_recent: int = 5,
    ) -> str:
        """Return the most recent memo entries, capped at ``max_chars``.

        Instead of dumping the entire accumulated memo into every prompt,
        this takes only the newest entries from each drawer — the ones most
        likely to be relevant to the current story arc.

        Recent entries naturally track the active cast, ongoing cultural
        patterns, and current pacing rhythm. Old entries (from ch50 when
        we're on ch1600) are rarely useful and cost prompt tokens.

        Keeps prompt size **constant** regardless of how many chapters
        have been translated.
        """
        parts: list[str] = []
        budget = max_chars

        for drawer in self._content_files():
            if budget <= 200:
                break

            path = self.root / drawer
            if not path.exists():
                continue
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            if len(lines) < 2:
                continue

            header = lines[0]
            entries = [line.strip()[:250] for line in lines[1:] if line.strip()]
            if not entries:
                continue

            # Take the most recent N entries
            recent = entries[-per_drawer_recent:]
            body = "\n".join(recent)
            block = f"{header}\n{body}"

            if len(block) > budget:
                block = block[:budget]
            parts.append(block)
            budget -= len(block)

        if not parts:
            return self.read_all()
        result = "\n\n---\n\n".join(parts)
        if len(result) > max_chars:
            result = result[:max_chars]
        return result

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

        # ── Dedup: skip if this lesson already exists ──
        # Extract the first ~50 chars of the lesson (excluding chapter tag)
        # as a fingerprint.  If a previous entry shares the same fingerprint,
        # this lesson is redundant — it was already learned from an earlier
        # chapter.
        fingerprint = lesson.strip()[:50].lower()
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if fingerprint in existing.lower():
                return  # Duplicate — skip

        tag = f"[ch{chapter_number}]" if chapter_number else ""
        entry = f"\n{tag} {lesson}".rstrip() + "\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(entry)

    def update_from_read_analysis(
        self,
        read_analysis: dict,
        chapter_number: int,
    ):
        """Extract lessons from the READ agent's analysis — runs EVERY chapter.

        Unlike ``update_from_feedback`` which requires a cold-read (sample
        chapters only), this method pulls directly from the READ agent's
        structured analysis which is available on every chapter regardless
        of fast/sample mode.

        Deterministic extraction — no LLM needed.
        """
        ra = read_analysis or {}

        # ── Terminology: capture every new term decision ──────
        for td in ra.get("terminology_decisions", []):
            cn = td.get("term_cn", "")
            en = td.get("proposed_en", "")
            reasoning = td.get("reasoning", "")
            cultural_note = td.get("cultural_note", "")
            if cn and en:
                entry = f"{cn} → {en}"
                if cultural_note:
                    entry += f" // {cultural_note[:120]}"
                elif reasoning:
                    entry += f" ({reasoning[:120]})"
                self.record_lesson("terms", entry, chapter_number)

        # ── Bridges: every cultural gap flagged ────────────────
        for cg in ra.get("cultural_gaps", []):
            element = cg.get("element", "")[:60]
            strategy = cg.get("bridge_strategy", "context")
            guidance = cg.get("bridge_guidance", "")[:150]
            if element:
                self.record_lesson(
                    "bridges",
                    f"[{strategy}] {element} — {guidance}",
                    chapter_number,
                )

        # ── Pacing: capture the READ agent's structural notes ──
        pacing = ra.get("pacing_notes", "")
        if pacing and len(pacing.strip()) > 10:
            self.record_lesson("pacing", pacing.strip()[:300], chapter_number)

        # ── Image gaps: record count + priority breakdown ──────
        image_gaps = ra.get("image_gaps", [])
        if image_gaps:
            critical = sum(1 for g in image_gaps if g.get("priority") == "critical")
            high = sum(1 for g in image_gaps if g.get("priority") == "high")
            self.record_lesson(
                "pacing",
                f"{len(image_gaps)} image gaps detected (critical={critical}, high={high}). "
                f"WRITER must rebuild these scenes with sensory_anchors.",
                chapter_number,
            )
        else:
            self.record_lesson(
                "pacing",
                "No image gaps detected — chapter may be too abstract. "
                "Check if cultural concepts were explained rather than shown.",
                chapter_number,
            )

    def update_from_feedback(
        self,
        readback_feedback: dict,
        read_analysis: dict,
        chapter_number: int,
    ):
        """Extract concrete lessons from READBACK cold-reader feedback.

        Runs only on sample chapters (when READBACK is active). Complements
        ``update_from_read_analysis`` which runs every chapter.
        """
        fb = readback_feedback or {}

        # ── Pacing: if reader was bored or wanted to skip ──────
        for eg in fb.get("engagement_gaps", []):
            passage = eg.get("passage", "")[:80]
            issue = eg.get("issue", "")
            if (
                "exposition" in issue.lower()
                or "info" in issue.lower() or "explain" in issue.lower()
            ):
                self.record_lesson(
                    "pacing",
                    f"Exposition drag: '{passage}' — {issue}. Keep "
                    f"explanatory passages ≤3 paragraphs, broken by action.",
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

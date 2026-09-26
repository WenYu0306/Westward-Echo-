"""Translation Style Memo — a 6-drawer knowledge base accumulating lessons
from each translated chapter.

Each book gets its own directory under data/translation_memory/<book_id>/.
Six markdown files track different categories of accumulated translation
experience.

⚠️ Do not reason about prompt size from file size. This docstring used to say
"At ~50 lines per file, the full memo is ~2500-4000 tokens", which was wrong on
a real book by three orders of magnitude: ``difu_xiao_xiansheng`` holds 31,818
lines in bridges.md alone (54,006 lines across all drawers). Nothing prunes the
files — see ``StyleMemoStore`` for the measurement and the reason pruning is a
non-goal here.

What actually bounds the prompt is **read-side truncation**: ``read_all`` keeps
only the newest ~60 lines per drawer, and ``read_relevant`` (what the agent
prompts use) keeps the newest 5 entries per drawer under ``max_chars``. So the
injected memo stays small and constant regardless of how long the book is.

Drawers:
  characters.md — dialogue registers, names, voice, character traits
  pacing.md    — exposition limits, scene rhythm, information density rules
  bridges.md   — cultural bridge patterns that worked (and didn't)
  prose.md     — sentence/paragraph rhythm, show-vs-tell enforcement
  terms.md     — key terminology decisions with cultural reasoning
  MEMO.md      — index pointing to the 5 content files

Consumers (measured, clause 9): READ and WRITE both render ``{style_memo}``
into their user prompt (``prompts/read.py:227``, ``prompts/write.py:180``).
FIX and READBACK deliberately do not — FIX's mandate is the cold reader's
complaints only (``prompts/fix.py`` rule 1), and READBACK must stay a blind
naive reader. Tests pin all four in ``test_prompt_personas.py``.
"""

from pathlib import Path

from .config import DATA_DIR, STYLE_MEMO_ENHANCED


class StyleMemoStore:
    """Manages the 6-drawer translation style memo for one book.

    Thread-safe for single-process use (one TranslationAgent per book).

    ⚠️ Updates are append-only and **nothing prunes the files on disk** — this
    docstring used to claim "periodic pruning of obsolete entries", but the
    module has no pruning code: the only writes are ``_ensure_files`` (creates
    empty drawers) and ``record_lesson`` (appends). Measured consequence on a
    real book: ``difu_xiao_xiansheng`` accumulated 31,818 lines / 3.66 MB in
    bridges.md alone, plus 18,329 in terms.md and 3,851 in pacing.md — 54,006
    lines total, against the ~50-lines-per-file this module's docstring assumed
    (636x the largest drawer).

    What keeps prompt cost bounded is *read-side truncation*, not disk pruning:
    ``read_all`` caps each drawer at the newest ~60 lines and ``read_relevant``
    takes the newest 5 entries per drawer under ``max_chars``. So the files grow
    without limit while every prompt stays constant — harmless for tokens, but
    the on-disk store is unbounded and dedup scans the whole file each append
    (``record_lesson`` does a substring test over the entire drawer).

    Pruning is a deliberate non-goal here: dropping accumulated entries would
    change what real translations see, so it needs an owner's decision rather
    than a drive-by edit. Recorded as a finding, not implemented.
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

        Reads MEMO.md + all linked files, concatenated. A drawer over 70 lines
        is cut to its newest ~60 (the oldest are dropped) to keep total tokens
        in check.

        Prefer ``read_relevant()`` for the main agent prompts — it keeps only
        the newest entries per drawer, so prompt size stays constant regardless
        of book length.
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
                # lines[-60:] never contains lines[0] here (the branch needs
                # >70 lines, 70 > 60), so re-prepending the header cannot
                # duplicate it. This used to read `lines[-60:] if header else
                # lines[-60:]` — both branches identical, so the condition was
                # unobservable dead code (and invisible to mutation testing,
                # whose operators don't cover ternaries).
                keep = lines[-60:]
                content = (header + "\n" if header else "") + "\n".join(keep)
            parts.append(content)
        return "\n\n---\n\n".join(parts)

    def read_relevant(
        self,
        *,
        max_chars: int = 5000,
        per_drawer_recent: int = 5,
    ) -> str:
        """Return the most recent memo entries, capped at ``max_chars``.

        Instead of dumping the entire accumulated memo into every prompt, this
        takes only the newest entries from each drawer.

        "Relevant" here means **recency**, nothing else: there is no matching
        against the current chapter. The bet is that the newest entries
        naturally track the active cast, the ongoing cultural patterns and the
        current pacing rhythm, while old entries (from ch50 when we're on
        ch1600) are rarely useful and cost prompt tokens.

        This signature used to accept ``chapter_content`` / ``exact_matches`` —
        added by `bdccc85` (2026-08-04, "scalable Style Memo") and never read by
        the body in any revision since. Real content-based retrieval would have
        to search entries, not slice the tail; until that exists, claiming the
        result is chapter-relevant is the same failure mode as a config value
        nobody reads. Callers still pass the chapter's terms into the prompt
        through ``exact_matches_text`` — see ``_make_state``.

        Keeps prompt size **constant** regardless of how many chapters have
        been translated.
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

            # No per-block truncation here. There used to be
            #     if len(block) > budget: block = block[:budget]
            # and it was provably redundant: when it fired, budget dropped to
            # exactly 0, so the `budget <= 200` check broke the loop on the next
            # iteration and the result was a single block that the final
            # `result[:max_chars]` below then cut anyway. The outer cut is the
            # binding constraint on both paths, so the inner one only ever
            # removed characters the outer one would have removed. Verified by
            # exhaustive enumeration over 5,250 (drawer, entry-count, entry-
            # length, max_chars, per_drawer_recent, content) combinations: zero
            # output differences.
            #
            # `len(block) > budget` -> `<=` survived mutation testing precisely
            # because nothing could observe it. A condition no input can
            # distinguish is not a behaviour worth keeping; it is a permanent
            # non-zero survivor count, which is what trains the next reader to
            # stop believing the number.
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

        if STYLE_MEMO_ENHANCED:
            # ── Characters: route character-name decisions ──────
            # Terminology decisions tagged as character names (2-4 char CN
            # with reasoning or cultural_note) are routed to characters.md
            # so the WRITE agent can maintain consistent character voice.
            for td in ra.get("terminology_decisions", []):
                cn = td.get("term_cn", "")
                reasoning = td.get("reasoning", "")
                cultural_note = td.get("cultural_note", "")
                if cn and 2 <= len(cn) <= 4 and (reasoning or cultural_note):
                    entry = f"{cn} → {td.get('proposed_en', '?')}"
                    if cultural_note:
                        entry += f" // {cultural_note[:120]}"
                    self.record_lesson("characters", entry, chapter_number)

            # ── Characters: crafted moments with character focus ──
            for m in ra.get("crafted_moments", []):
                if isinstance(m, str) and len(m) > 20:
                    self.record_lesson("characters", m[:200], chapter_number)

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

        if STYLE_MEMO_ENHANCED and pacing and len(pacing.strip()) > 10:
            # ── Prose: sentence/paragraph rhythm from READ analysis ──
            rhythm_keywords = ["句", "段", "节奏", "密度", "描写", "叙述",
                               "sentence", "paragraph", "rhythm", "density"]
            if any(kw in pacing.lower() for kw in rhythm_keywords):
                self.record_lesson("prose", pacing.strip()[:300], chapter_number)

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

        if STYLE_MEMO_ENHANCED:
            # ── Prose: cold reader standout moments (positive signal) ──
            for sm in fb.get("standout_moments", []):
                if isinstance(sm, str) and len(sm) > 20:
                    self.record_lesson(
                        "prose",
                        f"Standout: {sm[:200]}",
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

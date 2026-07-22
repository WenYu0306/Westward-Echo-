"""Domain models for glossary terms."""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class TermCategory(str, Enum):
    CHARACTER = "character"
    LOCATION = "location"
    TECHNIQUE = "technique"
    CULTURE = "culture"
    ITEM = "item"
    ERA = "era"


class TermStatus(str, Enum):
    CONFIRMED = "confirmed"
    PENDING_REVIEW = "pending_review"


@dataclass
class GlossaryTerm:
    """A single term entry in the glossary."""

    term_cn: str                              # Chinese original
    term_en: str                              # English translation
    category: TermCategory                    # Classification
    context: str = ""                         # Original sentence where first seen
    chapter_first_seen: int = 0               # Chapter number of first occurrence
    note: str = ""                            # Translator's note / rationale
    status: TermStatus = TermStatus.PENDING_REVIEW
    target_lang: str = "en-US"                # Target language code

    def to_dict(self) -> dict:
        d = asdict(self)
        d["category"] = self.category.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "GlossaryTerm":
        return cls(
            term_cn=d["term_cn"],
            term_en=d["term_en"],
            category=TermCategory(d.get("category", "culture")),
            context=d.get("context", ""),
            chapter_first_seen=d.get("chapter_first_seen", 0),
            note=d.get("note", ""),
            status=TermStatus(d.get("status", "pending_review")),
            target_lang=d.get("target_lang", "en-US"),
        )


@dataclass
class TranslationResult:
    """Output from the translate node."""

    chapter_number: int
    chapter_title: str
    translated_text: str
    new_terms: list[dict] = field(default_factory=list)
    adaptation_notes: list[str] = field(default_factory=list)
    chapter_summary: str = ""

    def to_format_string(self) -> str:
        """Render chapter in the standard output format."""
        header = f"# Chapter {self.chapter_number}: {self.chapter_title}"
        meta = f"<!-- meta: translated_at={self.chapter_number} -->\n"
        return f"{header}\n{meta}\n{self.translated_text}\n\n---\n<!-- END OF CHAPTER {self.chapter_number} -->\n"

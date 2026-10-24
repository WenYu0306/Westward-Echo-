"""Guards for the de/fr/es-ES cultural-rule data added this round.

Two failure modes matter here, and only one is obvious:

1. Structural: a language losing entries, or entries with a blank target, so a
   chapter silently gets no term guidance. Covered by the parity and
   completeness tests.
2. Semantic: an entry that *looks* complete but carries the wrong warning. The
   en-US notes contain hard-won constraints — 上身 is POSITIVE possession (never
   Christian 'Besessenheit'/'posesión'), 地府 is a BUREAUCRACY (never
   'Hölle'/'Enfer'/'Hell'), 活尸 is not a Western 'Zombie', 仙家 is not the 修真
   'immortal'. A translator who drops the warning leaves a plausible-looking
   entry that misdirects every chapter of every folk_religion book. The
   forbidden-*target* tests guard this: the note may NAME the bad option (that
   is its job), but the target must never BE one.

Also records the FIRST-ESTABLISHED count. Those 148 entries are coinages pending
native review — the count is pinned so the review backlog is machine-visible and
cannot grow unnoticed.
"""

import json

import pytest

from src.config import ROOT_DIR, SUPPORTED_LANGUAGES
from src.cultural_rules import load_rules

RULES_PATH = ROOT_DIR / "cultural_rules.json"
TAG = "⚠️ FIRST-ESTABLISHED"

# Term-level parity: every one of these must exist for all four languages.
PARITY_SECTIONS = [
    ("common", None, 17),
    ("genres", "romance_ceo", 3),
    ("genres", "xianxia", 20),
    ("genres", "folk_religion", 33),
    ("genres", "scifi", 15),
]


@pytest.fixture(scope="module")
def data() -> dict:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def _terms(data, partition, genre, lang):
    if partition == "common":
        return data["common"].get(lang, {})
    return data["genres"].get(genre, {}).get(lang, {})


class TestFourLanguageParity:
    """de/fr/es-ES must cover exactly the same terms as en-US.

    Skips genres.urban by design: its slot is deliberately empty after the
    无限恐怖 single-book terms moved to books.limitless_horror.
    """

    @pytest.mark.parametrize("partition,genre,count", PARITY_SECTIONS)
    def test_term_keys_identical_across_languages(self, data, partition, genre, count):
        en = set(_terms(data, partition, genre, "en-US"))
        assert len(en) == count, f"en-US {genre or partition}: {len(en)} != {count}"
        for lang in SUPPORTED_LANGUAGES:
            assert set(_terms(data, partition, genre, lang)) == en, (
                f"{lang} {genre or partition} term set drifted from en-US: "
                f"{set(_terms(data, partition, genre, lang)) ^ en}"
            )

    def test_urban_slot_stays_empty_for_every_language(self, data):
        for lang in SUPPORTED_LANGUAGES:
            assert _terms(data, "genres", "urban", lang) == {}, \
                f"genres.urban/{lang} must stay empty — single-book terms belong in books.*"

    @pytest.mark.parametrize("lang", ["de", "fr", "es-ES"])
    def test_load_rules_actually_returns_terms(self, lang):
        """Integration check: the data is reachable through the real loader."""
        assert len(load_rules(target_lang=lang, genre="folk_religion")) == 50
        assert len(load_rules(target_lang=lang, genre="scifi")) == 32
        assert len(load_rules(target_lang=lang, genre="xianxia")) == 37


class TestEntriesAreComplete:
    """A blank target renders as the _NO_TARGET marker, which tells the model
    'no fixed term exists'. For de/fr we deliberately DID supply renderings, so
    an empty target here means data loss, not a design choice."""

    @pytest.mark.parametrize("lang", ["de", "fr", "es-ES"])
    def test_every_entry_has_target_and_note(self, data, lang):
        blanks = []
        for partition, genre in [("common", None), ("genres", "romance_ceo"),
                                 ("genres", "xianxia"), ("genres", "folk_religion"),
                                 ("genres", "scifi")]:
            for term, entry in _terms(data, partition, genre, lang).items():
                if not str(entry.get("target", "")).strip():
                    blanks.append(f"{genre or partition}/{term}: empty target")
                if not str(entry.get("note", "")).strip():
                    blanks.append(f"{genre or partition}/{term}: empty note")
        assert not blanks, f"{lang} has incomplete entries: {blanks[:8]}"


class TestSemanticWarningsSurvivedTranslation:
    """The forbidden rendering must never appear in a *target*.

    Notes legitimately name the wrong option ("NEVER 'Hölle'"), so we assert on
    target only. Case-insensitive substring check: these words are unambiguous
    wherever they appear in a prescribed translation.
    """

    FORBIDDEN = [
        # (genre, term, {lang: [forbidden substrings in target]})
        ("folk_religion", "地府", {
            "de": ["hölle"], "fr": ["enfer"], "es-ES": ["infierno"], "en-US": ["hell"]}),
        ("folk_religion", "活尸", {
            "de": ["zombie"], "fr": ["zombie"], "es-ES": ["zombi"], "en-US": ["zombie"]}),
        ("folk_religion", "上身", {
            "de": ["besessenheit"], "fr": ["possession"],
            "es-ES": ["posesión"], "en-US": ["possession"]}),
        ("folk_religion", "鬼差", {
            "de": ["dämon"], "fr": ["démon"], "es-ES": ["demonio"], "en-US": ["demon"]}),
        ("folk_religion", "请神", {
            "de": ["dämon"], "fr": ["démon"], "es-ES": ["demonio"], "en-US": ["demon"]}),
    ]

    @pytest.mark.parametrize("genre,term,by_lang", FORBIDDEN)
    def test_target_avoids_the_forbidden_rendering(self, data, genre, term, by_lang):
        for lang, banned in by_lang.items():
            target = _terms(data, "genres", genre, lang)[term]["target"].lower()
            for word in banned:
                assert word not in target, (
                    f"{lang} {term} target {target!r} contains the forbidden "
                    f"rendering {word!r} — the en-US note explains why this is wrong"
                )

    @pytest.mark.parametrize("lang", ["de", "fr", "es-ES"])
    def test_positive_possession_warning_is_present(self, data, lang):
        """上身's warning is the single highest-stakes note in folk_religion."""
        note = _terms(data, "genres", "folk_religion", lang)["上身"]["note"]
        assert "CRÍTICO" in note or "KRITISCH" in note or "CRUCIAL" in note or "CRITICAL" in note, \
            f"{lang} 上身 lost its critical-severity marker"
        assert "POSITIVE" in note or "POSITIV" in note, \
            f"{lang} 上身 lost the 'this is positive possession' point"

    @pytest.mark.parametrize("lang", ["de", "fr", "es-ES"])
    def test_underworld_bureaucracy_warning_is_present(self, data, lang):
        """地府 is an administration; the note must say so or translators lose the joke."""
        note = _terms(data, "genres", "folk_religion", lang)["地府"]["note"]
        lowered = note.lower()
        # Every spelling the four languages actually use. Found the hard way:
        # the first version of this list omitted Spanish 'burocracia' (it had
        # only 'administración'), so a correct es-ES entry failed the check.
        assert any(w in lowered for w in (
            "bürokratie", "verwaltung",      # de
            "bureaucratique", "bureaucratie", "administration",  # fr
            "burocracia", "administración",  # es-ES
            "bureaucracy",                   # en-US (in the source note)
        )), f"{lang} 地府 lost the 'it is a bureaucracy' point"


class TestFalseFriendsAreDocumented:
    """Two verified false friends found while researching; if the notes lose
    them, the wrong word gets prescribed instead of avoided."""

    def test_german_kultivierung_caveat(self, data):
        note = _terms(data, "genres", "xianxia", "de")["修真"]["note"]
        assert "FALSCHER FREUND" in note or "falscher Freund" in note
        assert "Anbau" in note, "the German note must cite the agricultural false sense"

    def test_spanish_mecha_caveat(self, data):
        note = _terms(data, "genres", "scifi", "es-ES")["机甲"]["note"]
        assert "FALSO AMIGO" in note
        assert "mecha de vela" in note or "fusible" in note, \
            "the Spanish note must cite the wick/fuse sense"

    def test_french_mecha_caveat_cross_references_spanish(self, data):
        """French 'mecha' is NOT a false friend; the note says so explicitly so a
        future editor does not 'fix' it by copying the Spanish warning."""
        note = _terms(data, "genres", "scifi", "fr")["机甲"]["note"]
        assert "n'est pas un faux ami" in note or "pas un faux ami" in note


class TestReviewBacklogIsMachineVisible:
    """148 de/fr entries are coinages awaiting native review.

    Pinned count: if this changes, either someone reviewed them (good — update
    the number and remove the tags) or someone added unvetted terms (bad).
    """

    EXPECTED_TOTAL = 176          # 88 de + 88 fr
    EXPECTED_TAGGED = 148

    def _de_fr_entries(self, data):
        out = []
        for lang in ("de", "fr"):
            out += [(f"common/{lang}", t, v) for t, v in data["common"].get(lang, {}).items()]
            for genre, langs in data["genres"].items():
                for t, v in langs.get(lang, {}).items():
                    out.append((f"{genre}/{lang}", t, v))
        return out

    def test_tagged_count_is_pinned(self, data):
        entries = self._de_fr_entries(data)
        assert len(entries) == self.EXPECTED_TOTAL, \
            f"de/fr entry count changed: {len(entries)} != {self.EXPECTED_TOTAL}"
        tagged = [e for e in entries if e[2].get("note", "").startswith(TAG)]
        assert len(tagged) == self.EXPECTED_TAGGED, (
            f"FIRST-ESTABLISHED backlog changed: {len(tagged)} != {self.EXPECTED_TAGGED}. "
            f"If a native reviewer vetted terms, remove their tags and update this number."
        )

    def test_unmarked_entries_are_the_anchored_ones(self, data):
        """Untagged entries claim an external anchor, so they must not be
        silently expanded — spot-check a few known-anchored terms stay untagged."""
        anchored = [
            ("common", None, "de", "玛丽苏"),      # Mary Sue, loanword
            ("genres", "scifi", "de", "联邦"),     # Föderation, Star Trek dub
            ("genres", "scifi", "fr", "帝国"),     # l'Empire, Star Wars dub
            ("genres", "xianxia", "fr", "丹田"),   # dantian, fr.wikipedia + qigong
            ("genres", "folk_religion", "fr", "阎王"),  # roi Yan, fr.wikipedia
        ]
        for partition, genre, lang, term in anchored:
            entry = _terms(data, partition, genre, lang)[term]
            assert not entry["note"].startswith(TAG), \
                f"{lang} {term} was expected to be anchored (untagged)"

    def test_fidelity_still_en_us_only(self, data):
        """Known, accepted gap — pinned so it is a decision, not an oversight.

        de/fr/es-ES get no category-level fidelity rules; READ shows
        '(No cultural fidelity rules for this language.)'. Filling it is a
        separate scope from term-level adaptation.
        """
        assert set(data["fidelity"]) == {"en-US"}

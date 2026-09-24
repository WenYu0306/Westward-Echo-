"""Language registry sync: every language-keyed table must match config.

Why this file exists
--------------------
Clause 11 parameterised the *prompt text* (hardcoded "American"/"horror"/
"English" became ``{audience}``/``{genre_name}``/``{output_language}``), but the
set of legal *values* stayed closed and, worse, was re-typed in twelve places.
It had already drifted:

- ``routes.py`` carried a ``_KNOWN_LANGS`` frozenset that nothing ever read, and
  two inline ``for lang in [...]`` lists containing a bare ``"en"`` that no
  language table knew about;
- ``editor.py``'s copy of the same list omitted ``"en"`` — so the two modules
  could disagree about which output file a job had produced;
- ``genre`` had a three-way sync test (``TestGenreSync``) but language had only a
  two-way one, leaving ten of the twelve copies unguarded.

Adding a fifth language meant editing all twelve and any miss failed silently.
``config.SUPPORTED_LANGUAGES`` is now the single source of truth and these tests
assert every consumer matches it — the same invariant that fixed the ``寸``
unit-list drift in ``src/measurements.py``.
"""

import re
from pathlib import Path

from src.agent.prompts.personas import DEFAULT_LANG, known_languages
from src.agent.prompts.translation import LANGUAGE_NAMES, LANGUAGE_STYLE_NOTES
from src.config import (
    DEFAULT_TARGET_LANG,
    LANGUAGE_SHORT_LABELS,
    ROOT_DIR,
    SUPPORTED_LANGUAGES,
    target_language_label,
)
from src.evaluator_prompts import EVALUATOR_PROFILES

# Source files scanned for inline language lists that should have been replaced
# by the registry. Kept as paths, not imports, because the point is to read the
# text a human would have to edit.
SRC = ROOT_DIR / "src"


def _registry() -> set:
    return set(SUPPORTED_LANGUAGES)


class TestRegistryIsWellFormed:
    def test_registry_is_nonempty_and_unique(self):
        assert SUPPORTED_LANGUAGES, "SUPPORTED_LANGUAGES must not be empty"
        assert len(SUPPORTED_LANGUAGES) == len(_registry()), \
            "SUPPORTED_LANGUAGES contains duplicates"

    def test_default_lang_is_supported(self):
        assert DEFAULT_TARGET_LANG in _registry(), \
            f"DEFAULT_TARGET_LANG {DEFAULT_TARGET_LANG!r} is not in the registry"

    def test_personas_default_aliases_config(self):
        """personas.DEFAULT_LANG must not be an independent second default."""
        assert DEFAULT_LANG == DEFAULT_TARGET_LANG


class TestLanguageTablesMatchRegistry:
    """Every dict keyed by target language must cover exactly the registry.

    A missing key means that language silently loses a capability (no audience
    wording, no regional style constraints, no evaluator persona); an extra key
    means dead config that reads as support that was never wired up.
    """

    def test_language_names(self):
        assert set(LANGUAGE_NAMES) == _registry(), \
            f"LANGUAGE_NAMES drifted: {set(LANGUAGE_NAMES) ^ _registry()}"

    def test_language_style_notes(self):
        assert set(LANGUAGE_STYLE_NOTES) == _registry(), \
            f"LANGUAGE_STYLE_NOTES drifted: {set(LANGUAGE_STYLE_NOTES) ^ _registry()}"

    def test_lang_profiles(self):
        assert set(known_languages()) == _registry(), \
            f"_LANG_PROFILES drifted: {set(known_languages()) ^ _registry()}"

    def test_evaluator_profiles(self):
        assert set(EVALUATOR_PROFILES) == _registry(), \
            f"EVALUATOR_PROFILES drifted: {set(EVALUATOR_PROFILES) ^ _registry()}"

    def test_every_style_note_is_substantive(self):
        """Guard against a language being 'registered' with empty guidance."""
        for lang, note in LANGUAGE_STYLE_NOTES.items():
            assert note.strip(), f"LANGUAGE_STYLE_NOTES[{lang!r}] is empty"

    def test_every_evaluator_profile_has_persona(self):
        for lang, profile in EVALUATOR_PROFILES.items():
            assert profile.get("persona", "").strip(), \
                f"EVALUATOR_PROFILES[{lang!r}] has no persona"


class TestShortLabelsAreDerivedNotCopied:
    """LANGUAGE_SHORT_LABELS is prompt-table column text, not a 13th language
    list. It is guarded two ways so it cannot drift: its keys equal the
    registry, and each value equals the leading word of LANGUAGE_NAMES — the
    table it is meant to abbreviate.

    It replaced hard-coded ``| Chinese | English |`` headers that told the model
    the Spanish/German column was English.
    """

    def test_keys_equal_registry(self):
        assert set(LANGUAGE_SHORT_LABELS) == _registry(), \
            f"LANGUAGE_SHORT_LABELS drifted: {set(LANGUAGE_SHORT_LABELS) ^ _registry()}"

    def test_every_label_matches_language_names_first_word(self):
        for lang, label in LANGUAGE_SHORT_LABELS.items():
            assert label == LANGUAGE_NAMES[lang].split()[0], \
                f"{lang}: label {label!r} != LANGUAGE_NAMES {LANGUAGE_NAMES[lang]!r}"

    def test_label_resolves_per_language(self):
        assert target_language_label("es-ES") == "Spanish"
        assert target_language_label("de") == "German"
        assert target_language_label("fr") == "French"
        assert target_language_label("en-US") == "English"

    def test_unknown_language_is_not_silently_called_english(self):
        """An unregistered code must surface as itself, never as 'English'."""
        assert target_language_label("pt-BR") == "pt-BR"
        assert target_language_label("ja-JP") == "ja-JP"

    def test_empty_language_yields_neutral_header_not_english(self):
        assert target_language_label("") == "Target"
        assert target_language_label() == "Target"


class TestNoInlineLanguageLists:
    """The registry is only real if the copies are gone.

    These read source text rather than importing, because an inline list is a
    maintenance hazard precisely where a human would edit it.
    """

    def _inline_lists_in(self, rel_path: str) -> list:
        """Return inline list literals that enumerate supported languages."""
        text = (SRC / rel_path).read_text(encoding="utf-8")
        found = []
        for m in re.finditer(r"for lang in \[(.*?)\]", text, re.S):
            found.append(m.group(1))
        return found

    def test_routes_has_no_inline_lang_list(self):
        inline = self._inline_lists_in("api/routes.py")
        assert not inline, \
            f"routes.py re-declares languages inline: {inline} — use SUPPORTED_LANGUAGES"

    def test_editor_has_no_inline_lang_list(self):
        inline = self._inline_lists_in("api/editor.py")
        assert not inline, \
            f"editor.py re-declares languages inline: {inline} — use SUPPORTED_LANGUAGES"

    def test_routes_imports_registry(self):
        text = (SRC / "api/routes.py").read_text(encoding="utf-8")
        assert "SUPPORTED_LANGUAGES" in text, \
            "routes.py must consume SUPPORTED_LANGUAGES for output-file lookups"

    def test_editor_imports_registry(self):
        text = (SRC / "api/editor.py").read_text(encoding="utf-8")
        assert "SUPPORTED_LANGUAGES" in text, \
            "editor.py must consume SUPPORTED_LANGUAGES for output-file lookups"

    def test_known_langs_whitelist_stays_removed(self):
        """A dead whitelist reads as a guarantee that does not exist.

        It was never read, so any target_lang was accepted and unknown values
        fell back to en-US in personas._lang_profile. If validation is genuinely
        wanted it must be enforced, not merely declared.

        Only an actual assignment counts: routes.py keeps a NOTE explaining what
        was removed and why, and that prose legitimately names the constant.
        """
        text = (SRC / "api/routes.py").read_text(encoding="utf-8")
        code_lines = [
            line for line in text.splitlines()
            if not line.lstrip().startswith("#")
        ]
        assignment = re.compile(r"^\s*_KNOWN_LANGS\s*=")
        hits = [line for line in code_lines if assignment.match(line)]
        assert not hits, \
            f"_KNOWN_LANGS was dead config, re-added as: {hits} — enforce it or drop it"


class TestWebUiMatchesRegistry:
    """The UI must offer exactly the languages the pipeline can address.

    A language selectable in the UI but absent from LANGUAGE_NAMES would fall
    back to "American readers" in the prompt — the precise drift clause 11 was
    written to remove.
    """

    def _web_ui_text(self) -> str:
        return (SRC / "web_ui.py").read_text(encoding="utf-8")

    def test_dropdown_options_match_registry(self):
        block = self._web_ui_text()
        sel = re.search(r'<select id="target-lang">(.*?)</select>', block, re.S)
        assert sel, "target-lang select not found in web_ui.py"
        options = set(re.findall(r'<option value="([^"]+)"', sel.group(1)))
        assert options == _registry(), \
            f"UI dropdown drifted from registry: {options ^ _registry()}"

    def test_multi_lang_chips_match_registry(self):
        block = self._web_ui_text()
        chips = set(re.findall(r'data-lang="([^"]+)"', block))
        assert chips == _registry(), \
            f"multi-lang chips drifted from registry: {chips ^ _registry()}"

    def test_short_label_map_matches_registry(self):
        """The EN/ES/DE/FR badge map: an unmapped language falls through to its
        raw code, so drift is silent rather than loud."""
        block = self._web_ui_text()
        line = re.search(r"const langLabel=j\.target_lang===.*?;\n", block, re.S)
        assert line, "short-label map not found in web_ui.py"
        langs = set(re.findall(r"j\.target_lang==='([^']+)'", line.group(0)))
        assert langs == _registry(), \
            f"short-label map drifted from registry: {langs ^ _registry()}"

    def test_chinese_label_map_matches_registry(self):
        """The job-detail 目标语言 map: same silent fall-through to the raw code."""
        block = self._web_ui_text()
        seg = re.search(r"let langLabel=job\.target_lang;(.*?)\n\s*\n", block, re.S)
        assert seg, "chinese-label map not found in web_ui.py"
        langs = set(re.findall(r"job\.target_lang==='([^']+)'", seg.group(1)))
        assert langs == _registry(), \
            f"chinese-label map drifted from registry: {langs ^ _registry()}"

    def test_no_identity_label_map_remains(self):
        """The removed if/else chain mapped every language to itself.

        If someone re-adds a no-op map it is pure maintenance surface: it has to
        be updated for every new language and changes nothing when it is.
        """
        block = self._web_ui_text()
        identity = re.findall(
            r"target_lang==='([a-zA-Z\-]+)'\)langLabel='\1'", block)
        assert not identity, \
            f"identity language map re-added for: {identity} — it maps X to X"


class TestCulturalRulesLanguages:
    """Data-layer language keys must be a subset of the registry.

    Subset, not equality: ``common``/``fidelity`` coverage for de/fr is filled in
    separately, and an absent key degrades to "no genre-specific rules" rather
    than crashing. What must never happen is a rules key for a language the rest
    of the pipeline cannot address — that is unreachable config.
    """

    def _rules(self) -> dict:
        import json
        path = Path(ROOT_DIR / "cultural_rules.json")
        return json.loads(path.read_text(encoding="utf-8"))

    def test_common_languages_are_supported(self):
        data = self._rules()
        langs = set(data.get("common", {}))
        assert langs <= _registry(), \
            f"cultural_rules.json common has languages outside the registry: {langs - _registry()}"

    def test_fidelity_languages_are_supported(self):
        data = self._rules()
        langs = set(data.get("fidelity", {}))
        assert langs <= _registry(), \
            f"cultural_rules.json fidelity has languages outside the registry: {langs - _registry()}"

    def test_genre_rule_languages_are_supported(self):
        data = self._rules()
        langs = set()
        for genre_block in data.get("genres", {}).values():
            langs |= set(genre_block)
        assert langs <= _registry(), \
            f"cultural_rules.json genres have languages outside the registry: {langs - _registry()}"

    def test_book_rule_languages_are_supported(self):
        """books.<book_id>.<lang> is a third language-keyed layer."""
        data = self._rules()
        langs = set()
        for book_block in data.get("books", {}).values():
            langs |= set(book_block)
        assert langs <= _registry(), \
            f"cultural_rules.json books have languages outside the registry: {langs - _registry()}"

    def test_no_arabic_config_remains(self):
        """ar-SA was dropped in 5bba508 and its config removed in 445ec8b."""
        data = self._rules()
        langs = set(data.get("common", {})) | set(data.get("fidelity", {}))
        for genre_block in data.get("genres", {}).values():
            langs |= set(genre_block)
        for book_block in data.get("books", {}).values():
            langs |= set(book_block)
        assert not {lang for lang in langs if lang.startswith("ar")}, \
            f"ar-SA config reappeared: {langs}"

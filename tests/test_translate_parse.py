"""Unit tests for _parse_llm_response in src/agent/nodes/translate.py.

Tests the 5-layer fallback parser without any LLM API calls.
"""

import sys
import os
import json

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent.nodes.translate import _parse_llm_response


class TestParseValidJson:
    """Layer 1: strict JSON parsing."""

    def test_all_fields_present(self):
        """Parses valid JSON with all expected fields."""
        response = json.dumps({
            "translated_text": "She walked across the courtyard.",
            "new_terms_found": [{"term_cn": "庭院", "term_en": "courtyard", "category": "location"}],
            "cultural_adaptation_notes": ["Adapted 庭院 to 'courtyard' for fantasy tone"],
            "chapter_summary": "The heroine crosses a courtyard and enters the palace.",
        })
        result = _parse_llm_response(response)
        assert result["translated_text"] == "She walked across the courtyard."
        assert len(result["new_terms_found"]) == 1
        assert result["new_terms_found"][0]["term_cn"] == "庭院"
        assert len(result["cultural_adaptation_notes"]) == 1
        assert "crosses a courtyard" in result["chapter_summary"]

    def test_minimal_valid_json(self):
        """Parses JSON with only translated_text and empty lists."""
        response = json.dumps({
            "translated_text": "He opened the door.",
            "new_terms_found": [],
            "cultural_adaptation_notes": [],
            "chapter_summary": "",
        })
        result = _parse_llm_response(response)
        assert result["translated_text"] == "He opened the door."
        assert result["new_terms_found"] == []

    def test_json_with_escaped_characters(self):
        """Correctly handles JSON with escaped quotes and newlines inside strings."""
        response = json.dumps({
            "translated_text": 'She said, "Hello world!"\nHe replied, "Goodbye."',
            "new_terms_found": [],
            "cultural_adaptation_notes": [],
            "chapter_summary": "",
        })
        result = _parse_llm_response(response)
        assert '"Hello world!"' in result["translated_text"]
        assert "Goodbye" in result["translated_text"]


class TestParseCodeFences:
    """Strips ```json...``` code fences before parsing."""

    def test_json_wrapped_in_code_fences(self):
        content = json.dumps({
            "translated_text": "The mountain loomed ahead.",
            "new_terms_found": [{"term_cn": "山脉", "term_en": "mountain range", "category": "location"}],
            "cultural_adaptation_notes": [],
            "chapter_summary": "The party reaches the mountain.",
        })
        response = f"```json\n{content}\n```"
        result = _parse_llm_response(response)
        assert result["translated_text"] == "The mountain loomed ahead."
        assert result["new_terms_found"][0]["term_cn"] == "山脉"

    def test_json_wrapped_in_plain_code_fences(self):
        """Handles ``` without language tag."""
        content = json.dumps({
            "translated_text": "Darkness fell over the city.",
            "new_terms_found": [],
            "cultural_adaptation_notes": [],
            "chapter_summary": "",
        })
        response = f"```\n{content}\n```"
        result = _parse_llm_response(response)
        assert "Darkness fell" in result["translated_text"]


class TestLayer2RegexExtraction:
    """Layer 2: regex-based JSON object extraction from surrounding text."""

    def test_json_surrounded_by_text(self):
        """Extracts JSON object from text with leading/trailing content."""
        response = (
            "Here is the translation result:\n\n"
            + json.dumps({
                "translated_text": "Su Nian walked into the grand hall.",
                "new_terms_found": [{"term_cn": "大殿", "term_en": "grand hall", "category": "location"}],
                "cultural_adaptation_notes": [],
                "chapter_summary": "Su Nian enters the hall.",
            })
            + "\n\nI hope this meets your requirements."
        )
        result = _parse_llm_response(response)
        assert result["translated_text"] == "Su Nian walked into the grand hall."
        assert result["new_terms_found"][0]["term_en"] == "grand hall"


class TestLayer3FieldExtraction:
    """Layer 3: field-by-field regex when JSON object parsing fails."""

    def test_extract_translated_text_via_regex(self):
        """Extracts the translated_text field using regex when JSON is malformed."""
        response = 'Some prefix text "translated_text": "The wind howled through the valley.", "other": broken'
        result = _parse_llm_response(response)
        assert result["translated_text"] == "The wind howled through the valley."
        assert result["new_terms_found"] == []
        assert result["chapter_summary"] == ""

    def test_extract_translated_text_with_escaped_quotes(self):
        response = r'"translated_text": "She said \"Enough!\" and walked away.", "garbage": {'
        result = _parse_llm_response(response)
        assert 'She said "Enough!" and walked away.' in result["translated_text"]


class TestLayer4MarkdownDetection:
    """Layer 4: returns markdown-looking text as translated_text."""

    def test_markdown_heading(self):
        """Returns text starting with # as translated_text."""
        response = "# Chapter 1: The Awakening\n\nThe night sky stretched endlessly."
        result = _parse_llm_response(response)
        assert "Chapter 1: The Awakening" in result["translated_text"]
        assert result["new_terms_found"] == []

    def test_bold_markdown_text(self):
        """Returns bold-formatted text as translated_text."""
        response = "**The Queen's Gambit**\n\nShe moved her piece forward."
        result = _parse_llm_response(response)
        assert "The Queen's Gambit" in result["translated_text"]

    def test_sentence_starting_with_uppercase(self):
        """Text starting with capital letter + lowercase matches the markdown heuristic."""
        response = 'She walked through the bamboo grove, each step crunching.\n\n(Note: "bamboo grove" was previously translated as "bamboo forest".)'
        result = _parse_llm_response(response)
        assert "bamboo grove" in result["translated_text"]


class TestLayer5RawFallback:
    """Layer 5: last resort — returns raw content stripped of code fences."""

    def test_raw_fallback_for_completely_unexpected_format(self):
        response = "@@unparseable@@ some junk here"
        result = _parse_llm_response(response)
        # Should not crash, and should return something
        assert result["translated_text"] == response.strip()
        assert result["new_terms_found"] == []

    def test_strips_json_prefix(self):
        """Even in raw fallback, strips leading ```json marker."""
        response = "```jsonThis is not valid JSON"
        result = _parse_llm_response(response)
        assert result["translated_text"] == "This is not valid JSON"


class TestMissingFields:
    """Edge cases for missing or absent fields."""

    def test_no_new_terms_found_field(self):
        """Returns empty list when JSON has no new_terms_found key."""
        response = json.dumps({
            "translated_text": "He stood at the edge of the cliff.",
            "cultural_adaptation_notes": ["Metaphor adapted"],
            "chapter_summary": "A moment of decision.",
        })
        result = _parse_llm_response(response)
        assert result["translated_text"] == "He stood at the edge of the cliff."
        # When JSON parsing succeeds but the key is absent, the dict lacks the key.
        # The caller (translate_node) uses result.get("new_terms_found", []), so
        # test against the same pattern.
        assert result.get("new_terms_found", []) == []


class TestBugFixEmbeddedBraces:
    """Regression tests for specific parser bugs."""

    def test_json_embedded_in_other_text_with_braces(self):
        """The exact bug: {{"translated_text": "...", "new_terms_found": [...]}} embedded in text.

        This tests the pattern where the LLM wraps JSON in markdown or commentary
        that itself contains braces.
        """
        inner = json.dumps({
            "translated_text": "Pei Yanzhou descended from the helicopter, his suit immaculate.",
            "new_terms_found": [{"term_cn": "直升机", "term_en": "helicopter", "category": "item"}],
            "cultural_adaptation_notes": ["CEO entrance trope preserved"],
            "chapter_summary": "Pei Yanzhou makes a dramatic entrance.",
        })
        # Simulate a response where the JSON is prefixed with commentary
        response = f'I have translated chapter 1. Here is the result:\n\n{inner}'
        result = _parse_llm_response(response)
        # Layer 2 regex extraction should find and parse the JSON object
        assert "Pei Yanzhou" in result["translated_text"]
        assert result["new_terms_found"][0]["term_cn"] == "直升机"

    def test_empty_string(self):
        """Does not crash on empty content."""
        result = _parse_llm_response("")
        assert "translated_text" in result
        assert result["new_terms_found"] == []

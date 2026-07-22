"""Unit tests for src/tools.py — MCP-style tool definitions and handlers."""

import pytest
from src.tools import GLOSSARY_LOOKUP_TOOL, ALL_TOOLS, handle_glossary_lookup


class TestGlossaryLookupTool:
    """Tests for GLOSSARY_LOOKUP_TOOL schema."""

    def test_has_required_schema_structure(self):
        """Must have type, function.name, and function.parameters."""
        assert isinstance(GLOSSARY_LOOKUP_TOOL, dict)
        assert GLOSSARY_LOOKUP_TOOL["type"] == "function"
        assert "function" in GLOSSARY_LOOKUP_TOOL
        assert "name" in GLOSSARY_LOOKUP_TOOL["function"]
        assert GLOSSARY_LOOKUP_TOOL["function"]["name"] == "lookup_glossary"
        assert "parameters" in GLOSSARY_LOOKUP_TOOL["function"]

    def test_parameter_requires_term_cn(self):
        """The 'term_cn' parameter must be in required list."""
        params = GLOSSARY_LOOKUP_TOOL["function"]["parameters"]
        assert params["type"] == "object"
        assert "term_cn" in params["properties"]
        assert "term_cn" in params["required"]


class TestHandleGlossaryLookup:
    """Tests for handle_glossary_lookup()."""

    def test_returns_translation_when_term_found(self):
        """A known term should return its English translation."""
        result = handle_glossary_lookup("测试", {"测试": "test"})
        assert result == "test"

    def test_returns_not_found_when_term_absent(self):
        """An unknown term should return 'NOT_FOUND'."""
        result = handle_glossary_lookup("不存在", {})
        assert result == "NOT_FOUND"

    def test_works_with_exact_glossary_get_interface(self):
        """handle_glossary_lookup should work with ExactGlossary's .get() interface.

        ExactGlossary.get() returns None or a string, same as dict.get().
        We use a duck-typed object with the same .get() signature.
        """
        # Duck-type matching ExactGlossary.get() — returns None on miss
        class FakeGlossary:
            def __init__(self, data):
                self._data = data

            def get(self, term_cn):
                return self._data.get(term_cn)

        glossary = FakeGlossary({"金丹期": "Golden Core Stage"})
        result = handle_glossary_lookup("金丹期", glossary)
        assert result == "Golden Core Stage"

        result_missing = handle_glossary_lookup("不存在", glossary)
        assert result_missing == "NOT_FOUND"


class TestAllTools:
    """Tests for ALL_TOOLS list."""

    def test_all_tools_is_list_with_glossary_lookup_as_first(self):
        """ALL_TOOLS should be a list with GLOSSARY_LOOKUP_TOOL as element 0."""
        assert isinstance(ALL_TOOLS, list)
        assert len(ALL_TOOLS) >= 1
        assert ALL_TOOLS[0] is GLOSSARY_LOOKUP_TOOL

"""MCP-style tools for the translation agent.

These tools are defined as function-calling schemas that the LLM can invoke
during translation to query the glossary or perform other lookups.
"""

GLOSSARY_LOOKUP_TOOL = {
    "type": "function",
    "function": {
        "name": "lookup_glossary",
        "description": "Look up the canonical English translation for a Chinese term in the glossary. "
                       "Use this when you encounter a proper noun, character name, place name, "
                       "or culturally specific term and need the exact approved translation. "
                       "NEVER guess a proper noun translation — always look it up first.",
        "parameters": {
            "type": "object",
            "properties": {
                "term_cn": {
                    "type": "string",
                    "description": "The Chinese term to look up (e.g., '八零年代', '林小满', '金丹期')"
                }
            },
            "required": ["term_cn"]
        }
    }
}


def handle_glossary_lookup(term_cn: str, exact_store) -> str:
    """Execute a glossary lookup. Returns the English translation or 'NOT_FOUND'."""
    result = exact_store.get(term_cn)
    if result:
        return result
    # Also try semantic search
    return "NOT_FOUND"


ALL_TOOLS = [GLOSSARY_LOOKUP_TOOL]

# 西渡 / Westward Echo

AI-powered Chinese-to-English web novel translation with cultural adaptation and consistent terminology across all chapters.

## Architecture

```
upload .txt → chapter splitter → LangGraph Agent 4-node pipeline → output .md/.epub

LangGraph Nodes:
  1. fetch_glossary  — double-layer term retrieval (exact dict + Chroma vector)
  2. translate        — two-pass translation + cultural adaptation (single LLM call)
  3. update_glossary  — validate & persist new terms to both layers
  4. quality_check    — back-translation audit every N chapters (5-dim scoring)
```

## Quick Start

### 1. Clone and install

```bash
cd "Westward Echo（西渡）"
cp .env.example .env
# Edit .env with your API keys

pip install -r requirements.txt
```

### 2. Run tests (no API key needed)

```bash
python -m pytest tests/ -v
```

### 3. Run live translation test (requires API key)

```bash
DEEPSEEK_API_KEY=sk-xxx python -m pytest tests/test_translate_node.py -v -k "LiveTranslation"
```

### 4. Start the Gradio UI

```bash
python -m src.app
# Open http://localhost:7860
```

### 5. Start the API server

```bash
uvicorn src.main:create_app --host 0.0.0.0 --port 8000 --factory
# API docs at http://localhost:8000/docs
```

## Docker

```bash
# API only
docker compose up api

# With Gradio UI
docker compose --profile gradio up
```

## Project Structure

```
westward-echo/
├── src/
│   ├── main.py                  # FastAPI entry point
│   ├── config.py                # Environment + model routing
│   ├── chapter_splitter.py      # Regex chapter splitter with classification
│   ├── app.py                   # Gradio UI
│   ├── agent/
│   │   ├── graph.py             # LangGraph state graph + TranslationAgent
│   │   ├── state.py             # TranslatorState type definition
│   │   ├── nodes/
│   │   │   ├── fetch_glossary.py    # Double-layer term retrieval
│   │   │   ├── translate.py         # Translation + cultural adaptation
│   │   │   ├── update_glossary.py   # Term validation & persistence
│   │   │   └── quality_check.py     # Back-translation QA
│   │   └── prompts/
│   │       ├── term_extraction.py   # Initial glossary extraction prompt
│   │       ├── translation.py       # Core translation prompt (two-pass)
│   │       ├── term_validation.py   # Term review prompt
│   │       └── quality_check.py     # 5-dim scoring prompt
│   ├── glossary/
│   │   ├── models.py            # GlossaryTerm, TranslationResult
│   │   ├── exact_store.py       # O(1) dict + SQLite persistence
│   │   └── semantic_store.py    # Chroma vector search
│   └── api/
│       └── routes.py            # FastAPI REST + WebSocket endpoints
├── tests/
│   ├── fixtures/
│   │   └── pei_zong_ch1-3.txt   # Test novel (CEO romance / transmigration)
│   ├── test_chapter_splitter.py
│   ├── test_glossary.py
│   └── test_translate_node.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## Key Design Decisions

### Double-layer glossary
- **Exact layer** (Python dict + SQLite): O(1) string-contains match for character names, place names. Guarantees "林小满" → "Lin Xiaoman" across ALL chapters.
- **Semantic layer** (Chroma): Vector search for culturally relevant terms. Handles "this chapter is about cultivation → retrieve cultivation-related terms."

### Translation + Cultural Adaptation in ONE LLM call
Two-pass method inside a single prompt — literal comprehension (internal) → cultural rewriting (output). Separate nodes would lose context.

### Crash Recovery
SQLite checkpoint after every chapter. Restart picks up from the last completed chapter with full glossary intact.

### Per-node Model Routing
- V4 Flash: bulk translation (90%+ chapters), back-translation, term validation
- V4 Pro: initial term extraction, quality scoring, critical chapters (first/last/climax), retranslation
- Claude: optional arbitration for disputed quality scores

## Configuration

Model names are injected via environment variables — update `.env` when DeepSeek releases new models:

```bash
DEEPSEEK_FLASH_MODEL=deepseek-chat-flash  # Bulk translation
DEEPSEEK_PRO_MODEL=deepseek-chat-pro      # Quality-critical nodes
```

## License

Internal project for 点众科技 (Dianzhong Technology).

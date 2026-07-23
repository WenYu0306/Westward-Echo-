# 西渡 / Westward Echo

**A Multi-Agent LLM translation engine for Chinese web novels — with cultural adaptation, dialect preservation, and self-healing quality control.**

39 commits · 190 tests · v0.12.0

---

## Problem

Chinese web novels are a massive, fast-growing content category with a huge global readership — but human translation costs $30-60 per chapter, and naive LLM translation collapses after chapter 50: names drift, terms fragment, cultural references turn to nonsense.

Westward Echo solves all three with a 6-node Multi-Agent LangGraph pipeline — at under $0.02/chapter.

---

## Architecture

```
                        FastAPI Backend
                REST API + WebSocket + Dashboard
                              │
                              ▼
              6-NODE LANGGRAPH MULTI-AGENT PIPELINE

   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
   │ ① FETCH  │──▶│ ② TRANSL │──▶│ ③ UPDATE │──▶│ ④ ARBITR │──▶│ ⑤ QUALTY │
   │ GLOSSARY │   │ + ADAPT  │   │ GLOSSARY │   │ TERMS    │   │ CHECK    │
   │          │   │          │   │          │   │          │   │          │
   │ dict +   │   │ 2-pass   │   │ detect   │   │ resolve  │   │ back-    │
   │ Chroma   │   │ literal  │   │ conflicts│   │ winner   │   │ translate│
   │ + 9 ctx  │   │ → native │   │          │   │          │   │ 5-dim    │
   │ signals  │   │          │   │          │   │          │   │ score    │
   └──────────┘   └──────────┘   └──────────┘   └──────────┘   └─────┬────┘
                                                                       │
                                                          score < 3.5 │
                                                          ┌────────────▼──────────┐
                                                          │ ⑥ POLISH EDITOR       │
                                                          │ targeted fix, not      │
                                                          │ blind retranslation    │
                                                          └────────────────────────┘

   Storage:  dict (O(1) exact) + Chroma (semantic) + SQLite (checkpoint) + Redis (Celery)
   Models:   DeepSeek V4 Flash (bulk) + DeepSeek V4 Pro (QA + critical) + Claude Opus (arbitration)
   Tools:    lookup_glossary (MCP-style function calling — LLM can query glossary during translation)
```

### 9 Context Signals (auto-detected per chapter)
Dialect voice (5 Chinese→English mappings) · LitRPG system UI · Measurement localization · Onomatopoeia · Chengyu idioms · Cultural rules table · Human-confirmed/rejected terms · MCP Tool Use

---

## Key Technical Decisions

| Decision | Why | Alternative Rejected |
|---|---|---|
| **LangGraph 6-node Multi-Agent** (not raw API) | State management + conditional routing. Polish editor is a different agent with different prompt — fixes specific QA issues instead of blind retry | Dify (can't code-customize), raw LLM call (no state, no QA loop) |
| **Double-layer glossary** (dict + Chroma) | Character names need 100% deterministic match ("林小满" ≠ "林晓曼"). Cultural terms need semantic search | Pure Chroma (can't guarantee exact match), pure dict (can't handle semantic proximity) |
| **Translation + adaptation in ONE LLM call** | Cultural adaptation isn't post-processing — meaning is lost if you separate them | Two-pass pipeline (loses original context between stages) |
| **DeepSeek V4 Flash for bulk, Pro for QA** | Flash 3-5x cheaper, quality gap minimal with good prompts. Pro reserved for reasoning-heavy tasks | GPT-4o (more expensive, no Flash tier), all-Pro (wasteful) |
| **Celery + Redis for prod, sync for dev** | Same codebase, two modes gated by env var | WebSocket-only (breaks on 1000-chapter runs) |
| **SQLite checkpoints every chapter** | Crash at chapter 847 → resume from 847 with full glossary intact | In-memory only (catastrophic on crash) |
| **MCP-style Tool Use** | LLM can call `lookup_glossary` when it encounters unknown terms. Falls back to prompt injection if tool calling unavailable | Pure prompt injection (no autonomy), forced tool use (breaks on unsupported models) |

---

## Performance

```
50-chapter test (~150K Chinese characters, 6 named characters, 12 cultural term types):
  Chapter completion:   50/50 (100%)
  Empty translations:   0
  JSON residue:         0
  Avg quality score:    4.9/5.0 (back-translation QA, 5-dim automated audit)
  Cost:                 ~$1.50 total (DeepSeek V4 Flash)
  ZH:EN word ratio:     3.4x (expected 2.0-4.0x)
```

---

## Production Safety

- **Circuit breaker**: per-language isolation — if en-US API fails, es-ES continues unaffected
- **Backpressure guard**: rejects new tasks when queue depth > 100 chapters (HTTP 503)
- **Checkpoint recovery**: crash at any chapter → restart from last SQLite checkpoint
- **Startup pre-flight**: `start.sh` validates API keys, disk space, SQLite, Chroma before launch
- **Observability dashboard**: `GET /dashboard` — real-time worker status, throughput, error rates
- **Health endpoint**: `GET /health` — full subsystem status (not just "ok")

---

## Tech Stack

| Layer | Choice |
|---|---|
| Agent Framework | LangGraph (StateGraph + conditional routing) |
| Backend | FastAPI + Celery + Redis |
| Frontend | Vanilla HTML/CSS/JS (no framework, 3 pages: main UI + editor workbench + dashboard) |
| Vector DB | Chroma (ONNX all-MiniLM-L6-v2) |
| Exact Glossary | Python dict + SQLite |
| LLM | DeepSeek V4 Flash/Pro, Claude Opus (arbitration) |
| Deployment | Docker Compose (Redis + API + Worker) |

---

## Project Structure

```
westward-echo/
├── src/
│   ├── agent/
│   │   ├── graph.py              # LangGraph state graph (6 nodes)
│   │   ├── state.py              # TranslatorState TypedDict
│   │   ├── nodes/
│   │   │   ├── fetch_glossary.py # Double-layer term retrieval
│   │   │   ├── translate.py      # Translation + cultural adaptation (core)
│   │   │   ├── polish.py         # Editor Agent (targeted QA fixes)
│   │   │   ├── update_glossary.py# Term validation + conflict detection
│   │   │   ├── arbitrate_terms.py# Conflict resolution (pick best translation)
│   │   │   └── quality_check.py  # Back-translation 5-dim scoring
│   │   └── prompts/              # 5 prompt templates
│   ├── glossary/                 # Double-layer: exact_store + semantic_store
│   ├── api/                      # REST + WebSocket + auth + rate_limit + CMS + editor + review
│   ├── web_ui.py                 # Main translation UI
│   ├── editor_ui.py              # Human-in-the-loop editor workbench
│   ├── dashboard.py              # Observability dashboard
│   ├── cultural_rules.json       # Per-genre × per-language adaptation rules
│   ├── dialect.py                # 5 Chinese dialects → English dialect mapping
│   ├── idioms.py                 # 70+ chengyu detection + translation hints
│   ├── measurements.py           # Chinese unit detection + localization
│   ├── onomatopoeia.py           # 20 sound words → English equivalents
│   ├── tools.py                  # MCP-style function calling (lookup_glossary)
│   ├── circuit_breaker.py        # Per-language circuit breaker
│   ├── backpressure.py           # Queue depth guard
│   ├── stats.py                  # Token cost tracking + throughput metrics
│   ├── prefetch.py               # Parallel glossary prefetch
│   ├── celery_app.py             # Celery task definitions
│   ├── cms.py                    # CMS integration (file + webhook connectors)
│   ├── epub_builder.py           # EPUB 3 generator (stdlib only)
│   └── health.py                 # Startup pre-flight checks
├── tests/                        # 190 tests (unit + integration + fault injection)
├── start.sh                      # One-command launch with pre-flight
├── docker-compose.yml            # Redis + API + Worker
├── Dockerfile
├── ACCEPTANCE_CRITERIA.md        # 26-item production acceptance checklist
└── PROJECT_PLAN.md               # Design document
```

---

## Quick Start

```bash
cp .env.example .env   # add your DEEPSEEK_API_KEY
pip install -r requirements.txt
./start.sh             # pre-flight check + launch
# Open http://localhost:8000
```

**Key pages:**
- `/` — Translation UI (upload, multi-job management)
- `/editor/{job_id}` — Editor workbench (CN↔EN side-by-side, inline editing)
- `/review` — Glossary review (confirm/reject terms, Agent feedback loop)
- `/dashboard` — Observability (real-time metrics)

---

## What's Not Here (Yet)

- Real-world deployment validation (Docker config ready, never deployed)
- Celery + Redis end-to-end test (code complete, needs `redis-server`)
- es-ES / ar-SA native-speaker review (rules populated, quality unverified)
- 1000-chapter stress test (50-chapter test passed, scale TBD)

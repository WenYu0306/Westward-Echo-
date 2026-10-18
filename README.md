# Westward Echo / 西渡

**AI 文化编译引擎 — a reader-centric cultural compilation pipeline for Chinese web novels.**

`1840 tests` · `3,076 chapters validated` · `4 target languages` · `MIT`

---

## What This Is

Westward Echo does not translate word-for-word. It **compiles** — four reader agents (a cultural analyst, a writer, a cold reader, and an editor) each approach a chapter from a different reader's perspective, identify the cultural gaps an English reader cannot fill on their own, and rebuild the sensory and emotional imagery so the text lands the way it lands for a native Chinese reader.

The pipeline was validated by compiling two complete Chinese web novels end-to-end: *Infinite Horror* by zhttty (775 chapters) and *The Netherworld Calls Me Little Mister* by Jie Yu (2,301 chapters) — 5.35 million Chinese characters → 3.58 million English words, all cold-read quality checks PASS, 0 crashes.

Three rounds of independent audit (native English reader, bilingual accuracy auditor, professional editor) confirmed the output is structurally coherent across the full span, with prose quality holding steady from chapter 2 to chapter 770. The serial reader's final verdict: "I would read the sequel."

---

## Architecture

Five nodes, four of them readers:

```
START → READ → REVIEW_TERMS → WRITE → READBACK → (NEEDS_FIX?) → FIX → READBACK (loop)
                                           ↓ (PASS)
                                          END
```

| Node | Role | What It Does |
|------|------|-------------|
| **READ** | Bilingual cultural intelligence | Reads the Chinese chapter, analyzes cultural gaps, detects **sensory image gaps** (what a Chinese reader's brain fills in for free that an English reader's brain cannot), proposes terminology decisions |
| **REVIEW_TERMS** | Terminology consistency guard | Re-checks the chapter's terminology against the established glossary before writing begins |
| **WRITE** | Genre fiction writer | Retells the chapter in the target language — not a translator, a storyteller with permission to restructure, compress, and rebuild sensory images |
| **READBACK** | Cold reader | Reads ONLY the target-language output, has no idea this is a compilation, reports honest experience: "was I confused? bored? would I keep reading?" |
| **FIX** | Editor | Reads the cold reader's specific complaints, fixes only what's broken — targeted, surgical repairs |

`FIX → READBACK` loops at most 2 times per chapter. Most chapters run in **fast mode** (WRITE → END, no cold read); the full READ→WRITE→READBACK→FIX cycle runs every `QUALITY_CHECK_INTERVAL` chapters (default 20) to keep cost bounded on multi-thousand-chapter books.

### Why Reader-Centric

Chinese web novels carry a massive cultural payload. When a Chinese reader encounters "鬼节" (Ghost Festival), their brain automatically fills in: incense ash in the wind, paper money burning, a village holding its breath, old wood creaking, the wrongness of being outside after dark. These images cost the Chinese reader nothing because author and reader share the same cultural image library.

**An English reader has none of these images.** They see only the label. No picture forms. The scene fails.

The READ agent identifies every such **sensory image gap** — the full sensory picture the Chinese reader gets for free vs. the thin abstraction the English reader constructs from the same words — and provides **sensory anchors** (universal textures, sounds, colors: frozen meat, frost on skin, unmelted snow) that the WRITE agent uses to rebuild the scene.

---

## Model Routing

Since 2026-09 the project runs on **two providers, split by task shape** (DeepSeek is fully retired):

| Layer | Provider | Model | Nodes |
|-------|----------|-------|-------|
| **Judgment** (read/evaluate) | Qwen (通义千问) | `qwen3.8-max` | READ, READBACK, REVIEW_TERMS, WRITE_FALLBACK |
| **Text output** | GLM (智谱) | `glm-5.3-flash` | WRITE |
| **Text reasoning** | GLM (智谱) | `glm-5.3` | FIX |

The provider is derived from the model name (`qwen*` → Qwen endpoint, `glm*` → GLM endpoint), so cross-provider re-tiering works without changing node code. Every node can be individually re-tiered via environment variables (`XIDU_NODE_MODEL_READBACK=qwen3.8-flash`) without editing code. `WRITE_FALLBACK` swaps to Qwen if GLM's content filter rejects a real web-novel chapter (error 1301).

Why the split: GLM's content filter intermittently blocks real web-novel content at the READ layer, so all judgment/reading moved to Qwen; GLM handles the cheaper, higher-volume text-generation and repair nodes. An optional Anthropic Claude key is used only for adversarial arbitration, not the main pipeline.

---

## Quality Infrastructure

### Style Memo (Accumulated Translation Experience)

Six drawers of structured knowledge per book: character voices, pacing rules, cultural bridge patterns, prose rhythm, terminology decisions. Every chapter feeds into the memo — the READ agent's cultural analysis and terminology decisions write to it directly, cold-reader feedback and FIX repairs supplement it at sample points. By chapter 200, the WRITE agent has seen everything learned in chapters 1–199. Volume is bounded (max 5 FIX lessons per chapter) so the memo does not inflate the per-chapter prompt cost unboundedly.

### Cold Reader Blind Evaluation

At sample points, the READBACK agent cold-reads the target-language chapter without seeing the Chinese source. Verdict: PASS (clear and engaging) or NEEDS_FIX (significant friction). The cold reader receives a one-page briefing (recent plot, character roster, established terms) but never the source text, so it judges the output the way a real reader would.

### Glossary — Double-Layer Terminology Lock

Terms are locked by two stores: a SQLite exact-match dict for O(1) lookups, plus a Chroma vector store for semantic proximity (so "鬼差" can still hit the established "阴差" rendering). Terms accumulate per book across the whole run, keeping renderings consistent from chapter 5 to chapter 500.

### Output Guards & Cultural Fidelity

Every chapter passes through guards that strip Chinese-character residue and LLM meta-commentary. A cultural-fidelity check confirms WRITE did not flatten meaningful names into raw pinyin. Context-signal detectors localize dialect, idioms (chengyu), onomatopoeia, and measurement units, and inject the per-language cultural rules.

### Safety & Cost Controls

- **Circuit breaker** — rolling-window + probe-based throttling, isolated per language: an en-US API failure does not stop an es-ES run
- **Backpressure guard** — rejects new tasks when the queue exceeds 100 chapters (HTTP 503)
- **Checkpoint recovery** — crash at any chapter, resume from the last SQLite snapshot with the full glossary intact
- **Cost gate** — budget ceiling per job with a single source of truth for the price table (CNY / million tokens)
- **Token ledger** — per-book attribution of every call's cost against the real provider price list
- **Startup health check** — the server refuses to start if a critical dependency (DB, API keys) is missing rather than failing mid-run

---

## Service

A single FastAPI app (`python3.11 -m src.main`, port 8000) serves both the Web UI and a JSON API.

**Web UI** — five pages:

| Route | Page |
|-------|------|
| `/` | Landing page (upload + compile) |
| `/review` | Terminology review — confirm/reject machine-proposed renderings |
| `/editor/{job_id}` | Human-in-the-loop paragraph editor |
| `/dashboard` | Live progress / cost / error observability |
| `/usage` | Usage and error analytics |

**API** — the main routes under `/api`:

- `POST /api/translate` — submit a single book for compilation
- `POST /api/translate/multi` — batch submit
- `POST /api/translate/resume/{job_id}` — resume from a checkpoint
- `GET /api/translate/{job_id}` · `/api/translation/{job_id}` · `/api/glossary/{job_id}` · `/api/epub/{job_id}` — status, output, glossary, EPUB export
- `GET /api/jobs` · `/api/jobs/{job_id}` · `/api/jobs/{job_id}/cost` · `DELETE /api/jobs/{job_id}`
- `GET /api/projects` · `/api/projects/{project_id}`
- `GET/POST/DELETE /api/presets*` — translation presets
- `/api/review/*` — terminology confirm/reject/feedback
- `/api/editor/*` — chapter read/write/batch-replace
- `/api/cms/*` — import source novels, publish deliverables

**Middleware**: API-key auth (empty key = dev mode, no auth), token-bucket rate limiting (default 30 req/min), and CORS.

Async jobs run through **Celery + Redis** (`translate_novel_task`, `resume_translate_task`) with late-acknowledgement and worker-loss requeue. All SQLite stores (checkpoint, jobs, glossary, editor, events) use WAL mode for concurrent multi-worker access, and Chroma runs as a standalone HTTP server so multiple workers share one vector store.

---

## Validation: Two Full-Length Ground Truth Compilations

### Book 1 — *Infinite Horror* (无限恐怖) by zhttty

2.57 million Chinese characters, 775 chapters, compiled end-to-end.

```
Duration:       ~34 hours
Output:         1.13 million English words
Cold reads:     all PASS (serial reader, full-span context)
Prose:          7/10 (steady, chapters 2–770)
Comprehension:  10/10 (from chapter 25 onward)
Crashes:        0
Circuit breaker trips: 0
```

### Book 2 — *The Netherworld Calls Me Little Mister* (地府叫我小先生) by Jie Yu

2.78 million Chinese characters, 2,301 chapters, folk_religion genre, compiled end-to-end (completed 2026-08-03).

```
Output:       2.45 million English words
Cold reads:   59/59 PASS (every ~40 chapters + milestones)
Failed chapters after retry: 0
Crashes:      0
```

### Independent Audit Results (Round 2, Chapter 149)

| Auditor | Role | Verdict |
|---------|------|---------|
| Native English reader | Read ch2-5, 50-55, 120-130, 140-149 | "The translator now produces professional-grade genre fiction at English web-serial quality." Prose peaked at 9/10 (ch120-130). |
| Bilingual accuracy auditor | Compared ch100, 120, 131 source vs. translation side-by-side | "No new meaning errors found in the ch60-149 range." Terminology consistent across the full span. |
| Professional editor | Evaluated ch60-80, 100-120, 140-149 | "Needs one editing pass (80-100 hours). The core prose is readable and the pipeline preserved the novel's pacing." |

### Serial Cold Reader (Final, Chapter 774)

One reader, 8 checkpoints, accumulating knowledge like a real reader:

> "This is a deeply enjoyable progression fantasy that does not read like a translation — it reads like a mid-tier Royal Road serial. The characters are real, their deaths matter, and I would read the sequel."

---

## Quick Start

Requires **Python 3.11** (the `python3.11` interpreter, not a bare `python3`).

```bash
cp .env.example .env          # add LLM_API_KEY (Qwen) and GLM_API_KEY (GLM)
pip install -r requirements.txt
python3.11 -m src.main        # http://localhost:8000
```

Both provider keys are load-bearing — the pipeline needs Qwen for the judgment nodes and GLM for the text nodes, and the startup check refuses to start if either is missing.

**Compile a complete novel (terminal, no Redis):**

```bash
python3.11 scripts/run_one_segment.py
```

15-chapter segments with automatic checkpoint recovery. Ctrl+C safe. Resumes from the last checkpoint on restart. Quality sampled at milestone chapters, results saved to `_quality.json`.

Run the test suite (always ¥0 — no live API calls):

```bash
python3.11 -m pytest -q        # 1840 passed, 4 deselected
```

---

## Key Technical Decisions

| Decision | Why |
|----------|-----|
| **Compile, don't translate** | Word-for-word translation leaves the cultural payload behind. A reader-centric retelling rebuilds the sensory/emotional image the source reader got for free. |
| **Reader agents, not tool-calling executors** | Cultural analysis, writing, and evaluation are done by agents with distinct reader identities, not by a generic pipeline that calls tools. |
| **Two providers by task shape** | Qwen for judgment/reading (GLM's content filter blocks real web novels at the read layer), GLM for text generation and repair. |
| **Provider derived from model name** | `qwen*` → Qwen, `glm*` → GLM. Re-tiering a node across providers works without touching node code; unknown names raise instead of silently routing to the wrong endpoint. |
| **Per-node model override via env** | `XIDU_NODE_MODEL_<NODE>` re-tiers any node without a code change, so cost tuning is a config change, not a deploy. |
| **No MCP tool calling** | Pre-computed glossary injection eliminates tool-call reliability issues (wrong/duplicate/missed calls). |
| **max_retries=0 on all LLM calls** | The SDK default retries consume 3× the timeout before surfacing errors. Direct control prevents silent hangs. |
| **Style memo from READ analysis every chapter** | Not just from cold-reader feedback at sample chapters — every chapter's analysis writes to the memo, so nothing learned is lost. |

---

## Dependencies

```
langgraph ≥0.2.0      langchain ≥0.3.0      langchain-openai ≥0.2.0
chromadb ≥0.5.0       fastapi ≥0.115.0      uvicorn[standard] ≥0.32.0
pydantic ≥2.0         python-dotenv ≥1.0    tenacity ≥9.0
celery[redis] ≥5.4.0  redis ≥5.2.0          httpx ≥0.28.0
python-multipart ≥0.0.20
```

---

## What's Not Here Yet

- **Native-speaker review of the `es-ES` / `de` / `fr` languages.** Term-level data exists for all four languages, but many `de`/`fr` entries are first-established renderings tagged `⚠️ FIRST-ESTABLISHED — pending native review`, and the `de`/`fr`/`es-ES` cultural *fidelity* rules (the category-level guidance in `cultural_rules.json`) are `en-US`-only. Translation quality for every non-English language remains unverified.
- **Client-level multi-tenancy.** Authentication is a single shared API key; per-book data is isolated by `book_id` (glossary, style memo, token ledger), but there is no per-customer key/billing boundary yet. The shape of client isolation is still an open product decision.
- **Strictly-blind cold read.** The cold-read briefing's terminology section still carries Chinese source renderings (a known residual leak on the production path), so the "blind" read is not yet fully blind in practice.
- **Long-distance style memo A/B validation.** The infrastructure exists to compare memo-on vs. memo-off quality across chapters 1–200, but the comparison has never been run end-to-end.
- **Foreshadowing protection.** The pipeline can delete passages marked "pure cultural fluff"; no formal mechanism prevents dropping a seeded foreshadowing line.

---

## License

MIT · github.com/WenYu0306/Westward-Echo-

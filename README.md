# Westward Echo / 西渡

**A reader-centric multi-agent translation engine for Chinese web novels.**

`209 tests` · `775-chapter validated` · `3 independent audits` · `MIT`

---

## What This Is

Westward Echo doesn't translate. Four LLM agents — a reader, a writer, a cold reader, and an editor — each approach the text from a different reader's perspective. The pipeline was validated by translating a complete 775-chapter Chinese web novel (*Infinite Horror* by zhttty) end-to-end: 34 hours, 2.57 million Chinese characters → 1.13 million English words, 16 cold-read quality checks, 0 crashes.

Three rounds of independent audit (native English reader, bilingual accuracy auditor, professional editor) confirmed the translation is structurally coherent across the full span, with prose quality holding steady from chapter 2 to chapter 770. The serial reader's final verdict: "I would read the sequel."

---

## Architecture

```
START → READ → WRITE → READBACK → (NEEDS_FIX?) → FIX → READBACK (loop)
                            ↓ (PASS)
                           END
```

| Node | Role | What It Does | Model |
|------|------|-------------|-------|
| **READ** | Bilingual cultural intelligence | Reads the Chinese chapter, analyzes cultural gaps, detects **sensory image gaps** (what a Chinese reader's brain fills in for free that an English reader's brain cannot), proposes terminology decisions | DeepSeek V4 Pro |
| **WRITE** | English genre fiction writer | Retells the chapter in English — not a translator, a storyteller with permission to restructure, compress, and rebuild sensory images | DeepSeek V4 Flash |
| **READBACK** | Cold reader from Reddit | Reads ONLY the English output, has no idea this is a translation, reports honest experience: "was I confused? bored? would I keep reading?" | DeepSeek V4 Flash |
| **FIX** | Editor | Reads the cold reader's specific complaints, fixes only what's broken — targeted, surgical repairs | DeepSeek V4 Flash |

### Why Reader-Centric

Chinese web novels carry a massive cultural payload. When a Chinese reader encounters "鬼节" (Ghost Festival), their brain automatically fills in: incense ash in the wind, paper money burning, a village holding its breath, old wood creaking, the wrongness of being outside after dark. These images cost the Chinese reader nothing because author and reader share the same cultural image library.

**An English reader has none of these images.** They see only the label. No picture forms. The scene fails.

The READ agent identifies every such **sensory image gap** — the full sensory picture the Chinese reader gets for free vs. the thin abstraction the English reader constructs from the same words — and provides **sensory anchors** (universal textures, sounds, colors: frozen meat, frost on skin, unmelted snow) that the WRITE agent uses to rebuild the scene.

---

## Quality Infrastructure

### Style Memo (Accumulated Translation Experience)

Six drawers of structured knowledge per book: character voices, pacing rules, cultural bridge patterns, prose rhythm, terminology decisions. Every chapter feeds into the memo — the READ agent's cultural analysis and terminology decisions write to it directly, cold reader feedback supplements it at sample points. By chapter 200, the WRITE agent has seen everything learned in chapters 1-199.

### Cold Reader Blind Evaluation

At sample points (every ~50 chapters), the READBACK agent cold-reads the English chapter without seeing the Chinese source. Verdict: PASS (clear and engaging) or NEEDS_FIX (significant friction). A serial cold reader evaluated 8 checkpoints across the full 775-chapter span and reported prose quality holding steady at 7/10 from start to finish, comprehension never dropping below 10/10 after chapter 25.

### Output Guards

Every chapter passes through: Chinese character residue detection and auto-stripping, LLM meta-commentary removal, Arabic blasphemy scanning (ar-SA mode). Terms are locked by a double-layer glossary: SQLite dict for O(1) exact matching plus Chroma vector store for semantic proximity.

### Safety

- **Circuit breaker**: per-language isolation — if en-US API fails, es-ES continues unaffected
- **Backpressure guard**: rejects new tasks when queue depth exceeds 100 chapters (HTTP 503)
- **Checkpoint recovery**: crash at any chapter, resume from last SQLite snapshot with full glossary intact
- **Pre-commit hooks**: ruff (lint + format), mypy (type checking)

### 9 Context Signal Detectors

Dialect mapping (5 Chinese → English dialect equivalents), LitRPG system UI markers, measurement localization, onomatopoeia, chengyu idiom detection, cultural rule injection, human-confirmed/rejected term lists.

---

## Validation: 775-Chapter Ground Truth Translation

*Infinite Horror* (无限恐怖) by zhttty — 2.57 million Chinese characters, 775 chapters, translated end-to-end.

```
Duration:     ~34 hours
Output:       1.13 million English words
Cold reads:   16/16 PASS (old prompt) → 8/8 PASS (serial reader, new prompt + context)
Prose:        7/10 (steady, chapters 2-770)
Comprehension: 10/10 (from chapter 25 onward)
Cost:         ~$4 (DeepSeek V4 Flash + Pro mixed)
Crashes:      0
Circuit breaker trips: 0
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

```bash
cp .env.example .env          # add your DEEPSEEK_API_KEY
pip install -r requirements.txt
python3 -m src.main            # http://localhost:8000
```

**Translate a complete novel (terminal, no sandbox):**

```bash
python3 scripts/run_one_segment.py
```

15-chapter segments with automatic checkpoint recovery. Ctrl+C safe. Resumes from last checkpoint on restart. Quality sampled at milestone chapters, results saved to `_quality.json`.

---

## Key Technical Decisions

| Decision | Why |
|----------|-----|
| **4-node reader pipeline** (not 6-node worker pipeline) | Each node is a reader, not a tool-calling executor. Cultural analysis and translation are handled by agents with distinct reader identities. |
| **READ always Pro, WRITE/READBACK/FIX always Flash** | DeepSeek V4 Pro hangs on large inputs (>4000 chars) with JSON output. Flash is reliable and costs 3× less. READ is the only node that needs Pro-level cultural reasoning. |
| **No MCP tool calling** | Pre-computed glossary injection eliminates reliability issues (wrong tool calls, duplicate calls, missed calls). |
| **max_retries=0 on all LLM calls** | OpenAI SDK defaults to 2 retries, consuming 3× the request_timeout before surfacing errors. Direct control prevents silent hangs. |
| **Style memo from READ analysis every chapter** | Not just from cold reader feedback at sample chapters. Every chapter's READ analysis writes to the memo. |

---

## Dependencies

```
langgraph ≥0.2.0    langchain ≥0.3.0    langchain-openai ≥0.2.0
chromadb ≥0.5.0     fastapi ≥0.115.0    uvicorn[standard] ≥0.32.0
pydantic ≥2.0       python-dotenv ≥1.0   celery[redis] ≥5.4.0
redis ≥5.2.0        httpx ≥0.28.0
```

---

## What's Not Here

- `es-ES` / `ar-SA` native-speaker quality review (style notes populated, multi-language routing tested, quality unverified)
- Long-distance style memo A/B validation (infrastructure built, never compared memo-on vs. memo-off translation quality across chapters 1-200)
- Foreshadowing tracker (the pipeline can delete passages marked as "pure cultural fluff" — it has never been observed doing so, but no formal mechanism prevents it)

---

## License

MIT · github.com/WenYu0306/Westward-Echo-

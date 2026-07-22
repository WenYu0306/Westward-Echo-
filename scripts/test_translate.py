"""
Minimal end-to-end test using only httpx (already installed).
Verifies: API connectivity, translation quality, term extraction, glossary consistency.
"""

import json
import os
import sys
import httpx
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

API_KEY = os.getenv("DEEPSEEK_API_KEY")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = "deepseek-chat"  # DeepSeek V4 unified endpoint


def call_llm(system_prompt: str, user_prompt: str, temperature: float = 0.2, max_tokens: int = 8192) -> dict:
    """Call DeepSeek API with chat completions."""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    resp = httpx.post(
        f"{BASE_URL}/v1/chat/completions",
        headers=headers,
        json=body,
        timeout=120,
    )
    if not resp.is_success:
        print(f"\n[API ERROR] Status {resp.status_code}")
        print(f"Response: {resp.text[:500]}")
        resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"].strip()

    # Handle markdown-wrapped JSON
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        print(f"[WARN] JSON parse failed, raw response: {content[:300]}")
        return {"raw": content}


# ============================================================
# STEP 1: Term extraction (initial glossary from first 3 chapters)
# ============================================================

TERM_EXTRACTION_SYSTEM = """\
You are a terminology extraction specialist for Chinese-to-English web novel translation.
Scan the following Chinese web novel chapters and identify ALL proper nouns, culturally \
specific terms, and recurring expressions that need consistent translation.

Classify each as: character, location, technique, culture, item, or era.

## Cultural adaptation guidelines for en-US:
- 霸总 → "Alpha CEO"
- 穿越 → "Transmigration"
- 穿书 → "Transmigrated into a novel"
- 系统 → "System" (capitalized, LitRPG convention)
- 白莲花 → "goody-two-shoes"
- 备胎 → "backup / second choice"
- 社畜 → "corporate drone / wage slave"
- 带球跑 → "run away pregnant"
- 暖男 → "sweet guy"
- 金手指 → "cheat code"
- 996 → "996 grind"
- 打脸 → "face-slap"
- 父凭子贵 → paternal status through child
"""

TERM_EXTRACTION_USER = """\
Extract ALL proper nouns, names, places, techniques, and culturally specific terms from this text.

IMPORTANT: For each term, output BOTH the Chinese (term_cn) and the English translation (term_en) as SEPARATE fields. Also include category, context, and note fields.

Example format:
{{
  "terms": [
    {{"term_cn": "苏念", "term_en": "Su Nian", "category": "character", "context": "苏念今年24岁", "note": "female lead"}},
    {{"term_cn": "霸总", "term_en": "Alpha CEO", "category": "culture", "context": "狗血霸总文", "note": "adopt US romance novel archetype"}}
  ]
}}

Chapters:
{novel_text}
"""


# ============================================================
# STEP 2: Translate chapter with cultural adaptation
# ============================================================

TRANSLATION_SYSTEM = """\
You are a professional Chinese-to-English web novel translator specialized in cultural \
adaptation for the American market. Translate Chinese web novels into natural, engaging \
English that reads like it was originally written for American audiences.

## CORE RULES

### 1. Glossary First
Use the provided glossary translations EXACTLY. No variation.

### 2. Two-Pass Translation
- Pass 1 (internal): Understand every detail of the Chinese text.
- Pass 2 (output): Rewrite for American readers. Convert idioms. Adapt culture. Read like a Netflix show.

### 3. Cultural Mapping
| 中文 | Use This |
|------|----------|
| 霸总 | Alpha CEO |
| 白莲花 | goody-two-shoes |
| 吃瓜群众 | popcorn gallery |
| 社畜 | corporate drone |
| 暖男 | sweet guy / cinnamon roll |
| 996 | 996 grind |
| 打脸 | face-slap |
| 父凭子贵 | Daddy's Golden Ticket |
| 带球跑 | bun in the oven and gone |
| 金手指 | cheat code |
| 系统 | System |
| 穿越 | Transmigration |

### 4. Style
- Dialogue: casual American English (Netflix, not textbook)
- Paragraphs: short and punchy, 2-4 sentences
- Cliffhangers: preserve the hook
- Emotions: show don't tell
- Profanity: match intensity, don't sanitize

Return a JSON object:
{
  "translated_text": "Full English chapter",
  "new_terms_found": [{"term_cn": "", "term_en": "", "category": "", "context": ""}],
  "cultural_adaptation_notes": ["2-3 bullets"],
  "chapter_summary": "3-sentence summary"
}
"""


def main():
    # Load test fixture
    fixture_path = Path(__file__).parent.parent / "tests" / "fixtures" / "pei_zong_ch1-3.txt"
    text = fixture_path.read_text(encoding="utf-8")

    print("=" * 60)
    print("WESTWARD ECHO — End-to-End Translation Test")
    print("=" * 60)
    print(f"API: {BASE_URL}")
    print(f"Model: {MODEL}")
    print(f"Fixture: {len(text)} chars")

    # ------------------------------------------------------------------
    # STEP 1: Extract initial glossary
    # ------------------------------------------------------------------
    print("\n[1/3] Extracting glossary from first 3 chapters...")
    print("-" * 40)

    terms_result = call_llm(
        system_prompt=TERM_EXTRACTION_SYSTEM,
        user_prompt=TERM_EXTRACTION_USER.format(novel_text=text[:15000]),
        temperature=0.1,
        max_tokens=4096,
    )

    terms = terms_result.get("terms", [])
    print(f"Extracted {len(terms)} terms:")

    # Debug: show actual JSON structure
    if terms:
        print(f"  [DEBUG] First term keys: {list(terms[0].keys())}")

    glossary = {}
    for t in terms:
        # Handle possible key name variations from the API
        cn = t.get("term_cn") or t.get("chinese") or t.get("cn") or t.get("original") or t.get("term")
        en = t.get("term_en") or t.get("english") or t.get("en") or t.get("translation") or t.get("term")
        if not cn:
            print(f"  [SKIP] Can't parse: {json.dumps(t, ensure_ascii=False)[:100]}")
            continue
        # If the model returned only a Chinese term (no separate English),
        # use the term as both cn and place a placeholder for en
        if cn and not en:
            en = f"[PENDING] {cn}"  # Will be refined in translation phase
        cat = t.get("category", "culture")
        glossary[cn] = en
        context = t.get("context", "")[:50]
        print(f"  [{cat}] {cn} → {en}  ({context}...)")

    # ------------------------------------------------------------------
    # STEP 2: Translate Chapter 1 with glossary
    # ------------------------------------------------------------------
    print("\n[2/3] Translating Chapter 1 with glossary...")
    print("-" * 40)

    # Extract Chapter 1 text
    ch1_start = text.find("第一章")
    ch2_start = text.find("第二章")
    if ch2_start == -1:
        ch2_start = len(text)
    chapter1_text = text[ch1_start:ch2_start].strip()

    # Format glossary for prompt
    glossary_lines = ["| Chinese | English |", "|----------|---------|"]
    for cn, en in sorted(glossary.items(), key=lambda x: len(x[0]), reverse=True):
        if cn in chapter1_text:
            glossary_lines.append(f"| {cn} | {en} |")
    glossary_text = "\n".join(glossary_lines) if len(glossary_lines) > 2 else "(No matches)"

    translation_prompt = f"""\
## GLOSSARY — EXACT MATCHES (MUST USE EXACTLY)
{glossary_text}

## SOURCE TEXT
{chapter1_text}

## OUTPUT
Translate this chapter. Follow the System prompt's Two-Pass method. Output as JSON.
"""

    result = call_llm(
        system_prompt=TRANSLATION_SYSTEM,
        user_prompt=translation_prompt,
        temperature=0.2,
        max_tokens=8192,
    )

    translated = result.get("translated_text", "")
    new_terms = result.get("new_terms_found", [])
    notes = result.get("cultural_adaptation_notes", [])
    summary = result.get("chapter_summary", "")

    print(f"\n{'─' * 40}")
    print("TRANSLATION (first 800 chars):")
    print("─" * 40)
    print(translated[:800])
    if len(translated) > 800:
        print(f"\n... ({len(translated)} total chars)")

    print(f"\nNew terms found: {len(new_terms)}")
    for t in new_terms:
        print(f"  + {t.get('term_cn', '?')} → {t.get('term_en', '?')}")

    print(f"\nCultural adaptation notes:")
    for n in notes:
        print(f"  • {n}")

    print(f"\nChapter summary: {summary[:200]}")

    # ------------------------------------------------------------------
    # STEP 3: Verify terminology consistency in Chapter 2
    # ------------------------------------------------------------------
    print("\n[3/3] Verifying terminology consistency in Chapter 2...")
    print("-" * 40)

    ch2_real_start = text.find("第二章")
    ch3_start = text.find("第三章")
    if ch3_start == -1:
        ch3_start = len(text)
    chapter2_text = text[ch2_real_start:ch3_start].strip()

    # Update glossary with chapter 1 new terms
    for t in new_terms:
        glossary[t["term_cn"]] = t["term_en"]

    # Refresh glossary matches for chapter 2
    glossary_lines = ["| Chinese | English |", "|----------|---------|"]
    for cn, en in sorted(glossary.items(), key=lambda x: len(x[0]), reverse=True):
        if cn in chapter2_text:
            glossary_lines.append(f"| {cn} | {en} |")
    glossary_text = "\n".join(glossary_lines) if len(glossary_lines) > 2 else "(No matches)"

    ch2_prompt = f"""\
## PREVIOUS CHAPTER SUMMARY
{summary}

## GLOSSARY — MUST USE EXACTLY
{glossary_text}

## SOURCE TEXT
{chapter2_text[:3000]}

## OUTPUT
Translate. Output as JSON.
"""

    result2 = call_llm(
        system_prompt=TRANSLATION_SYSTEM,
        user_prompt=ch2_prompt,
        temperature=0.2,
        max_tokens=8192,
    )

    translated2 = result2.get("translated_text", "")

    # Check if key terms from ch1 appear correctly in ch2
    print("\nConsistency checks:")
    check_terms = ["Su Nian", "Pei Yanzhou", "Alpha CEO", "System", "Transmigrat"]
    for t in check_terms:
        in_ch1 = t.lower() in translated.lower()
        in_ch2 = t.lower() in translated2.lower()
        status = "✓" if (in_ch1 and in_ch2) else "✗"
        print(f"  {status} '{t}': ch1={in_ch1}, ch2={in_ch2}")

    print(f"\nChapter 2 translation ({len(translated2)} chars):")
    print(translated2[:600])
    if len(translated2) > 600:
        print(f"...")

    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    if not API_KEY:
        print("ERROR: DEEPSEEK_API_KEY not set in .env")
        sys.exit(1)

    print(f"Using API key: {API_KEY[:12]}...{API_KEY[-4:]}")
    main()

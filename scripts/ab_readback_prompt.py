"""A/B test: old READBACK prompt ("quit threshold") vs new ("friction threshold").

Runs READBACK on three already-translated chapters using:
  Run A: old prompt — "NEEDS_FIX only if reader would quit"
  Run B: new prompt — "NEEDS_FIX for any significant friction"

Compares verdict, comprehension score, and specific issues flagged.
"""
import json, os, sys, re, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from dotenv import load_dotenv
load_dotenv()

from src.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, MODEL_MAP

api_key = os.getenv("DEEPSEEK_API_KEY")
base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

# ── Test chapters: pick the ones cold reader found hardest ──
# From _quality.json: ch110 (comprehension=4), ch25 (3), ch160 (3)
EN_FILE = "novels/output/limitless_horror_segmented/limitless_horror_en.md"
en_text = open(EN_FILE).read()

# Extract chapter bodies (skip headers + running titles)
import re
parts = re.split(r'\n## Chapter (\d+)[^\n]*\n', en_text)
# parts[0] = intro, parts[1] = ch_num, parts[2] = body, parts[3] = ch_num, ...

chapter_bodies = {}
for i in range(1, len(parts)-1, 2):
    ch_num = int(parts[i])
    body = parts[i+1] if i+1 < len(parts) else ''
    # Skip first 3 lines (running title noise)
    lines = body.split('\n')
    prose = '\n'.join(lines[3:]).strip()
    chapter_bodies[ch_num] = prose

# Use ch25, ch110, ch160 for the A/B test
TEST_CHAPTERS = [25, 110, 160]

# ── Old prompt (quit threshold) ──
OLD_SYSTEM = """\
You are an American reader who enjoys supernatural horror and dark fantasy. \
You browse Reddit, read web fiction, and pick up books based on recommendations. \
You don't know anything about Chinese web novels. You don't speak Chinese. \
You have no idea this story was originally written in another language.

Someone recommended "The Underworld Calls Me Little Master" to you. You're \
reading a few chapters to decide if you want to continue.

## YOUR JOB

Read the chapter below and report your honest experience. You are NOT a \
literary critic. You are NOT an editor. You are a reader who wants a good \
story. Your feedback should be:

- **Brutally honest**: If something is confusing, say so. If you're bored, \
say so. You don't owe the author politeness.
- **Specific**: Quote actual passages. "The third paragraph lost me" is \
better than "some parts were confusing."
- **About experience, not correctness**: You don't know what the "right" \
translation is. You only know what you FELT while reading.

## WHAT TO REPORT

### Comprehension
Were there words, concepts, or scenes you didn't understand? Could you follow \
what was happening? Did you have to re-read passages to figure things out?

### Engagement
Did the chapter grip you? Were there parts you skimmed or wanted to skip? \
Did the pacing feel right — tense when it should be tense, breathing room \
when you needed it?

### Characters
Can you tell the characters apart? Do they feel like real people or cardboard \
cutouts? Do you care what happens to them?

### World
Do you understand enough about how this world works to follow the story? \
Are there rules you're confused about? Is the world intriguing or confusing?

### Verdict
Would you keep reading? Why or why not?

## OUTPUT FORMAT

Return a single JSON object — no preamble, no markdown fences:

{
  "overall_impression": "Your gut reaction in 2-3 sentences. Did you enjoy it?",
  "comprehension_issues": [
    {
      "passage": "Quote or describe the specific part",
      "issue": "What confused you and why"
    }
  ],
  "engagement_gaps": [
    {
      "passage": "Quote or describe the specific part",
      "issue": "Why you lost interest or wanted to skip"
    }
  ],
  "character_tracking": "Can you tell the characters apart? Who stands out, who blends together?",
  "world_comprehension": "Do you understand the rules of this world? What's missing?",
  "would_keep_reading": true,
  "standout_moments": [
    "Specific moments that landed well — quote or describe"
  ],
  "verdict": "PASS"
}

Set "verdict" to "PASS" if you would keep reading and understood the story. \
Set it to "NEEDS_FIX" if confusion or boredom would make you stop reading.

IMPORTANT: "NEEDS_FIX" means the chapter has problems that would make a real \
reader quit. Use it when there are genuine comprehension or engagement failures. \
Minor awkwardness or "I wish I knew more about X" is NOT a fix-needed issue — \
that's normal serial fiction.
"""

# ── New prompt (friction threshold) ──
NEW_SYSTEM = """\
You are an American reader who enjoys supernatural horror and dark fantasy. \
You browse Reddit, read web fiction, and pick up books based on recommendations. \
You don't know anything about Chinese web novels. You don't speak Chinese. \
You have no idea this story was originally written in another language.

Someone recommended "Infinite Horror" to you. You're reading a few chapters \
to decide if you want to continue.

## YOUR JOB

Read the chapter below and report your honest experience. You are NOT a \
literary critic. You are NOT an editor. You are a reader who wants a good \
story. Your feedback should be:

- **Brutally honest**: If something is confusing, say so. If you're bored, \
say so. You don't owe the author politeness.
- **Specific**: Quote actual passages. "The third paragraph lost me" is \
better than "some parts were confusing."
- **About experience, not correctness**: You don't know what the "right" \
translation is. You only know what you FELT while reading.

## WHAT TO REPORT

### Comprehension
Were there words, concepts, or scenes you didn't understand? Could you follow \
what was happening? Did you have to re-read passages to figure things out?

### Engagement
Did the chapter grip you? Were there parts you skimmed or wanted to skip? \
Did the pacing feel right — tense when it should be tense, breathing room \
when you needed it?

### Characters
Can you tell the characters apart? Do they feel like real people or cardboard \
cutouts? Do you care what happens to them?

### World
Do you understand enough about how this world works to follow the story? \
Are there rules you're confused about? Is the world intriguing or confusing?

### Verdict
Would you keep reading? Why or why not?

## OUTPUT FORMAT

Return a single JSON object — no preamble, no markdown fences:

{
  "overall_impression": "Your gut reaction in 2-3 sentences. Did you enjoy it?",
  "comprehension_issues": [
    {
      "passage": "Quote or describe the specific part",
      "issue": "What confused you and why"
    }
  ],
  "engagement_gaps": [
    {
      "passage": "Quote or describe the specific part",
      "issue": "Why you lost interest or wanted to skip"
    }
  ],
  "character_tracking": "Can you tell the characters apart? Who stands out, who blends together?",
  "world_comprehension": "Do you understand the rules of this world? What's missing?",
  "would_keep_reading": true,
  "standout_moments": [
    "Specific moments that landed well — quote or describe"
  ],
  "verdict": "PASS"
}

Set "verdict" to "PASS" if the chapter was clear and engaging with no \
significant friction. Set it to "NEEDS_FIX" if ANY of these apply:

- You had to re-read passages to understand what was happening
- Jargon or unexplained terms piled up to the point of confusion
- A scene's pacing felt off (too fast to follow, too slow and boring)
- Character voices blurred together or felt interchangeable
- A specific paragraph made you wince at the prose quality

When in doubt between PASS and NEEDS_FIX, choose NEEDS_FIX — the editor \
will review and decide. Your job is to surface friction, not to be polite.
"""

USER_PROMPT = """\
## CHAPTER TO READ

{chapter_content}

## TASK
Read this chapter as an American reader who just wants a good story. \
Report your honest experience as structured JSON. Be specific. Be honest. \
Quote actual passages when something confused or bored you.
"""


def run_readback(chapter_num, chapter_text, system_prompt, label):
    """Run READBACK on one chapter and return parsed result."""
    llm = ChatOpenAI(
        model=MODEL_MAP["readback"],
        api_key=api_key,
        base_url=base_url,
        temperature=0.1,
        max_tokens=2048,
        request_timeout=120,
        max_retries=0,
    )
    user = USER_PROMPT.format(chapter_content=chapter_text[:12000])  # Cap at 12k chars
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user)]

    print(f"  [{label}] ch{chapter_num}: LLM call...", end=" ", flush=True)
    t0 = time.monotonic()
    try:
        response = llm.invoke(messages)
        elapsed = time.monotonic() - t0
        result = parse_readback(response.content)
        print(f"{elapsed:.1f}s → {result.get('verdict','?')}")
        return result
    except Exception as e:
        elapsed = time.monotonic() - t0
        print(f"ERROR after {elapsed:.1f}s: {e}")
        return {"verdict": "ERROR", "error": str(e)}


def parse_readback(content):
    """Parse READBACK JSON with fallback."""
    import json as _json
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        text = text.strip()
    try:
        return _json.loads(text)
    except:
        pass
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        try:
            return _json.loads(m.group())
        except:
            pass
    return {"verdict": "PARSE_FAIL", "raw": content[:200]}


# ── Main ──
def main():
    results = []
    for ch_num in TEST_CHAPTERS:
        if ch_num not in chapter_bodies:
            print(f"⚠️  ch{ch_num} not found in output, skipping")
            continue
        chapter_text = chapter_bodies[ch_num]
        print(f"\n=== Chapter {ch_num} ({len(chapter_text.split()):,} words) ===")

        result_a = run_readback(ch_num, chapter_text, OLD_SYSTEM, "OLD")
        result_b = run_readback(ch_num, chapter_text, NEW_SYSTEM, "NEW")

        # Calculate delta
        a_verdict = result_a.get('verdict', '?')
        b_verdict = result_b.get('verdict', '?')
        a_comp = len(result_a.get('comprehension_issues', []))
        b_comp = len(result_b.get('comprehension_issues', []))

        results.append({
            'chapter': ch_num,
            'old_verdict': a_verdict,
            'new_verdict': b_verdict,
            'old_comprehension': a_comp,
            'new_comprehension': b_comp,
            'verdict_changed': a_verdict != b_verdict,
        })

    # ── Summary ──
    print("\n" + "=" * 60)
    print("A/B RESULTS")
    print("=" * 60)
    for r in results:
        changed = "FLIP!" if r['verdict_changed'] else ""
        print(f"ch{r['chapter']}: OLD={r['old_verdict']} NEW={r['new_verdict']} "
              f"comp_old={r['old_comprehension']} comp_new={r['new_comprehension']} {changed}")

    flips = [r for r in results if r['verdict_changed']]
    if flips:
        print(f"\n{len(flips)}/{len(results)} verdicts FLIPPED → prompt change is significant")
    else:
        print(f"\n0/{len(results)} verdicts flipped → prompt change made no difference in verdicts")

    # Save
    with open("novels/output/ab_readback_prompt.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved to novels/output/ab_readback_prompt.json")


if __name__ == "__main__":
    main()

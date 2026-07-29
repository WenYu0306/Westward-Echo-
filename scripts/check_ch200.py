"""Run new READBACK prompt on ch200 — one-shot cold read."""
import json, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env before any other imports
from dotenv import load_dotenv
load_dotenv()

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from src.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, MODEL_MAP

EN = "novels/output/limitless_horror_segmented/limitless_horror_en.md"
text = open(EN).read()
parts = re.split(r'\n## Chapter (\d+)[^\n]*\n', text)

chapter_body = ""
for i in range(1, len(parts)-1, 2):
    if parts[i] == "200":
        body = parts[i+1] if i+1 < len(parts) else ''
        lines = body.split('\n')
        chapter_body = '\n'.join(lines[3:]).strip()[:12000]
        break

if not chapter_body:
    print("ch200 not found")
    sys.exit(1)

print(f"ch200: {len(chapter_body.split())} words")

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
- **Specific**: Quote actual passages.
- **About experience, not correctness**: You don't know what the "right" \
translation is. You only know what you FELT while reading.

## WHAT TO REPORT

### Comprehension, Engagement, Characters, World, Verdict

## OUTPUT FORMAT

Return a single JSON object — no preamble, no markdown fences:

{
  "overall_impression": "Your gut reaction in 2-3 sentences.",
  "comprehension_issues": [{"passage": "quote", "issue": "what confused you"}],
  "engagement_gaps": [{"passage": "quote", "issue": "why you lost interest"}],
  "character_tracking": "Can you tell characters apart?",
  "world_comprehension": "Do you understand the rules?",
  "would_keep_reading": true,
  "standout_moments": ["moments that landed"],
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

{content}

## TASK
Read this chapter as an American reader who just wants a good story. \
Report your honest experience as structured JSON. Be specific. Be honest. \
Quote actual passages when something confused or bored you.
"""

llm = ChatOpenAI(
    model=MODEL_MAP["readback"],
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    temperature=0.1,
    max_tokens=2048,
    request_timeout=120,
    max_retries=0,
)

print(f"Running NEW prompt on ch200...", end=" ", flush=True)
t0 = time.monotonic()
messages = [SystemMessage(content=NEW_SYSTEM), HumanMessage(content=USER_PROMPT.format(content=chapter_body))]
response = llm.invoke(messages)
elapsed = time.monotonic() - t0

result_text = response.content.strip()
if result_text.startswith("```"):
    lines = result_text.split("\n")
    result_text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
try:
    result = json.loads(result_text)
except:
    m = re.search(r'\{[\s\S]*\}', result_text)
    result = json.loads(m.group()) if m else {"verdict": "PARSE_FAIL"}

print(f"{elapsed:.1f}s")
print(f"\nVerdict: {result.get('verdict', '?')}")
print(f"Keep reading: {result.get('would_keep_reading', '?')}")
print(f"Comprehension issues: {len(result.get('comprehension_issues', []))}")
print(f"Engagement gaps: {len(result.get('engagement_gaps', []))}")
print(f"\nImpression: {result.get('overall_impression', '')[:500]}")
if result.get('comprehension_issues'):
    print(f"\nComprehension issues:")
    for ci in result.get('comprehension_issues', [])[:3]:
        print(f"  - {ci.get('issue', '')[:200]}")
if result.get('engagement_gaps'):
    print(f"\nEngagement gaps:")
    for eg in result.get('engagement_gaps', [])[:3]:
        print(f"  - {eg.get('issue', '')[:200]}")

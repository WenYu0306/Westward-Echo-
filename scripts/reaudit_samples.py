"""Re-audit random chapters with new prompt + richer context.

Now builds a proper cold read briefing: character roster from glossary +
last 3 chapter titles + previous chapter ending.  Compare old PASS vs new.
"""
import json, os, sys, re, random, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from dotenv import load_dotenv
load_dotenv()

from src.agent.prompts.readback import READBACK_SYSTEM as NEW_SYSTEM
from src.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, MODEL_MAP

EN = "novels/output/limitless_horror_segmented/limitless_horror_en.md"
GLOSSARY = "novels/output/limitless_horror_segmented/_glossary.json"

text = open(EN).read()

# Chapter index
parts = re.split(r'\n## Chapter (\d+)[^\n]*\n', text)
chapter_map = {}
for i in range(1, len(parts)-1, 2):
    ch_num = int(parts[i])
    body = parts[i+1] if i+1 < len(parts) else ''
    lines = body.split('\n')
    prose = '\n'.join(lines[3:]).strip()
    if len(prose) > 200:
        chapter_map[ch_num] = prose

# Character roster from glossary
glossary = json.load(open(GLOSSARY)) if os.path.exists(GLOSSARY) else {}
char_lines = []
for cn, en in glossary.items():
    if len(cn) >= 2 and len(en) >= 2:
        char_lines.append(f"  - **{en}** ({cn})")
        if len(char_lines) >= 12: break
char_roster = "## CHARACTERS (main)\n" + '\n'.join(char_lines) + "\n\n" if char_lines else ""

def build_context(ch_num):
    parts = []
    if char_roster: parts.append(char_roster)
    prev_nums = [n for n in [ch_num-3, ch_num-2, ch_num-1] if n in chapter_map]
    if prev_nums:
        parts.append("## RECENTLY\n")
        for pn in prev_nums:
            for m in re.findall(r'^## Chapter ' + str(pn) + r':(.*)$', text, re.MULTILINE):
                parts.append(f"- Ch{pn}: {m.strip()[:80]}\n")
                break
        parts.append("")
    return '\n'.join(parts)

# Pick random chapters from 3 buckets
all_chapters = sorted(c for c in chapter_map.keys() if c > 0)
early  = [c for c in all_chapters if c <= 100]
middle = [c for c in all_chapters if 200 <= c <= 400]
late   = [c for c in all_chapters if c >= 550]
random.seed(42)
samples = sorted(
    random.sample(early, 2) + random.sample(middle, 2) + random.sample(late, 2))
print(f"Sampling: {samples}")

results = []
for ch_num in samples:
    chapter_text = chapter_map[ch_num][:12000]
    context = build_context(ch_num)
    user_prompt = f"{context}## CHAPTER TO READ\n\n{chapter_text}\n\n## TASK\n"
    user_prompt += "Read this chapter as an American reader who just wants a good story. "
    user_prompt += "Report your honest experience as structured JSON."

    llm = ChatOpenAI(model=MODEL_MAP["readback"], api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL,
                     temperature=0.1, max_tokens=2048, request_timeout=120, max_retries=0)

    print(f"Ch{ch_num}...", end=" ", flush=True)
    t0 = time.monotonic()
    try:
        response = llm.invoke([SystemMessage(content=NEW_SYSTEM), HumanMessage(content=user_prompt)])
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

        verdict = result.get('verdict', '?')
        comp = len(result.get('comprehension_issues', []))
        eng = len(result.get('engagement_gaps', []))
        keep = result.get('would_keep_reading', '?')
        print(f"{elapsed:.1f}s → {verdict} comp={comp} eng={eng} keep={keep}")

        results.append({
            "chapter": ch_num, "verdict": verdict,
            "comprehension_issues": comp, "engagement_gaps": eng,
            "would_keep_reading": keep,
        })
    except Exception as e:
        print(f"ERROR: {e}")

# Summary
print(f"\n{'='*60}")
print(f"RESULTS (new prompt + character roster + recent chapters context)")
print(f"{'='*60}")
pass_count = sum(1 for r in results if r['verdict'] == 'PASS')
fix_count = sum(1 for r in results if r['verdict'] == 'NEEDS_FIX')
print(f"PASS: {pass_count}/{len(results)}  NEEDS_FIX: {fix_count}/{len(results)}")
for r in results:
    print(f"  Ch{r['chapter']}: {r['verdict']} comp={r['comprehension_issues']} eng={r['engagement_gaps']} keep={r['would_keep_reading']}")

json.dump(results, open("novels/output/limitless_horror_segmented/_reaudit_v2.json", "w"), ensure_ascii=False, indent=2)
print(f"\nSaved to _reaudit_v2.json")

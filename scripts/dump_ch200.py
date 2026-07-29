"""Re-run new READBACK on ch200 and save the full JSON result."""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from src.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, MODEL_MAP
from src.agent.prompts.readback import READBACK_SYSTEM as NEW_SYSTEM

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

messages = [SystemMessage(content=NEW_SYSTEM), HumanMessage(content=USER_PROMPT.format(content=chapter_body))]
response = llm.invoke(messages)

result_text = response.content.strip()
if result_text.startswith("```"):
    lines = result_text.split("\n")
    result_text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
try:
    result = json.loads(result_text)
except:
    m = re.search(r'\{[\s\S]*\}', result_text)
    result = json.loads(m.group()) if m else {}

# Save full JSON
with open("novels/output/ch200_new_readback.json", "w") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print("Verdict:", result.get('verdict'))
print("Keep:", result.get('would_keep_reading'))
print()

print("=== 5 理解困难 ===")
for i, ci in enumerate(result.get('comprehension_issues', [])):
    print(f"\n{i+1}. {ci.get('issue', '')}")
    print(f"   引用: {ci.get('passage', '')[:200]}")

print(f"\n=== 2 无聊段落 ===")
for i, eg in enumerate(result.get('engagement_gaps', [])):
    print(f"\n{i+1}. {eg.get('issue', '')}")
    print(f"   引用: {eg.get('passage', '')[:200]}")

print(f"\n=== 整体印象 ===")
print(result.get('overall_impression', ''))
print(f"\n角色辨识: {result.get('character_tracking', '')[:300]}")
print(f"\n世界理解: {result.get('world_comprehension', '')[:300]}")

# Also print the chapter text for cross-reference
print("\n\n=== ch200 原文 (前2000字) ===")
print(chapter_body[:2000])

"""Diagnose DeepSeek API hang: isolate whether it's Flash, long output, or the pipeline."""
import sys, os, time, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from src.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL
from src.encoding import detect_and_read
from src.chapter_splitter import split_chapters, ParagraphTag

# Load the chapter that always hangs
text, enc = detect_and_read("tests/fixtures/《无限恐怖》 作者：zhttty.txt")
chapters = split_chapters(text)
chapters = [c for c in chapters if c.action != ParagraphTag.SKIP]
ch = chapters[3]  # Ch4, 4086 CN chars — the one that hangs
print(f"Target: {ch.title}, {ch.word_count} CN chars\n")

# Test 1: Bare Flash — does Flash itself work?
print("=== 1: Bare Flash (100 tokens) ===")
t0 = time.monotonic()
try:
    llm = ChatOpenAI(model="deepseek-v4-flash", api_key=DEEPSEEK_API_KEY,
                     base_url=DEEPSEEK_BASE_URL, max_tokens=100, request_timeout=30)
    r = llm.invoke([HumanMessage(content="Say hello")])
    print(f"  OK: {r.content[:50]}, {time.monotonic()-t0:.0f}s")
except Exception as e:
    print(f"  FAIL after {time.monotonic()-t0:.0f}s: {e}")

# Test 2: Flash with 16K output — does large output cause hang?
print("=== 2: Flash 16K output ===")
t0 = time.monotonic()
try:
    llm2 = ChatOpenAI(model="deepseek-v4-flash", api_key=DEEPSEEK_API_KEY,
                      base_url=DEEPSEEK_BASE_URL, max_tokens=16384, request_timeout=30)
    r2 = llm2.invoke([HumanMessage(content="Generate exactly 2000 words of lorem ipsum style filler text. Just keep writing.")])
    print(f"  OK: {len(r2.content)} chars, {time.monotonic()-t0:.0f}s")
except Exception as e:
    print(f"  FAIL after {time.monotonic()-t0:.0f}s: {e}")

# Test 3: Flash with real chapter content (no system prompt) — does long CN input cause hang?
print("=== 3: Flash + real chapter content (no system) ===")
t0 = time.monotonic()
try:
    llm3 = ChatOpenAI(model="deepseek-v4-flash", api_key=DEEPSEEK_API_KEY,
                      base_url=DEEPSEEK_BASE_URL, max_tokens=16384, request_timeout=30)
    r3 = llm3.invoke([HumanMessage(content=f"Translate this to English:\n\n{ch.content[:3000]}")])
    print(f"  OK: {len(r3.content)} chars, {time.monotonic()-t0:.0f}s")
except Exception as e:
    print(f"  FAIL after {time.monotonic()-t0:.0f}s: {e}")

# Test 4: Flash with the FULL WRITE system prompt + full chapter — the exact pipeline call
print("=== 4: Flash + full WRITE system prompt ===")
from src.agent.prompts.write import WRITE_SYSTEM
from src.agent.prompts.translation import LANGUAGE_STYLE_NOTES
from src.agent.graph import TranslationAgent
from src.agent.nodes.read import read_node

# Build the exact same call the pipeline makes
agent = TranslationAgent(book_id="diag_test")
state = agent._make_state(ch.title, ch.content, ch.index, "", "en-US", "urban", skip_readback=True, use_flash_writer=True)
read_result = read_node(state, agent.exact_store, agent.semantic_store)
analysis = read_result.get("read_analysis", {})
from src.agent.nodes.write import _format_read_analysis, _format_image_gaps
analysis_text = _format_read_analysis(analysis)
image_text = _format_image_gaps(read_result.get("image_gaps", []))

from src.agent.prompts.write import WRITE_USER
from src.job_store import job_store

target_lang = "en-US"
regional = LANGUAGE_STYLE_NOTES.get(target_lang, "")

user_prompt = WRITE_USER.format(
    style_memo=state.get("style_memo", ""),
    reader_analysis=analysis_text,
    image_gaps=image_text,
    chapter_number=ch.index,
    chapter_title=ch.title,
    genre="urban",
    exact_matches=state.get("exact_matches_text", ""),
    semantic_matches=state.get("semantic_matches_text", ""),
    previous_summary="(first chapter)",
    confirmed_terms="(none)",
    rejected_terms="(none)",
    regional_style=regional,
    chapter_content=ch.content,
)

t0 = time.monotonic()
try:
    llm4 = ChatOpenAI(model="deepseek-v4-flash", api_key=DEEPSEEK_API_KEY,
                      base_url=DEEPSEEK_BASE_URL, max_tokens=16384, request_timeout=30)
    r4 = llm4.invoke([SystemMessage(content=WRITE_SYSTEM), HumanMessage(content=user_prompt)])
    print(f"  OK: {len(r4.content)} chars, {time.monotonic()-t0:.0f}s")
except Exception as e:
    print(f"  FAIL after {time.monotonic()-t0:.0f}s: {e}")
    traceback.print_exc()

# Test 5: Pro with the full pipeline call — does Pro also hang?
print("=== 5: Pro + full WRITE prompt ===")
t0 = time.monotonic()
try:
    llm5 = ChatOpenAI(model="deepseek-v4-pro", api_key=DEEPSEEK_API_KEY,
                      base_url=DEEPSEEK_BASE_URL, max_tokens=16384, request_timeout=30)
    r5 = llm5.invoke([SystemMessage(content=WRITE_SYSTEM), HumanMessage(content=user_prompt)])
    print(f"  OK: {len(r5.content)} chars, {time.monotonic()-t0:.0f}s")
except Exception as e:
    print(f"  FAIL after {time.monotonic()-t0:.0f}s: {e}")

# Test 6: Check prompt token count
print("\n=== 6: Prompt sizes ===")
print(f"  System prompt: {len(WRITE_SYSTEM)} chars (~{len(WRITE_SYSTEM)//3} tokens)")
print(f"  User prompt: {len(user_prompt)} chars (~{len(user_prompt)//3} tokens)")
print(f"  Total: ~{(len(WRITE_SYSTEM) + len(user_prompt))//3} tokens")
print(f"  Chapter content: {len(ch.content)} chars")

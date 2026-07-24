"""A/B test: Flash vs Pro on the same chapter with READ analysis and glossary.

Translates the same chapter twice — once with Flash, once with Pro — both
using the same READ analysis context (Pro). Compares output length, cold
reader verdict, and saves both for manual comparison.
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from src.agent.graph import TranslationAgent
from src.agent.nodes.read import read_node, _build_context_signals
from src.agent.nodes.write import _format_read_analysis, _parse_write_response
from src.agent.prompts.write import WRITE_SYSTEM, WRITE_USER
from src.agent.prompts.translation import LANGUAGE_STYLE_NOTES
from src.chapter_splitter import split_chapters, ParagraphTag
from src.encoding import detect_and_read
from src.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, MODEL_MAP
from src.circuit_breaker import get_breaker
from src.stats import TranslationStats
from src.job_store import job_store
from src.cultural_rules import load_rules, format_rules_as_bullets
from src.output_guard import check_and_record, sanitize_translation


text, enc = detect_and_read("tests/fixtures/《无限恐怖》 作者：zhttty.txt")
chapters = split_chapters(text)
chapters = [c for c in chapters if c.action != ParagraphTag.SKIP]

# Pick a medium-length chapter for fair comparison
ch = chapters[3]  # Ch4, 4086 CN chars
print(f"Testing: {ch.title} ({ch.word_count} CN chars)\n")

# ── Build the READ context once (always Pro) ──
agent = TranslationAgent(book_id="ab_test")
state = agent._make_state(
    ch.title, ch.content, ch.index, "(first chapter)", "en-US", "urban", skip_readback=True,
)
read_result = read_node(state, agent.exact_store, agent.semantic_store)
analysis = read_result.get("read_analysis", {})
analysis_text = _format_read_analysis(analysis)
print(f"READ analysis: {len(analysis_text)} chars, {len(analysis.get('image_gaps',[]))} image gaps\n")

# ── Build WRITE prompt ──
image_gaps_text = ""
from src.agent.nodes.write import _format_image_gaps
image_gaps_text = _format_image_gaps(read_result.get("image_gaps", []))

target_lang = "en-US"
regional = LANGUAGE_STYLE_NOTES.get(target_lang, "")

user_prompt = WRITE_USER.format(
    style_memo="(no memo yet)",
    reader_analysis=analysis_text,
    image_gaps=image_gaps_text,
    chapter_number=ch.index,
    chapter_title=ch.title,
    genre="urban",
    exact_matches="(no glossary terms yet)",
    semantic_matches="(no semantic matches)",
    previous_summary="(first chapter)",
    confirmed_terms="(none)",
    rejected_terms="(none)",
    regional_style=regional,
    chapter_content=ch.content,
)

# ═══════════════════════════════════════════════════════════════
# FLASH run
# ═══════════════════════════════════════════════════════════════
flash_llm = ChatOpenAI(
    model="deepseek-v4-flash",
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    temperature=0.3,
    max_tokens=16384,
)

print("=" * 60)
print("FLASH")
print("=" * 60)
t0 = time.monotonic()
breaker = get_breaker(target_lang)
flash_resp = breaker.call(flash_llm.invoke, [
    SystemMessage(content=WRITE_SYSTEM),
    HumanMessage(content=user_prompt),
])
flash_elapsed = time.monotonic() - t0
flash_parsed = _parse_write_response(flash_resp.content)
flash_text = sanitize_translation(flash_parsed.get("translated_text", ""))
print(f"Time: {flash_elapsed:.0f}s, Output: {len(flash_text)} chars")
print(f"Preview: {flash_text[:250].replace(chr(10),' ')}...")

# ═══════════════════════════════════════════════════════════════
# PRO run
# ═══════════════════════════════════════════════════════════════
pro_llm = ChatOpenAI(
    model=MODEL_MAP["translate"],
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    temperature=0.3,
    max_tokens=16384,
)

print("\n" + "=" * 60)
print("PRO")
print("=" * 60)
t0 = time.monotonic()
pro_resp = breaker.call(pro_llm.invoke, [
    SystemMessage(content=WRITE_SYSTEM),
    HumanMessage(content=user_prompt),
])
pro_elapsed = time.monotonic() - t0
pro_parsed = _parse_write_response(pro_resp.content)
pro_text = sanitize_translation(pro_parsed.get("translated_text", ""))
print(f"Time: {pro_elapsed:.0f}s, Output: {len(pro_text)} chars")
print(f"Preview: {pro_text[:250].replace(chr(10),' ')}...")

# ═══════════════════════════════════════════════════════════════
# Save both for comparison
# ═══════════════════════════════════════════════════════════════
os.makedirs("novels/output", exist_ok=True)
with open("novels/output/ab_flash.md", "w") as f:
    f.write(f"# Flash Output ({flash_elapsed:.0f}s, {len(flash_text)} chars)\n\n{flash_text}")
with open("novels/output/ab_pro.md", "w") as f:
    f.write(f"# Pro Output ({pro_elapsed:.0f}s, {len(pro_text)} chars)\n\n{pro_text}")

print(f"\n=== Summary ===")
print(f"Flash: {flash_elapsed:.0f}s, {len(flash_text)} chars")
print(f"Pro:   {pro_elapsed:.0f}s, {len(pro_text)} chars")
speedup = flash_elapsed / max(pro_elapsed, 1) if pro_elapsed else 1
print(f"Speed: Flash is {speedup:.1f}x faster than Pro")

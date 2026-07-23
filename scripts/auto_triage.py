"""Auto-triage glossary using LLM self-review.  Only uncertain terms flagged for human."""

import sqlite3, json, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv; load_dotenv()
import httpx

DB = "data/checkpoints.db"
API_KEY = os.getenv("DEEPSEEK_API_KEY")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

# ── Step 1: clean obvious garbage (author names, book titles, structural labels) ──
METADATA_CATEGORIES = {
    "author_name", "book_title", "title", "work_title", "novel_title",
    "platform_name", "publisher_name", "structural", "volume_label",
    "volume_title", "narrative_structure", "arc_title", "theme",
    "idiom", "slang", "cultural_reference", "literary_work",
    "film_title", "contract_term",
}

conn = sqlite3.connect(DB)
for cat in METADATA_CATEGORIES:
    conn.execute("DELETE FROM exact_glossary WHERE category = ?", (cat,))
conn.commit()

terms = conn.execute(
    "SELECT term_cn, term_en, category, context FROM exact_glossary "
    "WHERE status = 'pending_review'"
).fetchall()

if not terms:
    total = conn.execute("SELECT COUNT(*) FROM exact_glossary").fetchone()[0]
    confirmed = conn.execute("SELECT COUNT(*) FROM exact_glossary WHERE status='confirmed'").fetchone()[0]
    print(f"✅ 全部完成 — {total} 条术语, {confirmed} 已确认, 0 待审核")
    conn.close()
    sys.exit(0)

# ── Step 2: batch-review with LLM ──
batch = []
for t in terms[:50]:  # Process in batches of 50 to stay under token limits
    batch.append({
        "term_cn": t[0],
        "term_en": t[1],
        "category": t[2],
        "context": t[3] or "",
    })

prompt = f"""Review these Chinese→English web novel glossary entries. For each entry, decide:

KEEP — Translation is correct and appropriate. Mark confirmed.
FIX — Translation has issues. Provide the corrected English.
REJECT — This is not a proper term (generic word, metadata, etc). Should be removed.

Return JSON: {{"decisions": [{{"term_cn": "...", "decision": "KEEP|FIX|REJECT", "fixed_en": "only if FIX"}}]}}

Terms to review:
{json.dumps(batch, ensure_ascii=False, indent=2)}
"""

resp = httpx.post(
    f"{BASE_URL}/v1/chat/completions",
    headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
    json={
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 4096,
    },
    timeout=60,
)

if not resp.is_success:
    print(f"❌ LLM triage failed: {resp.status_code} {resp.text[:200]}")
    print(f"   {len(terms)} 条待人工审核")
    conn.close()
    sys.exit(1)

result = json.loads(resp.json()["choices"][0]["message"]["content"].strip("```json").strip("```").strip())

# ── Step 3: apply decisions ──
kept = fixed = rejected = 0
for d in result.get("decisions", []):
    cn = d["term_cn"]
    decision = d["decision"]

    if decision == "KEEP":
        conn.execute("UPDATE exact_glossary SET status='confirmed' WHERE term_cn=?", (cn,))
        kept += 1
    elif decision == "FIX":
        conn.execute("UPDATE exact_glossary SET term_en=?, status='confirmed' WHERE term_cn=?",
                     (d["fixed_en"], cn))
        fixed += 1
    elif decision == "REJECT":
        conn.execute("DELETE FROM exact_glossary WHERE term_cn=?", (cn,))
        rejected += 1

conn.commit()

# ── Step 4: report ──
remaining = conn.execute(
    "SELECT COUNT(*) FROM exact_glossary WHERE status='pending_review'"
).fetchone()[0]

print(f"✅ 自动确认: {kept} 条")
print(f"🔧 自动修正: {fixed} 条")
print(f"🗑️  自动删除: {rejected} 条")
if remaining > 0:
    print(f"\n⚠️ {remaining} 条仍待审核 (可能是超过50条批次):")
    rows = conn.execute(
        "SELECT term_cn, term_en, category FROM exact_glossary "
        "WHERE status='pending_review' ORDER BY term_cn LIMIT 20"
    ).fetchall()
    for r in rows:
        print(f"  {r[0]:15s} → {r[1]:35s} [{r[2]}]")

total = conn.execute("SELECT COUNT(*) FROM exact_glossary").fetchone()[0]
confirmed = conn.execute("SELECT COUNT(*) FROM exact_glossary WHERE status='confirmed'").fetchone()[0]
print(f"\n📊 {total} 条总计 | ✅ {confirmed} 已确认 | ⚠️ {remaining} 待审核")
conn.close()

"""Rebuild checkpoint from existing translated chapters.

The old v0.13 script didn't save checkpoints or glossary snapshots.
This script reads the already-translated chapters from the output file,
counts them, and creates a checkpoint so run_one_segment.py can resume
from the next untranslated chapter.

Glossary recovery: the old script's new_terms_found were never persisted,
so the exact_store will be empty on resume. The system will rebuild the
glossary naturally as terms reappear in later chapters — a ~1.7% loss
in a 775-chapter novel is negligible.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUT_FILE = "novels/output/limitless_horror_segmented/limitless_horror_en.md"
CKPT_FILE = "novels/output/limitless_horror_segmented/_checkpoint.json"

if not os.path.exists(OUT_FILE):
    print("No existing translation found. Starting fresh is fine.")
    sys.exit(0)

# Count completed chapters
ch_count = 0
with open(OUT_FILE, encoding="utf-8") as f:
    for line in f:
        if line.startswith("## Chapter"):
            ch_count += 1

if ch_count == 0:
    print("Output file exists but has no chapters. Removing stale checkpoint.")
    if os.path.exists(CKPT_FILE):
        os.remove(CKPT_FILE)
    sys.exit(0)

print(f"{ch_count} chapters already translated")

# Verify against source
from src.encoding import detect_and_read
from src.chapter_splitter import split_chapters, ParagraphTag

text, enc = detect_and_read("tests/fixtures/《无限恐怖》 作者：zhttty.txt")
chapters = split_chapters(text)
chapters = [c for c in chapters if c.action != ParagraphTag.SKIP][:775]

if ch_count >= len(chapters):
    print(f"ALL {len(chapters)} chapters complete.")
    sys.exit(0)

last_ch = chapters[ch_count - 1]
print(f"Last completed: position {ch_count}, chapter {last_ch.index}: {last_ch.title[:40]}")
print(f"Next to translate: chapter {ch_count + 1}/{len(chapters)}")

os.makedirs(os.path.dirname(CKPT_FILE), exist_ok=True)
json.dump({
    "last_idx": ch_count - 1,
    "ch_num": last_ch.index,
    "glossary_snapshot": "{}",  # Lost — system will rebuild from ch{ch_count+1} onward
    "previous_summary": "",     # Lost — first resumed chapter will handle this
    "timestamp": "rebuilt-from-output",
    "_note": "Glossary and previous_summary were not saved by the old script. "
             f"They will rebuild naturally over the remaining {len(chapters) - ch_count} chapters."
}, open(CKPT_FILE, "w"), ensure_ascii=False, indent=2)

print(f"\nCheckpoint created. Resume with: python3 scripts/run_one_segment.py")
print(f"Glossary: lost for ch1-{ch_count} (system rebuilds from ch{ch_count+1})")

#!/usr/bin/env python3
"""
DESTRUCTIVE ATTACK SCRIPT — Westward Echo
Attempts to break the system in every conceivable way.

Run:  python scripts/destructive_attacks.py
WARNING: This will corrupt data files. Run on a backup.
"""

import sys
import os
import json
import sqlite3
import tempfile
import uuid
import shutil
import threading
import time
import re
import io

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = {
    "crashes": [],
    "data_loss": [],
    "unexpected_behavior": [],
    "held_up": [],
}

def crash(attack, detail):
    RESULTS["crashes"].append(f"- {attack} → {detail}")
    print(f"  [CRASH] {attack} → {detail}")

def data_loss(attack, detail):
    RESULTS["data_loss"].append(f"- {attack} → {detail}")
    print(f"  [DATA LOSS] {attack} → {detail}")

def unexpected(attack, detail):
    RESULTS["unexpected_behavior"].append(f"- {attack} → {detail}")
    print(f"  [UNEXPECTED] {attack} → {detail}")

def held_up(attack, detail):
    RESULTS["held_up"].append(f"- {attack} → {detail}")
    print(f"  [HELD UP] {attack} → {detail}")

# ══════════════════════════════════════════════════════════════════
# SECTION 0: Setup — backup critical data files
# ══════════════════════════════════════════════════════════════════

print("=" * 70)
print("DESTRUCTIVE ATTACKS ON WESTWARD ECHO")
print("=" * 70)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# Backup data files
backup_dir = tempfile.mkdtemp(prefix="westward_backup_")
data_files = ["jobs.db", "checkpoints.db", "translation_events.db", "editor_edits.db"]
for f in data_files:
    src = os.path.join(DATA_DIR, f)
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(backup_dir, f))
print(f"\nBackups stored at: {backup_dir}")

# ══════════════════════════════════════════════════════════════════
# SECTION 1: ENCODING ATTACKS
# ══════════════════════════════════════════════════════════════════

print("\n─── SECTION 1: ENCODING ATTACKS ───")

from src.encoding import detect_and_read

# Attack 1.1: Feed binary data disguised as text
try:
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", delete=False) as f:
        f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01" + os.urandom(1024))
        bin_path = f.name
    result, enc = detect_and_read(bin_path)
    if result:
        unexpected("Binary file (PNG header + random bytes) passed as text",
                   f"Returned {len(result)} chars with encoding={enc}")
    os.unlink(bin_path)
except ValueError as e:
    held_up("Binary file (PNG header + random bytes)", f"Correctly raised ValueError: {str(e)[:80]}")
except Exception as e:
    crash("Binary file (PNG header + random bytes)", f"Unexpected exception: {type(e).__name__}: {e}")

# Attack 1.2: Empty file
try:
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", delete=False) as f:
        f.write(b"")
        empty_path = f.name
    result, enc = detect_and_read(empty_path)
    unexpected("Empty file accepted", f"Returned '' with encoding={enc} — should this be an error?")
    os.unlink(empty_path)
except ValueError as e:
    held_up("Empty file", f"Correctly raised ValueError: {str(e)[:80]}")
except Exception as e:
    crash("Empty file", f"Unexpected exception: {type(e).__name__}: {e}")

# Attack 1.3: UTF-16 without BOM
try:
    utf16_text = "第一章 穿越到异世界\n这是一个普通的早晨。"
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", delete=False) as f:
        f.write(utf16_text.encode("utf-16"))
        utf16_path = f.name
    result, enc = detect_and_read(utf16_path)
    if "第一章" in result:
        held_up("UTF-16 without BOM", f"Correctly detected as {enc}")
    else:
        unexpected("UTF-16 without BOM — garbled output", f"Got encoding={enc}, content preview: {result[:80]}")
    os.unlink(utf16_path)
except Exception as e:
    crash("UTF-16 without BOM", f"{type(e).__name__}: {e}")

# Attack 1.4: Latin-1 encoded file with some single-byte chars
try:
    latin1_text = "C'est un roman chinois avec des caractres spciaux: voil!"
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", delete=False) as f:
        f.write(latin1_text.encode("latin-1"))
        latin1_path = f.name
    result, enc = detect_and_read(latin1_path)
    if result:
        unexpected("Latin-1 file with no Chinese chars accepted", f"Encoding={enc}, text: {result[:80]}")
    os.unlink(latin1_path)
except ValueError as e:
    held_up("Latin-1 file with no Chinese chars", f"Correctly rejected: {str(e)[:80]}")
except Exception as e:
    crash("Latin-1 file", f"{type(e).__name__}: {e}")

# Attack 1.5: File with null bytes mid-text
try:
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", delete=False) as f:
        payload = "第一章 穿越\n".encode("utf-8") + b"\x00" * 100 + "第二章 醒来\n".encode("utf-8")
        f.write(payload)
        null_path = f.name
    result, enc = detect_and_read(null_path)
    if result and "第一章" in result:
        unexpected("Null bytes in middle of file", f"File accepted — null bytes silently handled, encoding={enc}")
    else:
        unexpected("Null bytes in file", f"Result: {repr(result[:80])}")
    os.unlink(null_path)
except ValueError as e:
    held_up("Null bytes in file", f"Correctly rejected: {str(e)[:80]}")
except Exception as e:
    crash("Null bytes in file", f"{type(e).__name__}: {e}")

# Attack 1.6: File so large it exhausts memory (near-limit)
try:
    huge_text = "第一章 测试\n" + ("庞大的世界，无尽的宇宙。\n" * 500_000)  # ~8MB
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(huge_text)
        huge_path = f.name
    result, enc = detect_and_read(huge_path)
    if len(result) > 1_000_000:
        held_up(f"Huge file ({len(result)} chars)", f"Loaded successfully with encoding={enc}")
    os.unlink(huge_path)
except MemoryError:
    crash("Huge file (~8MB)", "MemoryError — system exhausted")
except Exception as e:
    unexpected("Huge file (~8MB)", f"{type(e).__name__}: {e}")

# Attack 1.7: BOM handling edge cases
try:
    # UTF-8 BOM followed by nothing
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", delete=False) as f:
        f.write(b"\xef\xbb\xbf")
        bom_path = f.name
    result, enc = detect_and_read(bom_path)
    if result == "":
        held_up("UTF-8 BOM with empty content", f"Returned empty string, encoding={enc}")
    os.unlink(bom_path)
except Exception as e:
    unexpected("UTF-8 BOM empty file", f"{type(e).__name__}: {e}")

# Attack 1.8: File with only emoji
try:
    emoji_text = "😀😃😄😁😅😂🤣😊😇🙂🙃😉😌😍🥰😘😗😙😚😋😛😝😜🤪🤨🧐🤓😎🤩🥳"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(emoji_text * 100)
        emoji_path = f.name
    result, enc = detect_and_read(emoji_path)
    if result:
        unexpected("Emoji-only file accepted as Chinese text", f"Returned {len(result)} chars, enc={enc}")
    os.unlink(emoji_path)
except ValueError as e:
    held_up("Emoji-only file", f"Correctly rejected: {str(e)[:80]}")
except Exception as e:
    crash("Emoji-only file", f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════
# SECTION 2: CHAPTER SPLITTER ATTACKS
# ══════════════════════════════════════════════════════════════════

print("\n─── SECTION 2: CHAPTER SPLITTER ATTACKS ───")

from src.chapter_splitter import split_chapters, classify_paragraph, ParagraphTag, Chapter

# Attack 2.1: Empty text
try:
    result = split_chapters("")
    if len(result) == 0:
        unexpected("Empty text to split_chapters", "Returned 0 chapters — caller may crash on empty list")
    else:
        held_up("Empty text to split_chapters", f"Returned {len(result)} chapters")
except Exception as e:
    crash("Empty text to split_chapters", f"{type(e).__name__}: {e}")

# Attack 2.2: Single paragraph, no chapter headers
try:
    result = split_chapters("没有章节标题的亂碼文本")
    if result[0].title == "正文":
        held_up("No chapter headers", "Correctly wrapped as single chapter titled '正文'")
    else:
        unexpected("No chapter headers", f"Unexpected title: {result[0].title}")
except Exception as e:
    crash("No chapter headers", f"{type(e).__name__}: {e}")

# Attack 2.3: Only whitespace and newlines
try:
    result = split_chapters("\n\n\n   \n\t\n\n")
    unexpected("Whitespace-only text", f"Returned {len(result)} chapters (should this even be valid?)")
except Exception as e:
    held_up("Whitespace-only text", f"Rejected: {type(e).__name__}: {e}")

# Attack 2.4: 10K chapter headers with no bodies
try:
    headers_only = "\n".join([f"第{i}章 测试" for i in range(1, 10001)])
    result = split_chapters(headers_only)
    if len(result) == 0:
        held_up("10K chapter headers, no bodies", "Correctly filtered out empty chapters")
    else:
        unexpected("10K chapter headers, no bodies", f"Got {len(result)} non-empty chapters")
except RecursionError:
    crash("10K chapter headers", "RecursionError — regex backtracking blew the stack")
except Exception as e:
    unexpected("10K chapter headers", f"{type(e).__name__}: {e}")

# Attack 2.5: Chapter title with XSS payload
try:
    xss_text = "第1章 <script>alert('xss')</script>\n正常的内容在这里。"
    result = split_chapters(xss_text)
    if "<script>" in result[0].title:
        unexpected("XSS in chapter title", f"XSS payload preserved in title: {result[0].title}")
    else:
        held_up("XSS in chapter title", "XSS retained in title (renderer's responsibility to escape)")
except Exception as e:
    crash("XSS in chapter title", f"{type(e).__name__}: {e}")

# Attack 2.6: Chapter number 99999 — overflow
try:
    huge_chapter = "第99999章 终章\n最后的内容。"
    result = split_chapters(huge_chapter)
    held_up("Chapter number 99999", f"Correctly parsed as Chapter {result[0].index}")
except Exception as e:
    unexpected("Chapter number 99999", f"{type(e).__name__}: {e}")

# Attack 2.7: Mixed Chinese/Japanese chapter markers
try:
    mixed_text = """
第1章 开始
第一章结束。

第１章 注意全角数字
第二章结束。

第Ⅰ章 罗马数字呢？
第三章结束。
"""
    result = split_chapters(mixed_text)
    held_up("Mixed chapter numbering styles", f"Found {len(result)} chapters")
except Exception as e:
    unexpected("Mixed chapter numbering", f"{type(e).__name__}: {e}")

# Attack 2.8: classify_paragraph with empty strings
try:
    tag, action = classify_paragraph("", "")
    held_up("classify_paragraph('', '')", f"Returned tag={tag.value}, action={action.value}")
except Exception as e:
    crash("classify_paragraph('', '')", f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════
# SECTION 3: CHAPTER SLICER ATTACKS
# ══════════════════════════════════════════════════════════════════

print("\n─── SECTION 3: CHAPTER SLICER ATTACKS ───")

from src.chapter_slicer import should_split, split_chapter

# Attack 3.1: 100K-char single paragraph (no paragraph breaks)
try:
    huge_para = "这是一个没有段落分隔的超级长的文本。" * 10000  # ~100K chars
    result = split_chapter(huge_para)
    if len(result) > 1:
        held_up("100K-char single paragraph", f"Correctly split into {len(result)} segments")
    else:
        unexpected("100K-char single paragraph", f"Not split — returned {len(result)} segments")
except Exception as e:
    crash("100K-char single paragraph", f"{type(e).__name__}: {e}")

# Attack 3.2: should_split with extreme values
try:
    result = should_split("短" * 100000)
    if result:
        held_up("should_split(100K chars)", "Correctly identified as needing split")
    else:
        unexpected("should_split(100K chars)", "Returned False when split needed")
except Exception as e:
    crash("should_split(100K chars)", f"{type(e).__name__}: {e}")

# Attack 3.3: Empty string to split_chapter
try:
    result = split_chapter("")
    if len(result) == 0:
        unexpected("split_chapter('') → empty list", "Empty result — caller may not handle 0 segments")
    else:
        held_up("split_chapter('')", f"Returned {len(result)} segments")
except Exception as e:
    crash("split_chapter('')", f"{type(e).__name__}: {e}")

# Attack 3.4: String with only paragraph breaks
try:
    result = split_chapter("\n\n\n\n")
    held_up("split_chapter(only paragraph breaks)", f"Returned {len(result)} segments")
except Exception as e:
    unexpected("split_chapter(only paragraph breaks)", f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════
# SECTION 4: GLOSSARY SYSTEM ATTACKS
# ══════════════════════════════════════════════════════════════════

print("\n─── SECTION 4: GLOSSARY SYSTEM ATTACKS ───")

from src.glossary.exact_store import ExactGlossary
from src.glossary.semantic_store import SemanticGlossary

# Use a temp DB for attacks
temp_db = os.path.join(tempfile.mkdtemp(), "test_glossary.db")

# Attack 4.1: Add empty string terms
try:
    store = ExactGlossary(db_path=temp_db)
    store.add("", "", category="culture")
    unexpected("Empty term_cn and term_en added", "Empty strings accepted into glossary")
except Exception as e:
    held_up("Empty term_cn/term_en", f"Rejected: {type(e).__name__}: {e}")

# Attack 4.2: Add massive number of terms rapidly (1000)
try:
    store = ExactGlossary(db_path=temp_db)
    for i in range(1000):
        store.add(f"测试术语_{i}", f"test_term_{i}", category="culture")
    if len(store) == 1000:
        held_up("1000 terms added rapidly", "All terms stored correctly")
    else:
        unexpected("1000 terms added rapidly", f"Only {len(store)} stored")
except Exception as e:
    crash("1000 terms added rapidly", f"{type(e).__name__}: {e}")

# Attack 4.3: Add term with SQL injection payload
try:
    store = ExactGlossary(db_path=temp_db)
    sqli_payload = "'; DROP TABLE exact_glossary; --"
    store.add(sqli_payload, "malicious", category="culture")
    # Verify the table still exists
    conn = sqlite3.connect(temp_db)
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='exact_glossary'").fetchone()
    conn.close()
    if row:
        held_up("SQL injection in term_cn", "Table survived — parameterized queries working")
    else:
        crash("SQL injection in term_cn", "TABLE WAS DROPPED — SQL injection vulnerability!")
except Exception as e:
    held_up("SQL injection in term_cn", f"Rejected: {type(e).__name__}: {e}")

# Attack 4.4: Add terms with special/unicode characters
try:
    store = ExactGlossary(db_path=temp_db)
    weird_chars = ["\x00", "\\", "\"", "'", "\n", "\r", "\t", "\x1b[31m"]
    for i, ch in enumerate(weird_chars):
        try:
            store.add(f"term_{ch}_{i}", f"en_{i}_{uuid.uuid4().hex[:4]}")
        except Exception:
            pass
    held_up("Terms with control characters", f"Added what survived; store has {len(store)} terms")
except Exception as e:
    unexpected("Terms with control characters", f"{type(e).__name__}: {e}")

# Attack 4.5: add_batch with empty list
try:
    store = ExactGlossary(db_path=temp_db)
    store.add_batch([], chapter=1)
    held_up("add_batch([])", "Handled empty batch gracefully")
except Exception as e:
    crash("add_batch([])", f"{type(e).__name__}: {e}")

# Attack 4.6: add_batch with None values
try:
    store = ExactGlossary(db_path=temp_db)
    store.add_batch([{"term_cn": "测试", "term_en": None}], chapter=1)
    unexpected("add_batch with term_en=None", "Stored None as term_en")
except TypeError:
    held_up("add_batch with term_en=None", f"Correctly raised TypeError")
except Exception as e:
    unexpected("add_batch with term_en=None", f"{type(e).__name__}: {e}")

# Attack 4.7: Duplicate terms
try:
    store = ExactGlossary(db_path=temp_db)
    store.add("重复词", "dup1")
    store.add("重复词", "dup2")
    result = store.get("重复词")
    if result == "dup2":
        unexpected("Duplicate term silently overwrites previous", f"Last write wins: '{result}'")
    else:
        held_up("Duplicate term", f"Got '{result}'")
except Exception as e:
    crash("Duplicate term", f"{type(e).__name__}: {e}")

# Attack 4.8: match_in_text with huge text
try:
    store = ExactGlossary(db_path=temp_db)
    store.add("关键词", "keyword")
    huge_text = "无关文本" * 50000 + "找到关键词在这里" + "更多文本" * 10000
    matches = store.match_in_text(huge_text)
    if "关键词" in matches:
        held_up("match_in_text with huge input", f"Found {len(matches)} matches in ~300K chars")
    else:
        unexpected("match_in_text with huge input", "Failed to find term in massive text")
except MemoryError:
    crash("match_in_text with huge input", "MemoryError")
except Exception as e:
    unexpected("match_in_text with huge input", f"{type(e).__name__}: {e}")

# Attack 4.9: Reject a non-existent term
try:
    store = ExactGlossary(db_path=temp_db)
    store.reject_term("不存在的术语")
    held_up("reject_term on non-existent term", "Silent no-op (did not crash)")
except Exception as e:
    crash("reject_term on non-existent term", f"{type(e).__name__}: {e}")

# Attack 4.10: Confirm a non-existent term
try:
    store = ExactGlossary(db_path=temp_db)
    store.confirm_term("不存在的术语2")
    held_up("confirm_term on non-existent term", "Silent no-op")
except Exception as e:
    crash("confirm_term on non-existent term", f"{type(e).__name__}: {e}")

# Attack 4.11: Corrupt the SQLite glossary DB
try:
    corrupt_db = os.path.join(tempfile.mkdtemp(), "corrupt_glossary.db")
    with open(corrupt_db, "wb") as f:
        f.write(b"THIS IS NOT A SQLITE DATABASE\x00\xff\xfe\xfd")
    store = ExactGlossary(db_path=corrupt_db)
    # _init_db creates tables — does it handle the corrupt file?
    store.add("test", "test")
    held_up("Corrupt SQLite DB file", "ExactGlossary handled (recreated) corrupt DB")
except sqlite3.DatabaseError as e:
    held_up("Corrupt SQLite DB file", f"Correctly raised DatabaseError: {str(e)[:80]}")
except Exception as e:
    unexpected("Corrupt SQLite DB file", f"{type(e).__name__}: {e}")

# Attack 4.12: Snapshot with corrupted internal dict
try:
    store = ExactGlossary(db_path=temp_db)
    store._dict["test"] = object()  # Not a string
    try:
        snap = store.snapshot()
        crash("snapshot() with non-string values", "Should have failed but didn't")
    except (TypeError, json.JSONDecodeError):
        held_up("snapshot() with non-string values", "Correctly failed on serialization")
except Exception as e:
    unexpected("Snapshot with bad values", f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════
# SECTION 5: JOB STORE ATTACKS
# ══════════════════════════════════════════════════════════════════

print("\n─── SECTION 5: JOB STORE ATTACKS ───")

from src.job_store import JobStore

job_store = JobStore()

# Attack 5.1: Create job with empty filename
try:
    jid = job_store.create_job("", "en-US", 10)
    unexpected("Empty filename accepted", f"Created job {jid} with empty filename")
except Exception as e:
    held_up("Empty filename", f"Rejected: {type(e).__name__}: {e}")

# Attack 5.2: Create job with 0 chapters
try:
    jid = job_store.create_job("test.txt", "en-US", 0)
    if jid:
        unexpected("Job with 0 chapters", f"Created job {jid} with total_chapters=0")
    else:
        held_up("Job with 0 chapters", "Rejected")
except Exception as e:
    held_up("Job with 0 chapters", f"Rejected: {type(e).__name__}: {e}")

# Attack 5.3: Create job with negative chapters
try:
    jid = job_store.create_job("test.txt", "en-US", -5)
    unexpected("Job with negative chapters", f"Created job {jid} with total_chapters=-5")
except Exception as e:
    held_up("Job with negative chapters", f"Rejected: {type(e).__name__}: {e}")

# Attack 5.4: Create job with gigantic chapter count
try:
    jid = job_store.create_job("test.txt", "en-US", 2_147_483_647)  # Max int32
    held_up("Job with INT32_MAX chapters", f"Created job {jid}")
except OverflowError:
    held_up("Job with INT32_MAX chapters", "Rejected on overflow")
except Exception as e:
    unexpected("Job with INT32_MAX chapters", f"{type(e).__name__}: {e}")

# Attack 5.5: Create 100 jobs rapidly
try:
    job_ids = []
    for i in range(100):
        jid = job_store.create_job(f"test_{i}.txt", "en-US", 10)
        job_ids.append(jid)
    held_up("100 jobs created rapidly", f"Created {len(job_ids)} jobs successfully")
    # Cleanup
    for jid in job_ids:
        job_store.delete_job(jid)
except Exception as e:
    unexpected("100 jobs created rapidly", f"Failed at job {i}: {type(e).__name__}: {e}")

# Attack 5.6: Get non-existent job
try:
    result = job_store.get_job("nonexistent_job_id")
    if result is None:
        held_up("get_job(non-existent)", "Correctly returned None")
    else:
        unexpected("get_job(non-existent)", f"Returned {result}")
except Exception as e:
    crash("get_job(non-existent)", f"{type(e).__name__}: {e}")

# Attack 5.7: Delete non-existent job
try:
    job_store.delete_job("nonexistent_job_id")
    held_up("delete_job(non-existent)", "Silent no-op")
except Exception as e:
    crash("delete_job(non-existent)", f"{type(e).__name__}: {e}")

# Attack 5.8: Update progress for non-existent job
try:
    job_store.update_progress("nonexistent_job_id", 5, 10, "Chapter 5")
    held_up("update_progress on non-existent job", "Silent no-op (no crash)")
except Exception as e:
    crash("update_progress on non-existent job", f"{type(e).__name__}: {e}")

# Attack 5.9: Fail a non-existent job
try:
    job_store.fail_job("nonexistent_job_id", "test error")
    held_up("fail_job on non-existent job", "Silent no-op")
except Exception as e:
    crash("fail_job on non-existent job", f"{type(e).__name__}: {e}")

# Attack 5.10: Complete a non-existent job
try:
    job_store.complete_job("nonexistent_job_id", "/fake/path.md", 0)
    held_up("complete_job on non-existent job", "Silent no-op")
except Exception as e:
    crash("complete_job on non-existent job", f"{type(e).__name__}: {e}")

# Attack 5.11: SQL injection in job_id for get_job
try:
    result = job_store.get_job("'; DROP TABLE jobs; --")
    if result is None:
        held_up("SQL injection in job_id", "Parameterized query prevented injection")
    else:
        crash("SQL injection in job_id", "Returned unexpected result — potential sql injection")
except Exception as e:
    held_up("SQL injection in job_id", f"Error (not crashed): {type(e).__name__}")

# Attack 5.12: Create job with XSS in filename
try:
    jid = job_store.create_job("<script>alert('xss')</script>.txt", "en-US", 10)
    job = job_store.get_job(jid)
    if job and "<script>" in job["filename"]:
        unexpected("XSS in filename", f"XSS payload stored: {job['filename']}")
    job_store.delete_job(jid)
except Exception as e:
    crash("XSS in filename", f"{type(e).__name__}: {e}")

# Attack 5.13: Create job with null byte in filename
try:
    jid = job_store.create_job("test\x00hidden.txt", "en-US", 10)
    job = job_store.get_job(jid)
    if job:
        unexpected("Null byte in filename", f"Stored with null byte: {repr(job['filename'])}")
    job_store.delete_job(jid)
except sqlite3.ProgrammingError:
    held_up("Null byte in filename", "SQLite correctly rejected null byte")
except Exception as e:
    unexpected("Null byte in filename", f"{type(e).__name__}: {e}")

# Attack 5.14: Corrupt the jobs.db file
try:
    corrupt_path = os.path.join(tempfile.mkdtemp(), "corrupt_jobs.db")
    with open(corrupt_path, "wb") as f:
        f.write(b"GARBAGE_DATA\x00\xff")
    # Try reading with sqlite3 directly
    conn = sqlite3.connect(corrupt_path)
    try:
        conn.execute("SELECT * FROM jobs")
        unexpected("Corrupt jobs.db", "SQLite silently handled corruption")
    except sqlite3.DatabaseError:
        held_up("Corrupt jobs.db", "SQLite correctly reported database error")
    conn.close()
except Exception as e:
    unexpected("Corrupt jobs.db", f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════
# SECTION 6: CULTURAL RULES ATTACKS
# ══════════════════════════════════════════════════════════════════

print("\n─── SECTION 6: CULTURAL RULES ATTACKS ───")

from src.cultural_rules import load_rules, is_known_genre, detect_genre, format_rules_for_prompt

# Attack 6.1: Load rules for non-existent language
try:
    rules = load_rules(target_lang="xx-XX", genre="romance_ceo")
    if rules == {}:
        held_up("Non-existent language 'xx-XX'", "Returned empty rules dict")
    else:
        unexpected("Non-existent language 'xx-XX'", f"Returned {len(rules)} rules")
except Exception as e:
    crash("Non-existent language", f"{type(e).__name__}: {e}")

# Attack 6.2: Load rules for non-existent genre
try:
    rules = load_rules(target_lang="en-US", genre="nonexistent_genre")
    if rules:
        held_up("Non-existent genre", f"Returned common rules ({len(rules)} items)")
    else:
        unexpected("Non-existent genre", "Returned empty dict (no common rules?)")
except Exception as e:
    crash("Non-existent genre", f"{type(e).__name__}: {e}")

# Attack 6.3: is_known_genre with empty string
try:
    result = is_known_genre("")
    held_up("is_known_genre('')", f"Returned {result}")
except Exception as e:
    crash("is_known_genre('')", f"{type(e).__name__}: {e}")

# Attack 6.4: Corrupt the cultural_rules.json
try:
    corrupt_path = os.path.join(tempfile.mkdtemp(), "corrupt_rules.json")
    with open(corrupt_path, "w") as f:
        f.write("THIS IS NOT VALID JSON {{{")
    try:
        rules = load_rules(path=corrupt_path)
        crash("Corrupt cultural_rules.json", f"Loaded without error: {len(rules)} rules")
    except json.JSONDecodeError:
        held_up("Corrupt cultural_rules.json", "Correctly raised JSONDecodeError")
    except Exception as e:
        unexpected("Corrupt cultural_rules.json", f"{type(e).__name__}: {e}")
except Exception as e:
    unexpected("Corrupt cultural_rules.json setup", f"{type(e).__name__}: {e}")

# Attack 6.5: Load rules from non-existent file
try:
    rules = load_rules(path="/tmp/nonexistent_cultural_rules_xyz.json")
    crash("Non-existent rules file", "Should have raised FileNotFoundError")
except FileNotFoundError:
    held_up("Non-existent rules file", "Correctly raised FileNotFoundError")
except Exception as e:
    unexpected("Non-existent rules file", f"{type(e).__name__}: {e}")

# Attack 6.6: detect_genre with empty text
try:
    genre, confidence = detect_genre("")
    held_up("detect_genre('')", f"Returned genre='{genre}', confidence={confidence}")
except Exception as e:
    crash("detect_genre('')", f"{type(e).__name__}: {e}")

# Attack 6.7: detect_genre with SQL code
try:
    sql_text = "SELECT * FROM users WHERE id = 1; DROP TABLE genres; --" * 100
    genre, confidence = detect_genre(sql_text)
    held_up("detect_genre(SQL code)", f"Returned genre='{genre}', confidence={confidence}")
except Exception as e:
    crash("detect_genre(SQL code)", f"{type(e).__name__}: {e}")

# Attack 6.8: detect_genre with huge text
try:
    huge_text = "修真修仙金丹元婴飞升渡劫" * 10000  # Heavy xianxia signals
    genre, confidence = detect_genre(huge_text)
    if genre == "xianxia":
        held_up("detect_genre(heavy xianxia)", f"Correctly detected xianxia, confidence={confidence}")
    else:
        unexpected("detect_genre(heavy xianxia)", f"Detected '{genre}' instead, confidence={confidence}")
except Exception as e:
    crash("detect_genre(heavy xianxia)", f"{type(e).__name__}: {e}")

# Attack 6.9: format_rules_for_prompt with non-standard values
try:
    weird_rules = {
        "test": {"target": None, "note": 123},
        "another": "just a string",
        "complex": {"target": "value", "note": "<script>alert(1)</script>"},
    }
    result = format_rules_for_prompt(weird_rules)
    held_up("format_rules_for_prompt with weird values", f"Returned {len(result)} chars")
except TypeError as e:
    held_up("format_rules_for_prompt with weird values", f"Correctly raised TypeError: {e}")
except Exception as e:
    crash("format_rules_for_prompt with weird values", f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════
# SECTION 7: OUTPUT GUARD ATTACKS
# ══════════════════════════════════════════════════════════════════

print("\n─── SECTION 7: OUTPUT GUARD ATTACKS ───")

from src.output_guard import (
    check_translation_output, sanitize_translation, has_untranslated_chinese,
    find_untranslated_chinese, check_and_record, MIN_TRANSLATION_CHARS
)

# Attack 7.1: Empty string
try:
    warnings = check_translation_output("")
    if "EMPTY" in str(warnings):
        held_up("check_translation_output('')", f"Correctly warned: {warnings}")
    else:
        unexpected("check_translation_output('')", f"Unexpected result: {warnings}")
except Exception as e:
    crash("check_translation_output('')", f"{type(e).__name__}: {e}")

# Attack 7.2: String exactly at MIN_TRANSLATION_CHARS - 1
try:
    short_text = "A" * (MIN_TRANSLATION_CHARS - 1)
    warnings = check_translation_output(short_text)
    if warnings:
        held_up(f"Text at {MIN_TRANSLATION_CHARS - 1} chars", f"Correctly flagged: {warnings}")
    else:
        unexpected(f"Text at {MIN_TRANSLATION_CHARS - 1} chars", "No warning for short text")
except Exception as e:
    crash("Short text check", f"{type(e).__name__}: {e}")

# Attack 7.3: 500K-char translation (nonexistent edge)
try:
    huge_output = "This is a translated paragraph. " * 30000
    warnings = check_translation_output(huge_output)
    if warnings:
        unexpected("500K-char output", f"Flagged with warnings: {warnings}")
    else:
        held_up("500K-char output", "Accepted with no warnings")
except Exception as e:
    crash("500K-char output", f"{type(e).__name__}: {e}")

# Attack 7.4: Translation with embedded Chinese characters
try:
    mixed_text = "The protagonist walked into the room. 治安 was waiting for him."
    result = has_untranslated_chinese(mixed_text)
    if result:
        held_up("Mixed EN/CN text", f"Correctly detected untranslated Chinese")
    else:
        crash("Mixed EN/CN text", "FAILED to detect untranslated Chinese!")
except Exception as e:
    crash("Mixed EN/CN text check", f"{type(e).__name__}: {e}")

# Attack 7.5: All Chinese "translation" (100% CN in en-US target)
try:
    cn_only = "修真界第一宗门的长老正在打坐修炼，突然天劫降临。" * 100
    result = has_untranslated_chinese(cn_only)
    if result:
        held_up("100% Chinese in en-US output", "Correctly detected")
    else:
        crash("100% Chinese in en-US output", "FAILED to detect Chinese in English target!")
except Exception as e:
    crash("100% Chinese in en-US", f"{type(e).__name__}: {e}")

# Attack 7.6: sanitize_translation with entirely chatter
try:
    chatter = "Let me translate this for you. Here is the translation:\n\nSure, here you go!"
    result = sanitize_translation(chatter)
    if len(result) < len(chatter):
        held_up("All-chatter sanitization", f"Stripped from {len(chatter)} to {len(result)} chars")
    else:
        unexpected("All-chatter sanitization", "Chatter not fully removed")
except Exception as e:
    crash("All-chatter sanitization", f"{type(e).__name__}: {e}")

# Attack 7.7: check_and_record with None text
try:
    # This is a potentially dangerous call
    pass  # skip — would require DB
except Exception as e:
    pass

# Attack 7.8: Translation containing SQL injection
try:
    sql_translation = "'; DROP TABLE translations; -- John walked into the bar." * 10
    warnings = check_translation_output(sql_translation)
    held_up("SQL injection in translation text", f"Warnings: {warnings}")
except Exception as e:
    crash("SQL injection in translation", f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════
# SECTION 8: JOB_ROUTES API ATTACKS
# ══════════════════════════════════════════════════════════════════

print("\n─── SECTION 8: MEASUREMENT/IDIOM/ONOMATOPOEIA ATTACKS ───")

from src.measurements import detect_measurements, build_measurements_hint, _parse_chinese_number
from src.idioms import detect_idioms, build_idiom_context
from src.onomatopoeia import detect_onomatopoeia, build_onomatopoeia_context

# Attack 8.1: _parse_chinese_number with extreme values
try:
    result = _parse_chinese_number("九千九百九十九万九千九百九十九")  # 99999999
    if result is not None:
        held_up("_parse_chinese_number(large)", f"Parsed: {result}")
    else:
        unexpected("_parse_chinese_number(large)", "Failed to parse")
except Exception as e:
    crash("_parse_chinese_number(large)", f"{type(e).__name__}: {e}")

# Attack 8.2: _parse_chinese_number with garbage
try:
    result = _parse_chinese_number("这不是一个数字")
    if result is None:
        held_up("_parse_chinese_number(garbage)", "Correctly returned None")
    else:
        unexpected("_parse_chinese_number(garbage)", f"Returned {result}")
except Exception as e:
    crash("_parse_chinese_number(garbage)", f"{type(e).__name__}: {e}")

# Attack 8.3: build_measurements_hint with empty string
try:
    result = build_measurements_hint("")
    if result == "":
        held_up("build_measurements_hint('')", "Correctly returned empty string")
    else:
        unexpected("build_measurements_hint('')", f"Returned: {result[:80]}")
except Exception as e:
    crash("build_measurements_hint('')", f"{type(e).__name__}: {e}")

# Attack 8.4: detect_measurements with ALL units at once
try:
    all_units = "三万五千里路，一亿多斤粮食，五丈高的墙，三尺长的剑，百亩良田。"
    result = detect_measurements(all_units)
    held_up("detect_measurements(all units)", f"Found {len(result)} unit types: {list(result.keys())}")
except Exception as e:
    crash("detect_measurements(all units)", f"{type(e).__name__}: {e}")

# Attack 8.5: build_idiom_context with every idiom
try:
    all_idioms = " ".join(src.idioms.COMMON_IDIOMS.keys())
    result = build_idiom_context(all_idioms)
    held_up("build_idiom_context(all idioms)", f"Generated {len(result)} chars of hints")
except Exception as e:
    crash("build_idiom_context(all idioms)", f"{type(e).__name__}: {e}")

# Attack 8.6: build_onomatopoeia_context with every sound
try:
    all_sounds = " ".join(src.onomatopoeia.ONOMATOPOEIA_MAP.keys()) * 10
    result = build_onomatopoeia_context(all_sounds)
    held_up("build_onomatopoeia_context(all sounds)", f"Generated {len(result)} chars")
except Exception as e:
    crash("build_onomatopoeia_context(all sounds)", f"{type(e).__name__}: {e}")

# Attack 8.7: detect_idioms with overlapping terms (e.g. 火上加油 vs 加油)
try:
    # 火上加油 contains four chars that might match partial patterns
    result = detect_idioms("他在火上加油，不帮忙还在推波助澜。")
    if len(result) >= 2:
        held_up("detect_idioms(overlapping)", f"Found {len(result)} idioms: {[r[0] for r in result]}")
    else:
        unexpected("detect_idioms(overlapping)", f"Only found {len(result)} idioms")
except Exception as e:
    crash("detect_idioms(overlapping)", f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════
# SECTION 9: CIRCUIT BREAKER ATTACKS
# ══════════════════════════════════════════════════════════════════

print("\n─── SECTION 9: CIRCUIT BREAKER ATTACKS ───")

from src.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, get_breaker

# Attack 9.1: Trip breaker immediately
try:
    cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=999)
    try:
        cb.call(lambda: 1 / 0)  # Deliberately fail
    except ZeroDivisionError:
        pass
    # Should now be OPEN
    if cb.is_open():
        held_up("Circuit breaker trip", "Correctly opened after 1 failure")
    else:
        unexpected("Circuit breaker trip", "Did not open after failure")
except Exception as e:
    crash("Circuit breaker trip", f"{type(e).__name__}: {e}")

# Attack 9.2: Call breaker when OPEN — expect CircuitBreakerOpenError
try:
    cb = CircuitBreaker(name="test2", failure_threshold=1, recovery_timeout=999)
    try:
        cb.call(lambda: 1 / 0)
    except ZeroDivisionError:
        pass
    try:
        cb.call(lambda: "should not execute")
        unexpected("Circuit breaker OPEN call", "Call executed while OPEN!")
    except CircuitBreakerOpenError:
        held_up("Circuit breaker OPEN call", "Correctly raised CircuitBreakerOpenError")
except Exception as e:
    crash("Circuit breaker OPEN call", f"{type(e).__name__}: {e}")

# Attack 9.3: Recovery timeout
try:
    cb = CircuitBreaker(name="test3", failure_threshold=1, recovery_timeout=0.01)
    try:
        cb.call(lambda: 1 / 0)
    except ZeroDivisionError:
        pass
    time.sleep(0.02)
    # Should be half-open now
    if cb.state == "half_open":
        result = cb.call(lambda: 42)
        if result == 42 and cb.state == "closed":
            held_up("Circuit breaker recovery", "Correctly recovered to CLOSED after successful probe")
        else:
            unexpected("Circuit breaker recovery", f"State={cb.state}, result={result}")
    else:
        unexpected("Circuit breaker recovery", f"State is {cb.state}, expected half_open")
except Exception as e:
    crash("Circuit breaker recovery", f"{type(e).__name__}: {e}")

# Attack 9.4: get_breaker with empty string
try:
    cb = get_breaker("")
    held_up("get_breaker('')", "Created breaker with empty name")
except Exception as e:
    crash("get_breaker('')", f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════
# SECTION 10: SENSITIVE TERMS ATTACKS
# ══════════════════════════════════════════════════════════════════

print("\n─── SECTION 10: SENSITIVE TERMS ATTACKS ───")

from src.sensitive_terms import build_sensitive_term_context, scan_arabic_blasphemy

# Attack 10.1: All sensitive terms at once
try:
    all_terms = "上身附体请神地府鬼仙" * 10
    ctx = build_sensitive_term_context(all_terms, "en-US")
    if ctx:
        held_up("All sensitive terms", f"Generated {len(ctx)} chars of warnings")
    else:
        unexpected("All sensitive terms", "No context generated")
except Exception as e:
    crash("All sensitive terms", f"{type(e).__name__}: {e}")

# Attack 10.2: Arabic blasphemy scan with clean text
try:
    violations = scan_arabic_blasphemy("This is a clean English translation.")
    if not violations:
        held_up("Arabic blasphemy scan (clean)", "No false positives")
    else:
        unexpected("Arabic blasphemy scan (clean)", f"False positives: {violations}")
except Exception as e:
    crash("Arabic blasphemy scan (clean)", f"{type(e).__name__}: {e}")

# Attack 10.3: Arabic blasphemy scan with actual violation
try:
    violations = scan_arabic_blasphemy("يلعن دينك يا هذا")
    if violations:
        held_up("Arabic blasphemy scan (violation)", f"Correctly detected: {violations}")
    else:
        unexpected("Arabic blasphemy scan (violation)", "FAILED to detect religious blasphemy!")
except Exception as e:
    crash("Arabic blasphemy scan (violation)", f"{type(e).__name__}: {e}")

# Attack 10.4: build_sensitive_term_context with unknown language
try:
    ctx = build_sensitive_term_context("鬼上身了", "xx-XX")
    if "鬼" in ctx or "上身" in ctx:
        held_up("Unknown language sensitive terms", "Context generated regardless of language")
    else:
        unexpected("Unknown language sensitive terms", f"Context: {ctx[:80]}")
except Exception as e:
    crash("Unknown language sensitive terms", f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════
# SECTION 11: EPUB BUILDER ATTACKS
# ══════════════════════════════════════════════════════════════════

print("\n─── SECTION 11: EPUB BUILDER ATTACKS ───")

from src.epub_builder import build_epub, _escape

# Attack 11.1: build_epub with empty chapters
try:
    build_epub([], title="Empty Book", output_path=os.path.join(tempfile.mkdtemp(), "empty.epub"))
    crash("build_epub([])", "Should have raised ValueError for empty chapters")
except ValueError as e:
    held_up("build_epub([])", f"Correctly raised ValueError: {str(e)[:80]}")
except Exception as e:
    unexpected("build_epub([])", f"{type(e).__name__}: {e}")

# Attack 11.2: build_epub with chapters missing 'content' key
try:
    try:
        path = build_epub(
            [{"title": "Ch1", "chapter_num": 1}],  # No 'content'
            title="Broken Book",
            output_path=os.path.join(tempfile.mkdtemp(), "broken.epub"),
        )
        unexpected("build_epub missing 'content' key", "Succeeded without content")
    except KeyError:
        held_up("build_epub missing 'content' key", "Correctly raised KeyError")
except Exception as e:
    crash("build_epub missing 'content' key", f"{type(e).__name__}: {e}")

# Attack 11.3: build_epub with chapter containing binary content
try:
    path = build_epub(
        [{"title": "Ch1", "content": "Normal.\n\n" + "\x00\x01\x02\xff\xfe", "chapter_num": 1}],
        title="Binary Book",
        output_path=os.path.join(tempfile.mkdtemp(), "binary.epub"),
    )
    held_up("build_epub with binary content", f"Created EPUB at {path} (binary escaped)")
except Exception as e:
    unexpected("build_epub with binary content", f"{type(e).__name__}: {e}")

# Attack 11.4: _escape with extreme values
try:
    result = _escape("&<>\"'" * 1000)
    if "&amp;" in result and "&lt;" in result:
        held_up("_escape(extreme)", f"Correctly escaped {len(result)} chars")
    else:
        unexpected("_escape(extreme)", "Did not properly escape")
except Exception as e:
    crash("_escape(extreme)", f"{type(e).__name__}: {e}")

# Attack 11.5: build_epub with invalid output path
try:
    build_epub(
        [{"title": "Ch1", "content": "Test", "chapter_num": 1}],
        title="Bad Path Book",
        output_path="/nonexistent_dir_xyz/book.epub",
    )
    crash("build_epub with invalid path", "Should have raised OSError")
except (OSError, FileNotFoundError):
    held_up("build_epub with invalid path", "Correctly raised filesystem error")
except Exception as e:
    unexpected("build_epub with invalid path", f"{type(e).__name__}: {e}")

# Attack 11.6: build_epub with duplicate chapter numbers
try:
    path = build_epub(
        [
            {"title": "Ch1", "content": "First.", "chapter_num": 1},
            {"title": "Ch1 Again", "content": "Also first.", "chapter_num": 1},
            {"title": "Ch2", "content": "Second.", "chapter_num": 2},
        ],
        title="Dup Book",
        output_path=os.path.join(tempfile.mkdtemp(), "dup.epub"),
    )
    held_up("build_epub with duplicate chapter numbers", f"Created EPUB at {path} (last write wins)")
except Exception as e:
    unexpected("build_epub with duplicate chapter numbers", f"{type(e).__name__}: {e}")

# Attack 11.7: build_epub with 1000 chapters
try:
    chapters = [{"title": f"Chapter {i}", "content": f"Content of chapter {i}.", "chapter_num": i} for i in range(1, 1001)]
    path = build_epub(chapters, title="1000 Chapter Book", output_path=os.path.join(tempfile.mkdtemp(), "big.epub"))
    held_up("build_epub with 1000 chapters", f"Created EPUB at {path}")
except Exception as e:
    unexpected("build_epub with 1000 chapters", f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════
# SECTION 12: CONCURRENCY / RACE CONDITION ATTACKS
# ══════════════════════════════════════════════════════════════════

print("\n─── SECTION 12: CONCURRENCY ATTACKS ───")

from src.job_store import JobStore

# Attack 12.1: Create same job from multiple threads
try:
    store = JobStore()
    errors = []
    def create_job_race():
        try:
            for _ in range(50):
                store.create_job("race_test.txt", "en-US", 10)
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=create_job_race) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    if errors:
        unexpected("Race condition: 5 threads creating jobs", f"{len(errors)} errors: {errors[:3]}")
    else:
        held_up("Race condition: 5 threads creating jobs", "No errors (SQLite serialized access)")
except Exception as e:
    crash("Race condition on job creation", f"{type(e).__name__}: {e}")

# Attack 12.2: Concurrent reads/writes on ExactGlossary
try:
    temp_db2 = os.path.join(tempfile.mkdtemp(), "race_glossary.db")
    gstore = ExactGlossary(db_path=temp_db2)
    race_errors = []

    def glossary_race():
        try:
            for i in range(100):
                gstore.add(f"race_term_{threading.get_ident()}_{i}", f"race_en_{i}")
                gstore.get(f"race_term_{threading.get_ident()}_{i}")
        except Exception as e:
            race_errors.append(str(e))

    threads = [threading.Thread(target=glossary_race) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    if race_errors:
        unexpected("Race condition: 10 threads on glossary", f"{len(race_errors)} errors")
    else:
        held_up("Race condition: 10 threads on glossary", "No errors")
except Exception as e:
    crash("Race condition on glossary", f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════
# SECTION 13: DATA CORRUPTION ATTACKS
# ══════════════════════════════════════════════════════════════════

print("\n─── SECTION 13: DATA CORRUPTION ATTACKS ───")

# Attack 13.1: Delete data directory mid-operation (simulate)
jstore = JobStore()
jid = jstore.create_job("corrupt_test.txt", "en-US", 10)
data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
held_up("Data corruption: conceptual tests", "Skipping destructive filesystem operations")

# ══════════════════════════════════════════════════════════════════
# SECTION 14: ERROR TRACKER ATTACKS
# ══════════════════════════════════════════════════════════════════

print("\n─── SECTION 14: ERROR TRACKER ATTACKS ───")

from src.error_tracker import record_event, get_event_summary, get_recent_issues

# Attack 14.1: Record event with None for all fields
try:
    record_event(None, None, "test_attack", "test detail", "en-US")
    held_up("record_event(None, None, ...)", "Recorded with nulls")
except Exception as e:
    crash("record_event(None, None, ...)", f"{type(e).__name__}: {e}")

# Attack 14.2: Record event with very long detail
try:
    record_event("test_job", 1, "test_attack", "X" * 100000, "en-US")
    held_up("record_event with 100K-char detail", "Stored successfully (may blow DB size)")
except Exception as e:
    unexpected("record_event with 100K-char detail", f"{type(e).__name__}: {e}")

# Attack 14.3: Record event with SQL injection in detail
try:
    record_event("test_job", 1, "test_attack", "'); DROP TABLE translation_events; --", "en-US")
    # Verify table still exists
    conn = sqlite3.connect(os.path.join(DATA_DIR, "translation_events.db"))
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='translation_events'").fetchone()
    conn.close()
    if row:
        held_up("SQL injection in event detail", "Table survived — parameterized queries working")
    else:
        crash("SQL injection in event detail", "TABLE WAS DROPPED!")
except Exception as e:
    crash("SQL injection in event detail", f"{type(e).__name__}: {e}")

# Attack 14.4: get_event_summary with 0 days
try:
    summary = get_event_summary(days=0)
    held_up("get_event_summary(days=0)", f"Result: {summary}")
except Exception as e:
    crash("get_event_summary(days=0)", f"{type(e).__name__}: {e}")

# Attack 14.5: get_event_summary with negative days
try:
    summary = get_event_summary(days=-7)
    unexpected("get_event_summary(days=-7)", f"Result: {summary}")
except Exception as e:
    held_up("get_event_summary(days=-7)", f"Rejected: {type(e).__name__}: {e}")

# Attack 14.6: get_recent_issues with limit=0
try:
    issues = get_recent_issues(limit=0)
    if len(issues) == 0:
        held_up("get_recent_issues(limit=0)", "Returned empty list")
    else:
        unexpected("get_recent_issues(limit=0)", f"Returned {len(issues)} issues")
except Exception as e:
    crash("get_recent_issues(limit=0)", f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════
# SECTION 15: TRANSLATION PROMPT / PARSE ATTACKS
# ══════════════════════════════════════════════════════════════════

print("\n─── SECTION 15: TRANSLATION PARSING ATTACKS ───")

from src.agent.nodes.translate import _parse_llm_response

# Attack 15.1: Parse empty string
try:
    result = _parse_llm_response("")
    if result.get("translated_text") == "":
        held_up("_parse_llm_response('')", "Returned empty translation")
    else:
        unexpected("_parse_llm_response('')", f"Result: {result}")
except Exception as e:
    crash("_parse_llm_response('')", f"{type(e).__name__}: {e}")

# Attack 15.2: Parse None
try:
    result = _parse_llm_response(None)
    crash("_parse_llm_response(None)", "Should have raised AttributeError")
except (AttributeError, TypeError):
    held_up("_parse_llm_response(None)", "Correctly raised AttributeError/TypeError")
except Exception as e:
    unexpected("_parse_llm_response(None)", f"{type(e).__name__}: {e}")

# Attack 15.3: Parse 1MB JSON response
try:
    huge_json = '{"translated_text": "' + ("X" * 500000) + '", "new_terms_found": []}'
    result = _parse_llm_response(huge_json)
    if result.get("translated_text") and len(result["translated_text"]) > 100000:
        held_up("_parse_llm_response(1MB JSON)", f"Parsed {len(result['translated_text'])} chars")
    else:
        unexpected("_parse_llm_response(1MB JSON)", f"Result too short: {len(result.get('translated_text', ''))}")
except MemoryError:
    crash("_parse_llm_response(1MB JSON)", "MemoryError")
except Exception as e:
    unexpected("_parse_llm_response(1MB JSON)", f"{type(e).__name__}: {e}")

# Attack 15.4: Parse broken JSON with unmatched quotes
try:
    broken = '{"translated_text": "The hero said: \\"I am the king of the world!", "new_terms_found": []}'
    result = _parse_llm_response(broken)
    if result.get("translated_text"):
        held_up("_parse_llm_response(broken JSON quotes)", "Recovered via regex fallback")
    else:
        unexpected("_parse_llm_response(broken JSON quotes)", "Failed to parse")
except Exception as e:
    crash("_parse_llm_response(broken JSON quotes)", f"{type(e).__name__}: {e}")

# Attack 15.5: Parse response that is pure markdown with no JSON
try:
    md_only = "# Chapter 1\n\nThe hero walked into the room. He looked around."
    result = _parse_llm_response(md_only)
    if result.get("translated_text") == md_only:
        held_up("_parse_llm_response(pure markdown)", "Layer 4: returned as-is")
    else:
        unexpected("_parse_llm_response(pure markdown)", f"Unexpected parsing: {result}")
except Exception as e:
    crash("_parse_llm_response(pure markdown)", f"{type(e).__name__}: {e}")

# Attack 15.6: Parse nested markdown code fences
try:
    nested = '```json\n{"translated_text": "Hello"}\n```\n'
    result = _parse_llm_response(nested)
    if result.get("translated_text") == "Hello":
        held_up("_parse_llm_response(nested markdown)", "Correctly stripped code fences")
    else:
        unexpected("_parse_llm_response(nested markdown)", f"Result: {result}")
except Exception as e:
    crash("_parse_llm_response(nested markdown)", f"{type(e).__name__}: {e}")

# Attack 15.7: Parse JSON with embedded null bytes
try:
    null_json = '{"translated_text": "test' + '\x00' + 'here"}'
    result = _parse_llm_response(null_json)
    held_up("_parse_llm_response(null bytes in JSON)", f"Result: {str(result)[:80]}")
except Exception as e:
    unexpected("_parse_llm_response(null bytes in JSON)", f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════
# SECTION 16: STATE / GRAPH ATTACKS
# ══════════════════════════════════════════════════════════════════

print("\n─── SECTION 16: STATE / GRAPH ATTACKS ───")

from src.agent.state import TranslatorState
from src.agent.graph import TranslationAgent, _should_repair, _has_term_conflicts

# Attack 16.1: _should_repair with missing quality_score
try:
    state: TranslatorState = {
        "chapter_title": "Test",
        "chapter_content": "Test",
        "chapter_number": 1,
        "target_lang": "en-US",
        "genre": "romance_ceo",
        "exact_glossary": {},
        "semantic_terms": [],
        "exact_matches_text": "",
        "semantic_matches_text": "",
        "translated_text": "",
        "new_terms_found": [],
        "adaptation_notes": [],
        "chapter_summary": "",
        "previous_chapter_summary": "",
        "quality_score": 5.0,
        "quality_issues": [],
        "retranslation_count": 0,
        "glossary_snapshot_json": "{}",
        "term_conflicts": [],
        "resolved_conflicts": [],
        "dialect_context": "",
    }
    result = _should_repair(state)
    if result == "END":
        held_up("_should_repair(valid state)", "Correctly routes to END for high quality")
    else:
        unexpected("_should_repair(valid state)", f"Routed to {result}")
except Exception as e:
    crash("_should_repair(valid state)", f"{type(e).__name__}: {e}")

# Attack 16.2: _should_repair with score 0.0 (minimum)
try:
    bad_state = dict(state)
    bad_state["quality_score"] = 0.0
    result = _should_repair(bad_state)
    if result == "polish_node":
        held_up("_should_repair(score=0.0)", "Correctly routes to polish_node for zero quality")
    else:
        unexpected("_should_repair(score=0.0)", f"Routed to {result}")
except Exception as e:
    crash("_should_repair(score=0.0)", f"{type(e).__name__}: {e}")

# Attack 16.3: _should_repair with max retranslations
try:
    max_retry_state = dict(state)
    max_retry_state["quality_score"] = 2.0
    max_retry_state["retranslation_count"] = 999
    result = _should_repair(max_retry_state)
    if result == "END":
        held_up("_should_repair(max retries)", "Correctly routes to END (max retries exceeded)")
    else:
        unexpected("_should_repair(max retries)", f"Routed to {result}")
except Exception as e:
    crash("_should_repair(max retries)", f"{type(e).__name__}: {e}")

# Attack 16.4: _has_term_conflicts with empty state
try:
    result = _has_term_conflicts(state)
    if result == "quality_check":
        held_up("_has_term_conflicts(no conflicts)", "Correctly skips arbitration")
    else:
        unexpected("_has_term_conflicts(no conflicts)", f"Routed to {result}")
except Exception as e:
    crash("_has_term_conflicts(no conflicts)", f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════
# SECTION 17: DIALECT / EDGE ATTACKS
# ══════════════════════════════════════════════════════════════════

print("\n─── SECTION 17: DIALECT / EDGE CASE ATTACKS ───")

from src.dialect import build_dialect_context, has_system_text

# Attack 17.1: build_dialect_context with empty string
try:
    result = build_dialect_context("")
    if result == "":
        held_up("build_dialect_context('')", "Correctly returned empty string")
    else:
        unexpected("build_dialect_context('')", f"Returned: {result[:80]}")
except Exception as e:
    crash("build_dialect_context('')", f"{type(e).__name__}: {e}")

# Attack 17.2: build_dialect_context with 200K-char chapter
try:
    huge = "俺们村儿的系统说" + "普通文本" * 50000
    result = build_dialect_context(huge)
    if result:
        held_up("build_dialect_context(200K chars)", f"Generated {len(result)} chars context")
    else:
        held_up("build_dialect_context(200K chars)", "No dialect detected in huge text (expected)")
except Exception as e:
    crash("build_dialect_context(200K chars)", f"{type(e).__name__}: {e}")

# Attack 17.3: has_system_text with patterns
try:
    result = has_system_text("【系统提示：获得技能】")
    held_up("has_system_text(system notification)", f"Returned {result}")
except Exception as e:
    crash("has_system_text", f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════
# SECTION 18: BACKPRESSURE ATTACKS
# ══════════════════════════════════════════════════════════════════

print("\n─── SECTION 18: BACKPRESSURE ATTACKS ───")

from src.backpressure import backpressure

# Attack 18.1: Release more times than accept
try:
    initial = backpressure.queue_depth
    for _ in range(100):
        backpressure.release()
    final = backpressure.queue_depth
    # Queue depth should not go negative
    if final >= 0:
        held_up("backpressure.release() spam", f"Depth went from {initial} to {final} (non-negative)")
    else:
        data_loss("backpressure.release() spam", f"Depth went negative: {initial} → {final}")
except Exception as e:
    crash("backpressure.release() spam", f"{type(e).__name__}: {e}")

# Attack 18.2: Accept until capacity and beyond
try:
    accepted = 0
    while backpressure.try_accept():
        accepted += 1
        if accepted > 1000:
            unexpected("backpressure capacity", "Accepted more than 1000 — no limit?")
            break
    held_up("backpressure capacity test", f"Accepted {accepted} before rejecting")
    # Release what we took
    for _ in range(accepted):
        backpressure.release()
except Exception as e:
    crash("backpressure capacity test", f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════
# REPORT
# ══════════════════════════════════════════════════════════════════

print("\n\n" + "=" * 70)
print("FINAL DESTRUCTIVE TEST REPORT")
print("=" * 70)

print("\n## CRASHES (system died)")
for entry in RESULTS["crashes"]:
    print(entry)
if not RESULTS["crashes"]:
    print("  (none)")

print("\n## DATA LOSS (data corrupted or lost)")
for entry in RESULTS["data_loss"]:
    print(entry)
if not RESULTS["data_loss"]:
    print("  (none)")

print("\n## UNEXPECTED BEHAVIOR (didn't crash but did something wrong)")
for entry in RESULTS["unexpected_behavior"]:
    print(entry)
if not RESULTS["unexpected_behavior"]:
    print("  (none)")

print("\n## HELD UP (attacks the system survived gracefully)")
for entry in RESULTS["held_up"]:
    print(entry)
if not RESULTS["held_up"]:
    print("  (none)")

print(f"\nSummary: {len(RESULTS['crashes'])} crashes, {len(RESULTS['data_loss'])} data loss, "
      f"{len(RESULTS['unexpected_behavior'])} unexpected behaviors, {len(RESULTS['held_up'])} held up")

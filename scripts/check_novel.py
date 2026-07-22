"""Quick structure check for the test novel."""
import sys
sys.path.insert(0, ".")
from src.chapter_splitter import split_chapters

text = open("tests/fixtures/test_novel_50ch.txt").read()
chapters = split_chapters(text)
translatable = [c for c in chapters if c.action.value != "skip"]

print(f"章节数: {len(translatable)}")
for c in translatable[:5]:
    print(f"  第{c.index}章: {c.title[:40]} ({c.word_count}字)")
print("  ...")
for c in translatable[-3:]:
    print(f"  第{c.index}章: {c.title[:40]} ({c.word_count}字)")
total = sum(c.word_count for c in translatable)
print(f"总字数: {total}")
terms = ['穿越', '系统', '霸总', '白莲花', '备胎', '暖男', '社畜', '带球跑', '父凭子贵', '打脸', '金手指', '好感度']
for t in terms:
    print(f"  「{t}」: {text.count(t)}次")

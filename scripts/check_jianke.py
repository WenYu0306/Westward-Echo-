"""Quick analysis of 间客 novel structure (GBK encoding)."""
import sys
sys.path.insert(0, ".")
from src.chapter_splitter import split_chapters

FIXTURE = "tests/fixtures/《间客》（精校版全本）作者：猫腻.txt"
text = open(FIXTURE, encoding="gbk").read()
chapters = split_chapters(text)
translatable = [c for c in chapters if c.action.value != "skip"]

print(f"总章节数: {len(translatable)}")
total = sum(c.word_count for c in translatable)
print(f"总字数: {total}")
print()
for c in translatable[:5]:
    print(f"  第{c.index}章: {c.title[:50]} ({c.word_count}字)")
print("  ...")
for c in translatable[-3:]:
    print(f"  第{c.index}章: {c.title[:50]} ({c.word_count}字)")

print(f"\n小说类型: 科幻/机甲/星际")
print(f"cultural_rules: 无此分类, 将使用 urban (17条通用规则)")

print(f"\n前 20 章字数: {sum(c.word_count for c in translatable[:20])}")
print(f"预估翻译时间: ~15-20 分钟")

# Check for special elements
print("\n特殊元素检测:")
print(f"  '机甲' 出现: {text.count('机甲')} 次")
print(f"  '联邦' 出现: {text.count('联邦')} 次")
print(f"  '帝国' 出现: {text.count('帝国')} 次")
print(f"  '星空' 出现: {text.count('星空')} 次")
print(f"  '战舰' 出现: {text.count('战舰')} 次")
print(f"  '修行' 出现: {text.count('修行')} 次")
print(f"  '内力' 出现: {text.count('内力')} 次")

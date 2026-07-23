"""Analyze 地府叫我小先生 for Westward Echo testing."""
import sys
sys.path.insert(0, ".")
from src.encoding import detect_and_read
from src.chapter_splitter import split_chapters
from src.cultural_rules import detect_genre, is_known_genre, list_known_genres

FIXTURE = "tests/fixtures/《地府叫我小先生》 作者：界玉.txt"
text, enc = detect_and_read(FIXTURE)
chapters = split_chapters(text)
translatable = [c for c in chapters if c.action.value != "skip"]

print(f"编码: {enc}")
print(f"章节数: {len(translatable)}")
total = sum(c.word_count for c in translatable)
print(f"总字数: {total}")
print(f"前 5 章总字数: {sum(c.word_count for c in translatable[:5])}")
print()

for c in translatable[:5]:
    preview = c.content[:60].replace('\n', ' ')
    print(f"  第{c.index}章: {c.title[:40]} ({c.word_count}字) | {preview}...")
print("  ...")
for c in translatable[-3:]:
    print(f"  第{c.index}章: {c.title[:40]} ({c.word_count}字)")

sample = text[:20000]
detected, conf = detect_genre(sample)
known = list_known_genres()
print(f"\n类型检测: '{detected}' (置信度: {conf})")
print(f"已知类型: {known}")
print(f"is_known: {is_known_genre(detected)}")

keywords = ['出马','仙家','上身','弟马','堂口','胡仙','黄仙','柳仙','灰仙','白仙',
            '地府','阎王','判官','阴差','鬼差','轮回','灵异','道术','符咒',
            '东北','老仙','香主','请神','打表','阴阳','鬼门','城隍','孟婆','奈何桥',
            '整','啥','咋','俺','唠嗑','得劲儿','忽悠','老鼻子','嘎哈','咋地','嗯呐']
found = {kw: text.count(kw) for kw in keywords if text.count(kw) > 0}
print(f"\n关键词 (出现 >0次, 共{len(found)}个):")
for kw, cnt in sorted(found.items(), key=lambda x: x[1], reverse=True)[:20]:
    print(f"  「{kw}」: {cnt}次")

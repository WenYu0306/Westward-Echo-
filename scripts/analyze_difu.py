"""Analyze 地府叫我小先生 for Westward Echo testing.

【中文·这个脚本干什么】
对《地府叫我小先生》这本小说的原文做一次"体检"：统计章节数、字数、
检测小说类型、扫描民俗/东北方言关键词，帮助开发者了解这本书的基本面貌。

【中文·怎么运行】
在项目根目录下执行：
```bash
python3.11 scripts/analyze_difu.py
```

【中文·运行前要准备什么】
需要 tests/fixtures/ 下有《地府叫我小先生》的原文 txt 文件。

【中文·⚠️ 会不会花真钱、会不会写生产数据库】
不会花真钱（不调用大模型 API）。
不会写生产数据库（只读原文、打印统计结果到终端）。

【中文·会产生/修改什么文件】
不产生任何文件，所有结果直接打印到终端。
"""
import sys
sys.path.insert(0, ".")
from src.text_io import detect_and_read
from src.chapter_splitter import split_chapters
from src.cultural_rules import detect_genre, is_known_genre, list_known_genres

# 【中文】硬编码的原文路径——这个脚本专门为《地府叫我小先生》写的，
# 不像 generate_report.py 那样支持多本书。
FIXTURE = "tests/fixtures/《地府叫我小先生》 作者：界玉.txt"
text, enc = detect_and_read(FIXTURE)
chapters = split_chapters(text)
# 【中文】过滤掉"跳过"标记的章节（如版权声明、作者感言等非正文内容），
# 只保留真正需要翻译的章节。
translatable = [c for c in chapters if c.action.value != "skip"]

# ── 基本统计 ──
print(f"编码: {enc}")
print(f"章节数: {len(translatable)}")
total = sum(c.word_count for c in translatable)
print(f"总字数: {total}")
print(f"前 5 章总字数: {sum(c.word_count for c in translatable[:5])}")
print()

# 【中文】打印前 5 章的预览（标题 + 前 60 字），让开发者快速确认
# 章节切分是否正确。
for c in translatable[:5]:
    preview = c.content[:60].replace('\n', ' ')
    print(f"  第{c.index}章: {c.title[:40]} ({c.word_count}字) | {preview}...")
print("  ...")
# 【中文】再打印最后 3 章，确认尾部章节也被正确识别。
for c in translatable[-3:]:
    print(f"  第{c.index}章: {c.title[:40]} ({c.word_count}字)")

# ── 类型检测 ──
# 【中文】取前 20000 字符做样本，调用文化规则模块的类型检测器。
# 为什么只用前 20000 字：全书几百万字全喂进去太慢，
# 开头部分通常已包含足够的类型特征词。
sample = text[:20000]
detected, conf = detect_genre(sample)
known = list_known_genres()
print(f"\n类型检测: '{detected}' (置信度: {conf})")
print(f"已知类型: {known}")
print(f"is_known: {is_known_genre(detected)}")

# ── 民俗 / 东北方言关键词扫描 ──
# 【中文】这本书的核心题材是"东北出马仙 + 地府灵异"，下面两组关键词
# 分别对应这两个维度。扫描结果帮助开发者判断：
#   · 民俗术语密度有多高 → 影响术语表需要多大
#   · 东北方言占比多少 → 影响 WRITE 节点要不要保留方言味道
keywords = ['出马','仙家','上身','弟马','堂口','胡仙','黄仙','柳仙','灰仙','白仙',
            '地府','阎王','判官','阴差','鬼差','轮回','灵异','道术','符咒',
            '东北','老仙','香主','请神','打表','阴阳','鬼门','城隍','孟婆','奈何桥',
            '整','啥','咋','俺','唠嗑','得劲儿','忽悠','老鼻子','嘎哈','咋地','嗯呐']
found = {kw: text.count(kw) for kw in keywords if text.count(kw) > 0}
print(f"\n关键词 (出现 >0次, 共{len(found)}个):")
for kw, cnt in sorted(found.items(), key=lambda x: x[1], reverse=True)[:20]:
    print(f"  「{kw}」: {cnt}次")

"""Backfill cultural_rules.json from a completed book's style memo.

⚠️ **本脚本会修改项目核心数据文件 cultural_rules.json，运行前请确认已备份！**
· 不会花真钱（不调用大模型 API）
· 但会直接覆写项目根目录下的 cultural_rules.json
· 这个文件是所有翻译任务的文化规则来源，改坏了会影响后续所有翻译

【中文·这个脚本干什么】
从一本已翻完的书的"经验本"（style memo）里提取经过验证的翻译桥段和术语，
合并到 cultural_rules.json 里。这样同类型的下一本书就能自动继承这些经验。
好比一个老编辑退休前把自己的笔记整理成手册，留给新人用。

【中文·怎么运行】
```bash
python3.11 scripts/backfill_rules.py <book_id> <genre>
# 例如：
python3.11 scripts/backfill_rules.py limitless_horror_segmented urban
```

【中文·运行前要准备什么】
① data/translation_memory/{book_id}/ 目录下要有 bridges.md 和 terms.md
   （这是翻译过程中自动积累的经验本）
② 项目根目录下要有 cultural_rules.json

【中文·⚠️ 会不会花真钱、会不会写生产数据库】
不会花真钱（纯文本处理，不调用大模型）。
⚠️ **会修改 cultural_rules.json**：以 JSON 格式覆写整个文件。
不会写 checkpoints.db 或其他数据库。

【中文·会产生/修改什么文件】
· 修改：项目根目录/cultural_rules.json（整体覆写）
"""
import json, os, sys, re
from datetime import datetime, timezone

# 【中文】文化规则文件的路径，在项目根目录下。
# ⚠️ 这个文件是所有翻译任务的文化适配规则来源。
RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "cultural_rules.json")

# 【中文】系统已知的小说类型集合。传入的 genre 参数必须在这个集合里，
# 否则脚本拒绝执行，防止拼写错误创建出无用的类型分支。
KNOWN_GENRES = {"romance_ceo", "xianxia", "urban", "scifi", "folk_religion"}


def load_rules():
    """读取当前的文化规则文件。"""
    with open(RULES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_rules(rules):
    """⚠️ 覆写整个文化规则文件。调用前请确认已备份。"""
    with open(RULES_PATH, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)


def extract_bridges(memo_dir: str) -> list[str]:
    """Parse bridges.md: only keep cold-reader confusion reports and bridge patterns.

    【中文】从经验本的 bridges.md 里提取"翻译桥段"——即冷读者表示困惑、
    或叙事节奏拖沓的记录。只保留高信号条目（包含 "confused by" 或
    "exposition...drag"），过滤掉一般性笔记。去重后最多返回 100 条。
    """
    bridges_path = os.path.join(memo_dir, "bridges.md")
    if not os.path.exists(bridges_path):
        return []

    text = open(bridges_path, encoding="utf-8").read()
    rules = []
    seen = set()

    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Only keep specific high-signal entries
        # 【中文】只保留两类高价值记录：
        # · 冷读者明确表示困惑的（"confused by" + "reader confused"）
        # · 叙事节奏拖沓的（"exposition" + "drag"）
        lowered = line.lower()
        is_confusion = "confused by" in lowered and "reader confused" in lowered
        is_exposition = "exposition" in lowered and "drag" in lowered

        if not (is_confusion or is_exposition):
            continue

        # 【中文】去掉行首的 [ch123] 章节标记，只留内容。
        cleaned = re.sub(r'^\[ch\d+\]\s*', '', line)
        if not cleaned or len(cleaned) < 30:
            continue

        # Dedup: first 80 chars as fingerprint
        # 【中文】用前 80 字符做指纹去重，避免同一问题被多次记录。
        fingerprint = cleaned[:80].lower()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)

        # Extract just the actionable guidance
        # 【中文】只取 ". Test:" 之前的部分作为可操作的指导语。
        parts = cleaned.split(". Test:", 1)
        summary = parts[0][:200].strip()
        if summary:
            rules.append(summary)

    # Cap at 100 bridges to keep rules file manageable
    # 【中文】上限 100 条，防止规则文件膨胀到影响提示词长度。
    return rules[:100]


def extract_terms(memo_dir: str) -> list[tuple[str, str]]:
    """Parse terms.md: only keep clear CN → EN term mappings.

    【中文】从经验本的 terms.md 里提取"中文 → 英文"术语对照。
    匹配格式为 "中文 → English" 或 "中文 > English" 的行。
    去重后最多返回 100 条。
    """
    terms_path = os.path.join(memo_dir, "terms.md")
    if not os.path.exists(terms_path):
        return []

    text = open(terms_path, encoding="utf-8").read()
    rules = []
    seen = set()

    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        fingerprint = line[:40].lower()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)

        cleaned = re.sub(r'^\[ch\d+\]\s*', '', line)
        # 【中文】正则匹配 "中文 → 英文" 或 "中文 > 英文" 格式，
        # 可选的 // 后面是注释。要求中英文都至少 2 个字符。
        m = re.match(r'(.+?)\s*[→>]\s*(.+?)(?:\s*//\s*(.+))?$', cleaned)
        if m:
            cn = m.group(1).strip()
            en = m.group(2).strip()
            if len(cn) >= 2 and len(en) >= 2:
                rules.append((cn, en))

    return rules[:100]


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 scripts/backfill_rules.py <book_id> <genre>")
        sys.exit(1)

    book_id = sys.argv[1]
    genre = sys.argv[2]

    # 【中文】校验类型名是否在已知集合里，防止拼写错误。
    if genre not in KNOWN_GENRES:
        print(f"Unknown genre '{genre}'. Known: {', '.join(KNOWN_GENRES)}")
        sys.exit(1)

    # 【中文】经验本目录：data/translation_memory/{book_id}/
    memo_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data/translation_memory", book_id)

    if not os.path.isdir(memo_dir):
        print(f"Memo directory not found: {memo_dir}")
        sys.exit(1)

    # 【中文】从经验本中提取桥段和术语候选。
    bridges = extract_bridges(memo_dir)
    terms = extract_terms(memo_dir)

    # 【中文】⚠️ 读取当前规则文件，后面会合并新条目再整体覆写。
    rules = load_rules()

    # Ensure genre structure
    # 【中文】确保目标类型和目标语的嵌套结构存在。
    if genre not in rules["genres"]:
        rules["genres"][genre] = {}
    if "en-US" not in rules["genres"][genre]:
        rules["genres"][genre]["en-US"] = {}

    genre_rules = rules["genres"][genre]["en-US"]
    before_count = len(genre_rules)
    added = 0

    tag = f"[{book_id}]"

    # Add bridges as build rules
    # 【中文】把桥段加到规则里。用 hash 生成唯一键名，避免覆盖已有规则。
    for b in bridges:
        key = f"_bridge_{abs(hash(b)) % 100000}"
        if key not in genre_rules:
            genre_rules[key] = {
                "target": b[:120],
                "note": f"Bridge pattern from {book_id} style memo",
            }
            added += 1

    # Add terms
    # 【中文】把术语加到规则里。如果中文键已存在则跳过（不覆盖人工精调过的规则）。
    for cn, en in terms:
        if cn == "_note":
            key = f"_term_note_{abs(hash(en)) % 100000}"
            genre_rules[key] = {
                "target": en[:120],
                "note": f"Term note from {book_id} style memo",
            }
            added += 1
        elif cn not in genre_rules:
            genre_rules[cn] = {
                "target": en,
                "note": f"Auto-extracted from {book_id} translation memory",
            }
            added += 1

    # 【中文】⚠️ 覆写整个 cultural_rules.json。
    save_rules(rules)

    after_count = len(genre_rules)
    print(f"Genre '{genre}': {before_count} → {after_count} rules (+{added})")
    print(f"  Bridges: {len(bridges)} candidates")
    print(f"  Terms: {len(terms)} candidates")
    print(f"  Total file: {len(json.dumps(rules)):,} bytes")


if __name__ == "__main__":
    main()

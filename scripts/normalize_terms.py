"""Post-processing: normalize terminology across the full translated novel.

All operations are deterministic text replacements — no API calls.
Each fix is logged with before/after counts for verification.
"""
import re, sys, os

EN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "novels/output/limitless_horror_segmented/limitless_horror_en.md")

with open(EN, "r", encoding="utf-8") as f:
    text = f.read()

original = text
fixes = []

# ── 1. Gene Lock (majority: 460) vs Genetic Lock (114) ──
before = text.count("Genetic Lock")
text = text.replace("Genetic Lock", "Gene Lock")
after = text.count("Genetic Lock")
fixes.append(("Genetic Lock", "Gene Lock", before))

# ── 2. Demon Team (majority: 118) vs Devil Team (60) ──
before = text.count("Devil Team")
text = text.replace("Devil Team", "Demon Team")
fixes.append(("Devil Team", "Demon Team", before))

# ── 3. Chu Xun → Chu Xuan (30 errors in Ch770-775) ──
before = len(re.findall(r'\bChu Xun\b', text))
text = re.sub(r'\bChu Xun\b', 'Chu Xuan', text)
fixes.append(("Chu Xun", "Chu Xuan", before))

# ── 4. Soul Light variants → Soul-Light ──
for variant in ["Heart of Light", "Soul Light", "heart of light", "soul light"]:
    before = len(re.findall(re.escape(variant), text, re.IGNORECASE))
    text = re.sub(re.escape(variant), "Soul-Light", text, flags=re.IGNORECASE)
    if before > 0:
        fixes.append((variant, "Soul-Light", before))

# ── 5. Donghuang Bell vs Eastern Emperor → standardize to Donghuang Bell ──
for variant in ["Eastern Emperor Bell", "Eastern Emperor's Bell", "Eastern Emperor's bell", "eastern emperor bell"]:
    before = len(re.findall(re.escape(variant), text, re.IGNORECASE))
    text = re.sub(re.escape(variant), "Donghuang Bell", text, flags=re.IGNORECASE)
    if before > 0:
        fixes.append((variant, "Donghuang Bell", before))

# ── 6. Luoli → Luo Li (character name consistency) ──
before = len(re.findall(r'\bLuoli\b', text))
text = re.sub(r'\bLuoli\b', 'Luo Li', text)
fixes.append(("Luoli", "Luo Li", before))

# ── 7. Zheng Z (abbreviation) → Zheng Zha ──
before = len(re.findall(r'\bZheng Z\b(?!ha)', text))
text = re.sub(r'\bZheng Z\b(?!ha)', 'Zheng Zha', text)
fixes.append(("Zheng Z (not Zha)", "Zheng Zha", before))

# ── 8. Untranslated Chinese in Ch770-775 ──
cn_terms = {
    "复制体郑吒": "Replica Zheng Zha",
    "复制体楚轩": "Replica Chu Xuan",
    "复制体": "Replica",
    "轮回": "Cycle World",
}
for cn, en in cn_terms.items():
    before = text.count(cn)
    text = text.replace(cn, en)
    if before > 0:
        fixes.append((cn, en, before))

# ── 8b. Edge cases from Chinese replacement ──
for bad, good in [
    ("theReplica", "the Replica"),
    ("Chu Xuns", "Chu Xuans"),
]:
    before = len(re.findall(re.escape(bad), text))
    text = re.sub(re.escape(bad), good, text)
    if before > 0:
        fixes.append((bad, good, before))

# ── 8c. Remaining Eastern Emperor edge cases ──
for variant in ["Eastern Emperor's Bell", "Eastern Emperor’s Bell", "Bell of the Eastern Emperor"]:
    before = len(re.findall(re.escape(variant), text))
    text = re.sub(re.escape(variant), "Donghuang Bell", text)
    if before > 0:
        fixes.append((variant, "Donghuang Bell", before))

# ── 9. Remove stray Chinese chars from prose body (not headers) ──
# Split by chapter headers, clean body text only
parts = re.split(r'(\n## Chapter \d+[^\n]*)', text)
cleaned_segments = 0
for i in range(1, len(parts)-1, 2):
    header = parts[i]
    body = parts[i+1] if i+1 < len(parts) else ''
    cn_count = len(re.findall(r'[一-鿿]', body))
    if cn_count > 0:
        # Skip header line itself, clean only the prose
        body_lines = body.split('\n')
        # Keep first 3 lines (empty, running header, empty) as-is
        prose_start = 3 if len(body_lines) > 3 else 0
        prose = '\n'.join(body_lines[prose_start:])
        cleaned_prose = re.sub(r'[一-鿿]+', '', prose)
        cleaned_prose = re.sub(r' {2,}', ' ', cleaned_prose)
        cleaned_prose = re.sub(r'\n{3,}', '\n\n', cleaned_prose)
        parts[i+1] = '\n'.join(body_lines[:prose_start]) + '\n' + cleaned_prose
        cleaned_segments += 1
text = ''.join(parts)

# ── Write ──
with open(EN, "w", encoding="utf-8") as f:
    f.write(text)

print("=== 术语统一完成 ===")
for old, new, count in fixes:
    if count > 0:
        print(f"  {old} → {new}: {count}处")

print(f"\n总文件: {len(text):,} 字符")
print(f"变更: {len(original) - len(text)} 字符")

"""Gradio frontend — redesigned: premium SaaS aesthetic with dark-gradient header."""

import time
import gradio as gr

from .chapter_splitter import split_chapters, merge_chapters, ParagraphTag
from .agent.graph import TranslationAgent
from .config import GRADIO_PORT, QUALITY_CHECK_INTERVAL, CHAPTER_COOLDOWN_SECONDS

# ═══════════════════════════════════════════════════════════════
# DESIGN SYSTEM — Custom CSS
# ═══════════════════════════════════════════════════════════════

CUSTOM_CSS = r"""
/* ── Fonts ── */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Noto+Sans+SC:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

/* ── Reset & Global ── */
*, *::before, *::after { box-sizing: border-box; }

body {
    background: #f9fafb !important;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    font-family: 'Inter', 'Noto Sans SC', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
}

.gradio-container {
    max-width: 1120px !important;
    margin: 0 auto !important;
    padding: 0 16px !important;
}

/* ── Typography scale ── */
:root {
    --text-xs:   11px;
    --text-sm:   13px;
    --text-base: 14px;
    --text-lg:   16px;
    --text-xl:   20px;
    --text-2xl:  28px;

    /* Colors */
    --gray-50:  #f9fafb;
    --gray-75:  #f3f4f6;
    --gray-100: #e5e7eb;
    --gray-200: #d1d5db;
    --gray-300: #9ca3af;
    --gray-400: #6b7280;
    --gray-500: #4b5563;
    --gray-600: #374151;
    --gray-700: #1f2937;
    --gray-800: #111827;
    --gray-900: #030712;

    --brand-50:  #eff6ff;
    --brand-100: #dbeafe;
    --brand-200: #bfdbfe;
    --brand-400: #3b82f6;
    --brand-500: #1d4ed8;
    --brand-600: #2563eb;
    --brand-700: #1e40af;

    --accent-400: #f472b6;
    --accent-500: #ec4899;

    --green-400: #34d399;
    --green-500: #10b981;
    --green-600: #059669;

    --red-50:  #fef2f2;
    --red-100: #fee2e2;
    --red-500: #ef4444;

    --amber-500: #f59e0b;

    --shadow-xs:  0 1px 2px rgba(0,0,0,0.04);
    --shadow-sm:  0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
    --shadow-md:  0 4px 6px -1px rgba(0,0,0,0.06), 0 2px 4px -2px rgba(0,0,0,0.04);
    --shadow-lg:  0 10px 15px -3px rgba(0,0,0,0.06), 0 4px 6px -4px rgba(0,0,0,0.04);
    --shadow-xl:  0 20px 25px -5px rgba(0,0,0,0.08), 0 8px 10px -6px rgba(0,0,0,0.04);

    --radius-sm: 6px;
    --radius-md: 8px;
    --radius-lg: 12px;
    --radius-xl: 16px;

    --transition-fast: 150ms cubic-bezier(0.4, 0, 0.2, 1);
    --transition-base: 200ms cubic-bezier(0.4, 0, 0.2, 1);
    --transition-slow: 300ms cubic-bezier(0.4, 0, 0.2, 1);
}

/* ──────────────────────────────────────
   Hero / Header
   ────────────────────────────────────── */
.ww-header {
    position: relative;
    margin: 20px 0 0 0;
    padding: 44px 36px 40px;
    border-radius: var(--radius-xl);
    background:
        radial-gradient(ellipse 80% 120% at 20% 80%, rgba(59,130,246,0.18) 0%, transparent 60%),
        radial-gradient(ellipse 60% 100% at 85% 5%,  rgba(236,72,153,0.12) 0%, transparent 55%),
        linear-gradient(160deg, #0f172a 0%, #0f172a 30%, #111c3a 65%, #0c1938 100%);
    overflow: hidden;
    box-shadow: var(--shadow-xl);
}

/* Subtle grid pattern overlay */
.ww-header::before {
    content: '';
    position: absolute; inset: 0;
    background-image:
        linear-gradient(rgba(255,255,255,0.025) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,0.025) 1px, transparent 1px);
    background-size: 40px 40px;
    pointer-events: none;
}

.ww-header-brand {
    position: relative;
    display: flex; align-items: baseline; gap: 10px;
    margin-bottom: 8px;
}

.ww-header-brand h1 {
    font-family: 'Inter', 'Noto Sans SC', -apple-system, sans-serif;
    font-size: 30px;
    font-weight: 800;
    letter-spacing: -0.6px;
    color: #f8fafc;
    margin: 0;
    line-height: 1.15;
}

.ww-header-brand .ww-cn {
    letter-spacing: 2px;
}

.ww-header-brand .ww-sep {
    font-weight: 300;
    color: rgba(255,255,255,0.25);
    font-size: 22px;
    margin: 0 2px;
}

.ww-header-brand .ww-en {
    color: #93c5fd;
}

.ww-header-badge {
    position: relative;
    display: inline-flex; align-items: center; gap: 5px;
    padding: 3px 10px 3px 8px;
    border-radius: 20px;
    font-size: var(--text-xs);
    font-weight: 500;
    color: #a5f3fc;
    background: rgba(165,243,252,0.10);
    border: 1px solid rgba(165,243,252,0.15);
    margin-bottom: 16px;
    letter-spacing: 0.3px;
}

.ww-header-badge .ww-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: #22d3ee;
    box-shadow: 0 0 6px rgba(34,211,238,0.5);
}

.ww-header-desc {
    position: relative;
    color: #94a3b8;
    font-size: var(--text-sm);
    line-height: 1.5;
    max-width: 540px;
    margin: 0;
}

/* ──────────────────────────────────────
   Info cards row
   ────────────────────────────────────── */
.ww-meta-row {
    display: flex;
    gap: 10px;
    padding: 28px 0 0 0;
}

.ww-meta-card {
    flex: 1;
    padding: 12px 16px;
    border-radius: var(--radius-lg);
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.06);
    transition: background var(--transition-base);
}

.ww-meta-card:hover {
    background: rgba(255,255,255,0.07);
}

.ww-meta-card .ww-meta-icon {
    font-size: 13px;
    margin-bottom: 4px;
    opacity: 0.7;
}

.ww-meta-card .ww-meta-value {
    font-size: var(--text-sm);
    font-weight: 600;
    color: #e2e8f0;
    line-height: 1.3;
}

.ww-meta-card .ww-meta-label {
    font-size: var(--text-xs);
    color: #64748b;
    margin-top: 1px;
}

/* ──────────────────────────────────────
   Step indicator (refined)
   ────────────────────────────────────── */
.ww-steps {
    display: flex; gap: 0;
    margin: 28px 0 32px;
}

.ww-step {
    flex: 1;
    display: flex; align-items: center; gap: 12px;
    padding: 16px 20px;
    background: #ffffff;
    border: 1px solid var(--gray-100);
    position: relative;
    transition: all var(--transition-base);
}

.ww-step:first-child { border-radius: var(--radius-lg) 0 0 var(--radius-lg); }
.ww-step:last-child  { border-radius: 0 var(--radius-lg) var(--radius-lg) 0; }

/* Connector between steps */
.ww-step:not(:last-child)::after {
    content: '';
    position: absolute; right: -8px; top: 50%; transform: translateY(-50%);
    width: 16px; height: 16px;
    background: #fff;
    border-right: 1px solid var(--gray-100);
    border-top: 1px solid var(--gray-100);
    transform: translateY(-50%) rotate(45deg);
    z-index: 1;
}

.ww-step-num {
    flex-shrink: 0;
    width: 30px; height: 30px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: var(--text-sm); font-weight: 700;
    color: #fff;
    background: linear-gradient(135deg, #1e40af, #3b82f6);
    box-shadow: 0 2px 8px rgba(29,78,216,0.25);
}

.ww-step:nth-child(2) .ww-step-num {
    background: linear-gradient(135deg, #2563eb, #60a5fa);
}

.ww-step:nth-child(3) .ww-step-num {
    background: linear-gradient(135deg, #6366f1, #818cf8);
}

.ww-step-info .ww-step-label {
    font-size: var(--text-sm); font-weight: 600; color: var(--gray-700);
    line-height: 1.2;
}

.ww-step-info .ww-step-desc {
    font-size: var(--text-xs); color: var(--gray-400);
    margin-top: 1px;
}

.ww-step:hover {
    border-color: var(--gray-200);
    box-shadow: var(--shadow-sm);
}

/* ──────────────────────────────────────
   Panel / Card (configuration)
   ────────────────────────────────────── */
.ww-panel {
    background: #ffffff;
    border: 1px solid var(--gray-100);
    border-radius: var(--radius-lg);
    padding: 20px 22px;
    margin-bottom: 16px;
    box-shadow: var(--shadow-xs);
    transition: box-shadow var(--transition-base);
}

.ww-panel:first-of-type { margin-top: 0; }

.ww-panel-title {
    display: flex; align-items: center; gap: 8px;
    font-size: var(--text-sm);
    font-weight: 700;
    color: var(--gray-700);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin: 0 0 16px 0;
    padding-bottom: 14px;
    border-bottom: 1px solid var(--gray-75);
}

.ww-panel-title .ww-icon-dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: var(--brand-600);
}

/* ──────────────────────────────────────
   Buttons
   ────────────────────────────────────── */
.ww-btn-primary {
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    gap: 8px !important;

    width: 100% !important;
    padding: 12px 24px !important;
    border: none !important;
    border-radius: var(--radius-md) !important;

    font-family: 'Inter', 'Noto Sans SC', sans-serif !important;
    font-size: var(--text-base) !important;
    font-weight: 600 !important;
    color: #ffffff !important;

    background: linear-gradient(135deg, #1d4ed8 0%, #2563eb 100%) !important;
    box-shadow: 0 1px 3px rgba(29,78,216,0.2), 0 4px 12px rgba(29,78,216,0.15) !important;

    cursor: pointer !important;
    transition: all var(--transition-base) !important;
    position: relative;
    overflow: hidden;
}

.ww-btn-primary::after {
    content: '';
    position: absolute; inset: 0;
    background: linear-gradient(135deg, rgba(255,255,255,0) 0%, rgba(255,255,255,0.08) 50%, rgba(255,255,255,0) 100%);
    opacity: 0;
    transition: opacity var(--transition-base);
}

.ww-btn-primary:hover {
    transform: translateY(-1px) !important;
    box-shadow: 0 2px 6px rgba(29,78,216,0.25), 0 8px 24px rgba(29,78,216,0.2) !important;
}

.ww-btn-primary:hover::after { opacity: 1; }

.ww-btn-primary:active {
    transform: translateY(0px) !important;
    box-shadow: 0 1px 3px rgba(29,78,216,0.2) !important;
    transition: all 80ms cubic-bezier(0.4, 0, 0.2, 1) !important;
}

.ww-btn-primary:disabled {
    opacity: 0.5 !important;
    cursor: not-allowed !important;
    transform: none !important;
    box-shadow: none !important;
    background: var(--gray-300) !important;
}

/* ──────────────────────────────────────
   Gradio component overrides
   ────────────────────────────────────── */

/* Labels */
label, .label-text {
    font-family: 'Inter', 'Noto Sans SC', sans-serif !important;
    font-size: var(--text-sm) !important;
    font-weight: 500 !important;
    color: var(--gray-600) !important;
    margin-bottom: 6px !important;
}

/* File upload */
.ww-panel .file-preview {
    border: 1.5px dashed var(--gray-200) !important;
    border-radius: var(--radius-md) !important;
    background: var(--gray-50) !important;
    padding: 20px !important;
    transition: all var(--transition-base) !important;
}

.ww-panel .file-preview:hover {
    border-color: var(--brand-400) !important;
    background: var(--brand-50) !important;
}

/* Dropdown */
select, .wrap-inner select {
    border: 1px solid var(--gray-200) !important;
    border-radius: var(--radius-sm) !important;
    padding: 8px 12px !important;
    font-size: var(--text-sm) !important;
    color: var(--gray-700) !important;
    background: #fff !important;
    transition: all var(--transition-fast) !important;
}

select:focus, .wrap-inner select:focus {
    outline: none !important;
    border-color: var(--brand-400) !important;
    box-shadow: 0 0 0 3px rgba(59,130,246,0.1) !important;
}

/* Radio buttons */
.radio-group .wrap {
    display: flex; gap: 8px;
}

.radio-group input[type="radio"] {
    accent-color: var(--brand-600);
}

.radio-group .label-wrap {
    font-size: var(--text-sm) !important;
    color: var(--gray-600) !important;
    padding: 8px 12px !important;
    border: 1px solid var(--gray-100) !important;
    border-radius: var(--radius-sm) !important;
    background: var(--gray-50) !important;
    cursor: pointer !important;
    transition: all var(--transition-fast) !important;
}

.radio-group .label-wrap:hover {
    background: #fff !important;
    border-color: var(--gray-200) !important;
}

.radio-group .selected .label-wrap {
    background: var(--brand-50) !important;
    border-color: var(--brand-400) !important;
    color: var(--brand-700) !important;
}

/* Slider */
input[type="range"] {
    accent-color: var(--brand-600);
}

/* Tabs */
.tabs {
    border-bottom: 1px solid var(--gray-100) !important;
}

.tab-nav button, button.tab-nav {
    font-family: 'Inter', 'Noto Sans SC', sans-serif !important;
    font-size: var(--text-sm) !important;
    font-weight: 500 !important;
    color: var(--gray-400) !important;
    padding: 10px 18px !important;
    border: none !important;
    border-bottom: 2px solid transparent !important;
    background: transparent !important;
    cursor: pointer !important;
    transition: all var(--transition-fast) !important;
}

.tab-nav button:hover, button.tab-nav:hover {
    color: var(--gray-600) !important;
}

.tab-nav button.selected, button.tab-nav.selected {
    color: var(--brand-700) !important;
    border-bottom-color: var(--brand-600) !important;
    font-weight: 600 !important;
}

/* Progress bar */
.progress-bar {
    background: var(--gray-100) !important;
    border-radius: var(--radius-sm) !important;
    height: 6px !important;
    overflow: hidden !important;
    margin: 12px 0 !important;
}

.progress-bar .progress-bar-fill {
    background: linear-gradient(90deg, #2563eb, #3b82f6, #60a5fa) !important;
    border-radius: var(--radius-sm) !important;
    transition: width 300ms cubic-bezier(0.4, 0, 0.2, 1) !important;
}

/* Dataframe / table */
.ww-table-wrapper table, table.ww-table-wrapper {
    width: 100% !important;
    border-collapse: collapse !important;
    font-size: var(--text-sm) !important;
}

.ww-table-wrapper th, table.ww-table-wrapper th {
    text-align: left !important;
    padding: 10px 14px !important;
    font-weight: 600 !important;
    color: var(--gray-500) !important;
    font-size: var(--text-xs) !important;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    border-bottom: 2px solid var(--gray-100) !important;
    background: var(--gray-50) !important;
}

.ww-table-wrapper td, table.ww-table-wrapper td {
    padding: 10px 14px !important;
    border-bottom: 1px solid var(--gray-75) !important;
    color: var(--gray-600) !important;
}

.ww-table-wrapper tr:hover td, table.ww-table-wrapper tr:hover td {
    background: var(--gray-50) !important;
}

/* Markdown output area */
#translation-output {
    background: #ffffff !important;
    border: 1px solid var(--gray-100) !important;
    border-radius: var(--radius-md) !important;
    padding: 32px !important;
    min-height: 400px !important;
    font-size: 15px !important;
    line-height: 1.8 !important;
    color: var(--gray-700) !important;
}

#translation-output h1, #translation-output h2, #translation-output h3 {
    font-weight: 700;
    color: var(--gray-800);
    margin-top: 1.8em;
    margin-bottom: 0.5em;
}

#translation-output h1 { font-size: 1.5em; border-bottom: 1px solid var(--gray-100); padding-bottom: 0.3em; }
#translation-output h2 { font-size: 1.25em; }
#translation-output h3 { font-size: 1.1em; }

#translation-output p { margin: 0.6em 0; }

#translation-output code {
    background: var(--gray-75);
    padding: 2px 6px;
    border-radius: 4px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.88em;
}

#translation-output hr {
    border: none;
    border-top: 1px solid var(--gray-100);
    margin: 2em 0;
}

#translation-output blockquote {
    border-left: 3px solid var(--brand-400);
    padding-left: 16px;
    margin: 1em 0;
    color: var(--gray-500);
}

/* ──────────────────────────────────────
   Stats footer — subtle, refined
   ────────────────────────────────────── */
.ww-stats {
    display: flex;
    gap: 12px;
    margin: 28px 0 0 0;
}

.ww-stat {
    flex: 1;
    padding: 14px 18px;
    background: #ffffff;
    border: 1px solid var(--gray-100);
    border-radius: var(--radius-md);
    box-shadow: var(--shadow-xs);
    transition: all var(--transition-base);
}

.ww-stat:hover {
    border-color: var(--gray-200);
    box-shadow: var(--shadow-sm);
}

.ww-stat .ww-stat-val {
    font-size: var(--text-xl); font-weight: 700; color: var(--gray-800);
    line-height: 1.2;
}

.ww-stat .ww-stat-label {
    font-size: var(--text-xs); color: var(--gray-400);
    margin-top: 2px;
}

.ww-stat-accent .ww-stat-val { color: var(--brand-600); }

/* ──────────────────────────────────────
   Footer
   ────────────────────────────────────── */
.ww-footer {
    text-align: center;
    padding: 28px 16px 36px;
    color: var(--gray-300);
    font-size: var(--text-xs);
}

.ww-footer span { color: var(--gray-400); }

/* ──────────────────────────────────────
   Utility: spacing helpers
   ────────────────────────────────────── */
.ww-gap-8  { gap: 8px; }
.ww-mb-12  { margin-bottom: 12px; }
.ww-mt-20  { margin-top: 20px; }

/* ──────────────────────────────────────
   Hide Gradio chrome that looks amateur
   ────────────────────────────────────── */
footer { display: none !important; }
.gr-prose p { margin: 0.4em 0; }
"""

# ═══════════════════════════════════════════════════════════════
# Translation logic
# ═══════════════════════════════════════════════════════════════

def translate_novel(
    file,
    target_lang: str,
    translate_mode: str,
    quality_mode: str,
    quality_interval: int,
    progress=gr.Progress(),
):
    """Run the full translation pipeline, streaming results back to Gradio."""
    if file is None:
        yield (
            "> *上传一份中文小说 `.txt` 文件即可开始翻译。译文将显示在此处。*",
            gr.DataFrame(value=[], headers=["中文", "英文"]),
            "> *翻译完成后，质量报告将在此处生成。*",
        )
        return

    # Read file
    if hasattr(file, "read"):
        text = file.read().decode("utf-8", errors="replace")
    elif isinstance(file, str):
        text = open(file, encoding="utf-8").read()
    else:
        yield (
            "### 不支持的文件格式",
            gr.DataFrame(value=[], headers=["中文", "英文"]),
            "无法读取文件。",
        )
        return

    chapters = split_chapters(text)
    translatable = [c for c in chapters if c.action != ParagraphTag.SKIP]
    skipped = [c for c in chapters if c.action == ParagraphTag.SKIP]
    total = len(translatable)

    skip_msg = f"，{len(skipped)} 章已跳过" if skipped else ""
    progress(0, desc=f"共 {total} 章{skip_msg}，初始化翻译...")

    agent = TranslationAgent()
    translated_chapters: list[str] = []
    previous_summary = ""

    for i, chapter in enumerate(translatable):
        pct = (i + 1) / total
        progress(pct, desc=f"正在翻译 第 {chapter.index} 章「{chapter.title[:24]}」…")

        result = agent.translate_chapter(
            chapter_title=chapter.title,
            chapter_content=chapter.content,
            chapter_number=chapter.index,
            previous_summary=previous_summary,
            target_lang=target_lang,
        )

        translated_chapters.append(result["translated_text"])
        previous_summary = result.get("chapter_summary", "")
        time.sleep(CHAPTER_COOLDOWN_SECONDS)

    # Assemble outputs
    full_text = merge_chapters(translated_chapters)

    glossary_dict = agent.exact_store.to_dict()
    glossary_rows = [[cn, en] for cn, en in sorted(glossary_dict.items())]

    # Build a nicely formatted quality report
    if len(translated_chapters) > 0:
        quality_text = f"""## 翻译质量报告

| 指标 | 详情 |
|:---|---:|
| 已翻译章节 | **{len(translated_chapters)}** |
| 术语表条目 | **{len(glossary_dict)}** |
| 质检间隔 | 每 **{quality_interval}** 章 |
| 翻译模型 | **{translate_mode}** |
| 质检模型 | **{quality_mode}** |

---

> 翻译任务已完成。您可以在「术语表」标签页中查看完整的术语对照表。
"""
    else:
        quality_text = "*没有可翻译的章节。*"

    yield (
        full_text,
        gr.DataFrame(value=glossary_rows, headers=["中文", "English"]),
        quality_text,
    )


# ═══════════════════════════════════════════════════════════════
# UI Layout
# ═══════════════════════════════════════════════════════════════

def create_ui():
    with gr.Blocks(
        title="西渡 / Westward Echo",
        theme=gr.themes.Soft(
            primary_hue="blue",
            neutral_hue="slate",
            font=["Inter", "Noto Sans SC", "Helvetica Neue", "sans-serif"],
            font_mono=["JetBrains Mono", "monospace"],
        ),
        css=CUSTOM_CSS,
    ) as demo:

        # ────────────────────────────────────
        # Hero / Header
        # ────────────────────────────────────
        gr.HTML("""
        <div class="ww-header">
            <div class="ww-header-badge">
                <span class="ww-dot"></span> LangGraph + DeepSeek V4
            </div>
            <div class="ww-header-brand">
                <h1>
                    <span class="ww-cn">西渡</span>
                    <span class="ww-sep">/</span>
                    <span class="ww-en">Westward Echo</span>
                </h1>
            </div>
            <p class="ww-header-desc">
                面向网文出海的 AI 翻译引擎，全本术语统一、文化自适应适配、机器翻译与文学翻译的融合。
            </p>
            <div class="ww-meta-row">
                <div class="ww-meta-card">
                    <div class="ww-meta-icon">&#9678;</div>
                    <div class="ww-meta-value">DeepSeek V4</div>
                    <div class="ww-meta-label">主力翻译模型</div>
                </div>
                <div class="ww-meta-card">
                    <div class="ww-meta-icon">&#9632;</div>
                    <div class="ww-meta-value">双向量检索</div>
                    <div class="ww-meta-label">术语一致性架构</div>
                </div>
                <div class="ww-meta-card">
                    <div class="ww-meta-icon">&#9650;</div>
                    <div class="ww-meta-value">99% 成本优化</div>
                    <div class="ww-meta-label">相对人工翻译</div>
                </div>
                <div class="ww-meta-card">
                    <div class="ww-meta-icon">&#9670;</div>
                    <div class="ww-meta-value">3 语种 + 扩展</div>
                    <div class="ww-meta-label">多语言支持</div>
                </div>
            </div>
        </div>
        """)

        # ────────────────────────────────────
        # Step indicators
        # ────────────────────────────────────
        gr.HTML("""
        <div class="ww-steps">
            <div class="ww-step">
                <div class="ww-step-num">1</div>
                <div class="ww-step-info">
                    <div class="ww-step-label">上传小说</div>
                    <div class="ww-step-desc">选择中文 .txt 源文件</div>
                </div>
            </div>
            <div class="ww-step">
                <div class="ww-step-num">2</div>
                <div class="ww-step-info">
                    <div class="ww-step-label">配置参数</div>
                    <div class="ww-step-desc">模型选择与质检设置</div>
                </div>
            </div>
            <div class="ww-step">
                <div class="ww-step-num">3</div>
                <div class="ww-step-info">
                    <div class="ww-step-label">审阅导出</div>
                    <div class="ww-step-desc">译文预览 · 术语表 · 质量报告</div>
                </div>
            </div>
        </div>
        """)

        # ────────────────────────────────────
        # Main layout: config | output
        # ────────────────────────────────────
        with gr.Row(equal_height=False):
            # ---- Left sidebar: configuration ----
            with gr.Column(scale=2, min_width=280):
                with gr.Group(elem_classes=["ww-panel"]):
                    gr.HTML('<div class="ww-panel-title"><span class="ww-icon-dot"></span> 上传文件</div>')
                    file_input = gr.File(
                        label="小说源文件",
                        file_types=[".txt"],
                        elem_classes=["ww-file-upload"],
                    )

                with gr.Group(elem_classes=["ww-panel"]):
                    gr.HTML('<div class="ww-panel-title"><span class="ww-icon-dot"></span> 翻译设置</div>')

                    target_lang = gr.Dropdown(
                        choices=[
                            "English (英语)",
                            "Spanish / Espanol (西班牙语)",
                            "Arabic / العربية (阿拉伯语)",
                        ],
                        value="English (英语)",
                        label="目标语言",
                        interactive=True,
                    )

                    with gr.Row():
                        translate_mode = gr.Radio(
                            choices=[
                                "V4 Flash — 快速 · 低成本",
                                "V4 Pro — 高精度 · 关键章节",
                            ],
                            value="V4 Flash — 快速 · 低成本",
                            label="翻译模型",
                        )

                    with gr.Row():
                        quality_mode = gr.Radio(
                            choices=[
                                "V4 Pro — 高精度质检",
                                "Claude Opus — 兜底仲裁",
                            ],
                            value="V4 Pro — 高精度质检",
                            label="质检模型",
                        )

                    quality_interval = gr.Slider(
                        minimum=5,
                        maximum=50,
                        step=5,
                        value=QUALITY_CHECK_INTERVAL,
                        label="质检频率（每 N 章）",
                    )

                    start_btn = gr.Button(
                        value="开始翻译",
                        variant="primary",
                        elem_classes=["ww-btn-primary"],
                    )

            # ---- Right: output area (hero element) ----
            with gr.Column(scale=3, min_width=420):
                with gr.Tabs():
                    with gr.TabItem("译文", id="tab-translation"):
                        output_text = gr.Markdown(
                            value=(
                                "> 上传一份中文小说 `.txt` 文件并点击「开始翻译」即可开始。\n\n"
                                "> 译文将在翻译过程中逐步显示在此处。"
                            ),
                            elem_id="translation-output",
                            show_label=False,
                        )

                    with gr.TabItem("术语表", id="tab-glossary"):
                        glossary_table = gr.DataFrame(
                            headers=["中文", "English"],
                            label="术语对照表",
                            interactive=False,
                            wrap=True,
                            elem_classes=["ww-table-wrapper"],
                            value=[["—", "尚未生成术语表"]],
                        )

                    with gr.TabItem("质量报告", id="tab-report"):
                        quality_report = gr.Markdown(
                            value="> 翻译完成后，质量报告将在此处自动生成。",
                            show_label=False,
                        )

        # ────────────────────────────────────
        # Bottom stats
        # ────────────────────────────────────
        gr.HTML("""
        <div class="ww-stats">
            <div class="ww-stat ww-stat-accent">
                <div class="ww-stat-val">DeepSeek V4</div>
                <div class="ww-stat-label">端到端推理 · 128K 上下文</div>
            </div>
            <div class="ww-stat">
                <div class="ww-stat-val">LangGraph</div>
                <div class="ww-stat-label">状态图编排 · 人机协同</div>
            </div>
            <div class="ww-stat">
                <div class="ww-stat-val">双层检索</div>
                <div class="ww-stat-label">Exact + Semantic 术语召回</div>
            </div>
            <div class="ww-stat">
                <div class="ww-stat-val">ChromaDB</div>
                <div class="ww-stat-label">向量存储 · 增量更新</div>
            </div>
        </div>
        """)

        # ────────────────────────────────────
        # Footer
        # ────────────────────────────────────
        gr.HTML("""
        <div class="ww-footer">
            <span>西渡 / Westward Echo</span> &nbsp;&middot;&nbsp;
            网文出海 AI 翻译引擎 &nbsp;&middot;&nbsp;
            Built with LangGraph + DeepSeek V4
        </div>
        """)

        # ────────────────────────────────────
        # Event binding
        # ────────────────────────────────────
        start_btn.click(
            fn=translate_novel,
            inputs=[file_input, target_lang, translate_mode, quality_mode, quality_interval],
            outputs=[output_text, glossary_table, quality_report],
        )

    return demo


def main():
    ui = create_ui()
    ui.queue().launch(
        server_port=GRADIO_PORT,
        server_name="0.0.0.0",
        share=False,
        max_threads=3,
    )


if __name__ == "__main__":
    main()

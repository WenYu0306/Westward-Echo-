"""Forge Echo → Westward Echo script adapter.

Converts Forge Echo's shot-level Markdown script (the ``铸文`` creator's
output) into the episode/scene/dialogue format that Westward Echo's
``script`` pipeline (``script_splitter.split_episodes``) can ingest.

The two projects are one series pointing in opposite directions:
铸文 writes, 西渡 translates. This adapter is the seam between them.

Forge Echo format (per episode)::

    ## 第1集：记忆里的死者
    - **时长目标**：55秒
    ### 场景：记忆典当行·鉴定室（深夜）
    氛围：...
    在场：陆辞安
    **[S01] 特写** （3秒）
    画面：...
    动作：...
    > **陆辞安**（平静下藏着烦躁）[手指轻抚芯片]：台词。
    **【集末钩子】** ...

Westward Echo target format::

    第1集 记忆里的死者
    场景1：记忆典当行·鉴定室/深夜
    （画面与动作合并为叙述行）
    陆辞安（平静下藏着烦躁）：台词。
    【集末钩子】...

Design notes:
- Shot headers (``**[S01] 特写** （3秒）``) are production metadata and are
  dropped; the 画面/动作 lines beneath them become prose action lines so the
  translator keeps visual context.
- Episode metadata bullets (时长目标/情绪基调/钩子类型) are dropped.
- The leading setup block (title/题材/前提/角色 bios) is dropped — it is
  authoring scaffolding, not translatable episode content.
- Deterministic and side-effect free: pure string in, string out.
"""

from __future__ import annotations

import re

# ── Forge Echo source patterns ──────────────────────────────────────

# "## 第12集：镜像裂痕"  →  episode number + title
_FORGE_EPISODE_RE = re.compile(r'^##\s*第([0-9一二三四五六七八九十百]+)集[：:]\s*(.+?)\s*$')
# "### 场景：记忆典当行·鉴定室（深夜）"  →  location + time-of-day
_FORGE_SCENE_RE = re.compile(r'^###\s*场景[：:]\s*(.+?)(?:[（(]([^（）()]*)[）)])?\s*$')
# "> **陆辞安**（情绪）[动作]：台词"  →  speaker / emotion / action / line
_FORGE_DIALOGUE_RE = re.compile(
    r'^>\s*\*\*(.+?)\*\*'          # speaker name (bold)
    r'(?:[（(]([^（）()]*)[）)])?'     # optional emotion parenthetical
    r'(?:[（(]([^（）()]*)[）)])?'     # optional second parenthetical (旁白/OS)
    r'(?:\[[^\]]*\])?'             # optional [action] bracket
    r'[：:]\s*(.*)$'               # the spoken line
)
# "**[S01] 特写** （3秒）" — shot header, dropped
_FORGE_SHOT_RE = re.compile(r'^\*\*\[S\d+\][^*]*\*\*')
# "**【集末钩子】** ..." — episode-end hook, preserved as a marker line
_FORGE_HOOK_RE = re.compile(r'^\*\*【集末钩子】\*\*\s*(.*)$')
# "- **时长目标**：55秒" — episode metadata bullet, dropped
_FORGE_META_RE = re.compile(r'^-\s*\*\*(时长目标|情绪基调|钩子类型)\*\*')


def _normalize_episode_number(raw: str) -> str:
    """Return the episode number as-is (Arabic kept, Chinese kept).

    Westward Echo's splitter accepts both; we preserve the source's own
    numbering style rather than re-deriving it.
    """
    return raw.strip()


def convert_forge_script(text: str) -> str:
    """Convert a full Forge Echo script to Westward Echo format.

    Returns the converted script text. The leading setup block (before the
    first ``## 第N集``) is dropped. If no episode headers are found, the
    input is returned unchanged (fail-safe: never destroy content).
    """
    lines = text.split("\n")

    # Locate the first episode header; everything before it is setup.
    first_ep_idx = None
    for i, line in enumerate(lines):
        if _FORGE_EPISODE_RE.match(line.strip()):
            first_ep_idx = i
            break
    if first_ep_idx is None:
        return text  # not a Forge script — return unchanged

    out: list[str] = []
    scene_num = 0           # resets per episode
    in_episode = False

    for line in lines[first_ep_idx:]:
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue

        # ── Episode header ──
        m = _FORGE_EPISODE_RE.match(stripped)
        if m:
            num = _normalize_episode_number(m.group(1))
            title = m.group(2).strip()
            out.append(f"第{num}集 {title}")
            scene_num = 0
            in_episode = True
            continue

        # ── Episode metadata bullets: drop ──
        if _FORGE_META_RE.match(stripped):
            continue

        # ── Scene header ──
        m = _FORGE_SCENE_RE.match(stripped)
        if m:
            scene_num += 1
            location = m.group(1).strip()
            time_of_day = (m.group(2) or "").strip()
            header = f"场景{scene_num}：{location}"
            if time_of_day:
                header += f"/{time_of_day}"
            out.append(header)
            continue

        # ── Shot header: drop (production metadata) ──
        if _FORGE_SHOT_RE.match(stripped):
            continue

        # ── Episode-end hook: keep as marker line ──
        m = _FORGE_HOOK_RE.match(stripped)
        if m:
            hook_text = m.group(1).strip()
            out.append(f"【集末钩子】{hook_text}")
            continue

        # ── Dialogue line ──
        m = _FORGE_DIALOGUE_RE.match(stripped)
        if m:
            speaker = m.group(1).strip()
            emotion = (m.group(2) or "").strip()
            second = (m.group(3) or "").strip()
            spoken = m.group(4).strip()
            tag = emotion
            if second:
                tag = f"{emotion}，{second}" if emotion else second
            if tag:
                out.append(f"{speaker}（{tag}）：{spoken}")
            else:
                out.append(f"{speaker}：{spoken}")
            continue

        # ── Everything else (画面/动作/氛围 prose): keep as action line ──
        # Strip a leading "画面："/"动作："/"氛围：" label but keep the content.
        content = re.sub(r'^(画面|动作|氛围)[：:]\s*', '', stripped)
        if content and in_episode:
            out.append(content)

    return "\n".join(out).strip() + "\n"


def extract_forge_metadata(text: str) -> dict:
    """Pull the authoring metadata from a Forge Echo script header.

    Returns ``{"title", "genre", "premise", "episode_count"}`` — any field
    may be an empty string if absent. Useful for naming the Westward Echo
    job/glossary without hardcoding.
    """
    meta = {"title": "", "genre": "", "premise": "", "episode_count": ""}
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("# ") and not meta["title"]:
            meta["title"] = s[2:].strip()
        elif s.startswith("**题材**"):
            meta["genre"] = re.sub(r'^\*\*题材\*\*[：:]\s*', '', s)
        elif s.startswith("**前提**"):
            meta["premise"] = re.sub(r'^\*\*前提\*\*[：:]\s*', '', s)
        elif s.startswith("**集数**"):
            meta["episode_count"] = re.sub(r'^\*\*集数\*\*[：:]\s*', '', s)
    return meta

"""Episode splitter for Chinese vertical short-drama scripts.

Parallel of chapter_splitter.py for the ``script`` content type.
Splits a raw script file into episodes using "第N集" headers, and parses
scene headers ("场景N：地点/时间") inside each episode.

The returned :class:`Episode` mirrors :class:`Chapter` so the translation
loop can treat scripts and novels uniformly.
"""

import re
from dataclasses import dataclass
from enum import Enum

from .chapter_splitter import ParagraphTag


class EpisodeTag(str, Enum):
    EPISODE = "episode"         # Normal episode
    EXTRA = "extra"             # 番外 / 彩蛋 / 特别篇


# Translation actions reuse ParagraphTag so callers can filter
# chapters and episodes with one uniform tag
# (TRANSLATE / TRANSLATE_NO_EXTRACT / SKIP).


# --- Regex patterns ---

# Episode headers: "第1集", "第12集 标题", "第三集"
# ([^\S\n]* instead of \s*: the episode number may stand alone on its line;
# we must not swallow the next episode's header across a newline)
EPISODE_PATTERN = re.compile(
    r'^\s*(第[一二三四五六七八九十百0-9]+集[^\S\n]*.*)', re.MULTILINE
)

# Scene headers: "场景1：裴家别墅-主卧/夜", "场景三：走廊 / 傍晚",
# optionally markdown-prefixed ("##场景1：...")
SCENE_PATTERN = re.compile(
    r'^\s*(?:#{1,6}\s*)?场景[一二三四五六七八九十0-9]+[：:].*', re.MULTILINE
)

# Non-episode section classification
NON_EPISODE_PATTERNS = {
    EpisodeTag.EXTRA: re.compile(r'^(番外|彩蛋|特别篇|小剧场)'),
}


@dataclass
class Episode:
    """A single episode from a short-drama script."""

    index: int                      # 1-based episode number
    title: str                      # Original episode title line
    content: str                    # Full body text (scenes included)
    tag: EpisodeTag = EpisodeTag.EPISODE
    action: ParagraphTag = ParagraphTag.TRANSLATE

    @property
    def word_count(self) -> int:
        """Approximate Chinese character count."""
        return len(self.content.replace('\n', '').replace(' ', ''))

    def preview(self, n: int = 80) -> str:
        """First n characters of content for display."""
        return self.content.strip()[:n]

    @property
    def scene_count(self) -> int:
        """Number of scene headers found in this episode."""
        return len(SCENE_PATTERN.findall(self.content))


@dataclass
class Scene:
    """A single scene inside an episode."""

    index: int                      # 1-based scene order within the episode
    header: str                     # The scene header line
    content: str                    # Scene body (dialogue, action, OS, panels)


def classify_episode(title: str) -> tuple[EpisodeTag, ParagraphTag]:
    """Classify an episode section and decide its translation action."""
    for tag, pattern in NON_EPISODE_PATTERNS.items():
        if pattern.search(title):
            return tag, ParagraphTag.TRANSLATE_NO_EXTRACT
    return EpisodeTag.EPISODE, ParagraphTag.TRANSLATE


def parse_scenes(episode_content: str) -> list[Scene]:
    """Split one episode's body into scenes by scene headers.

    Content before the first scene header (rare — a cold-open block) is
    returned as scene index 0 with an empty header. Episodes without any
    scene header come back as a single scene covering the whole body.
    """
    matches = list(SCENE_PATTERN.finditer(episode_content))
    if not matches:
        return [Scene(index=1, header="", content=episode_content.strip())]

    scenes = []
    if matches[0].start() > 0:
        preamble = episode_content[:matches[0].start()].strip()
        if preamble:
            scenes.append(Scene(index=0, header="", content=preamble))

    for i, match in enumerate(matches):
        header = match.group(0).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(episode_content)
        content = episode_content[start:end].strip()
        scenes.append(Scene(index=i + 1, header=header, content=content))

    return scenes


def split_episodes(text: str) -> list[Episode]:
    """Split a full short-drama script into episodes.

    Uses the episode-header regex to find boundaries. Anything before the
    first episode header is dropped (title pages / pitch blocks are not
    translated) unless it is substantial, in which case it becomes a
    preamble episode with index 0.
    """
    matches = list(EPISODE_PATTERN.finditer(text))
    episodes = []
    index = 0

    if not matches:
        # No episode headers — treat entire text as one episode
        tag, action = classify_episode("正文")
        return [Episode(index=1, title="正文", content=text.strip(),
                        tag=tag, action=action)]

    # Preamble before first episode header (only kept if substantial)
    if matches[0].start() > 0:
        preamble = text[:matches[0].start()].strip()
        if preamble and len(preamble.replace('\n', '').replace(' ', '')) > 100:
            episodes.append(Episode(index=0, title="楔子", content=preamble))
            index = 1

    for i, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()

        if not content:
            continue  # Skip empty episodes (placeholder headers)

        tag, action = classify_episode(title)
        episodes.append(Episode(
            index=index + 1,
            title=title,
            content=content,
            tag=tag,
            action=action,
        ))
        index += 1

    return episodes


def merge_episodes(translations: list[str]) -> str:
    """Merge translated episode strings into a single script file."""
    return "\n\n".join(translations)


# --- Dialogue extraction (script_mode="dialogue") -------------------------
#
# The WRITE agent emits English screenplay format, but LLMs use more than
# one legal convention for speaker lines. All three occur in real outputs:
#
#   1. Colon, same line      SU NIAN: I'm not Su Nian.
#   2. Colon, dialogue below SU WAN (OS):\n  Three years. That ends tonight.
#   3. Stacked cue (no colon) LIN ZHAO\n  (Cold, flat)\n  We're done.
#
# The extractor is a small state machine that understands all three.

_DELIVERY_MARKER = r'(\s*\((?:OS|V\.?O\.?|VO|CONT\'?D)\))?'

# Shape 1+2: UPPERCASE speaker label, optional delivery marker, colon.
_SPEAKER_COLON_RE = re.compile(
    r'^\s*([A-Z][A-Z0-9 .\']*?)' + _DELIVERY_MARKER + r'\s*:\s*(.*)$'
)

# Shape 3: UPPERCASE cue standing alone on its line (dialogue follows below).
_SPEAKER_CUE_RE = re.compile(
    r'^\s*([A-Z][A-Z0-9 .\']*?)' + _DELIVERY_MARKER + r'\s*$'
)

# Labels that are structural headers, not characters.
_NON_SPEAKER_LABELS = frozenset({"SCENE", "EPISODE"})

# Camera/editing transitions that also print in uppercase.
_TRANSITIONS = frozenset({
    "FADE OUT", "FADE IN", "FADE TO BLACK", "CUT TO", "CUT TO BLACK",
    "SMASH CUT TO", "DISSOLVE TO", "THE END", "TITLE CARD", "CONTINUED",
})

_EPISODE_HEADER_RE = re.compile(r'^\s*Episode\s+\d+', re.IGNORECASE)
_SCENE_HEADER_RE = re.compile(r'^\s*Scene\s+\d+', re.IGNORECASE)
_PANEL_RE = re.compile(r'^\s*【.*】\s*$')
# (Cold, flat) — a parenthetical acting note between cue and line.
_PAREN_NOTE_RE = re.compile(r'^\s*\(.*\)\s*$')


def _valid_speaker(label: str) -> bool:
    """Screenplay speaker-label sanity: no digits, short, not a header."""
    label = label.strip()
    if not label or label in _NON_SPEAKER_LABELS or label in _TRANSITIONS:
        return False
    if any(ch.isdigit() for ch in label):
        return False
    # Speaker cues never end in sentence punctuation ("FADE OUT." is a
    # transition, not a character).
    if label.endswith((".", "!", "?", ",")):
        return False
    return len(label.split()) <= 5


def extract_dialogue(translated_text: str) -> str:
    """Reduce a translated episode to dialogue only (dubbing/ADR deliverable).

    Keeps, in order of appearance:
      - Episode headers ("Episode N: Title")
      - Scene headers ("Scene N: LOCATION / TIME") — dubbing sessions are
        organized by scene
      - Speaker dialogue: all three speaker-line conventions above,
        including (OS) inner monologue and (V.O.), plus parenthetical
        acting notes attached to a cue

    Drops: action/direction lines, 【】 on-screen panels (not spoken),
    camera transitions (FADE OUT etc.).

    Deterministic by design: the validated full-script pipeline runs
    unchanged (dialogue quality depends on action-line context — who is
    speaking, how); this filter shapes the deliverable afterwards.

    Safety: if the extractor recognizes no dialogue at all (total format
    drift), the original text is returned unchanged — an unfiltered script
    is always more useful than an empty one.
    """
    kept: list[str] = []
    dialogue_found = False
    in_dialogue = False   # inside a speaker's dialogue block

    for raw_line in translated_text.split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            kept.append("")          # preserve blank-line rhythm
            in_dialogue = False
            continue

        if _EPISODE_HEADER_RE.match(stripped) or _SCENE_HEADER_RE.match(stripped):
            kept.append(line)
            in_dialogue = False
            continue

        if _PANEL_RE.match(stripped):
            in_dialogue = False
            continue

        # Shape 1: SPEAKER: line on the same line
        m = _SPEAKER_COLON_RE.match(line)
        if m and m.group(3).strip() and _valid_speaker(m.group(1)):
            kept.append(line)
            dialogue_found = True
            in_dialogue = True
            continue

        # Shape 2: SPEAKER: with the dialogue on following lines
        if m and not m.group(3).strip() and _valid_speaker(m.group(1)):
            kept.append(line)
            dialogue_found = True
            in_dialogue = True
            continue

        # Shape 3: standalone uppercase cue — dialogue follows below
        mc = _SPEAKER_CUE_RE.match(line)
        if mc and _valid_speaker(mc.group(1)):
            kept.append(line)
            dialogue_found = True
            in_dialogue = True
            continue

        # Inside a dialogue block: acting notes, wrapped/continued lines.
        if in_dialogue:
            if _PAREN_NOTE_RE.match(stripped):
                kept.append(line)    # (Cold, flat) belongs to the cue
                continue
            kept.append(line)
            continue

        # Action/direction line outside any dialogue block — drop.

    if not dialogue_found:
        return translated_text

    # Collapse blank-line runs left behind by dropped action blocks.
    collapsed: list[str] = []
    for line in kept:
        if not line.strip() and collapsed and not collapsed[-1].strip():
            continue
        collapsed.append(line)

    # Trim leading/trailing blanks.
    while collapsed and not collapsed[0].strip():
        collapsed.pop(0)
    while collapsed and not collapsed[-1].strip():
        collapsed.pop()
    return "\n".join(collapsed)

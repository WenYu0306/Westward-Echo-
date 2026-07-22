"""Onomatopoeia detection and English equivalents for Chinese web novels.

Maps common Chinese sound words to natural English equivalents so the LLM
can choose context-appropriate translations. Injected as a context hint
alongside the dialect context during translation.
"""

from typing import Optional

ONOMATOPOEIA_MAP: dict[str, str] = {
    # Nature sounds
    "哗啦啦": "whoosh / splash / rustle (choose by context: water=splash, wind=whoosh, leaves=rustle)",
    "轰隆隆": "rumble / boom (thunder=rumble, explosion=boom)",
    "呼呼": "whoosh / howl (wind=howl, breathing=wheeze)",
    "淅淅沥沥": "pitter-patter (rain)",
    "沙沙": "rustle (leaves, fabric)",
    "滴滴答答": "drip-drop / tick-tock (water=drip, clock=tick)",

    # Action sounds
    "啪": "slap / crack / snap (hit=slap, break=crack, finger=snap)",
    "砰": "bang / thud (door=bang, bodyfall=thud)",
    "咔嚓": "crack / snap / click (break=crack, photo=click)",
    "嗖": "whoosh / zip (fast movement, like an arrow or person dashing)",
    "咣当": "clang / crash (metal=clang, fall=crash)",
    "叮": "ting / ping (small bell=ting, notification=ping)",

    # Speech/body sounds
    "噗": "pfft (disbelief) / splurt (liquid) / thump (impact)",
    "咳咳": "ahem / cough cough",
    "咕噜": "gurgle / rumble (stomach=growl, water=gurgle)",
    "吧唧": "smack / chomp (eating sounds)",
    "嘎吱": "creak (door, floorboard)",
    "咯噔": "thump (heartbeat skip, sudden dread)",
}


def detect_onomatopoeia(text: str) -> list[str]:
    """Return list of Chinese onomatopoeia found in text.

    Each entry is the matched Chinese sound word. Duplicates are preserved
    (each occurrence is listed separately) so the caller can gauge frequency.
    """
    found: list[str] = []
    for sound in ONOMATOPOEIA_MAP:
        # Count occurrences so the LLM knows how many times a sound appears
        count = text.count(sound)
        found.extend([sound] * count)
    return found


def build_onomatopoeia_context(text: str) -> str:
    """Build context hints for the LLM about onomatopoeia in the source text.

    Returns an empty string if no known onomatopoeia are found.
    """
    found = detect_onomatopoeia(text)
    if not found:
        return ""

    # De-duplicate for display but preserve count info
    unique = sorted(set(found), key=lambda s: -found.count(s))
    lines = []
    for sound in unique:
        count = found.count(sound)
        hint = ONOMATOPOEIA_MAP[sound]
        if count > 1:
            lines.append(f"- **{sound}** (appears {count}x): {hint}")
        else:
            lines.append(f"- **{sound}**: {hint}")

    header = (
        "## ONOMATOPOEIA CONTEXT\n"
        "The source text contains Chinese sound words (onomatopoeia). "
        "Use the suggested English equivalents, choosing the variant that "
        "best fits the scene context.\n\n"
    )
    return header + "\n".join(lines)

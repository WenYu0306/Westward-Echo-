"""Convert a Forge Echo (铸文) script into Westward Echo (西渡) ingest format.

Usage:
    python3 scripts/convert_forge_to_westward.py <forge_script.md> [output.txt]

Default output: pilots/<name>/<name>_westward.txt

This is the seam between the two halves of the Echo series:
铸文 writes the script, 西渡 translates it. Run the converted file
through scripts/run_script_pilot.py (or the Web UI with content_type=script)
to produce the English version.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.chapter_splitter import ParagraphTag
from src.script_adapter import convert_forge_script, extract_forge_metadata
from src.script_splitter import split_episodes


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    src = sys.argv[1]
    text = open(src, encoding="utf-8").read()

    meta = extract_forge_metadata(text)
    converted = convert_forge_script(text)

    if converted == text:
        print("WARNING: no Forge Echo episode headers found — file returned unchanged.")
        sys.exit(1)

    episodes = [e for e in split_episodes(converted) if e.action != ParagraphTag.SKIP]

    # Default output path
    if len(sys.argv) >= 3:
        out = sys.argv[2]
    else:
        name = os.path.splitext(os.path.basename(src))[0]
        # Strip the _剧本 suffix if present
        name = name.replace("_剧本", "")
        os.makedirs(f"pilots/{name}", exist_ok=True)
        out = f"pilots/{name}/{name}_westward.txt"

    with open(out, "w", encoding="utf-8") as f:
        f.write(converted)

    total_chars = sum(e.word_count for e in episodes)
    print(f"Forge Echo script: {meta['title'] or src}")
    print(f"  Genre: {meta['genre'] or '?'} | Declared episodes: {meta['episode_count'] or '?'}")
    print(f"  Converted: {len(episodes)} episodes, {total_chars:,} chars")
    print(f"  Output: {out}")
    print()
    print("Next: run through the script pipeline, e.g.")
    print(f"  python3 scripts/run_script_pilot.py  (edit SCRIPT_PATH to {out})")


if __name__ == "__main__":
    main()

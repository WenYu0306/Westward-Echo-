"""Run READ -> WRITE for one arm in a fresh process.

A subprocess is required: config.NODE_MODELS is evaluated at import time, so
changing XIDU_NODE_MODEL_READ after import has no effect in-process.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ["XIDU_AB_ROOT"])
sys.path.insert(0, str(ROOT))

payload = json.loads(Path(os.environ["XIDU_AB_PAYLOAD"]).read_text(encoding="utf-8"))
glossary = json.loads(Path(os.environ["XIDU_AB_GLOSSARY"]).read_text(encoding="utf-8"))

from src.agent.graph import TranslationAgent  # noqa: E402
from src.agent.nodes.read import read_node  # noqa: E402
from src.agent.nodes.review_terms import review_terms_node  # noqa: E402
from src.agent.nodes.write import write_node  # noqa: E402

agent = TranslationAgent(book_id=payload["book_id"])
if glossary:
    agent.load_glossary_snapshot(
        json.dumps(glossary, ensure_ascii=False), target_lang="en-US"
    )

rows = []
for item in payload["chapters"]:
    try:
        state = agent._make_state(
            item["title"], item["content"], item["ch"], "",
            "en-US", payload["genre"], skip_readback=True,
        )
        state.update(read_node(state, agent.exact_store, agent.semantic_store) or {})
        state.update(review_terms_node(state) or {})
        out = write_node(state) or {}
        rows.append({
            "ch": item["ch"], "ok": True,
            "title": item["title"],
            "text": out.get("translated_text") or "",
            "analysis_len": len(json.dumps(state.get("read_analysis") or {}, ensure_ascii=False)),
        })
    except Exception as exc:
        rows.append({"ch": item["ch"], "ok": False, "error": repr(exc)})

Path(os.environ["XIDU_AB_RESULT"]).write_text(
    json.dumps(rows, ensure_ascii=False), encoding="utf-8")

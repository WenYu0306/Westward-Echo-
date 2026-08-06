"""Prompt registry — selects the per-node prompt set by content type.

Branching mechanism (v0.16+): the pipeline supports parallel content
types — ``novel`` (the validated web-novel path) and, later, ``script``
(vertical short drama) and ``game``. Each type maps to a :class:`PromptSet`
holding the four nodes' SYSTEM/USER templates.

Invariants (enforced by tests/test_prompt_registry.py):

- ``novel`` returns the exact module-level constants that existed before
  this registry was introduced — the web-novel path is byte-identical.
- Unknown content types fall back to ``novel``.
- Every prompt set MUST keep the same ``.format()`` placeholder signature
  and the same output JSON schema as the novel set, so node logic
  (including all parse fallbacks) is shared unchanged.
"""

from dataclasses import dataclass

from .fix import FIX_SYSTEM, FIX_USER
from .read import READ_SYSTEM, READ_USER
from .readback import READBACK_SYSTEM, READBACK_USER
from .script_fix import SCRIPT_FIX_SYSTEM, SCRIPT_FIX_USER
from .script_read import SCRIPT_READ_SYSTEM, SCRIPT_READ_USER
from .script_readback import SCRIPT_READBACK_SYSTEM, SCRIPT_READBACK_USER
from .script_write import SCRIPT_WRITE_SYSTEM, SCRIPT_WRITE_USER
from .write import WRITE_SYSTEM, WRITE_USER


@dataclass(frozen=True)
class PromptSet:
    read_system: str
    read_user: str
    write_system: str
    write_user: str
    readback_system: str
    readback_user: str
    fix_system: str
    fix_user: str


NOVEL_PROMPTS = PromptSet(
    read_system=READ_SYSTEM,
    read_user=READ_USER,
    write_system=WRITE_SYSTEM,
    write_user=WRITE_USER,
    readback_system=READBACK_SYSTEM,
    readback_user=READBACK_USER,
    fix_system=FIX_SYSTEM,
    fix_user=FIX_USER,
)

SCRIPT_PROMPTS = PromptSet(
    read_system=SCRIPT_READ_SYSTEM,
    read_user=SCRIPT_READ_USER,
    write_system=SCRIPT_WRITE_SYSTEM,
    write_user=SCRIPT_WRITE_USER,
    readback_system=SCRIPT_READBACK_SYSTEM,
    readback_user=SCRIPT_READBACK_USER,
    fix_system=SCRIPT_FIX_SYSTEM,
    fix_user=SCRIPT_FIX_USER,
)

_REGISTRY: dict[str, PromptSet] = {
    "novel": NOVEL_PROMPTS,
    "script": SCRIPT_PROMPTS,
}


def get_prompt_set(content_type: str = "novel") -> PromptSet:
    """Return the prompt set for a content type; unknown types get novel."""
    key = (content_type or "novel").strip().lower()
    return _REGISTRY.get(key, NOVEL_PROMPTS)

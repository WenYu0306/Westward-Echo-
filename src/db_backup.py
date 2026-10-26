"""Point-in-time copies of the SQLite store before a bulk write.

Why this exists
---------------
``data/`` is gitignored, so ``checkpoints.db`` has no version history. A bad
bulk operation (a mis-specified import, a cleaner that flags the wrong rows)
would be unrecoverable. Single-term writes are far too frequent to back up, so
this is called only before whole-glossary operations.

Retention is bounded: the newest ``retain`` copies per database are kept and the
rest pruned, so the backup directory cannot grow without limit.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

DEFAULT_RETAIN = 5


def backup_sqlite(db_path, retain: int = DEFAULT_RETAIN) -> Path | None:
    """Copy ``db_path`` next to itself as ``<name>.bak_<timestamp>``.

    Returns the backup path, or ``None`` when the database does not exist yet
    (nothing to lose). Also prunes older backups beyond ``retain``.
    """
    source = Path(db_path)
    if not source.exists():
        return None
    # Microseconds, so names stay unique and monotonic even when several backups
    # land in the same second — and, crucially, a pruned name is never reused
    # (which would make "newest" ambiguous to anything sorting by name).
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target = source.with_name(f"{source.name}.bak_{stamp}")
    # Avoid a collision when two calls land in the same second.
    suffix = 1
    while target.exists():
        target = source.with_name(f"{source.name}.bak_{stamp}_{suffix}")
        suffix += 1
    shutil.copy2(source, target)

    pattern = f"{source.name}.bak_*"
    # Sort by name, not mtime: several copies can land in the same second, and
    # filenames already encode the timestamp plus a collision counter.
    existing = sorted(source.parent.glob(pattern),
                      key=lambda p: p.name, reverse=True)
    for stale in existing[retain:]:
        try:
            stale.unlink()
        except OSError:
            pass
    return target

"""Files written whole or not at all."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_text_atomic(path: str | Path, text: str) -> Path:
    """Write ``text`` to ``path`` through a temporary file beside it renamed
    over it, so a reader in another process sees the old file or the new one,
    never half of it (example 31 rewrites its clock profiles on every run; a
    second run reading one mid-write found no ``l_dbc_hz``). The file gets the
    mode ``open(path, "w")`` would give it."""
    p = Path(path)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(tmp, 0o666 & ~umask)
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return p

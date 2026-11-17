"""Explicit text/JSON replacement shared by root collectors; no import-time I/O."""
from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path


def write_text_atomic(path: str | Path, text: str, *, encoding: str = "utf-8") -> None:
    """Replace one complete file, never expose a half-written JSON document.

    Serialize before calling; leave the previous file intact on failure. Keep an
    existing file's permissions, and use a same-directory temporary file so the
    final rename remains atomic. This is not a multi-file transaction or lock.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        if target.exists():
            os.fchmod(fd, stat.S_IMODE(target.stat().st_mode))
        else:
            os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding=encoding, newline="") as stream:
            fd = -1
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if fd >= 0:
            os.close(fd)
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json_atomic(path: str | Path, payload: object, **json_options) -> None:
    """Caller owns separators, indentation, field order and missing-value policy."""
    write_text_atomic(path, json.dumps(payload, **json_options))

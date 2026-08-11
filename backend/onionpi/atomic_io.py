"""Small durable file writes for state shared with system services.

The web process and the root helpers communicate through short files.  A
power loss or a reader opening a file between truncate and write must never
turn one of those files into a half document, so every replacement is written
next to its destination, flushed, and renamed atomically.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(
    path: Path,
    content: str,
    *,
    mode: int = 0o640,
    encoding: str = "utf-8",
) -> None:
    """Durably replace *path* with *content* without exposing partial data."""
    temporary: Path | None = None
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding=encoding,
        dir=path.parent,
        delete=False,
        prefix=f".{path.name}.",
    )
    try:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        os.fchmod(handle.fileno(), mode)
        handle.close()
        os.replace(temporary, path)
        temporary = None

        # The rename is atomic, but syncing the directory is what makes the
        # new name survive an abrupt power loss on Linux filesystems.
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # Some development filesystems do not allow fsync on directories.
            # The file contents were still flushed before the atomic rename.
            pass
    finally:
        if not handle.closed:
            handle.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)

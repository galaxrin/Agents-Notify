from __future__ import annotations

import os


def last_complete_offset(handle) -> int:
    handle.seek(0, os.SEEK_END)
    position = handle.tell()
    while position:
        start = max(0, position - 4096)
        handle.seek(start)
        chunk = handle.read(position - start)
        newline = chunk.rfind(b"\n")
        if newline >= 0:
            return start + newline + 1
        position = start
    return 0

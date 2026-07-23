from __future__ import annotations

import sys
from pathlib import Path


def resource_path(relative: str) -> Path:
    package_dir = Path(__file__).resolve().parent
    candidates = [package_dir / relative]
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "agent_watch_notify" / relative)
    candidates.append(
        Path(sys.executable).resolve().parent.parent
        / "Resources" / "agent_watch_notify" / relative
    )
    return next((path for path in candidates if path.exists()), candidates[0])

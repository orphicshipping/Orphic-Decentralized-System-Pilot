"""Find the current interpreter's installed NET launcher without searching PATH."""
from __future__ import annotations

import os
from pathlib import Path
import sysconfig


def installed_net_command() -> Path:
    # Windows base installations put python.exe beside Scripts/, while venvs
    # usually put it inside Scripts/. sysconfig owns both installation layouts.
    executable = Path(sysconfig.get_path("scripts")) / ("net.exe" if os.name == "nt" else "net")
    if not executable.is_file():
        raise ValueError(f"Missing installed native NET command: {executable}")
    return executable

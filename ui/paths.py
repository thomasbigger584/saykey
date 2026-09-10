"""Filesystem locations, resolved relative to the project so cwd doesn't matter."""

from __future__ import annotations

import sys
from pathlib import Path

# ui/ lives directly under the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]

CONFIG_FILE = PROJECT_ROOT / "config.ini"
CONFIG_EXAMPLE = PROJECT_ROOT / "config.example.ini"
RECORD_PY = PROJECT_ROOT / "recorder" / "record.py"
AHK_SCRIPT = PROJECT_ROOT / "agent" / "saykey.ahk"
SERVER_DIR = PROJECT_ROOT / "server"
COMPOSE_FILE = SERVER_DIR / "docker-compose.yml"
COMPOSE_GPU_FILE = SERVER_DIR / "docker-compose.gpu.yml"
MODELS_DIR = PROJECT_ROOT / "models"


def venv_python(windowless: bool = False) -> str:
    """Best guess at the interpreter to run record.py with."""
    if sys.platform == "win32":
        base = PROJECT_ROOT / ".venv" / "Scripts"
        exe = base / ("pythonw.exe" if windowless else "python.exe")
    else:
        base = PROJECT_ROOT / ".venv" / "bin"
        exe = base / "python"
    return str(exe) if exe.exists() else sys.executable

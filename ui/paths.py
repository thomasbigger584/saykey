"""Filesystem locations, resolved relative to the project so cwd doesn't matter."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# ui/ lives directly under the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]

CONFIG_FILE = PROJECT_ROOT / "config.ini"
CONFIG_EXAMPLE = PROJECT_ROOT / "config.example.ini"
RECORD_PY = PROJECT_ROOT / "recorder" / "record.py"
AHK_SCRIPT = PROJECT_ROOT / "agent" / "saykey.ahk"
SERVER_DIR = PROJECT_ROOT / "server"
MODELS_DIR = PROJECT_ROOT / "models"
LOG_FILE = Path(os.environ.get("TEMP", tempfile.gettempdir())) / "saykey.log"
# raw stdout/stderr of the ASR server child process (same path run-server.ps1 uses)
ASR_LOG_FILE = Path(os.environ.get("TEMP", tempfile.gettempdir())) / "saykey_asr_server.log"

# The headless agent (agent/saykey.ahk) and this UI rendezvous here. It matches
# the AHK script's  CTLDIR := A_Temp "\saykey_ctl".
CTL_DIR = Path(os.environ.get("TEMP", tempfile.gettempdir())) / "saykey_ctl"
ST_ACTIVITY = CTL_DIR / "activity"      # agent -> UI: one word
ST_EVENTS = CTL_DIR / "events.tsv"      # agent -> UI: append-only feed
UI_QUIT = CTL_DIR / "ui.quit"           # UI -> agent: exit
UI_RELOAD = CTL_DIR / "ui.reload"       # UI -> agent: reload the script
UI_BUTTON = CTL_DIR / "ui.button"       # UI -> agent: re-read [button] enabled
UI_SUSPEND = CTL_DIR / "ui.suspend"     # UI -> agent: pause triggers (capturing a shortcut)


def venv_python(windowless: bool = False) -> str:
    """Best guess at the interpreter to run record.py with."""
    if sys.platform == "win32":
        base = PROJECT_ROOT / ".venv" / "Scripts"
        exe = base / ("pythonw.exe" if windowless else "python.exe")
    else:
        base = PROJECT_ROOT / ".venv" / "bin"
        exe = base / "python"
    return str(exe) if exe.exists() else sys.executable

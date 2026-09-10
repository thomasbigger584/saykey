"""
Cross-platform "launch on OS startup".

    Windows : HKCU\...\Run  registry value
    macOS   : ~/Library/LaunchAgents/<id>.plist   (LaunchAgent)
    Linux   : ~/.config/autostart/<id>.desktop    (XDG autostart)

The command launched is:  <pythonw> -m ui --tray
resolved so that it works regardless of the current directory.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .paths import PROJECT_ROOT, venv_python

APP_ID = "com.saykey.ui"
APP_NAME = "Saykey"

_ENTRY = str(PROJECT_ROOT / "ui" / "__main__.py")


def _launch_command() -> list[str]:
    # a bare file path (not `-m ui`) so it works from any working directory
    return [venv_python(windowless=True), _ENTRY, "--tray"]


# ----------------------------------------------------------------- Windows
def _win_run_key():
    import winreg

    return winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_ALL_ACCESS,
    )


def _win_value() -> str:
    exe, *args = _launch_command()
    return " ".join([f'"{exe}"', *args])


def _win_enabled() -> bool:
    import winreg

    try:
        with _win_run_key() as k:
            val, _ = winreg.QueryValueEx(k, APP_NAME)
            return bool(val)
    except OSError:
        return False


def _win_set(enable: bool) -> None:
    import winreg

    with _win_run_key() as k:
        if enable:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _win_value())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass


# ----------------------------------------------------------------- macOS
def _mac_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{APP_ID}.plist"


def _mac_set(enable: bool) -> None:
    path = _mac_plist_path()
    if not enable:
        if path.exists():
            subprocess.run(["launchctl", "unload", str(path)], check=False)
            path.unlink()
        return
    args = "".join(f"\n        <string>{a}</string>" for a in _launch_command())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{APP_ID}</string>
    <key>ProgramArguments</key><array>{args}
    </array>
    <key>RunAtLoad</key><true/>
    <key>WorkingDirectory</key><string>{PROJECT_ROOT}</string>
</dict>
</plist>
""",
        encoding="utf-8",
    )
    subprocess.run(["launchctl", "load", str(path)], check=False)


def _mac_enabled() -> bool:
    return _mac_plist_path().exists()


# ----------------------------------------------------------------- Linux
def _linux_desktop_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "autostart" / "saykey.desktop"


def _linux_set(enable: bool) -> None:
    path = _linux_desktop_path()
    if not enable:
        if path.exists():
            path.unlink()
        return
    exec_line = " ".join(_launch_command())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        f"Exec={exec_line}\n"
        f"Path={PROJECT_ROOT}\n"
        "X-GNOME-Autostart-enabled=true\n"
        "Terminal=false\n",
        encoding="utf-8",
    )


def _linux_enabled() -> bool:
    return _linux_desktop_path().exists()


# ----------------------------------------------------------------- public
def is_enabled() -> bool:
    if sys.platform == "win32":
        return _win_enabled()
    if sys.platform == "darwin":
        return _mac_enabled()
    return _linux_enabled()


def set_enabled(enable: bool) -> None:
    if sys.platform == "win32":
        _win_set(enable)
    elif sys.platform == "darwin":
        _mac_set(enable)
    else:
        _linux_set(enable)


def sync(desired: bool) -> None:
    """Make the OS state match `desired` if it doesn't already."""
    try:
        if is_enabled() != desired:
            set_enabled(desired)
    except Exception:  # noqa: BLE001  -- autostart is best-effort
        pass

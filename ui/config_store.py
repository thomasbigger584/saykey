"""
Typed read/write access to config.ini.

This is the single source of truth shared by every part of the project:
  * saykey.ahk  (Windows dictation agent)
  * record.py        (recorder daemon + transcription)
  * run-server.ps1   (Docker ASR server)
  * this UI

The UI only ever edits keys that already exist for those tools, plus a small
[ui] section for its own window behaviour. Comments and section order in the
file are preserved on save.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field, asdict

from .paths import CONFIG_EXAMPLE, CONFIG_FILE

# ---------------------------------------------------------------- schema
#   dataclass field  ->  (section, key, default)
_MAP = {
    "shortcut":          ("hotkey", "key", "^Space"),
    "shortcut_mode":     ("hotkey", "mode", "hold"),
    "cancel_shortcut":   ("hotkey", "cancel", "^+Space"),
    "min_hold_ms":       ("hotkey", "min_hold_ms", "250"),

    "mic_device_index":  ("recording", "device_index", "-1"),
    "silence_timeout":   ("recording", "silence_timeout", "2.0"),
    "max_seconds":       ("recording", "max_seconds", "60"),

    "backend":           ("transcription", "backend", "server"),
    "server_url":        ("transcription", "server_url", "http://127.0.0.1:9000"),

    "engine":            ("server", "engine", "parakeet"),
    "model_override":    ("server", "model", ""),
    "device":            ("server", "device", "auto"),
    "quantization":      ("server", "quantization", "none"),
    "server_port":       ("server", "port", "9000"),

    "local_model":       ("local", "model", "base.en"),

    "language":          ("general", "language", "en"),
    "debug":             ("general", "debug", "false"),

    "injection_mode":    ("injection", "mode", "Raw"),
    "key_delay":         ("injection", "key_delay", "10"),
    "chunk_size":        ("injection", "chunk_size", "20"),
    "chunk_delay":       ("injection", "chunk_delay", "15"),

    "toast_enabled":     ("toast", "enabled", "true"),
    "toast_position":    ("toast", "position", "bottom"),

    "button_enabled":    ("button", "enabled", "false"),
    "button_position":   ("button", "position", "bottom-right"),

    "start_hidden":      ("ui", "start_hidden", "false"),
    "launch_on_startup": ("ui", "launch_on_startup", "false"),
    "show_tray_icon":    ("ui", "show_tray_icon", "true"),
    "autostart_server":  ("ui", "autostart_server", "true"),
    "autostart_agent":   ("ui", "autostart_agent", "true"),
    "developer_options": ("ui", "developer_options", "false"),
}

_BOOLS = {
    "debug", "toast_enabled", "button_enabled", "start_hidden", "launch_on_startup",
    "show_tray_icon", "autostart_server", "autostart_agent", "developer_options",
}
_INTS = {"mic_device_index", "min_hold_ms", "max_seconds", "server_port",
         "key_delay", "chunk_size", "chunk_delay"}


@dataclass
class Settings:
    shortcut: str = "^Space"
    shortcut_mode: str = "hold"
    cancel_shortcut: str = "^+Space"
    min_hold_ms: int = 250
    mic_device_index: int = -1
    silence_timeout: str = "2.0"
    max_seconds: int = 60
    backend: str = "server"
    server_url: str = "http://127.0.0.1:9000"
    engine: str = "parakeet"
    model_override: str = ""
    device: str = "auto"
    quantization: str = "none"
    server_port: int = 9000
    local_model: str = "base.en"
    language: str = "en"
    debug: bool = False
    injection_mode: str = "Raw"
    key_delay: int = 10
    chunk_size: int = 20
    chunk_delay: int = 15
    toast_enabled: bool = True
    toast_position: str = "bottom"
    button_enabled: bool = False
    button_position: str = "bottom-right"
    start_hidden: bool = False
    launch_on_startup: bool = False
    show_tray_icon: bool = True
    autostart_server: bool = True
    autostart_agent: bool = True
    developer_options: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def _coerce(name: str, raw: str):
    raw = (raw or "").strip()
    if name in _BOOLS:
        return raw.lower() == "true"
    if name in _INTS:
        try:
            return int(float(raw))
        except ValueError:
            return int(_MAP[name][2])
    return raw


def _to_str(name: str, value) -> str:
    if name in _BOOLS:
        return "true" if value else "false"
    return str(value)


def _ensure_file() -> None:
    if not CONFIG_FILE.exists() and CONFIG_EXAMPLE.exists():
        shutil.copyfile(CONFIG_EXAMPLE, CONFIG_FILE)


def load() -> Settings:
    _ensure_file()
    import configparser

    cp = configparser.ConfigParser()
    if CONFIG_FILE.exists():
        cp.read(CONFIG_FILE, encoding="utf-8")

    values = {}
    for name, (section, key, default) in _MAP.items():
        raw = cp.get(section, key, fallback=default)
        values[name] = _coerce(name, raw)
    return Settings(**values)


def save(settings: Settings) -> None:
    """Write changed values back, preserving comments / layout as much as possible."""
    _ensure_file()
    lines = CONFIG_FILE.read_text(encoding="utf-8").splitlines() if CONFIG_FILE.exists() else []

    # group desired (section -> {key: value})
    desired: dict[str, dict[str, str]] = {}
    for name, (section, key, _default) in _MAP.items():
        desired.setdefault(section, {})[key] = _to_str(name, getattr(settings, name))

    out: list[str] = []
    cur_section = None
    seen: dict[str, set] = {s: set() for s in desired}

    def flush_missing(section: str):
        for key, val in desired.get(section, {}).items():
            if key not in seen[section]:
                out.append(f"{key} = {val}")
                seen[section].add(key)

    for line in lines:
        stripped = line.strip()
        m = stripped.startswith("[") and stripped.endswith("]")
        if m:
            if cur_section in desired:
                flush_missing(cur_section)
            cur_section = stripped[1:-1].strip()
            out.append(line)
            continue
        if cur_section in desired and "=" in stripped and not stripped.startswith((";", "#")):
            key = stripped.split("=", 1)[0].strip()
            if key in desired[cur_section]:
                out.append(f"{key} = {desired[cur_section][key]}")
                seen[cur_section].add(key)
                continue
        out.append(line)

    if cur_section in desired:
        flush_missing(cur_section)

    # any section that wasn't in the file at all
    for section, kv in desired.items():
        if not seen[section]:
            out.append("")
            out.append(f"[{section}]")
            for key, val in kv.items():
                out.append(f"{key} = {val}")

    CONFIG_FILE.write_text("\n".join(out).rstrip() + "\n", encoding="ascii", errors="replace")

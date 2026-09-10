"""
Translate between the config file's AutoHotkey hotkey syntax (``^!Space``) and
a portable, human "Ctrl+Alt+Space" form used by the Qt capture widget.

Keeping AutoHotkey syntax as the stored form means saykey.ahk keeps working
unchanged; a future native (mac/linux) agent will read the same portable parse.
"""

from __future__ import annotations

_AHK_TO_NAME = {"^": "Ctrl", "!": "Alt", "+": "Shift", "#": "Win"}
_NAME_TO_AHK = {"ctrl": "^", "control": "^", "alt": "!", "option": "!",
                "shift": "+", "win": "#", "meta": "#", "cmd": "#", "super": "#"}

# key-name normalisation (portable name -> AHK key name)
_KEY_TO_AHK = {
    "space": "Space", "return": "Enter", "enter": "Enter", "esc": "Escape",
    "escape": "Escape", "tab": "Tab", "backspace": "Backspace", "del": "Delete",
    "delete": "Delete", "ins": "Insert", "insert": "Insert", "home": "Home",
    "end": "End", "pgup": "PgUp", "pageup": "PgUp", "pgdn": "PgDn",
    "pagedown": "PgDn", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "capslock": "CapsLock",
}
_AHK_TO_KEY = {v.lower(): k for k, v in _KEY_TO_AHK.items()}


def ahk_to_portable(combo: str) -> str:
    """'^!Space' -> 'Ctrl+Alt+Space'"""
    combo = (combo or "").strip()
    i, mods = 0, []
    while i < len(combo) and combo[i] in _AHK_TO_NAME:
        mods.append(_AHK_TO_NAME[combo[i]])
        i += 1
    key = combo[i:]
    if not key:
        return "+".join(mods)
    key = _AHK_TO_KEY.get(key.lower(), key)
    if len(key) == 1:
        key = key.upper()
    else:
        key = key[:1].upper() + key[1:]
    return "+".join(mods + [key])


def portable_to_ahk(text: str) -> str:
    """'Ctrl+Alt+Space' -> '^!Space'  (returns '' if there's no non-modifier key)"""
    parts = [p.strip() for p in (text or "").split("+") if p.strip()]
    if not parts:
        return ""
    prefix, key = "", ""
    for p in parts:
        low = p.lower()
        if low in _NAME_TO_AHK and not key:
            prefix += _NAME_TO_AHK[low]
        else:
            key = p
    if not key:
        return ""
    key = _KEY_TO_AHK.get(key.lower(), key)
    if len(key) == 1:
        key = key.lower()
    return prefix + key


def describe(combo: str) -> str:
    p = ahk_to_portable(combo)
    return p or "(none)"

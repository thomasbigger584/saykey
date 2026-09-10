"""
Reads the headless dictation agent's status files and merges in events that
originate in the UI itself, so the Settings window can show one unified feed
(what used to be system-tray notifications) plus a live activity line.

The agent (agent/saykey.ahk) writes:
    <ctl>/activity     one word: idle | preparing | recording | transcribing | stopped
    <ctl>/events.tsv   append-only lines:  <unix-seconds> TAB <level> TAB <message>
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .paths import ST_ACTIVITY, ST_EVENTS

_MAX_EVENTS = 200
_LEVELS = ("info", "ok", "warn", "error")


@dataclass
class Event:
    ts: float
    level: str
    text: str


class StatusFeed:
    """Poll `refresh()` on a timer; read `activity` / `events()` any time."""

    def __init__(self) -> None:
        self._ui_events: list[Event] = []
        self._file_events: list[Event] = []
        self._file_sig: tuple[float, int] = (0.0, 0)
        self.activity: str = "unknown"

    # ---- UI-originated events (server start/stop, settings applied, …) -------
    def add(self, level: str, text: str) -> None:
        if level not in _LEVELS:
            level = "info"
        self._ui_events.append(Event(time.time(), level, text))
        self._ui_events = self._ui_events[-_MAX_EVENTS:]

    # ---- pull the agent's files -------------------------------------------
    def refresh(self) -> None:
        try:
            self.activity = (ST_ACTIVITY.read_text(encoding="utf-8", errors="replace").strip()
                             or "unknown")
        except OSError:
            self.activity = "stopped"

        try:
            st = ST_EVENTS.stat()
            sig = (st.st_mtime, st.st_size)
            if sig != self._file_sig:
                self._file_sig = sig
                self._file_events = self._parse(
                    ST_EVENTS.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            self._file_events = []
            self._file_sig = (0.0, 0)

    @staticmethod
    def _parse(raw: str) -> list[Event]:
        out: list[Event] = []
        for line in raw.splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            try:
                ts = float(parts[0])
            except ValueError:
                continue
            level = parts[1].strip().lower()
            out.append(Event(ts, level if level in _LEVELS else "info", parts[2]))
        return out[-_MAX_EVENTS:]

    def events(self) -> list[Event]:
        merged = self._file_events + self._ui_events
        merged.sort(key=lambda e: e.ts)
        return merged[-_MAX_EVENTS:]

    def latest(self) -> Event | None:
        ev = self.events()
        return ev[-1] if ev else None

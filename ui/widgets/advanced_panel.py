"""Advanced & Developer panel -- dev toggles, activity history, quit."""

from __future__ import annotations

import time

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..config_store import Settings

_LEVEL_COLOUR = {"error": theme.RECORDING, "warn": theme.BUSY,
                 "ok": theme.ACCENT, "info": theme.TEXT_DIM}


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class AdvancedPanel(QWidget):
    open_log = Signal()
    edit_config = Signal()
    restart_agent = Signal()
    quit_app = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._seen: set = set()        # (ts, text) of events already in the view
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 18)
        v.setSpacing(12)

        title = QLabel("Advanced & Developer")
        title.setProperty("h1", True)
        v.addWidget(title)

        self.cb_dev = QCheckBox("Enable developer options")
        self.cb_dev.toggled.connect(self._toggle_dev)
        v.addWidget(self.cb_dev)

        self._dev = QWidget()
        dv = QVBoxLayout(self._dev)
        dv.setContentsMargins(18, 0, 0, 0)
        dv.setSpacing(8)
        self.cb_debug = QCheckBox("Verbose debug logging (%TEMP%\\saykey.log)")
        dv.addWidget(self.cb_debug)
        btns = QHBoxLayout()
        for text, sig in (("Open log", self.open_log),
                          ("Edit config.ini", self.edit_config),
                          ("Restart agent", self.restart_agent)):
            b = QPushButton(text)
            b.clicked.connect(sig)
            btns.addWidget(b)
        btns.addStretch(1)
        dv.addLayout(btns)
        v.addWidget(self._dev)

        v.addWidget(QLabel("Recent activity"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1000)
        self.log.setPlaceholderText("Server, agent and dictation events show here.")
        v.addWidget(self.log, 1)

        quit_row = QHBoxLayout()
        quit_row.addStretch(1)
        self.btn_quit = QPushButton("Quit Saykey")
        self.btn_quit.setToolTip("Stop the ASR server and the dictation agent, then exit")
        self.btn_quit.setStyleSheet(
            f"QPushButton {{ border: 1px solid {theme.RECORDING};"
            f" color: {theme.RECORDING}; padding: 7px 18px; }}"
            f"QPushButton:hover {{ background: #2a1618; }}")
        self.btn_quit.clicked.connect(self.quit_app)
        quit_row.addWidget(self.btn_quit)
        v.addLayout(quit_row)

    def _toggle_dev(self, on: bool) -> None:
        self._dev.setVisible(on)

    def load(self, s: Settings) -> None:
        self.cb_dev.setChecked(s.developer_options)
        self._dev.setVisible(s.developer_options)
        self.cb_debug.setChecked(s.debug)

    def apply_to(self, s: Settings) -> None:
        s.developer_options = self.cb_dev.isChecked()
        s.debug = self.cb_debug.isChecked()

    def set_events(self, events) -> None:
        """Append-only, chronological -- a chat/status history. Every event that
        turns up is appended in order; nothing is ever filtered or removed."""
        appended = False
        for e in events:
            key = (round(e.ts, 3), e.text)
            if key in self._seen:
                continue
            self._seen.add(key)
            hms = time.strftime("%H:%M:%S", time.localtime(e.ts))
            colour = _LEVEL_COLOUR.get(e.level, theme.TEXT_DIM)
            self.log.appendHtml(
                f'<span style="color:{theme.OFFLINE}">{hms}</span>&nbsp;&nbsp;'
                f'<span style="color:{colour}">{_esc(e.text)}</span>')
            appended = True
        if len(self._seen) > 4000:
            self._seen = set(list(self._seen)[-2000:])
        if appended:
            sb = self.log.verticalScrollBar()
            sb.setValue(sb.maximum())

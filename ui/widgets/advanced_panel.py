"""Advanced & Developer panel -- dev toggles + the activity log."""

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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._sig: tuple = ()
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
        self.cb_as_server = QCheckBox("Start the ASR server when Saykey launches")
        self.cb_as_agent = QCheckBox("Start the dictation agent when Saykey launches")
        for cb in (self.cb_debug, self.cb_as_server, self.cb_as_agent):
            dv.addWidget(cb)
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
        self.log.setMaximumBlockCount(400)
        self.log.setPlaceholderText("Server, agent and dictation events show here.")
        v.addWidget(self.log, 1)

    def _toggle_dev(self, on: bool) -> None:
        self._dev.setVisible(on)

    def load(self, s: Settings) -> None:
        self.cb_dev.setChecked(s.developer_options)
        self._dev.setVisible(s.developer_options)
        self.cb_debug.setChecked(s.debug)
        self.cb_as_server.setChecked(s.autostart_server)
        self.cb_as_agent.setChecked(s.autostart_agent)

    def apply_to(self, s: Settings) -> None:
        s.developer_options = self.cb_dev.isChecked()
        s.debug = self.cb_debug.isChecked()
        s.autostart_server = self.cb_as_server.isChecked()
        s.autostart_agent = self.cb_as_agent.isChecked()

    def set_events(self, events) -> None:
        sig = tuple((e.ts, e.text) for e in events)
        if sig == self._sig:
            return
        self._sig = sig
        self.log.clear()
        for e in events:
            hhmm = time.strftime("%H:%M", time.localtime(e.ts))
            colour = _LEVEL_COLOUR.get(e.level, theme.TEXT_DIM)
            self.log.appendHtml(
                f'<span style="color:{theme.OFFLINE}">{hhmm}</span> '
                f'<span style="color:{colour}">{_esc(e.text)}</span>')
        sb = self.log.verticalScrollBar()
        sb.setValue(sb.maximum())

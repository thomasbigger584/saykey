"""General panel -- app behaviour."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .. import autostart
from ..config_store import Settings

_LANGS = [("Auto-detect", "auto"), ("English", "en"), ("German", "de"),
          ("French", "fr"), ("Spanish", "es"), ("Italian", "it"),
          ("Portuguese", "pt"), ("Dutch", "nl")]


class GeneralPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 18)
        v.setSpacing(14)

        title = QLabel("General")
        title.setProperty("h1", True)
        v.addWidget(title)

        form = QFormLayout()
        form.setSpacing(12)
        self.lang = QComboBox()
        for label, code in _LANGS:
            self.lang.addItem(label, code)
        form.addRow("Transcription language", self.lang)
        v.addLayout(form)

        self.cb_startup = QCheckBox("Launch Saykey when I sign in")
        self.cb_hidden = QCheckBox("Start minimised to the tray (no window)")
        self.cb_tray = QCheckBox("Show the tray icon")
        for cb in (self.cb_startup, self.cb_hidden, self.cb_tray):
            v.addWidget(cb)

        note = QLabel("With the tray icon hidden, run Saykey again to reopen this window.")
        note.setWordWrap(True)
        note.setProperty("dim", True)
        v.addWidget(note)
        v.addStretch(1)

    def load(self, s: Settings) -> None:
        i = self.lang.findData(s.language)
        self.lang.setCurrentIndex(i if i >= 0 else 1)
        self.cb_startup.setChecked(s.launch_on_startup or autostart.is_enabled())
        self.cb_hidden.setChecked(s.start_hidden)
        self.cb_tray.setChecked(s.show_tray_icon)

    def apply_to(self, s: Settings) -> None:
        s.language = self.lang.currentData()
        s.launch_on_startup = self.cb_startup.isChecked()
        s.start_hidden = self.cb_hidden.isChecked()
        s.show_tray_icon = self.cb_tray.isChecked()

"""Dictation & Hotkeys panel."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import shortcuts
from ..audio import list_input_devices
from ..config_store import Settings
from .audio_meter import AudioMeter

_POSITIONS = ["bottom", "top", "center", "bottom-left", "bottom-right",
              "top-left", "top-right"]
_BTN_POSITIONS = ["bottom-right", "bottom-left", "top-right", "top-left",
                  "top", "bottom", "center"]


def _section(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("section", True)
    return lbl


class DictationPanel(QWidget):
    recording_hotkey = Signal(bool)     # pause the agent while capturing
    mic_changed = Signal(int)           # device index -> live meter follows

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(12)

        # ---- Core Hotkey ------------------------------------------------
        v.addWidget(_section("CORE HOTKEY"))
        self.hotkey = QKeySequenceEdit()
        self.hotkey.setMaximumSequenceLength(1)
        self.hotkey.setEnabled(False)
        self.hotkey.setMinimumHeight(44)
        self.hotkey.editingFinished.connect(self._captured)
        self.btn_change = QPushButton("Change")
        self.btn_change.setCheckable(True)
        self.btn_change.toggled.connect(self._toggle_capture)
        self.btn_reset = QPushButton("Reset")
        self.btn_reset.clicked.connect(self._reset)
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(lambda: self.btn_change.setChecked(False))
        hk_row = QHBoxLayout()
        hk_row.addWidget(self.hotkey, 1)
        btn_col = QVBoxLayout()
        btn_col.addWidget(self.btn_change)
        btn_col.addWidget(self.btn_reset)
        hk_row.addLayout(btn_col)
        v.addLayout(hk_row)

        # ---- Shortcut behaviour --------------------------------------
        beh = QHBoxLayout()
        beh.addWidget(QLabel("Shortcut Behaviour"))
        self.btn_hold = QPushButton("Hold to talk")
        self.btn_toggle = QPushButton("Toggle")
        seg = QHBoxLayout()
        seg.setSpacing(0)
        for b in (self.btn_hold, self.btn_toggle):
            b.setCheckable(True)
            b.setProperty("segment", True)
            b.setAutoExclusive(True)
            seg.addWidget(b)
        beh.addLayout(seg)
        beh.addStretch(1)
        v.addLayout(beh)

        # ---- Microphone + live meter -------------------------------
        mic_row = QHBoxLayout()
        mic_row.addWidget(QLabel("Microphone"))
        self.mic = QComboBox()
        self.mic.currentIndexChanged.connect(
            lambda _i: self.mic_changed.emit(int(self.mic.currentData() or -1)))
        self.meter = AudioMeter(bars=16)
        self.meter.setFixedWidth(110)
        btn_refresh = QPushButton("↻ Refresh")
        btn_refresh.clicked.connect(self._reload_mics)
        mic_row.addWidget(self.mic, 1)
        mic_row.addWidget(self.meter)
        mic_row.addWidget(btn_refresh)
        v.addLayout(mic_row)
        self._reload_mics()

        # ---- Visual indicators ------------------------------------
        vi = QGroupBox("VISUAL INDICATORS")
        vil = QVBoxLayout(vi)
        vil.setSpacing(10)
        self.cb_button = QCheckBox("Show Floating Talk Button")
        self.btn_pos = QComboBox()
        self.btn_pos.addItems(_BTN_POSITIONS)
        self.btn_pos.setFixedWidth(130)
        row1 = QHBoxLayout()
        row1.addWidget(self.cb_button, 1)
        row1.addWidget(QLabel("Position"))
        row1.addWidget(self.btn_pos)
        vil.addLayout(row1)
        self.cb_toast = QCheckBox("Show On-screen Toast")
        self.toast_pos = QComboBox()
        self.toast_pos.addItems(_POSITIONS)
        self.toast_pos.setFixedWidth(130)
        row2 = QHBoxLayout()
        row2.addWidget(self.cb_toast, 1)
        row2.addWidget(QLabel("Position"))
        row2.addWidget(self.toast_pos)
        vil.addLayout(row2)
        v.addWidget(vi)

        # ---- Tuning (always visible, grid) -----------------------
        tuning = QGroupBox("TUNING (VDI SAFE INJECTION)")
        self.inj_mode = QComboBox()
        self.inj_mode.addItems(["Raw", "Event", "Text"])
        self.inj_mode.setFixedWidth(120)
        self.key_delay = QSpinBox()
        self.key_delay.setRange(0, 200)
        self.key_delay.setSuffix(" ms")
        self.chunk_size = QSpinBox()
        self.chunk_size.setRange(0, 500)
        self.chunk_delay = QSpinBox()
        self.chunk_delay.setRange(0, 500)
        self.chunk_delay.setSuffix(" ms")
        for sb in (self.key_delay, self.chunk_size, self.chunk_delay):
            sb.setFixedWidth(120)
        grid = QGridLayout(tuning)
        grid.setContentsMargins(4, 10, 4, 4)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        cells = [("Injection Mode", self.inj_mode), ("Key Delay", self.key_delay),
                 ("Chunk Size", self.chunk_size), ("Chunk Delay", self.chunk_delay)]
        for i, (text, widget) in enumerate(cells):
            r, c = divmod(i, 2)
            lbl = QLabel(text)
            lbl.setMinimumWidth(110)
            grid.addWidget(lbl, r, c * 2, Qt.AlignVCenter)
            grid.addWidget(widget, r, c * 2 + 1, Qt.AlignVCenter | Qt.AlignLeft)
        grid.setColumnMinimumWidth(1, 130)
        grid.setColumnStretch(4, 1)
        v.addWidget(tuning)

        hint = QLabel("Raw is fastest; switch to Event (then raise the delays) or "
                      "Text if a remote desktop drops or duplicates characters.")
        hint.setWordWrap(True)
        hint.setProperty("dim", True)
        v.addWidget(hint)
        v.addStretch(1)

    # ---- data <-> widgets -------------------------------------------
    def load(self, s: Settings) -> None:
        self.hotkey.setKeySequence(QKeySequence(shortcuts.ahk_to_portable(s.shortcut)))
        (self.btn_toggle if s.shortcut_mode == "toggle" else self.btn_hold).setChecked(True)
        i = self.mic.findData(s.mic_device_index)
        self.mic.setCurrentIndex(i if i >= 0 else 0)
        self.cb_button.setChecked(s.button_enabled)
        self.btn_pos.setCurrentText(s.button_position)
        self.cb_toast.setChecked(s.toast_enabled)
        self.toast_pos.setCurrentText(s.toast_position)
        self.inj_mode.setCurrentText(s.injection_mode or "Raw")
        self.key_delay.setValue(s.key_delay)
        self.chunk_size.setValue(s.chunk_size)
        self.chunk_delay.setValue(s.chunk_delay)

    def apply_to(self, s: Settings) -> None:
        combo = shortcuts.portable_to_ahk(
            self.hotkey.keySequence().toString(QKeySequence.PortableText))
        if combo:
            s.shortcut = combo
        s.shortcut_mode = "toggle" if self.btn_toggle.isChecked() else "hold"
        s.mic_device_index = int(self.mic.currentData() or -1)
        s.button_enabled = self.cb_button.isChecked()
        s.button_position = self.btn_pos.currentText()
        s.toast_enabled = self.cb_toast.isChecked()
        s.toast_position = self.toast_pos.currentText()
        s.injection_mode = self.inj_mode.currentText()
        s.key_delay = self.key_delay.value()
        s.chunk_size = self.chunk_size.value()
        s.chunk_delay = self.chunk_delay.value()

    def set_mic_level(self, level: float) -> None:
        self.meter.set_level(level)

    # ---- hotkey capture -------------------------------------------
    def _toggle_capture(self, on: bool) -> None:
        self.recording_hotkey.emit(on)
        self.hotkey.setEnabled(on)
        self.btn_change.setText("Press a key…" if on else "Change")
        if on:
            self.hotkey.clear()
            self.hotkey.setFocus()
            self._timeout.start(20000)
        else:
            self._timeout.stop()

    def _captured(self) -> None:
        if self.btn_change.isChecked() and not self.hotkey.keySequence().isEmpty():
            self.btn_change.setChecked(False)

    def _reset(self) -> None:
        if self.btn_change.isChecked():
            self.btn_change.setChecked(False)
        self.hotkey.setKeySequence(
            QKeySequence(shortcuts.ahk_to_portable(Settings().shortcut)))

    def cancel_capture(self) -> None:
        if self.btn_change.isChecked():
            self.btn_change.setChecked(False)

    def _reload_mics(self) -> None:
        keep = self.mic.currentData()
        self.mic.blockSignals(True)
        self.mic.clear()
        for idx, name in list_input_devices():
            self.mic.addItem(name, idx)
        j = self.mic.findData(keep)
        self.mic.setCurrentIndex(j if j >= 0 else 0)
        self.mic.blockSignals(False)
        self.mic_changed.emit(int(self.mic.currentData() or -1))

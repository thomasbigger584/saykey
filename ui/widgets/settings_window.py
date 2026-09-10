"""The settings window: General / Advanced / Models tabs."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import autostart, models_catalog, shortcuts
from ..config_store import Settings, load, save

_LEVEL_COLOUR = {"error": "#C0392B", "warn": "#B9770E", "ok": "#1E8449", "info": "#555"}
_HINT_WIDTH = 400   # keeps word-wrapped hints from stretching the fixed window


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setMaximumWidth(_HINT_WIDTH)
    lbl.setStyleSheet("color: gray;")
    return lbl


def _list_input_devices() -> list[tuple[int, str]]:
    devices = [(-1, "System default")]
    try:
        import sounddevice as sd

        default_in = sd.default.device[0]
        for idx, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] < 1:
                continue
            name = dev["name"]
            if idx == default_in:
                name += "  (default)"
            devices.append((idx, name))
    except Exception as exc:  # noqa: BLE001
        devices.append((-2, f"(could not enumerate devices: {exc})"))
    return devices


class SettingsWindow(QWidget):
    applied = Signal(Settings, dict)   # (new settings, {changed field: (old, new)})
    recording_hotkey = Signal(bool)    # True while the shortcut field is capturing

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Saykey")
        self._settings = load()
        self._event_sig: tuple = ()
        self._model_dirty = False   # True once the user actually changes the model pick

        self.tabs = QTabWidget()
        self.tabs.addTab(self._general_tab(), "General")
        self.tabs.addTab(self._advanced_tab(), "Advanced")
        self.tabs.addTab(self._models_tab(), "Models")

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel | QDialogButtonBox.Apply
        )
        buttons.button(QDialogButtonBox.Save).clicked.connect(self._save_and_close)
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self._apply)
        buttons.button(QDialogButtonBox.Cancel).clicked.connect(self.close)

        root = QVBoxLayout(self)
        root.addWidget(self._status_panel())
        root.addWidget(self.tabs)
        root.addWidget(buttons)
        self._load_into_widgets()
        self._lock_size()

    _WIDTH = 440   # fixed content width; word-wrapped hints wrap to fit it

    def _lock_size(self) -> None:
        """Non-resizable window, as small as fits every tab (measured at the
        fixed width so word-wrapped hints report their true height)."""
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.setFixedWidth(self._WIDTH)
        cur = self.tabs.currentIndex()
        tallest = 0
        self.tabs.setUpdatesEnabled(False)       # measure each tab without flicker
        try:
            for i in range(self.tabs.count()):
                self.tabs.setCurrentIndex(i)
                self.layout().activate()
                tallest = max(tallest, self.sizeHint().height())
            self.tabs.setCurrentIndex(cur)
        finally:
            self.tabs.setUpdatesEnabled(True)
        self.setFixedSize(self._WIDTH, tallest)

    # --------------------------------------------------------- status panel
    def _status_panel(self) -> QWidget:
        box = QFrame()
        box.setFrameShape(QFrame.StyledPanel)
        v = QVBoxLayout(box)

        self.lbl_services = QLabel("")
        self.lbl_services.setWordWrap(True)
        self.lbl_services.setMaximumWidth(_HINT_WIDTH)
        self.lbl_services.setStyleSheet("color: gray;")

        self.event_view = QPlainTextEdit()
        self.event_view.setReadOnly(True)
        self.event_view.setMaximumBlockCount(300)
        self.event_view.setFixedHeight(100)
        self.event_view.setMinimumWidth(_HINT_WIDTH)
        self.event_view.setPlaceholderText("Activity and updates appear here.")

        v.addWidget(self.lbl_services)
        v.addWidget(self.event_view)
        return box

    def update_status(self, server_txt: str, agent_txt: str, events) -> None:
        self.lbl_services.setText(f"ASR server: {server_txt}      Dictation agent: {agent_txt}")

        sig = tuple((e.ts, e.text) for e in events)
        if sig == self._event_sig:
            return
        self._event_sig = sig
        self.event_view.clear()
        for e in events:
            hhmm = time.strftime("%H:%M", time.localtime(e.ts))
            colour = _LEVEL_COLOUR.get(e.level, "#555")
            self.event_view.appendHtml(
                f'<span style="color:#999">{hhmm}</span> '
                f'<span style="color:{colour}">{_escape(e.text)}</span>')
        sb = self.event_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    # --------------------------------------------------------- tabs
    def _general_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.shortcut_edit = QKeySequenceEdit()
        self.shortcut_edit.setMaximumSequenceLength(1)
        self.shortcut_edit.setEnabled(False)          # only editable while recording
        self.shortcut_edit.editingFinished.connect(self._stop_recording_shortcut)
        self.shortcut_btn = QPushButton("Change…")
        self.shortcut_btn.setCheckable(True)
        self.shortcut_btn.toggled.connect(self._toggle_recording_shortcut)
        default_portable = shortcuts.ahk_to_portable(Settings().shortcut)  # "Ctrl+Space"
        self.shortcut_reset = QPushButton("Reset")
        self.shortcut_reset.setToolTip(f"Reset to the default ({default_portable})")
        self.shortcut_reset.clicked.connect(self._reset_shortcut)
        self._sc_timeout = QTimer(self)
        self._sc_timeout.setSingleShot(True)
        self._sc_timeout.timeout.connect(lambda: self.shortcut_btn.setChecked(False))
        sc_row = QHBoxLayout()
        sc_row.addWidget(self.shortcut_edit, 1)
        sc_row.addWidget(self.shortcut_btn)
        sc_row.addWidget(self.shortcut_reset)
        sc_holder = QWidget()
        sc_holder.setLayout(sc_row)
        form.addRow("Transcribe shortcut", sc_holder)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Hold to talk (push-to-talk)", "hold")
        self.mode_combo.addItem("Toggle (press / press again)", "toggle")
        form.addRow("Shortcut behaviour", self.mode_combo)

        self.mic_combo = QComboBox()
        for idx, name in _list_input_devices():
            self.mic_combo.addItem(name, idx)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._reload_mics)
        row = QHBoxLayout()
        row.addWidget(self.mic_combo, 1)
        row.addWidget(refresh)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("Microphone", holder)

        self.cb_button = QCheckBox("Show the floating talk button")
        form.addRow("", self.cb_button)
        return w

    def _advanced_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.cb_start_hidden = QCheckBox("Start hidden (tray only, no window)")
        self.cb_launch_startup = QCheckBox("Launch on OS startup")
        self.cb_tray_icon = QCheckBox("Show tray icon")
        for cb in (self.cb_start_hidden, self.cb_launch_startup, self.cb_tray_icon):
            form.addRow(cb)

        form.addRow(_hint("Turning off the tray icon means the app can only be "
                          "reopened by launching it again."))

        # --- Developer options: hidden unless the toggle below is on ----------
        self.cb_dev_options = QCheckBox("Developer options")
        self.cb_dev_options.toggled.connect(self._toggle_dev_options)
        form.addRow(self.cb_dev_options)

        self.cb_debug = QCheckBox("Enable debugging (verbose log to %TEMP%\\saykey.log)")
        self.cb_autostart_server = QCheckBox("Start the ASR server when this app launches")
        self.cb_autostart_agent = QCheckBox("Start the dictation agent when this app launches")
        self._dev_box = QWidget()
        dev_layout = QVBoxLayout(self._dev_box)
        dev_layout.setContentsMargins(16, 0, 0, 0)
        for cb in (self.cb_debug, self.cb_autostart_server, self.cb_autostart_agent):
            dev_layout.addWidget(cb)
        dev_layout.addWidget(_hint("Also adds a Developer submenu to the tray icon "
                                   "(open log, edit config.ini, restart the agent)."))
        form.addRow(self._dev_box)
        return w

    def _toggle_dev_options(self, on: bool) -> None:
        self._dev_box.setVisible(on)
        QTimer.singleShot(0, self._lock_size)   # regrow / shrink to fit

    def _models_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.model_list = QListWidget()
        for m in models_catalog.CATALOG:
            item = QListWidgetItem(m.label)
            item.setData(Qt.UserRole, m.id)
            item.setToolTip(f"{m.description}\n\nsize {m.size} · {m.languages}")
            self.model_list.addItem(item)
        self.model_list.currentItemChanged.connect(self._model_selected)
        row_h = self.model_list.sizeHintForRow(0) if self.model_list.count() else 20
        self.model_list.setFixedHeight(row_h * self.model_list.count() + 6)
        v.addWidget(self.model_list)

        self.model_desc = QLabel()
        self.model_desc.setWordWrap(True)
        self.model_desc.setFixedHeight(58)
        self.model_desc.setMaximumWidth(_HINT_WIDTH)
        self.model_desc.setAlignment(Qt.AlignTop)
        self.model_desc.setStyleSheet("color: #444;")
        v.addWidget(self.model_desc)

        v.addWidget(_hint("Server models run in Docker on the GPU; the local model "
                          "runs on the CPU with no Docker. Changing it applies on Save."))
        return w

    # --------------------------------------------------------- data <-> widgets
    def _load_into_widgets(self) -> None:
        s = self._settings
        self.shortcut_edit.setKeySequence(
            QKeySequence(shortcuts.ahk_to_portable(s.shortcut)))
        self.mode_combo.setCurrentIndex(0 if s.shortcut_mode != "toggle" else 1)
        i = self.mic_combo.findData(s.mic_device_index)
        self.mic_combo.setCurrentIndex(i if i >= 0 else 0)
        self.cb_button.setChecked(s.button_enabled)

        self.cb_start_hidden.setChecked(s.start_hidden)
        self.cb_launch_startup.setChecked(s.launch_on_startup or autostart.is_enabled())
        self.cb_tray_icon.setChecked(s.show_tray_icon)
        self.cb_debug.setChecked(s.debug)
        self.cb_autostart_server.setChecked(s.autostart_server)
        self.cb_autostart_agent.setChecked(s.autostart_agent)
        self.cb_dev_options.setChecked(s.developer_options)
        self._dev_box.setVisible(s.developer_options)

        current = models_catalog.match(
            backend=s.backend, engine=s.engine, model_override=s.model_override)
        target_id = current.id if current else models_catalog.DEFAULT_ID
        for row in range(self.model_list.count()):
            if self.model_list.item(row).data(Qt.UserRole) == target_id:
                self.model_list.setCurrentRow(row)
                break
        # currentItemChanged fired during setCurrentRow above -- that's not a
        # user edit, so clear the flag. _collect() only writes the model keys
        # (and _on_settings_applied only restarts the server) if this is True.
        self._model_dirty = False

    def _collect(self) -> Settings:
        s = load()   # start from disk so we don't clobber keys we don't manage
        s.shortcut = shortcuts.portable_to_ahk(
            self.shortcut_edit.keySequence().toString(QKeySequence.PortableText)) or s.shortcut
        s.shortcut_mode = self.mode_combo.currentData()
        s.mic_device_index = int(self.mic_combo.currentData() or -1)
        s.button_enabled = self.cb_button.isChecked()

        s.start_hidden = self.cb_start_hidden.isChecked()
        s.launch_on_startup = self.cb_launch_startup.isChecked()
        s.show_tray_icon = self.cb_tray_icon.isChecked()
        s.debug = self.cb_debug.isChecked()
        s.autostart_server = self.cb_autostart_server.isChecked()
        s.autostart_agent = self.cb_autostart_agent.isChecked()
        s.developer_options = self.cb_dev_options.isChecked()

        # Only touch the model keys when the user actually changed the pick --
        # otherwise a config that doesn't map cleanly onto the catalogue would
        # look "changed" on every Apply and needlessly restart the ASR server.
        if self._model_dirty:
            item = self.model_list.currentItem()
            m = models_catalog.by_id(item.data(Qt.UserRole)) if item else None
            if m:
                s.backend = m.backend
                s.engine = m.engine
                s.model_override = m.model_override
        return s

    # --------------------------------------------------------- actions
    def _toggle_recording_shortcut(self, on: bool) -> None:
        # pause the agent so the keys land in the field, not in a recording
        self.recording_hotkey.emit(on)
        self.shortcut_edit.setEnabled(on)
        self.shortcut_btn.setText("Press a key…" if on else "Change…")
        if on:
            self.shortcut_edit.clear()
            self.shortcut_edit.setFocus()
            self._sc_timeout.start(20000)         # give up if nothing is pressed
        else:
            self._sc_timeout.stop()

    def _stop_recording_shortcut(self) -> None:
        # editingFinished also fires on an empty clear() / focus-out; only finish
        # when a real chord was captured (the timeout / close handles the rest).
        if self.shortcut_btn.isChecked() and not self.shortcut_edit.keySequence().isEmpty():
            self.shortcut_btn.setChecked(False)

    def _reset_shortcut(self) -> None:
        if self.shortcut_btn.isChecked():                 # stop recording first
            self.shortcut_btn.setChecked(False)
        self.shortcut_edit.setKeySequence(
            QKeySequence(shortcuts.ahk_to_portable(Settings().shortcut)))

    def closeEvent(self, e) -> None:  # noqa: N802  (Qt override)
        if self.shortcut_btn.isChecked():
            self.shortcut_btn.setChecked(False)
        super().closeEvent(e)

    def hideEvent(self, e) -> None:  # noqa: N802
        if self.shortcut_btn.isChecked():
            self.shortcut_btn.setChecked(False)
        super().hideEvent(e)

    def _reload_mics(self) -> None:
        keep = self.mic_combo.currentData()
        self.mic_combo.clear()
        for idx, name in _list_input_devices():
            self.mic_combo.addItem(name, idx)
        i = self.mic_combo.findData(keep)
        self.mic_combo.setCurrentIndex(i if i >= 0 else 0)

    def _model_selected(self, item: QListWidgetItem, _prev) -> None:
        self._model_dirty = True
        if not item:
            return
        m = models_catalog.by_id(item.data(Qt.UserRole))
        self.model_desc.setText(m.description if m else "")

    def _diff(self, new: Settings) -> dict:
        old = self._settings
        return {f: (getattr(old, f), getattr(new, f))
                for f in new.as_dict() if getattr(old, f) != getattr(new, f)}

    def _apply(self) -> None:
        new = self._collect()
        changed = self._diff(new)
        if not changed:
            return
        save(new)
        try:
            autostart.set_enabled(new.launch_on_startup)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Autostart",
                                f"Could not update OS autostart:\n{exc}")
        self._settings = load()
        self._model_dirty = False   # what's on disk is now the baseline
        self.applied.emit(self._settings, changed)

    def _save_and_close(self) -> None:
        self._apply()
        self.close()

"""The settings window: General / Advanced / Models tabs."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import autostart, models_catalog, shortcuts
from ..config_store import Settings, load, save


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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Saykey — Settings")
        self.setMinimumWidth(520)
        self._settings = load()

        tabs = QTabWidget()
        tabs.addTab(self._general_tab(), "General")
        tabs.addTab(self._advanced_tab(), "Advanced")
        tabs.addTab(self._models_tab(), "Models")

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel | QDialogButtonBox.Apply
        )
        buttons.button(QDialogButtonBox.Save).clicked.connect(self._save_and_close)
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self._apply)
        buttons.button(QDialogButtonBox.Cancel).clicked.connect(self.close)

        root = QVBoxLayout(self)
        root.addWidget(tabs)
        root.addWidget(buttons)
        self._load_into_widgets()

    # --------------------------------------------------------- tabs
    def _general_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.shortcut_edit = QKeySequenceEdit()
        self.shortcut_edit.setMaximumSequenceLength(1)
        form.addRow("Transcribe shortcut", self.shortcut_edit)

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

        hint = QLabel("Ctrl+Space can clash with IME / editor autocomplete — try "
                      "Ctrl+Alt+Space if it misbehaves.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        form.addRow("", hint)
        return w

    def _advanced_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.cb_start_hidden = QCheckBox("Start hidden (tray only, no window)")
        self.cb_launch_startup = QCheckBox("Launch on OS startup")
        self.cb_tray_icon = QCheckBox("Show tray icon")
        for cb in (self.cb_start_hidden, self.cb_launch_startup, self.cb_tray_icon):
            form.addRow(cb)

        note = QLabel("Turning off the tray icon means the app can only be reopened by "
                      "launching it again (which shows this window).")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        form.addRow(note)

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
        dev_note = QLabel("Also unlocks the agent tray menu's Developer Options "
                          "(edit config, engine check, restart recorder, ASR server).")
        dev_note.setWordWrap(True)
        dev_note.setStyleSheet("color: gray;")
        dev_layout.addWidget(dev_note)
        form.addRow(self._dev_box)
        return w

    def _toggle_dev_options(self, on: bool) -> None:
        self._dev_box.setVisible(on)

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
        v.addWidget(self.model_list)

        self.model_desc = QLabel()
        self.model_desc.setWordWrap(True)
        self.model_desc.setMinimumHeight(70)
        self.model_desc.setStyleSheet("color: #444;")
        v.addWidget(self.model_desc)

        note = QLabel("Server models run in Docker on the GPU. Changing the model "
                      "rebuilds / restarts the ASR server.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        v.addWidget(note)
        return w

    # --------------------------------------------------------- data <-> widgets
    def _load_into_widgets(self) -> None:
        s = self._settings
        self.shortcut_edit.setKeySequence(
            QKeySequence(shortcuts.ahk_to_portable(s.shortcut)))
        self.mode_combo.setCurrentIndex(0 if s.shortcut_mode != "toggle" else 1)
        i = self.mic_combo.findData(s.mic_device_index)
        self.mic_combo.setCurrentIndex(i if i >= 0 else 0)

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

    def _collect(self) -> Settings:
        s = load()   # start from disk so we don't clobber keys we don't manage
        s.shortcut = shortcuts.portable_to_ahk(
            self.shortcut_edit.keySequence().toString(QKeySequence.PortableText)) or s.shortcut
        s.shortcut_mode = self.mode_combo.currentData()
        s.mic_device_index = int(self.mic_combo.currentData() or -1)

        s.start_hidden = self.cb_start_hidden.isChecked()
        s.launch_on_startup = self.cb_launch_startup.isChecked()
        s.show_tray_icon = self.cb_tray_icon.isChecked()
        s.debug = self.cb_debug.isChecked()
        s.autostart_server = self.cb_autostart_server.isChecked()
        s.autostart_agent = self.cb_autostart_agent.isChecked()
        s.developer_options = self.cb_dev_options.isChecked()

        item = self.model_list.currentItem()
        if item:
            m = models_catalog.by_id(item.data(Qt.UserRole))
            if m:
                s.backend = m.backend
                s.engine = m.engine
                s.model_override = m.model_override
        return s

    # --------------------------------------------------------- actions
    def _reload_mics(self) -> None:
        keep = self.mic_combo.currentData()
        self.mic_combo.clear()
        for idx, name in _list_input_devices():
            self.mic_combo.addItem(name, idx)
        i = self.mic_combo.findData(keep)
        self.mic_combo.setCurrentIndex(i if i >= 0 else 0)

    def _model_selected(self, item: QListWidgetItem, _prev) -> None:
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
        self.applied.emit(self._settings, changed)

    def _save_and_close(self) -> None:
        self._apply()
        self.close()

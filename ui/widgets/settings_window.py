"""
The Saykey main window.

A single window with a Dashboard (default view) and four settings panels
reached through a left-hand navigation rail, plus a shared bottom status bar.
The class name stays ``SettingsWindow`` for import compatibility with app.py.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QHBoxLayout,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import autostart, shortcuts, theme
from ..audio import AudioLevel
from ..config_store import Settings, load, save
from .advanced_panel import AdvancedPanel
from .dashboard import DashboardView
from .dictation_panel import DictationPanel
from .general_panel import GeneralPanel
from .models_panel import ModelsPanel
from .nav_rail import NavRail
from .status_bar import StatusBar

_DICTATION = 1   # panel index of "Dictation & Hotkeys"


class SettingsWindow(QWidget):
    applied = Signal(Settings, dict)     # (new settings, {changed field: (old, new)})
    recording_hotkey = Signal(bool)
    open_log = Signal()
    edit_config = Signal()
    restart_agent = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Root")
        self.setWindowTitle("Saykey")
        self.setStyleSheet(theme.STYLESHEET)
        self.setMinimumSize(720, 520)
        self.resize(940, 600)

        self._settings = load()
        self._activity = "unknown"

        # ---- views -----------------------------------------------------
        self.stack = QStackedWidget()
        self.dashboard = DashboardView()
        self.dashboard.open_settings.connect(lambda: self._show_settings(0))
        self.stack.addWidget(self.dashboard)

        self.stack.addWidget(self._build_settings())

        # ---- status bar + sampler -----------------------------------
        self.status_bar = StatusBar()
        self._audio = AudioLevel(self)
        self._audio.level.connect(self._on_level)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.stack, 1)
        root.addWidget(self.status_bar)

        self._load_all()

    # ------------------------------------------------------------- build
    def _build_settings(self) -> QWidget:
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.nav = NavRail()
        self.nav.navigate.connect(self._navigate)
        self.nav.home.connect(self._show_dashboard)
        body.addWidget(self.nav)

        self.panels = QStackedWidget()
        self.p_general = GeneralPanel()
        self.p_dictation = DictationPanel()
        self.p_models = ModelsPanel()
        self.p_advanced = AdvancedPanel()
        for p in (self.p_general, self.p_dictation, self.p_models, self.p_advanced):
            self.panels.addWidget(p)
        body.addWidget(self.panels, 1)
        outer.addLayout(body, 1)

        self.p_dictation.recording_hotkey.connect(self.recording_hotkey)
        self.p_dictation.mic_changed.connect(self._audio_follow)
        self.p_advanced.open_log.connect(self.open_log)
        self.p_advanced.edit_config.connect(self.edit_config)
        self.p_advanced.restart_agent.connect(self.restart_agent)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).clicked.connect(self._save_and_home)
        self.buttons.button(QDialogButtonBox.Cancel).clicked.connect(self._cancel)
        self.buttons.button(QDialogButtonBox.Save).setProperty("accent", True)
        bar = QHBoxLayout()
        bar.setContentsMargins(12, 8, 12, 10)
        bar.addStretch(1)
        bar.addWidget(self.buttons)
        outer.addLayout(bar)
        return container

    # ------------------------------------------------------------- nav
    def _show_dashboard(self) -> None:
        self.p_dictation.cancel_capture()
        self.stack.setCurrentIndex(0)
        self._sync_sampler()

    def _show_settings(self, panel: int) -> None:
        self.stack.setCurrentIndex(1)
        self._navigate(panel)
        self.nav.set_index(panel)
        self._sync_sampler()

    def _navigate(self, panel: int) -> None:
        if panel < 0:
            return
        self.p_dictation.cancel_capture()
        self.panels.setCurrentIndex(panel)
        self._sync_sampler()

    # ------------------------------------------------------------- data
    def _load_all(self) -> None:
        s = self._settings = load()
        self.p_general.load(s)
        self.p_dictation.load(s)
        self.p_models.load(s)
        self.p_advanced.load(s)
        self._audio.set_device(s.mic_device_index)

    def _collect(self) -> Settings:
        s = load()   # keep keys the UI doesn't manage
        for p in (self.p_general, self.p_dictation, self.p_models, self.p_advanced):
            p.apply_to(s)
        return s

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
            QMessageBox.warning(self, "Autostart", f"Could not update OS autostart:\n{exc}")
        self._settings = load()
        self._load_all()
        self.applied.emit(self._settings, changed)

    def _save_and_home(self) -> None:
        self._apply()
        self._show_dashboard()

    def _cancel(self) -> None:
        self._load_all()          # discard edits
        self._show_dashboard()

    # ------------------------------------------------------------- status in
    def set_status(self, *, server_txt: str, agent_ok: bool, activity: str,
                   events, shortcut: str) -> None:
        self._activity = activity
        portable = shortcuts.ahk_to_portable(shortcut)
        self.dashboard.set_state(activity, portable)
        self.status_bar.set_status(server_txt, agent_ok)
        self.p_advanced.set_events(events)
        self._sync_sampler()

    # ------------------------------------------------------------- mic sampler
    def _audio_follow(self, device_index: int) -> None:
        self._audio.set_device(device_index)

    def _on_level(self, level: float) -> None:
        self.p_dictation.set_mic_level(level)

    def _sync_sampler(self) -> None:
        # the live meter lives only on the Dictation & Hotkeys panel
        on_mic_view = (self.stack.currentIndex() == 1
                       and self.panels.currentIndex() == _DICTATION)
        want = (self.isVisible() and on_mic_view
                and self._activity in ("idle", "unknown", "stopped"))
        if want and not self._audio.running():
            self._audio.start()
        elif not want and self._audio.running():
            self._audio.stop()

    # ------------------------------------------------------------- Qt events
    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._load_all()
        self._sync_sampler()

    def hideEvent(self, e) -> None:  # noqa: N802
        self.p_dictation.cancel_capture()
        self._audio.stop()
        super().hideEvent(e)

    def closeEvent(self, e) -> None:  # noqa: N802
        self.p_dictation.cancel_capture()
        self._audio.stop()
        super().closeEvent(e)

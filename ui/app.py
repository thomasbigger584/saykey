"""
Saykey tray application.

On launch it (optionally) starts the ASR server and the dictation agent, drops a
tray icon, and — unless "start hidden" is set — opens Settings. A second launch
of the app re-opens Settings instead of starting a new instance.
"""

from __future__ import annotations

import sys
import threading
import time

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import autostart
from . import orchestrator as orch
from .config_store import load, save
from .icon import make_icon
from .widgets.settings_window import SettingsWindow

_IPC_NAME = "saykey-ui-singleton"


class App(QObject):
    def __init__(self, argv: list[str]) -> None:
        super().__init__()
        self.qt = QApplication(argv)
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setApplicationName("Saykey")

        self.settings = load()
        self.agent = orch.Agent()
        self._settings_win: SettingsWindow | None = None

        autostart.sync(self.settings.launch_on_startup)

        self._build_tray()

        if self.settings.autostart_server:
            self._start_server_async()
        if self.settings.autostart_agent and orch.agent_supported():
            ok, msg = self.agent.start()
            if not ok:
                self._notify("Dictation agent", msg, warning=True)

        if not self.settings.start_hidden or not self.settings.show_tray_icon:
            self.open_settings()

        # periodic status refresh
        self._poll = QTimer()
        self._poll.timeout.connect(self._refresh_status)
        self._poll.start(5000)

    # ------------------------------------------------------------- tray
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(make_icon("idle"))
        self.tray.setToolTip("Saykey")
        menu = QMenu()

        self.act_status = menu.addAction("Starting…")
        self.act_status.setEnabled(False)
        menu.addSeparator()

        menu.addAction("Settings…", self.open_settings)
        menu.addSeparator()

        self.act_server = menu.addAction("ASR server", self._toggle_server)
        self.act_server.setCheckable(True)
        self.act_agent = menu.addAction("Dictation agent", self._toggle_agent)
        self.act_agent.setCheckable(True)
        self.act_agent.setEnabled(orch.agent_supported())
        menu.addSeparator()

        self.act_startup = menu.addAction("Launch on startup", self._toggle_startup)
        self.act_startup.setCheckable(True)
        self.act_startup.setChecked(autostart.is_enabled())
        menu.addSeparator()

        menu.addAction("Quit", self.quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        if self.settings.show_tray_icon:
            self.tray.show()

    def _tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_settings()

    def _notify(self, title: str, msg: str, warning: bool = False) -> None:
        icon = QSystemTrayIcon.Warning if warning else QSystemTrayIcon.Information
        if self.settings.show_tray_icon:
            self.tray.showMessage(title, msg, icon, 4000)
        elif warning:
            QMessageBox.warning(None, title, msg)

    # ------------------------------------------------------------- settings window
    def open_settings(self) -> None:
        if self._settings_win is None:
            self._settings_win = SettingsWindow()
            self._settings_win.applied.connect(self._on_settings_applied)
        self._settings_win.show()
        self._settings_win.raise_()
        self._settings_win.activateWindow()

    def _on_settings_applied(self, new_settings, changed: dict) -> None:
        self.settings = new_settings
        self.tray.setVisible(new_settings.show_tray_icon)

        if any(k in changed for k in ("shortcut", "shortcut_mode", "mic_device_index",
                                      "debug", "toast_enabled", "toast_position")):
            if self.agent.running():
                self.agent.restart()
                self._notify("Dictation agent", "Restarted with new settings.")

        if any(k in changed for k in ("engine", "model_override", "backend",
                                      "device", "quantization")):
            if changed.get("backend", (None, None))[1] == "server" or self.settings.backend == "server":
                self._prompt_server_restart(changed)

        self._refresh_status()

    def _prompt_server_restart(self, changed: dict) -> None:
        if QMessageBox.question(
            None, "ASR server",
            "The model changed. Rebuild and restart the ASR server now?\n"
            "(first build of a new model can take several minutes)",
        ) == QMessageBox.Yes:
            self._notify("ASR server", "Restarting with the new model…")
            threading.Thread(target=orch.restart_server, args=(self.settings,),
                             daemon=True).start()

    # ------------------------------------------------------------- server / agent
    def _start_server_async(self) -> None:
        if not orch.docker_available():
            self._notify("ASR server",
                         "Docker isn't running — using the local Whisper fallback.",
                         warning=True)
            return
        self._notify("ASR server", "Starting… (first run downloads the model)")
        threading.Thread(target=self._server_up_and_wait, daemon=True).start()

    def _server_up_and_wait(self) -> None:
        proc = orch.start_server(self.settings)
        if proc:
            proc.wait()
        for _ in range(120):
            if orch.server_running(self.settings):
                self._notify("ASR server", "Ready.")
                return
            time.sleep(2)

    def _toggle_server(self, checked: bool) -> None:
        if checked:
            self._start_server_async()
        else:
            threading.Thread(target=orch.stop_server, args=(self.settings,),
                             daemon=True).start()

    def _toggle_agent(self, checked: bool) -> None:
        if checked:
            ok, msg = self.agent.start()
            if not ok:
                self._notify("Dictation agent", msg, warning=True)
                self.act_agent.setChecked(False)
        else:
            self.agent.stop()

    def _toggle_startup(self, checked: bool) -> None:
        autostart.set_enabled(checked)
        s = load()
        s.launch_on_startup = checked
        save(s)
        self.settings = s

    # ------------------------------------------------------------- status
    def _refresh_status(self) -> None:
        server = orch.server_running(self.settings)
        agent = self.agent.running()
        self.act_server.setChecked(server)
        self.act_agent.setChecked(agent)

        if server:
            state, txt = "idle", "ASR server: online"
        elif orch.docker_available():
            state, txt = "busy", "ASR server: starting / offline"
        else:
            state, txt = "offline", "ASR server: offline (local fallback)"
        if agent:
            txt += "  ·  agent: running"
        elif orch.agent_supported():
            txt += "  ·  agent: stopped"
        self.act_status.setText(txt)
        self.tray.setIcon(make_icon(state))
        self.tray.setToolTip(f"Saykey — {txt}")

    # ------------------------------------------------------------- lifecycle
    def quit(self) -> None:
        try:
            self.agent.stop()
        finally:
            self.qt.quit()

    def run(self) -> int:
        return self.qt.exec()


# ------------------------------------------------------------------ single instance
def _raise_existing() -> bool:
    sock = QLocalSocket()
    sock.connectToServer(_IPC_NAME)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    if _raise_existing():
        return 0

    app = App(argv)

    server = QLocalServer()
    QLocalServer.removeServer(_IPC_NAME)
    server.listen(_IPC_NAME)

    def _on_new_conn():
        conn = server.nextPendingConnection()
        if conn:
            conn.readyRead.connect(lambda: (conn.readAll(), app.open_settings()))

    server.newConnection.connect(_on_new_conn)

    return app.run()

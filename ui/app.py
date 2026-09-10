"""
Saykey tray application.

On launch it (optionally) starts the ASR server and the dictation agent, drops a
tray icon, and — unless "start hidden" is set — opens Settings. A second launch
of the app re-opens Settings instead of starting a new instance.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import autostart
from . import orchestrator as orch
from .config_store import load, save
from .icon import make_icon
from .status import StatusFeed
from .widgets.settings_window import SettingsWindow

_IPC_NAME = "saykey-ui-singleton"

# activity word (from the agent) -> (icon state, human label)
_ACTIVITY = {
    "recording":    ("recording", "Recording…"),
    "preparing":    ("busy", "Starting…"),
    "transcribing": ("busy", "Transcribing…"),
    "idle":         ("idle", "Idle"),
    "stopped":      ("offline", "Agent stopped"),
}


def _open_path(path: Path) -> None:
    """Open a file with the OS default handler (used for the log / config.ini)."""
    try:
        if not path.exists():
            path.write_text("", encoding="utf-8")
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:  # noqa: BLE001
        pass


class App(QObject):
    def __init__(self, argv: list[str]) -> None:
        super().__init__()
        self.qt = QApplication(argv)
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setApplicationName("Saykey")

        self.settings = load()
        self.agent = orch.Agent()
        self.status = StatusFeed()
        self._settings_win: SettingsWindow | None = None
        # cached results of the slow network / docker probes (updated off-thread)
        self._server_online = False
        self._docker_ok = False
        self._probing = False
        self._probed_once = False
        self._server_state: str | None = None   # unused|online|starting|unreachable|stopped
        self._server_wanted = True               # False after the user stops it by hand

        autostart.sync(self.settings.launch_on_startup)

        self._build_tray()

        # the server + agent always start with Saykey (the server only when a
        # server model is configured -- a local model needs no Docker).
        if self.settings.backend == "server":
            self._start_server_async()
        if orch.agent_supported():
            ok, msg = self.agent.start()
            if not ok:
                self.status.add("error", f"Dictation agent: {msg}")

        if not self.settings.start_hidden or not self.settings.show_tray_icon:
            self.open_settings()

        # fast, cheap tick: agent activity + the event feed (file reads only)
        self._poll = QTimer()
        self._poll.timeout.connect(self._refresh_status)
        self._poll.start(1000)
        # slow tick: kick a background probe of the ASR server / Docker
        self._probe_timer = QTimer()
        self._probe_timer.timeout.connect(self._kick_probe)
        self._probe_timer.start(3000)
        self._kick_probe()
        self._refresh_status()

    # ------------------------------------------------------------- tray
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(make_icon("idle"))
        self.tray.setToolTip("Saykey")
        menu = QMenu()

        self.act_status = menu.addAction("Starting…")
        self.act_status.setEnabled(False)
        menu.addSeparator()

        menu.addAction("Open Saykey…", self.open_settings)
        menu.addSeparator()

        self.act_server = menu.addAction("ASR server", self._toggle_server)
        self.act_server.setCheckable(True)
        self.act_agent = menu.addAction("Dictation agent", self._toggle_agent)
        self.act_agent.setCheckable(True)
        self.act_agent.setEnabled(orch.agent_supported())
        self.act_button = menu.addAction("Floating talk button", self._toggle_button)
        self.act_button.setCheckable(True)
        self.act_button.setChecked(self.settings.button_enabled)
        menu.addSeparator()

        self.act_startup = menu.addAction("Launch on startup", self._toggle_startup)
        self.act_startup.setCheckable(True)
        self.act_startup.setChecked(autostart.is_enabled())

        # developer-only shortcuts
        self._dev_menu = menu.addMenu("Developer")
        self._dev_menu.addAction("Open log file", self._open_log)
        self._dev_menu.addAction("Edit config.ini", self._edit_config)
        self._dev_menu.addAction("Restart dictation agent", self._restart_agent)
        self._dev_menu.menuAction().setVisible(self.settings.developer_options)
        menu.addSeparator()

        menu.addAction("Quit Saykey", self.quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        if self.settings.show_tray_icon:
            self.tray.show()

    def _tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_settings()

    def _notify(self, title: str, msg: str, warning: bool = False) -> None:
        """No more system notifications -- everything goes to the in-app feed."""
        self.status.add("warn" if warning else "info",
                        f"{title}: {msg}" if title else msg)
        self._refresh_status()

    # ------------------------------------------------------------- settings window
    def open_settings(self) -> None:
        if self._settings_win is None:
            self._settings_win = SettingsWindow()
            self._settings_win.applied.connect(self._on_settings_applied)
            self._settings_win.recording_hotkey.connect(self._suspend_agent)
            self._settings_win.open_log.connect(self._open_log)
            self._settings_win.edit_config.connect(self._edit_config)
            self._settings_win.restart_agent.connect(self._restart_agent)
            self._settings_win.quit_app.connect(self._quit_full)
        self._settings_win.show()
        self._settings_win.raise_()
        self._settings_win.activateWindow()
        self._refresh_status()

    def _on_settings_applied(self, new_settings, changed: dict) -> None:
        self.settings = new_settings
        self.tray.setVisible(new_settings.show_tray_icon)
        self.act_button.setChecked(new_settings.button_enabled)
        self._dev_menu.menuAction().setVisible(new_settings.developer_options)

        if "backend" in changed:
            self._server_wanted = new_settings.backend == "server"

        # a live re-read on the agent, no restart needed
        if "button_enabled" in changed and self.agent.running():
            self.agent.signal_button()

        # keys the agent / recorder read only at start-up -> restart to apply
        _AGENT_KEYS = ("shortcut", "shortcut_mode", "mic_device_index", "debug",
                       "toast_enabled", "toast_position", "button_position",
                       "injection_mode", "key_delay", "chunk_size", "chunk_delay",
                       "language")
        if any(k in changed for k in _AGENT_KEYS):
            if self.agent.running():
                self.agent.restart()
                self.status.add("info", "Dictation agent restarted with new settings")

        if changed.get("backend", (None, None))[1] == "local":
            # a local model needs no container -- shut it down
            self.status.add("info", "ASR server: stopping (the local model needs no Docker)")
            self._server_online = False
            threading.Thread(target=orch.stop_server, args=(self.settings,),
                             daemon=True).start()
        elif any(k in changed for k in ("engine", "model_override", "backend",
                                        "device", "quantization")):
            if (changed.get("backend", (None, None))[1] == "server"
                    or self.settings.backend == "server"):
                self._prompt_server_restart(changed)

        # the recorder daemon resolves the backend at start-up, so a server<->local
        # switch needs it restarted to actually take effect
        if "backend" in changed and self.agent.running():
            self.agent.restart()
            self.status.add("info", "Dictation agent restarted for the new backend")

        self._refresh_status()

    def _prompt_server_restart(self, changed: dict) -> None:
        if QMessageBox.question(
            None, "ASR server",
            "The model changed. Rebuild and restart the ASR server now?\n"
            "(first build of a new model can take several minutes)",
        ) == QMessageBox.Yes:
            self.status.add("info", "ASR server: restarting with the new model…")
            threading.Thread(target=self._server_task, args=("restart",),
                             daemon=True).start()

    # ------------------------------------------------------------- tray actions
    def _toggle_server(self, checked: bool) -> None:
        if checked:
            self._start_server_async()
        else:
            self._server_wanted = False
            self._server_online = False
            self.status.add("info", "ASR server: stopped")
            threading.Thread(target=orch.stop_server, args=(self.settings,),
                             daemon=True).start()

    def _toggle_agent(self, checked: bool) -> None:
        if checked:
            ok, msg = self.agent.start()
            if not ok:
                self.status.add("error", f"Dictation agent: {msg}")
                self.act_agent.setChecked(False)
        else:
            self.status.add("info", "Dictation agent stopped")
            self.agent.stop()

    def _toggle_button(self, checked: bool) -> None:
        s = load()
        s.button_enabled = checked
        save(s)
        self.settings = s
        if self.agent.running():
            self.agent.signal_button()
        self.status.add("info", f"Floating talk button {'on' if checked else 'off'}")

    def _restart_agent(self) -> None:
        if orch.agent_supported():
            self.agent.restart()
            self.status.add("info", "Dictation agent restarted")

    def _suspend_agent(self, on: bool) -> None:
        """Pause / resume every trigger while the shortcut field is capturing."""
        self.agent.set_suspended(on)

    def _open_log(self) -> None:
        from .paths import LOG_FILE
        _open_path(LOG_FILE)

    def _edit_config(self) -> None:
        from .paths import CONFIG_FILE
        _open_path(CONFIG_FILE)

    def _toggle_startup(self, checked: bool) -> None:
        autostart.set_enabled(checked)
        s = load()
        s.launch_on_startup = checked
        save(s)
        self.settings = s

    # ------------------------------------------------------------- server
    def _start_server_async(self) -> None:
        # everything (incl. the blocking `docker info`) happens off the UI thread;
        # _refresh_status turns the result into a single status-feed message
        self._server_wanted = True
        threading.Thread(target=self._server_task, args=("start",), daemon=True).start()

    def _server_task(self, kind: str) -> None:
        """Bring the container up/back; leave the messaging to _refresh_status."""
        if not orch.docker_available():
            self._docker_ok = False
            self._probed_once = True
            return                                # -> "not reachable" transition
        if kind == "start":
            self.status.add("info", "ASR server: starting… (first run downloads the model)")
            proc = orch.start_server(self.settings)
            if proc:
                proc.wait()
        else:
            orch.restart_server(self.settings)
        self._probed_once = True
        for _ in range(150):                       # up to ~5 min for a first build
            self._server_online = orch.server_running(self.settings)
            if self._server_online:
                return
            if not orch.docker_available():        # Docker Desktop was closed
                self._docker_ok = False
                return
            time.sleep(2)

    # ------------------------------------------------------------- status
    def _kick_probe(self) -> None:
        """Run the blocking server / Docker checks on a worker thread."""
        if self._probing:
            return
        self._probing = True

        def work():
            try:
                if self.settings.backend != "server":
                    self._server_online = self._docker_ok = False
                    return
                self._server_online = orch.server_running(self.settings)
                self._docker_ok = self._server_online or orch.docker_available()
            finally:
                self._probed_once = True
                self._probing = False

        threading.Thread(target=work, daemon=True).start()

    _SERVER_TXT = {
        "unused": "not used (local model)",
        "online": "online",
        "starting": "starting…",
        "unreachable": "not reachable — local fallback",
        "stopped": "stopped",
    }
    _SERVER_EVENT = {
        "online": ("ok", "ASR server: online"),
        "unreachable": ("warn", "ASR server not reachable — Docker Desktop isn't "
                        "running. Dictation still works via the local Whisper fallback."),
    }

    def _server_status(self) -> str:
        if self.settings.backend != "server":
            return "unused"
        if self._server_online:
            return "online"
        if not self._server_wanted:
            return "stopped"
        if self._docker_ok:
            return "starting"
        return "unreachable"

    def _refresh_status(self) -> None:
        self.status.refresh()
        server = self._server_online
        agent = self.agent.running()
        self.act_server.setChecked(server)
        self.act_agent.setChecked(agent)

        srv = self._server_status()
        if not self._probed_once and srv != "unused":
            server_txt = "checking…"
        else:
            # one status-feed message per real transition
            if srv != self._server_state:
                self._server_state = srv
                evt = self._SERVER_EVENT.get(srv)
                if evt:
                    self.status.add(*evt)
            server_txt = self._SERVER_TXT[srv]
        agent_txt = "running" if agent else ("stopped" if orch.agent_supported()
                                             else "n/a on this OS")

        activity = self.status.activity if agent else "stopped"
        act_state, act_label = _ACTIVITY.get(activity, ("idle", ""))

        if act_state == "recording":
            state = "recording"
        elif act_state == "busy":
            state = "busy"
        elif srv in ("unused", "online"):
            state = "idle"
        elif srv == "starting":
            state = "busy"
        else:
            state = "offline"

        # only surface the activity word when something is actually happening
        prefix = f"{act_label}   ·   " if act_state in ("recording", "busy") else ""
        line = f"{prefix}server {server_txt}   ·   agent {agent_txt}"
        self.act_status.setText(line)
        self.tray.setIcon(make_icon(state))
        self.tray.setToolTip(f"Saykey — {line}")

        if self._settings_win is not None and self._settings_win.isVisible():
            self._settings_win.set_status(
                server_txt=server_txt,
                agent_ok=agent,
                activity=activity if agent else "stopped",
                events=self.status.events(),
                shortcut=self.settings.shortcut)

    # ------------------------------------------------------------- lifecycle
    def _quit_full(self) -> None:
        """The in-app Quit button: stop the ASR container too, then exit
        (same effect as scripts/stop.ps1)."""
        if self._settings_win is not None:
            self._settings_win.hide()
        threading.Thread(target=orch.stop_server_detached,
                         args=(self.settings,), daemon=True).start()
        self.quit()

    def quit(self) -> None:
        try:
            self.agent.set_suspended(False)
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

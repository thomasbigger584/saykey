"""
Starts / stops the two moving parts:

  * ASR server      -- a local `uvicorn` child process (no Docker), cross-platform
  * dictation agent -- the thing that owns the global hotkey and types the
                       transcript into the focused window.
                       Windows: saykey.ahk (AutoHotkey v2).
                       macOS / Linux: not implemented yet -- a native agent is
                       planned; until then the UI manages the server + settings
                       and you run the agent yourself.
"""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

from .config_store import Settings
from .paths import (
    AHK_SCRIPT,
    ASR_LOG_FILE,
    CTL_DIR,
    MODELS_DIR,
    PROJECT_ROOT,
    SERVER_DIR,
    venv_python,
)

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


# ============================================================ ASR server
def _has_nvidia_gpu() -> bool:
    return gpu_name() is not None


@functools.lru_cache(maxsize=1)
def gpu_name() -> str | None:
    """Name of the first NVIDIA GPU, or None (no nvidia-smi / no GPU / macOS).
    Cached -- the hardware doesn't change while the app runs."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=8, creationflags=_NO_WINDOW)
        if out.returncode != 0:
            return None
        name = (out.stdout or "").splitlines()[0].strip()
        return name or None
    except Exception:  # noqa: BLE001
        return None


def server_health(settings: Settings) -> dict | None:
    url = f"http://127.0.0.1:{settings.server_port}/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def server_running(settings: Settings) -> bool:
    return server_health(settings) is not None


_PROGRESS_RE = re.compile(r"(\d{1,3})%\|")


class AsrServer:
    """Owns the local ASR server process -- a plain `uvicorn` child of this
    app, running server/app.py in the project's own venv. No Docker, no
    daemon: switching models just means stopping and re-starting this
    process with new env vars.

    Its stdout/stderr are drained by a background thread into ASR_LOG_FILE
    and scanned for a few recognisable lines (huggingface_hub download
    progress, engine-ready / engine-load-failed) so the UI can show more than
    a static "starting...", including a progress bar while a model
    downloads. Draining the pipe also avoids the child blocking once the OS
    pipe buffer fills -- nobody was reading it before."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._phase = "stopped"        # stopped|launching|loading|ready|error
        self._progress: int | None = None
        self._detail = ""
        self._intentional_stop = False

    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def status(self) -> tuple[str, int | None, str]:
        """(phase, progress 0-100 or None, human detail line)."""
        with self._lock:
            return self._phase, self._progress, self._detail

    def start(self, settings: Settings) -> tuple[bool, str]:
        if self.running():
            return True, "already running"
        if not (SERVER_DIR / "app.py").exists():
            return False, "missing server/app.py"
        MODELS_DIR.mkdir(exist_ok=True)
        python = venv_python()
        env = os.environ.copy()
        env.update(
            ASR_ENGINE=settings.engine,
            ASR_MODEL=settings.model_override,
            ASR_DEVICE=settings.device,
            ASR_QUANTIZATION=settings.quantization,
            HF_HOME=str(MODELS_DIR),   # replaces the old Docker `../models:/models` mount
            PYTHONUNBUFFERED="1",
        )
        self._intentional_stop = False
        with self._lock:
            self._phase, self._progress = "launching", None
            self._detail = "launching the ASR server process…"
        try:
            proc = subprocess.Popen(
                [python, "-m", "uvicorn", "app:app",
                 "--host", "127.0.0.1", "--port", str(settings.server_port)],
                cwd=str(SERVER_DIR), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, creationflags=_NO_WINDOW,
            )
        except OSError as exc:
            self._proc = None
            with self._lock:
                self._phase, self._detail = "error", f"failed to launch: {exc}"
            return False, f"failed to launch: {exc}"
        self._proc = proc
        threading.Thread(target=self._pump, args=(proc,), daemon=True).start()
        return True, "started"

    def _pump(self, proc: subprocess.Popen) -> None:
        try:
            with open(ASR_LOG_FILE, "a", encoding="utf-8", errors="replace") as log:
                log.write(f"\n--- Saykey ASR server starting (PID {proc.pid}) ---\n")
                for line in iter(proc.stdout.readline, ""):
                    line = line.rstrip("\r\n")
                    if not line:
                        continue
                    log.write(line + "\n")
                    log.flush()
                    self._interpret(line)
        except Exception:  # noqa: BLE001
            pass
        finally:
            code = proc.poll()
            with self._lock:
                if not self._intentional_stop and code not in (None, 0):
                    self._phase = "error"
                    if not self._detail:
                        self._detail = f"process exited (code {code})"

    def _interpret(self, line: str) -> None:
        m = _PROGRESS_RE.search(line)
        if m:
            name = line.split(":", 1)[0].strip()[:40]
            pct = int(m.group(1))
            with self._lock:
                self._phase = "loading"
                self._progress = pct
                self._detail = f"downloading {name}… {pct}%" if name else f"downloading model… {pct}%"
            return
        low = line.lower()
        if "engine ready" in low:
            with self._lock:
                self._phase, self._progress = "ready", 100
                self._detail = line.split("]", 1)[-1].strip() or "engine ready"
            return
        if "engine load failed" in low:
            with self._lock:
                self._phase, self._progress = "error", None
                self._detail = line.split("]", 1)[-1].strip() or "engine load failed"
            return
        if ("application startup complete" in low or "uvicorn running on" in low):
            with self._lock:
                if self._phase == "launching":
                    self._phase = "loading"
                    self._detail = "loading the model… (first use can take several minutes)"

    def stop(self) -> None:
        self._intentional_stop = True
        if not self.running():
            self._proc = None
            with self._lock:
                self._phase, self._progress, self._detail = "stopped", None, ""
            return
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None
        with self._lock:
            self._phase, self._progress, self._detail = "stopped", None, ""

    def restart(self, settings: Settings) -> tuple[bool, str]:
        self.stop()
        return self.start(settings)


# ========================================================= dictation agent
def agent_supported() -> bool:
    return sys.platform == "win32"


def _find_autohotkey() -> str | None:
    for c in (
        os.path.expandvars(r"%ProgramFiles%\AutoHotkey\v2\AutoHotkey64.exe"),
        os.path.expandvars(r"%ProgramFiles%\AutoHotkey\v2\AutoHotkey32.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\AutoHotkey\v2\AutoHotkey64.exe"),
        os.path.expandvars(r"%ProgramFiles%\AutoHotkey\AutoHotkey64.exe"),
    ):
        if Path(c).exists():
            return c
    return shutil.which("AutoHotkey64.exe") or shutil.which("AutoHotkey.exe")


class Agent:
    """Owns the running dictation-agent process (AutoHotkey on Windows)."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None

    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> tuple[bool, str]:
        if self.running():
            return True, "already running"
        if not agent_supported():
            return False, "the dictation agent is Windows-only for now (AutoHotkey)"
        ahk = _find_autohotkey()
        if not ahk:
            return False, "AutoHotkey v2 not found -- install it (winget install AutoHotkey.AutoHotkey)"
        if not AHK_SCRIPT.exists():
            return False, f"missing {AHK_SCRIPT.name}"
        self._proc = subprocess.Popen([ahk, str(AHK_SCRIPT)], cwd=str(PROJECT_ROOT))
        return True, "started"

    def stop(self) -> None:
        _signal("ui.quit")                       # let the agent exit cleanly
        if self.running():
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
        self._proc = None

    def restart(self) -> tuple[bool, str]:
        self.stop()
        return self.start()

    def signal_button(self) -> None:
        """Tell a running agent to re-read [button] enabled (no restart)."""
        _signal("ui.button")

    def signal_reload(self) -> None:
        _signal("ui.reload")

    def set_suspended(self, on: bool) -> None:
        """Pause / resume all triggers -- used while the UI captures a shortcut."""
        if on:
            _signal("ui.suspend")
        else:
            try:
                (CTL_DIR / "ui.suspend").unlink(missing_ok=True)
            except OSError:
                pass


def _signal(name: str) -> None:
    try:
        CTL_DIR.mkdir(parents=True, exist_ok=True)
        (CTL_DIR / name).write_text("1", encoding="utf-8")
    except OSError:
        pass

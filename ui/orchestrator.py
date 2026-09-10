"""
Starts / stops the two moving parts:

  * ASR server      -- the Docker container (docker compose), cross-platform
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
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from .config_store import Settings
from .paths import (
    AHK_SCRIPT,
    COMPOSE_FILE,
    COMPOSE_GPU_FILE,
    CTL_DIR,
    MODELS_DIR,
    PROJECT_ROOT,
    SERVER_DIR,
)

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


# ============================================================ ASR server
def docker_available() -> bool:
    """True only if the Docker CLI exists AND the daemon answers (Desktop running)."""
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=8,
                               creationflags=_NO_WINDOW).returncode == 0
    except Exception:  # noqa: BLE001  (timeout / OSError -> daemon not reachable)
        return False


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


def _compose_cmd(settings: Settings, extra: list[str]) -> list[str]:
    files = ["-f", str(COMPOSE_FILE)]
    if _has_nvidia_gpu() and settings.device != "cpu":
        files += ["-f", str(COMPOSE_GPU_FILE)]
    return ["docker", "compose", *files, *extra]


def _compose_env(settings: Settings) -> dict:
    env = os.environ.copy()
    gpu = _has_nvidia_gpu() and settings.device != "cpu"
    env.update(
        ASR_ENGINE=settings.engine,
        ASR_MODEL=settings.model_override,
        ASR_DEVICE=settings.device,
        ASR_QUANTIZATION=settings.quantization,
        ASR_PORT=str(settings.server_port),
        ORT_PACKAGE="onnxruntime-gpu" if gpu else "onnxruntime",
        INSTALL_HF="true" if settings.engine.startswith("hf:") else "false",
    )
    return env


def server_health(settings: Settings) -> dict | None:
    url = f"http://127.0.0.1:{settings.server_port}/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def server_running(settings: Settings) -> bool:
    return server_health(settings) is not None


def start_server(settings: Settings) -> subprocess.Popen | None:
    if not COMPOSE_FILE.exists() or not docker_available():
        return None
    MODELS_DIR.mkdir(exist_ok=True)
    return subprocess.Popen(
        _compose_cmd(settings, ["up", "-d", "--build"]),
        cwd=str(SERVER_DIR), env=_compose_env(settings),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=_NO_WINDOW,
    )


def stop_server(settings: Settings) -> None:
    if not COMPOSE_FILE.exists() or not docker_available():
        return
    subprocess.run(_compose_cmd(settings, ["down"]), cwd=str(SERVER_DIR),
                   env=_compose_env(settings), capture_output=True,
                   creationflags=_NO_WINDOW)


def restart_server(settings: Settings) -> None:
    if not COMPOSE_FILE.exists() or not docker_available():
        return
    subprocess.run(_compose_cmd(settings, ["up", "-d", "--build", "--force-recreate"]),
                   cwd=str(SERVER_DIR), env=_compose_env(settings),
                   capture_output=True, creationflags=_NO_WINDOW)


def stop_server_detached(settings: Settings) -> None:
    """Fire `docker compose down` and return at once -- it finishes on its own
    even after the app has exited. Used by the in-app Quit button."""
    if not COMPOSE_FILE.exists():
        return
    kwargs: dict = dict(cwd=str(SERVER_DIR), env=_compose_env(settings),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        stdin=subprocess.DEVNULL)
    if sys.platform == "win32":
        kwargs["creationflags"] = _NO_WINDOW | 0x00000008   # DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(_compose_cmd(settings, ["down"]), **kwargs)
    except Exception:  # noqa: BLE001
        pass


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

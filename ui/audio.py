"""Microphone enumeration + a lightweight live input-level sampler.

Cross-platform via ``sounddevice`` (PortAudio). The sampler only holds the
mic open while it's explicitly running (the window starts it when visible on
a mic-showing view and stops it during a recording / when hidden), so the OS
mic indicator isn't on the whole time.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QObject, QTimer, Signal


def list_input_devices() -> list[tuple[int, str]]:
    """[(device_index, label)]  -- index -1 means 'system default'."""
    devices = [(-1, "System default")]
    try:
        import sounddevice as sd

        try:
            default_in = sd.default.device[0]
        except Exception:  # noqa: BLE001
            default_in = None
        for idx, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] < 1:
                continue
            name = dev["name"]
            if idx == default_in:
                name += "  (default)"
            devices.append((idx, name))
    except Exception as exc:  # noqa: BLE001
        devices.append((-2, f"(no audio devices: {exc})"))
    return devices


class AudioLevel(QObject):
    """Emits ``level`` in 0.0‥1.0 a few times a second while running."""

    level = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stream = None
        self._device: int | None = None
        self._peak = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._emit)

    def set_device(self, index: int) -> None:
        new = None if index is None or index < 0 else index
        if new == self._device:
            return
        self._device = new
        if self._stream is not None:          # restart on the new device
            self.stop()
            self.start()

    def start(self) -> None:
        if self._stream is not None:
            return
        try:
            import numpy as np
            import sounddevice as sd

            def cb(indata, frames, t, status):  # noqa: ANN001, ARG001
                if indata.size:
                    rms = float(np.sqrt(np.mean(np.square(indata[:, 0]))))
                    # ~ -50 dBFS floor -> 0, 0 dBFS -> 1
                    db = 20 * math.log10(rms + 1e-7)
                    self._peak = max(self._peak, min(1.0, max(0.0, (db + 50) / 50)))

            self._stream = sd.InputStream(
                samplerate=16000, channels=1, dtype="float32",
                blocksize=1024, device=self._device, callback=cb)
            self._stream.start()
            self._timer.start()
        except Exception:  # noqa: BLE001  -- no mic / device busy: just stay at 0
            self._stream = None

    def stop(self) -> None:
        self._timer.stop()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._stream = None
        self._peak = 0.0
        self.level.emit(0.0)

    def running(self) -> bool:
        return self._stream is not None

    def _emit(self) -> None:
        self.level.emit(self._peak)
        self._peak *= 0.55                     # decay between reads

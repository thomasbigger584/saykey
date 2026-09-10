#!/usr/bin/env python3
"""
record.py -- local microphone capture + transcription dispatch.

Part of the "VDI Dictate" project.

Two ways to run:
  * ``--serve``  the resident recorder daemon used by vdi-dictate.ahk: keeps the
                 microphone and transcription backend warm and is driven by
                 signal files in ``--control-dir`` (see serve() below).
  * one-shot     record once and exit -- used for ``--warmup``, ``--list-devices``,
                 ``--transcribe-wav``, and manual testing.

One-shot capture stops on the first of:
  * ``--stop-file`` appearing on disk
  * ``--silence-timeout`` seconds of trailing silence (0 disables this)
  * ``--max-seconds`` elapsed (hard safety cap)

The captured audio is handed to the configured transcription backend
(``[transcription] backend`` in config.ini):
  * ``server`` -> POST to the ASR HTTP server (Docker container / Parakeet),
                  with automatic fall back to local faster-whisper
  * ``local``  -> in-process faster-whisper

The transcript is written to ``--output`` (UTF-8, no BOM) and echoed to stdout.
``--done-file`` is written last, as a "transcript is ready" signal.
"""

from __future__ import annotations

import argparse
import configparser
import os
import sys
import time
import traceback
import wave
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")

SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK_SECONDS = 0.1

_LOG_PATH: "Path | None" = None


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    try:
        if sys.stderr is not None:
            print(line, file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001  (pythonw has no usable stderr)
        pass
    if _LOG_PATH is not None:
        try:
            with _LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


def rms(block) -> float:
    import numpy as np

    if block.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(block.astype("float64")))))


def list_devices(output: "Path | None") -> None:
    import sounddevice as sd

    default_in = sd.default.device[0]
    lines = [
        "Audio input devices",
        "(put the index into config.ini -> [recording] device_index; -1 = system default)",
        "",
    ]
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] < 1:
            continue
        mark = "   <-- current default" if idx == default_in else ""
        lines.append(
            f"  [{idx:>2}] {dev['name']}  "
            f"({dev['max_input_channels']} ch @ {int(dev['default_samplerate'])} Hz){mark}"
        )
    text = "\n".join(lines) + "\n"
    if output:
        output.write_text(text, encoding="utf-8")
    print(text)


def read_wav(path: Path):
    """Load a WAV file as mono float32 at SAMPLE_RATE (for --transcribe-wav)."""
    import numpy as np

    with wave.open(str(path), "rb") as wf:
        rate, width, chans = wf.getframerate(), wf.getsampwidth(), wf.getnchannels()
        raw = wf.readframes(wf.getnframes())
    dtype = {1: np.int8, 2: np.int16, 4: np.int32}.get(width)
    if dtype is None:
        raise ValueError(f"unsupported sample width: {width} bytes")
    data = np.frombuffer(raw, dtype=dtype).astype("float32")
    data = (data - 128.0) / 128.0 if width == 1 else data / float(np.iinfo(dtype).max)
    if chans > 1:
        data = data.reshape(-1, chans).mean(axis=1)
    if rate != SAMPLE_RATE and data.size:
        idx = np.linspace(0, data.size - 1, int(data.size * SAMPLE_RATE / rate))
        data = np.interp(idx, np.arange(data.size), data).astype("float32")
    return data


def record(args):
    import numpy as np
    import sounddevice as sd

    device = None
    if args.device_index is not None and args.device_index >= 0:
        device = args.device_index

    blocksize = int(SAMPLE_RATE * BLOCK_SECONDS)
    stop_file = Path(args.stop_file) if args.stop_file else None
    if stop_file and stop_file.exists():
        stop_file.unlink()

    frames = []
    speech_started = False
    silence_run = 0.0
    elapsed = 0.0

    log(f"opening input stream (device={device if device is not None else 'default'})")
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="float32",
                        blocksize=blocksize, device=device) as stream:
        if args.ready_file:
            Path(args.ready_file).write_text("1", encoding="utf-8")

        noise = []
        for _ in range(int(0.4 / BLOCK_SECONDS)):
            blk, _of = stream.read(blocksize)
            noise.append(rms(blk))
            frames.append(blk.copy())
            elapsed += BLOCK_SECONDS
        floor = sorted(noise)[len(noise) // 2] if noise else 0.0
        threshold = max(floor * 3.0, args.silence_threshold)
        log(f"noise floor={floor:.4f}  silence threshold={threshold:.4f}")

        while True:
            blk, _of = stream.read(blocksize)
            frames.append(blk.copy())
            elapsed += BLOCK_SECONDS
            level = rms(blk)

            if level >= threshold:
                speech_started = True
                silence_run = 0.0
            elif speech_started:
                silence_run += BLOCK_SECONDS

            if stop_file is not None and stop_file.exists():
                log("stop-file seen -> stopping")
                break
            if args.max_seconds and elapsed >= args.max_seconds:
                log("max-seconds reached -> stopping")
                break
            if args.silence_timeout and speech_started and silence_run >= args.silence_timeout:
                log("silence timeout reached -> stopping")
                break

    audio = (np.concatenate(frames, axis=0).astype("float32").flatten()
             if frames else np.zeros(0, dtype="float32"))
    log(f"captured {audio.size / SAMPLE_RATE:.1f}s  speech_detected={speech_started}")
    return audio, speech_started


def write_wav(path: Path, audio) -> None:
    import numpy as np

    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())


def _safe_unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    """True if a process with this PID is currently running (Windows)."""
    if not pid:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes

    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_ulong()
    ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
    k32.CloseHandle(handle)
    return bool(ok) and code.value == 259  # STILL_ACTIVE


def serve(args) -> int:
    """
    Resident recorder. Keeps the Python process + transcription backend warm so
    a key press starts capture in ~100 ms instead of ~1 s.

    Control is via files in --control-dir (the AHK script writes/reads these):
        start      -> begin capturing
        stop       -> finish + transcribe + write result.txt, then done
        cancel     -> discard current capture; write cancelled + done
        quit       -> exit the daemon
    The daemon writes:
        up         -> process is alive (contains its PID)
        ready      -> microphone stream is open, safe to speak
        done       -> result.txt is final
        error      -> something failed (contains the message)
        cancelled  -> the capture was aborted
    """
    import numpy as np
    import sounddevice as sd

    import transcriber

    ctl = Path(args.control_dir)
    ctl.mkdir(parents=True, exist_ok=True)
    for name in ("start", "stop", "cancel", "quit", "ready", "done",
                 "result.txt", "up", "error", "cancelled"):
        _safe_unlink(ctl / name)

    cfg = load_transcription_cfg(args.config, args)
    if args.debug:
        safe = {k: ("***" if "key" in k else v) for k, v in cfg.items()}
        log(f"serve: config = {safe}")
        log(f"serve: args = max_seconds={args.max_seconds} silence_timeout={args.silence_timeout} "
            f"silence_threshold={args.silence_threshold} device_index={args.device_index}")
    tr = transcriber.make_transcriber(cfg)
    try:
        tr.warmup()
    except Exception as exc:  # noqa: BLE001
        log(f"serve: warmup warning: {exc}")

    device = None if (args.device_index is None or args.device_index < 0) else args.device_index
    blocksize = int(SAMPLE_RATE * BLOCK_SECONDS)

    parent = int(args.parent_pid) if args.parent_pid else 0

    (ctl / "up").write_text(str(os.getpid()), encoding="utf-8")
    log(f"serve: ready  backend={tr.describe()}  control={ctl}  "
        f"silence_timeout={args.silence_timeout}  parent_pid={parent or 'none'}")

    idle_ticks = 0
    while True:
        if (ctl / "quit").exists():
            break
        if parent and idle_ticks % 100 == 0 and not _pid_alive(parent):
            log("serve: parent process gone -> exiting")
            break
        if not (ctl / "start").exists():
            idle_ticks += 1
            time.sleep(0.01)
            continue
        idle_ticks = 0
        _safe_unlink(ctl / "start")
        for name in ("stop", "cancel", "done", "result.txt", "error", "cancelled"):
            _safe_unlink(ctl / name)
        log("serve: start -> opening stream")

        frames = []
        cancelled = False
        speech_started = False
        silence_run = 0.0
        elapsed = 0.0
        started = time.time()
        try:
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="float32",
                                blocksize=blocksize, device=device) as stream:
                (ctl / "ready").write_text("1", encoding="utf-8")

                noise = []
                for _ in range(int(0.3 / BLOCK_SECONDS)):
                    blk, _of = stream.read(blocksize)
                    noise.append(rms(blk))
                    frames.append(blk.copy())
                    elapsed += BLOCK_SECONDS
                floor = sorted(noise)[len(noise) // 2] if noise else 0.0
                threshold = max(floor * 3.0, args.silence_threshold)

                loops = 0
                peak = 0.0
                while True:
                    blk, _of = stream.read(blocksize)
                    frames.append(blk.copy())
                    elapsed += BLOCK_SECONDS
                    loops += 1
                    if args.debug:
                        lvl = rms(blk)
                        peak = max(peak, lvl)
                        if loops % 10 == 0:
                            log(f"serve: level={lvl:.4f} peak={peak:.4f} thr={threshold:.4f}")

                    if parent and loops % 20 == 0 and not _pid_alive(parent):
                        cancelled = True
                        break

                    if args.silence_timeout:
                        if rms(blk) >= threshold:
                            speech_started = True
                            silence_run = 0.0
                        elif speech_started:
                            silence_run += BLOCK_SECONDS

                    if (ctl / "stop").exists():
                        _safe_unlink(ctl / "stop")
                        break
                    if (ctl / "cancel").exists() or (ctl / "quit").exists():
                        _safe_unlink(ctl / "cancel")
                        cancelled = True
                        break
                    if args.max_seconds and elapsed >= args.max_seconds:
                        break
                    if (args.silence_timeout and speech_started
                            and silence_run >= args.silence_timeout):
                        break
        except Exception as exc:  # noqa: BLE001
            log(f"serve: capture error: {exc}")
            (ctl / "error").write_text(str(exc), encoding="utf-8")
            (ctl / "result.txt").write_text("", encoding="utf-8")
            (ctl / "done").write_text("1", encoding="utf-8")
            _safe_unlink(ctl / "ready")
            continue
        finally:
            _safe_unlink(ctl / "ready")

        audio = (np.concatenate(frames, axis=0).astype("float32").flatten()
                 if frames else np.zeros(0, dtype="float32"))
        text = ""
        if cancelled:
            (ctl / "cancelled").write_text("1", encoding="utf-8")
        elif audio.size:
            try:
                text = tr.transcribe(audio, SAMPLE_RATE)
            except Exception as exc:  # noqa: BLE001
                log(f"serve: transcribe error: {exc}")
                (ctl / "error").write_text(str(exc), encoding="utf-8")

        (ctl / "result.txt").write_text(text, encoding="utf-8")
        (ctl / "done").write_text("1", encoding="utf-8")
        log(f"serve: {'cancelled' if cancelled else 'result'} "
            f"({time.time() - started:.1f}s, {audio.size / SAMPLE_RATE:.1f}s audio): {text!r}")

    _safe_unlink(ctl / "up")
    log("serve: exit")
    return 0


def _resolve(base: "Path | None", value: str) -> str:
    p = Path(value)
    if p.is_absolute():
        return str(p)
    root = base if base is not None else Path.cwd()
    return str((root / p).resolve())


def load_transcription_cfg(config_path: str, args) -> dict:
    cp = configparser.ConfigParser()
    cfg_dir = None
    if config_path and Path(config_path).exists():
        cp.read(config_path, encoding="utf-8")
        cfg_dir = Path(config_path).resolve().parent

    def g(section, key, default=""):
        try:
            return cp.get(section, key).strip()
        except Exception:
            return default

    return {
        "language": (args.language or g("general", "language", "en")),
        "offline": g("general", "offline", "false"),
        "models_dir": _resolve(cfg_dir, g("general", "models_dir", "models")),
        "backend": (args.backend or g("transcription", "backend", "server")),
        "server_url": (args.server_url or g("transcription", "server_url", "http://127.0.0.1:9000")),
        "server_model": g("transcription", "server_model", "parakeet"),
        "server_timeout": g("transcription", "server_timeout", "30"),
        "server_api_key": g("transcription", "server_api_key", ""),
        "fallback_to_local": g("transcription", "fallback_to_local", "true"),
        "local_model": g("local", "model", "base.en"),
        "local_compute_type": g("local", "compute_type", "int8"),
        "local_beam_size": g("local", "beam_size", "1"),
    }


def finish(args, text: str) -> None:
    Path(args.output or "transcript.txt").write_text(text, encoding="utf-8")
    if args.done_file:
        Path(args.done_file).write_text("1", encoding="utf-8")
    if text:
        sys.stdout.write(text + "\n")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    default_cfg = str(Path(__file__).resolve().parent / "config.ini")
    p.add_argument("--config", default=default_cfg, help="path to config.ini")
    p.add_argument("--backend", default="", help="override [transcription] backend")
    p.add_argument("--server-url", default="", help="override [transcription] server_url")
    p.add_argument("--language", default="", help="override language, or 'auto'")

    p.add_argument("--max-seconds", type=float, default=60.0)
    p.add_argument("--silence-timeout", type=float, default=2.0)
    p.add_argument("--silence-threshold", type=float, default=0.012)
    p.add_argument("--device-index", type=int, default=-1)

    p.add_argument("--stop-file", default="")
    p.add_argument("--ready-file", default="")
    p.add_argument("--done-file", default="")
    p.add_argument("--err-file", default="")
    p.add_argument("--output", default="", help="transcript file (default: transcript.txt)")
    p.add_argument("--log-file", default="")
    p.add_argument("--keep-wav", default="", help="also write captured audio to this .wav")
    p.add_argument("--transcribe-wav", default="", help="skip recording, transcribe this .wav")

    p.add_argument("--list-devices", action="store_true")
    p.add_argument("--print-engine", action="store_true", help="print the resolved backend and exit")
    p.add_argument("--warmup", action="store_true", help="load / ping the backend and exit")
    p.add_argument("--serve", action="store_true", help="run the resident recorder daemon")
    p.add_argument("--control-dir", default="", help="signal-file directory for --serve")
    p.add_argument("--parent-pid", default="", help="--serve: exit if this process disappears")
    p.add_argument("--debug", action="store_true", help="verbose logging (config dump, audio levels)")
    return p


def main(argv=None) -> int:
    global _LOG_PATH
    args = build_parser().parse_args(argv)
    if args.log_file:
        _LOG_PATH = Path(args.log_file)

    try:
        if args.list_devices:
            list_devices(Path(args.output) if args.output else None)
            return 0

        if args.serve:
            if not args.control_dir:
                print("--serve requires --control-dir", file=sys.stderr)
                return 2
            return serve(args)

        import transcriber

        cfg = load_transcription_cfg(args.config, args)
        tr = transcriber.make_transcriber(cfg)
        log(f"backend: {tr.describe()}")

        if args.print_engine:
            print(tr.describe())
            return 0

        if args.warmup:
            info = tr.warmup()
            msg = f"warmup ok: {tr.describe()}"
            print(msg)
            log(msg)
            if info:
                print("server health:", info)
                log(f"server health: {info}")
            return 0

        if args.transcribe_wav:
            audio, had_speech = read_wav(Path(args.transcribe_wav)), True
        else:
            audio, had_speech = record(args)
            if args.keep_wav and audio.size:
                try:
                    write_wav(Path(args.keep_wav), audio)
                except Exception as exc:  # noqa: BLE001
                    log(f"wav write failed: {exc}")

        text = tr.transcribe(audio, SAMPLE_RATE) if (had_speech and audio.size) else ""
        log(f"transcript: {text!r}")
        finish(args, text)
        return 0
    except Exception:  # noqa: BLE001
        log("ERROR\n" + traceback.format_exc())
        try:
            if args.err_file:
                Path(args.err_file).write_text("1", encoding="utf-8")
            if args.serve and args.control_dir:
                ctl = Path(args.control_dir)
                (ctl / "error").write_text("recorder daemon crashed", encoding="utf-8")
                (ctl / "done").write_text("1", encoding="utf-8")
                _safe_unlink(ctl / "up")
        except Exception:  # noqa: BLE001
            pass
        if not args.serve:
            try:
                finish(args, "")
            except Exception:  # noqa: BLE001
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())

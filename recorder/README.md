# recorder/ — audio capture + transcription routing

Host-side Python. Captures microphone audio and gets it turned into text, either
by the local ASR server process or the in-process fallback.

## `record.py`

**Resident daemon** (`--serve`, how the agent uses it): keeps the microphone and
the transcription backend warm so a hotkey press starts capture in ~100 ms.
Driven by signal files in `--control-dir`:

| agent writes | daemon writes |
|---|---|
| `start` `stop` `cancel` `quit` | `up` `ready` `done` `result.txt` `error` `cancelled` |

Exits automatically if `--parent-pid` disappears.

**One-shot** (no `--serve`): record once and exit. Used by `install.ps1` and for
troubleshooting:

```
python record.py --list-devices
python record.py --warmup                 # load / ping the configured backend
python record.py --print-engine
python record.py --transcribe-wav a.wav   # skip the mic
```

`--config` defaults to the project-root `config.ini`; `--debug` adds a config
dump and live audio-level logging (for tuning `[recording] silence_threshold`).

## `transcriber.py`

`make_transcriber(cfg)` returns either:

- **`RemoteServer`** — POSTs a 16 kHz mono WAV to an OpenAI-compatible
  `/v1/audio/transcriptions` endpoint, with automatic fall back to…
- **`LocalWhisper`** — in-process `faster-whisper` on the CPU.

Stdlib only for the server path (no extra client dependency).

## Dependencies

`requirements.txt` — `sounddevice`, `numpy`, `faster-whisper` (CPU fallback).
Installed into the shared project `.venv` by `scripts/install.ps1`.

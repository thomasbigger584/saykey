# VDI Dictate

Local, offline **speech-to-text** for Windows that types the transcript into
whatever window has focus — including remote-desktop / VDI clients (Citrix,
VMware Horizon, RDP, AVD) where host-to-guest **clipboard sharing is disabled**.

Nothing is pasted. The transcribed text is injected as ordinary keystrokes via
AutoHotkey's `SendInput`, the same way a hardware keyboard or Dragon /
Windows Speech Recognition sends input.

**Push-to-talk by default:** hold **Ctrl+Space**, speak, release — the text is
typed into whatever has focus. (Switch to press-once/press-again with
`[hotkey] mode = toggle`.) A resident recorder process keeps the microphone and
the transcription backend warm, so a key press starts capture in ~100 ms.

Transcription runs on **NVIDIA Parakeet** by default, served from a local
**Docker** container on your GPU. The engine is swappable — Parakeet v2/v3,
Canary, Whisper, any HuggingFace ASR model, or any OpenAI-compatible endpoint —
via one line in `config.ini`. If the container isn't running, the client falls
back to in-process `faster-whisper` on the CPU so dictation still works.

---

## Architecture

```
  hold Ctrl+Space  (global hotkey, host)
       │  press → "start" signal     release → "stop" signal
       ▼
  vdi-dictate.ahk ── manages ──►  record.py --serve   (resident, .venv)
       │                                │  mic capture (sounddevice), warm
       │                                │
       │                        transcriber.py
       │                          ├── backend = server ──► POST /v1/audio/transcriptions
       │                          │                         ┌───────────────────────────┐
       │                          │                         │  Docker: vdi-dictate-asr  │
       │                          │                         │  FastAPI + engine:        │
       │                          │                         │   Parakeet (onnx-asr) GPU │
       │                          │                         └───────────────────────────┘
       │                          └── fallback ───────────► faster-whisper (CPU, in-process)
       │       ◄──── transcript ────────┘
       ▼
  SendInput "{Raw}…"  ──►  focused window = VDI / RDP / local app
                            keystrokes only — no clipboard
```

- **`vdi-dictate.ahk`** — global hotkey (push-to-talk or toggle), starts/monitors the recorder, `SendInput` injection, tray UI.
- **`record.py --serve`** — resident recorder: holds the mic + backend warm, driven by signal files in `%TEMP%\vdi_dictate_ctl`. Exits automatically if the AHK script goes away. (`record.py` without `--serve` still works one-shot for `--warmup` / `--transcribe-wav` / `--list-devices`.)
- **`transcriber.py`** — routes to the ASR server (with automatic local fallback) or straight to local `faster-whisper`.
- **`server/`** — the Dockerised ASR service (see [`server/README.md`](server/README.md)).
- **`run-server.ps1`** — start/stop/observe the container; reads model choice from `config.ini`.

---

## Security / privacy

- **Offline.** The container downloads the model once, then serves it locally; no
  audio or text leaves the machine. The local fallback runs with `HF_HUB_OFFLINE`
  after setup. HF telemetry is disabled.
- **No clipboard.** The host clipboard is never touched — clipboard-isolation
  policy is unaffected. `SendInput` delivers the same keystrokes you could type
  by hand; it doesn't bypass DLP, session recording, or any other control, and
  moves no data out of the remote session.
- **Local only.** Audio stays in RAM; the transcript lives in `%TEMP%\vdi_dictate.txt`
  and is deleted after it's typed.
- **No elevation.** Nothing here needs admin (installing Docker/AHK/Python via winget may).

---

## Requirements

| Component | Version | For |
|---|---|---|
| Windows | 10 / 11 | — |
| AutoHotkey | **v2** | the hotkey script |
| Python | 3.9+ | capture + local fallback |
| Docker Desktop | recent | the Parakeet ASR server (GPU strongly recommended) |
| NVIDIA GPU + driver | any recent | Parakeet at full speed (CPU works but is slow) |

`backend = local` (Whisper only, CPU) needs neither Docker nor a GPU.

---

## Install

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Common variants:

```powershell
.\install.ps1 -StartServer                 # also build + launch the Docker server now
.\install.ps1 -Engine parakeet-v3          # multilingual Parakeet
.\install.ps1 -Backend local               # Whisper-only, no server
.\install.ps1 -Yes                         # non-interactive
```

The installer sets up the client `.venv`, downloads the Whisper fallback model,
writes `config.ini`, and (for `-Backend server`) offers to build + start the
container. First container build pulls several GB and downloads the Parakeet
model — `run-server.ps1 up` waits up to 5 minutes for it.

Start the ASR server any time:

```powershell
.\run-server.ps1 up          # build if needed, start, wait for /health
.\run-server.ps1 status
.\run-server.ps1 logs -Follow
.\run-server.ps1 down
```

---

## Usage

Start it: double-click **`vdi-dictate.ahk`** (or `start.ps1`). A tray icon
appears and a "Recorder ready" notification shows once the backend is warm.

**Push-to-talk** (`[hotkey] mode = hold`, default):

1. Click into the target window (VDI session, text box, editor…).
2. **Hold Ctrl+Space.** An on-screen indicator appears immediately ("starting"),
   then turns red **"RECORDING"** with a beep once the mic is actually live.
3. Speak while holding.
4. **Release.** The indicator disappears and the transcript is typed in.

A tap shorter than `min_hold_ms` (250 ms) is ignored.

The indicator is a small always-on-top pill that never takes keyboard focus.
Configure it under `[toast]` (position / size), or `enabled = false` to hide it.

**Toggle** (`[hotkey] mode = toggle`): press Ctrl+Space to start, press again (or
pause ~2 s) to stop.

**Ctrl+Shift+Space** cancels the current recording without transcribing.

Tray menu: edit config, list audio devices, check the transcription backend,
**restart recorder** (after changing config), open the log, and an **ASR Docker
server** submenu (start/status/logs/stop).

---

## Swapping the transcription model

Everything is driven by `config.ini`.

**Where transcription happens** — `[transcription] backend`:

| value | meaning |
|---|---|
| `server` *(default)* | POST to the ASR server at `server_url`. Works with the bundled container **or any OpenAI-compatible ASR endpoint** (speaches, an NVIDIA Riva gateway, a remote box, a cloud API). |
| `local` | in-process `faster-whisper` (CPU), no server needed |

**Which model the bundled server loads** — `[server] engine`, then `.\run-server.ps1 restart`:

| `engine` | model | notes |
|---|---|---|
| `parakeet` | `nemo-parakeet-tdt-0.6b-v2` | English, recommended |
| `parakeet-v3` | `nemo-parakeet-tdt-0.6b-v3` | 25 languages |
| `parakeet-ctc` | `nemo-parakeet-ctc-0.6b` | lighter |
| `canary` | `nemo-canary-1b-v2` | multilingual |
| `whisper` | faster-whisper (`[server] model`, e.g. `large-v3`) | |
| `onnx:<id>` | any onnx-asr model id | |
| `fw:<id>` | any faster-whisper model id | |
| `hf:<repo/id>` | any HuggingFace ASR model | build with `run-server.ps1 -Hf` (adds torch) |
| `openai` | forward to `[server] upstream_url` | run the model elsewhere |

`[server] model` overrides the checkpoint; `[server] quantization = int8` makes
onnx-asr / faster-whisper models smaller and faster.

---

## Configuration (`config.ini`)

| Section | Key | Default | Meaning |
|---|---|---|---|
| `general` | `python` | `.venv\Scripts\pythonw.exe` | interpreter for `record.py` |
| | `language` | `en` | language code or `auto` |
| | `models_dir` | `models` | fallback + container model cache |
| | `offline` | `false` → `true` after setup | local fallback: no network |
| | `debug` | `false` | verbose logging to `%TEMP%\vdi_dictate.log` (also a tray toggle) |
| `transcription` | `backend` | `server` | `server` \| `local` |
| | `server_url` | `http://127.0.0.1:9000` | ASR endpoint |
| | `server_model` | `parakeet` | model name sent in the request |
| | `server_timeout` | `30` | seconds |
| | `server_api_key` | — | bearer token for remote endpoints |
| | `fallback_to_local` | `true` | use Whisper if the server is down |
| `server` | `engine` | `parakeet` | model the container loads (table above) |
| | `model` | — | explicit checkpoint override |
| | `device` | `auto` | `cuda` \| `cpu` \| `auto` |
| | `quantization` | `none` | `none` \| `int8` |
| | `port` | `9000` | host port |
| | `image` | `vdi-dictate-asr:latest` | image tag |
| | `upstream_url` / `upstream_key` | — | `engine = openai` target |
| `local` | `model` | `base.en` | faster-whisper model |
| | `compute_type` | `int8` | |
| | `beam_size` | `1` | |
| `recording` | `max_seconds` | `60` | hard cap |
| | `silence_timeout` | `2.0` | auto-stop after N s silence (`0` = off) |
| | `silence_threshold` | `0.012` | RMS level = silence |
| | `device_index` | `-1` | mic index (`-1` = default) |
| `injection` | `mode` | `Raw` | `Raw` \| `Event` \| `Text` |
| | `key_delay` | `10` | ms/key, `Event` mode |
| | `chunk_size` | `20` | chars per burst (`0` = all at once) |
| | `chunk_delay` | `15` | ms between bursts |
| | `trailing_space` / `trim` / `normalize_ascii` / `collapse_newlines` | `true` | text cleanup |
| `hotkey` | `mode` | `hold` | `hold` = push-to-talk, `toggle` = press/press |
| | `min_hold_ms` | `250` | `hold`: ignore taps shorter than this |
| | `key` | `^Space` | dictation key (`^`Ctrl `!`Alt `+`Shift `#`Win) |
| | `cancel` | `^+Space` | cancel without transcribing |
| `toast` | `enabled` | `true` | show the on-screen recording indicator |
| | `position` | `bottom` | `bottom`/`top`/`center`/`bottom-left`/`bottom-right`/`top-left`/`top-right` |
| | `margin` | `90` | pixels from the screen edge |
| | `font_size` | `12` | indicator text size (pt) |

---

## Tuning for your VDI client

| `mode` | mechanism | use when |
|---|---|---|
| `Raw` | `SendInput "{Raw}…"` fast scan-code burst | default; most modern Citrix/Horizon/RDP |
| `Event` | `SendEvent "{Raw}…"` paced by `key_delay` | characters **dropped / doubled / reordered** in `Raw` |
| `Text` | `SendText` Unicode events | non-Latin scripts, non-US guest layouts |

If text comes through wrong: `mode = Event` → raise `key_delay` to 20–40 → raise
`chunk_delay` to 30–60 and lower `chunk_size` to 10 → last resort `mode = Text`.
Keep the guest keyboard layout matching the host for `Raw` / `Event`.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Python executable not found" | run `install.ps1`; check `[general] python` |
| Nothing on Ctrl+Space | another app / IME owns the hotkey — set `[hotkey] key = ^!Space`, tray → Reload |
| "Recorder daemon failed to start" | check the log; usually a mic-permission or dependency issue. Tray → Restart recorder |
| First word clipped | wait for the beep before speaking — it means the mic is open |
| Long pause on first server start | model download — `run-server.ps1 logs -Follow` |
| Dictation works but "server unreachable" in log | container down; `run-server.ps1 up`. Fallback Whisper is being used |
| "No speech detected" | wrong mic (tray → List audio devices → set `device_index`) or raise `silence_threshold` |
| "Recorder error — opening log" | the log opens automatically; usually a mic permission or missing dependency |
| CUDA provider fails in the container | it falls back to CPU automatically; force with `[server] device = cpu` |
| Characters dropped / doubled in VDI | see **Tuning** above |
| Accuracy poor | `engine = whisper` + `[server] model = large-v3`, or `parakeet` (best English) |
| SmartScreen / AV flags `AutoHotkey64.exe` | expected for AHK; allow it |

Check the backend without a microphone:

```powershell
.\.venv\Scripts\python.exe record.py --print-engine
.\.venv\Scripts\python.exe record.py --warmup
.\.venv\Scripts\python.exe record.py --transcribe-wav some-speech.wav
```

Logs: `%TEMP%\vdi_dictate.log`. Server: `run-server.ps1 logs`.

---

## Acceptable use

A dictation / accessibility tool. `SendInput` delivers the same keystrokes you
could type by hand — it does not defeat DLP, session recording, or other
security controls, and moves no data out of the remote session. Use it only
where you're permitted to use dictation software and to provide keyboard input
to the systems you connect to. Check your organisation's endpoint policy first.

---

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall.ps1
```

Reverses `install.ps1`: stops the app + recorder daemon (which deregisters the
hotkeys), stops and removes the Docker container and image, and deletes `.venv`,
`models`, `config.ini`, and generated logs / temp files. Source files and `.git`
are left untouched.

```powershell
.\uninstall.ps1 -DryRun              # show what would be removed
.\uninstall.ps1 -KeepModels          # keep .\models (slow to re-download)
.\uninstall.ps1 -RemoveAutoHotkey -RemovePython   # also winget-uninstall those
```

Then delete the folder to remove the project entirely.

---

## Project layout

```
vdi-dictate.ahk        hotkey, recorder orchestration, SendInput injection
record.py              mic capture + dispatch
transcriber.py         server / local routing + fallback
config.example.ini     template copied to config.ini
requirements.txt       client Python deps
install.ps1            client setup (+ optional server bootstrap)
uninstall.ps1          reverse install.ps1 (env, Docker image, config)
run-server.ps1         manage the ASR Docker container
start.ps1              launch helper (finds AutoHotkey v2)
docker-compose.yml     ASR service (CPU baseline)
docker-compose.gpu.yml GPU overlay
server/
  app.py               FastAPI, OpenAI-compatible endpoints
  engines.py           pluggable engine registry
  Dockerfile           CUDA base + onnxruntime-gpu
  requirements*.txt     server deps
  README.md            server details
models/                downloaded models (git-ignored)
```

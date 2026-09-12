# Saykey

Local, offline **speech-to-text** for Windows that types the transcript into
whatever window has focus — including remote-desktop / VDI clients (Citrix,
VMware Horizon, RDP, AVD) where host-to-guest **clipboard sharing is disabled**.

Nothing is pasted. The transcribed text is injected as ordinary keystrokes via
AutoHotkey's `SendInput`, the same way a hardware keyboard or Dragon /
Windows Speech Recognition sends input.

**Push-to-talk by default:** hold **Ctrl+Space**, speak, release — the text is
typed into the focused window. (Switch to press/press with `[hotkey] mode =
toggle`.) A resident recorder keeps the microphone and the transcription backend
warm, so a key press starts capture in ~100 ms.

Transcription runs on **NVIDIA Parakeet** by default, served from a small local
ASR server process on your GPU — no Docker required. The engine is swappable —
Parakeet v2/v3, Canary, Whisper, any HuggingFace ASR model, or any
OpenAI-compatible endpoint — from `config.ini` or the UI. If the server isn't
running, the client falls back to in-process `faster-whisper` on the CPU so
dictation still works.

---

## Directory layout

```
saykey/
├── config.example.ini      shared config template (→ config.ini on first run)
├── README.md
│
├── agent/                   host-side dictation agent: global hotkey + keystroke injection
│   └── saykey.ahk        Windows (AutoHotkey v2) — the only platform-specific piece
│
├── recorder/                host-side audio capture + transcription routing (Python)
│   ├── record.py             resident daemon (--serve) + one-shot CLI
│   ├── transcriber.py        server vs. local routing, automatic fallback
│   └── requirements.txt
│
├── server/                  ASR HTTP server (a plain local process, no Docker)
│   ├── app.py                FastAPI, OpenAI-compatible /v1/audio/transcriptions
│   ├── engines.py            pluggable engine registry (onnx-asr / faster-whisper / transformers / proxy)
│   ├── requirements.txt  requirements-hf.txt
│   └── README.md
│
├── ui/                      cross-platform desktop app (PySide6)
│   ├── app.py  __main__.py   tray app, single-instance, orchestration
│   ├── widgets/settings_window.py     General / Advanced / Models tabs
│   ├── config_store.py  models_catalog.py  autostart.py  orchestrator.py  ...
│   └── requirements.txt
│
├── scripts/                 Windows; PowerShell
│   ├── install.ps1                       one-time setup (deps, venv, model, ASR server deps)
│   ├── run.ps1   stop.ps1                start / stop everything
│   ├── uninstall.ps1                     remove everything install.ps1 created
│   └── run-server.ps1                    local ASR server process management
│
├── models/                  downloaded models (git-ignored, shared by recorder + server)
└── .venv/                   Python environment (git-ignored)
```

Each concern is a folder; `config.ini` at the root is the one file every part
reads, so they always agree.

---

## Scripts

```powershell
.\scripts\install.ps1     # one-time: deps, .venv, UI, fallback model, ASR server deps. Starts nothing.
.\scripts\run.ps1         # start Saykey (tray app -> ASR server + dictation agent)
.\scripts\stop.ps1        # stop all of it (keeps everything installed)
.\scripts\uninstall.ps1   # remove .venv, models, config, caches
```

`install.ps1` is standalone: it creates every dependency on the machine and
stops. `run.ps1` is the only thing that starts anything; `stop.ps1` is its
inverse and removes nothing.

## Desktop UI

A cross-platform tray app (**PySide6** — one Python codebase for Windows / macOS
/ Linux) that is the single face of the app:

- **One tray icon** (colour reflects state). On launch it always starts the
  headless dictation agent (`agent/saykey.ahk`) and — unless a local model is
  configured — the ASR server (a local process, no Docker). The agent has no
  icon of its own.
- **Dashboard** — the default view: a big state‑coloured glowing mic (Ready /
  Recording / Transcribing / Error), the current instruction ("Hold **Ctrl+Space**
  to talk"), and a **Settings** button.
- **Settings** — a left navigation rail: **General** (language, startup, tray) ·
  **Dictation & Hotkeys** (hotkey recorder, hold-vs-toggle, microphone with a
  live meter, floating‑button / toast toggles + positions, and a
  *Tuning (VDI Safe Injection)* grid: injection mode / key‑delay / chunk size &
  delay) · **ASR Engine & Models** (model cards badged GPU‑local vs Local‑CPU,
  with **✓ APPLIED** vs **SELECTED** states and detected‑hardware context) ·
  **Advanced & Developer** (debug toggle, a chronological activity log, and a
  **Quit Saykey** button that also stops the ASR server process).
- A **status bar** on every view: ASR Server and Dictation Agent state (the live
  mic meter lives on the Dictation panel).
- **Save / Cancel**: `Save` writes every change to `config.ini` and returns to the
  dashboard; it only restarts the agent or the ASR server process when a
  setting that actually needs it changed (picking a different model is the only
  thing that restarts the server).
- **start hidden**, **launch on OS startup**, single‑instance (relaunch reopens
  the window).

> The dictation agent (global hotkey + keystroke injection) is Windows-only for
> now via AutoHotkey. A native macOS/Linux agent (pynput-based) is the planned
> replacement; the UI, server, and config are already cross-platform.

---

## Architecture

```
  hold Ctrl+Space  (global hotkey, host)
       │  press → "start" signal     release → "stop" signal
       ▼
  agent/saykey.ahk ── manages ──►  recorder/record.py --serve   (resident)
       │                                   │  mic capture (sounddevice), warm
       │                        recorder/transcriber.py
       │                          ├── backend = server ──► POST /v1/audio/transcriptions
       │                          │                         ┌───────────────────────────┐
       │                          │                         │  local process: server/app.py │
       │                          │                         │  FastAPI + engine:        │
       │                          │                         │   Parakeet (onnx-asr) GPU │
       │                          │                         └───────────────────────────┘
       │                          └── fallback ───────────► faster-whisper (CPU, in-process)
       │       ◄──── transcript ────────┘
       ▼
  SendInput "{Raw}…"  ──►  focused window = VDI / RDP / local app   (no clipboard)
```

- **`agent/`** — captures the hotkey, injects text. Platform-specific; Windows only for now.
- **`recorder/`** — captures audio, gets it transcribed. `record.py --serve` is the resident
  daemon (signal files in `%TEMP%\saykey_ctl`, exits if the agent dies); without `--serve`
  it's a one-shot CLI for `--warmup` / `--transcribe-wav` / `--list-devices`.
- **`server/`** — transcribes, as a local process (no Docker). See [`server/README.md`](server/README.md).
- **`ui/`** — controls all of it. Cross-platform.

---

## Security / privacy

- **Offline.** The ASR server downloads the model once, then serves it locally; no
  audio or text leaves the machine. The local fallback runs with `HF_HUB_OFFLINE`
  after setup. HF telemetry is disabled.
- **No clipboard.** The host clipboard is never touched — clipboard-isolation
  policy is unaffected. `SendInput` delivers the same keystrokes you could type
  by hand; it doesn't bypass DLP, session recording, or any other control, and
  moves no data out of the remote session.
- **Local only.** Audio stays in RAM; the transcript lives in `%TEMP%` and is
  deleted after it's typed.
- **No elevation.** Nothing here needs admin (installing AHK/Python via winget may).

---

## Requirements

| Component | Version | For |
|---|---|---|
| Windows | 10 / 11 | the dictation agent (UI + server are cross-platform) |
| AutoHotkey | **v2** | the dictation agent |
| Python | 3.10+ | recorder + local fallback + UI + ASR server (all in one `.venv`, no Docker) |
| NVIDIA GPU + driver | any recent | Parakeet at full speed (CPU works but is slow) |

`backend = local` (Whisper only, CPU) needs no GPU either.

---

## Install

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

Checks for / installs AutoHotkey v2 and Python (winget, with consent), creates
`.venv` with the recorder, desktop-UI, **and** ASR server dependencies (picking
`onnxruntime` or `onnxruntime-gpu` based on detected GPU — no Docker, no image
build), downloads the Whisper fallback model, and writes `config.ini`. It
**starts nothing** — `run.ps1` does that.

Variants:

```powershell
.\scripts\install.ps1 -Engine parakeet-v3    # multilingual Parakeet
.\scripts\install.ps1 -Backend local         # Whisper-only, no ASR server install
.\scripts\install.ps1 -InstallHF              # also install torch/transformers (hf:<id> engines)
.\scripts\install.ps1 -Yes                    # non-interactive
.\scripts\install.ps1 -SkipModel              # don't download the fallback model
```

---

## Run

```powershell
.\scripts\run.ps1
```

Launches the tray app, which starts the ASR server (a local process, no
Docker) and the dictation agent. Right-click the tray icon for Settings.
Double-clicking `agent/saykey.ahk` runs just the agent.

If Saykey isn't installed yet, `run.ps1` says so and offers to run `install.ps1`
for you.

> Windows only for now. The UI, server, and config are cross-platform (`python -m
> ui` runs anywhere), but there's no dictation agent for macOS / Linux yet, so
> there's no `run` script for them.

Manage the ASR server process directly (restart after a model change, logs, …):

```powershell
.\scripts\run-server.ps1 restart
.\scripts\run-server.ps1 status
.\scripts\run-server.ps1 logs -Follow
.\scripts\run-server.ps1 down
```

---

## Stop

```powershell
.\scripts\stop.ps1                # stop everything
.\scripts\stop.ps1 -KeepServer    # ... but leave the ASR server process running
```

Stops the tray app, the dictation agent (which deregisters the global hotkey),
the resident recorder, and the ASR server process, and clears the transient
signal files in `%TEMP%`. It removes **nothing** installed — `.venv`, `models`,
`config.ini`, and the "launch on startup" setting all stay.
`run.ps1` brings it straight back; `uninstall.ps1` is the one that deletes.

---

## Usage

**Push-to-talk** (`[hotkey] mode = hold`, default):

1. Click into the target window (VDI session, text box, editor…).
2. **Hold Ctrl+Space.** An on-screen indicator appears immediately ("starting"),
   then turns red **"RECORDING"** with a beep once the mic is live.
3. Speak while holding.
4. **Release.** The indicator disappears and the transcript is typed in.

A tap shorter than `min_hold_ms` (250 ms) is ignored. **Ctrl+Shift+Space**
cancels. The indicator (`[toast]`) never takes keyboard focus.

**Toggle** (`[hotkey] mode = toggle`): press to start, press again (or pause
~2 s) to stop.

The agent is headless — control it from the **UI's tray menu**: open the window,
toggle the ASR server / dictation agent / floating talk button, launch on
startup, quit. With `[ui] developer_options = true` (Settings → Advanced →
**Developer options**) the tray also gains a **Developer** submenu: open the log,
edit `config.ini`, restart the dictation agent.

---

## Swapping the transcription model

Driven by `config.ini` (or the UI's **Models** tab).

**Where transcription happens** — `[transcription] backend`:

| value | meaning |
|---|---|
| `server` *(default)* | POST to the ASR server at `server_url`. Works with the bundled container **or any OpenAI-compatible ASR endpoint** (speaches, an NVIDIA Riva gateway, a remote box, a cloud API). |
| `local` | in-process `faster-whisper` (CPU), no server needed |

**Which model the bundled server loads** — `[server] engine`, then
`.\scripts\run-server.ps1 restart`:

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
| `general` | `python` | `.venv\Scripts\pythonw.exe` | interpreter for `record.py` (root-relative) |
| | `language` | `en` | language code or `auto` |
| | `models_dir` | `models` | fallback + container model cache |
| | `offline` | `false` → `true` after setup | local fallback: no network |
| | `debug` | `false` | verbose logging to `%TEMP%\saykey.log` (also a tray toggle) |
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
| | `image` | `saykey-asr:latest` | image tag |
| | `upstream_url` / `upstream_key` | — | `engine = openai` target |
| `local` | `model` | `base.en` | faster-whisper model |
| | `compute_type` | `int8` | |
| | `beam_size` | `1` | |
| `recording` | `max_seconds` | `60` | hard cap |
| | `silence_timeout` | `2.0` | toggle mode: auto-stop after N s silence (`0` = off) |
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
| `toast` | `enabled` | `true` | on-screen recording indicator |
| | `position` | `bottom` | `bottom`/`top`/`center`/`bottom-left`/`bottom-right`/`top-left`/`top-right` |
| | `margin` | `90` | pixels from the screen edge |
| | `font_size` | `12` | indicator text size (pt) |
| `button` | `enabled` | `false` | floating mouse trigger — left-click-hold to talk, right-drag to move (for VDI clients that block every keyboard path) |
| | `position` | `bottom-right` | same keywords as `toast` |
| | `margin` | `28` | pixels from the screen edge |
| | `font_size` | `11` | button text size (pt) |
| | `x` / `y` | — | explicit pixel position; written automatically when you drag it |
| `ui` | `start_hidden` / `launch_on_startup` / `show_tray_icon` | | UI window behaviour |
| | `autostart_server` / `autostart_agent` | `true` | legacy — the app now always starts both (server skipped for a local model) |
| | `developer_options` | `false` | reveal the tray's Developer submenu + the debug toggle in Settings → Advanced |

---

## Tuning for your VDI client

| `mode` | mechanism | use when |
|---|---|---|
| `Raw` | `SendInput "{Raw}…"` fast scan-code burst | default; most modern Citrix/Horizon/RDP |
| `Event` | `SendEvent "{Raw}…"` paced by `key_delay` | characters **dropped / doubled / reordered** in `Raw` |
| `Text` | `SendText` Unicode events | non-Latin scripts, non-US guest layouts |

Adjust these from **Settings → Dictation & Hotkeys → Tuning (VDI Safe Injection)**,
or in `config.ini`. If text comes through wrong: `mode = Event` → raise
`key_delay` to 20–40 → raise `chunk_delay` to 30–60 and lower `chunk_size` to 10
→ last resort `mode = Text`. Keep the guest keyboard layout matching the host
for `Raw` / `Event`.

**The dictation key.** Some clients (Omnisa / VMware Horizon, Citrix) grab the
keyboard while their window has focus, so a normal global hotkey never fires
there. Two things try to catch it anyway:

1. a **hook hotkey** (`$` prefix) instead of `RegisterHotkey`;
2. **raw input** (`RIDEV_INPUTSINK`) — a separate pipeline the client can't
   intercept. The key press then *also* reaches the guest (`Ctrl+Space` is
   harmless in most apps; if not, set `[hotkey] key` to a spare key like `Pause`,
   `AppsKey`, `ScrollLock`, `SC152`).

For clients that capture the keyboard so completely that neither fires (e.g. the
Omnisa Cloud Desktop), use the **floating talk button** (`[button] enabled =
true`, or tray → *Floating talk button*): a small always‑on‑top button,
**left‑click‑hold to talk** (click to start/stop in `mode = toggle`),
**right‑drag to move**. Mouse clicks on a separate host window aren't affected by
the client's keyboard capture, so it always works, and it never takes focus off
the VDI.

Turn on `[general] debug` and watch `%TEMP%\saykey.log`: `raw: trigger key down`
means path 2 is working; if that never appears with the VDI focused, use the
button.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Python executable not found" | run `scripts\install.ps1`; check `[general] python` |
| Nothing on Ctrl+Space | another app / IME owns the hotkey — set `[hotkey] key = ^!Space`, tray → Reload |
| Nothing on Ctrl+Space **only while the VDI window is focused** | the client is grabbing the keyboard. The agent uses a hook hotkey and also listens via raw input. If `%TEMP%\saykey.log` (with `[general] debug`) shows no `raw: trigger key down` when you press the key with the VDI focused, the client blocks every keyboard path — enable the **floating talk button**: `[button] enabled = true` (tray → *Floating talk button*), then left-click-hold it to dictate |
| "Recorder daemon failed to start" | check the log; usually a mic-permission or dependency issue. Tray → Restart recorder |
| First word clipped | wait for the beep before speaking |
| Long pause on first server start | model download — `scripts\run-server.ps1 logs -Follow` |
| "server unreachable" in log | container down; `scripts\run-server.ps1 up`. Fallback Whisper is being used |
| "No speech detected" | wrong mic (tray → List audio devices → set `device_index`) or raise `silence_threshold` |
| CUDA provider fails in the container | it falls back to CPU automatically; force with `[server] device = cpu` |
| Characters dropped / doubled in VDI | see **Tuning** above |
| Accuracy poor | `engine = whisper` + `[server] model = large-v3`, or `parakeet` (best English) |

Check the backend without a microphone:

```powershell
.\.venv\Scripts\python.exe recorder\record.py --print-engine
.\.venv\Scripts\python.exe recorder\record.py --warmup
.\.venv\Scripts\python.exe recorder\record.py --transcribe-wav some-speech.wav
```

Verbose logs: set `[general] debug = true` (or the agent tray → Debug logging).
Logs land in `%TEMP%\saykey.log`; server logs via `scripts\run-server.ps1 logs`.

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
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall.ps1
```

Reverses `install.ps1`: stops the UI / agent / recorder / ASR server process
(which deregisters the hotkeys), clears the "launch on startup" entry, and
deletes `.venv`, `models`, `config.ini`, and generated logs / temp files.
Source files and `.git` are untouched.

```powershell
.\scripts\uninstall.ps1 -DryRun              # show what would be removed
.\scripts\uninstall.ps1 -KeepModels          # keep .\models (slow to re-download)
.\scripts\uninstall.ps1 -RemoveAutoHotkey -RemovePython   # also winget-uninstall those
```

Then delete the folder to remove the project entirely.

# ui/ — desktop app (PySide6)

Cross-platform (Windows / macOS / Linux) tray app + window. The **single face**
of Saykey: one tray icon and one dark-themed window. Reuses the project's config
and spawns the same `recorder` / `server`; the headless `agent/saykey.ahk`
reports to it via files in `%TEMP%\saykey_ctl` (`activity`, `events.tsv`).

```
python -m ui            # Windows: ..\scripts\run.ps1
```

## Window layout

* **Dashboard** (default view) — a big state-coloured glowing mic, the current
  usage instruction, and a **Settings** button.
* **Settings** — a left **navigation rail**: General · Dictation & Hotkeys ·
  ASR Engine & Models · Advanced & Developer. A full-width footer with **Save** / **Cancel**.
* A **status bar** across the bottom of every view: ASR Server and Dictation
  Agent state (the live mic meter is on the Dictation panel).

## Modules

| file | role |
|---|---|
| `app.py` | the one tray icon + menu, single-instance, status polling (fast file tick + off-thread ASR server health probe), orchestration wiring |
| `status.py` | reads the agent's `activity` / `events.tsv` and merges in UI-side events → one feed |
| `theme.py` | the dark QSS stylesheet, palette constants, runtime-drawn glyphs |
| `audio.py` | microphone enumeration + a `sounddevice` input-level sampler for the VU meters |
| `config_store.py` | typed load/save over the root `config.ini` — preserves comments; the only writer the UI uses |
| `models_catalog.py` | the model list; each entry maps to `[server] engine` / `model` / `backend` (default: Parakeet Unified EN 0.6B) |
| `orchestrator.py` | `AsrServer` (starts/stops the local `uvicorn` ASR server process, no Docker) + `gpu_name()`, and the Windows AHK agent lifecycle (graceful stop / reload / button / suspend signals) |
| `autostart.py` | launch-on-startup: Windows registry · macOS LaunchAgent · Linux `.desktop` |
| `shortcuts.py` | `^!Space` ⇄ `Ctrl+Alt+Space` (config form ⇄ the capture widget) |
| `paths.py` | every project path, resolved from the package location |
| `icon.py` | tray icon drawn at runtime |
| `widgets/settings_window.py` | the window shell — dashboard + nav rail + panel stack + status bar; owns Save / Cancel |
| `widgets/dashboard.py` | the primary view (`GlowMic` + instructions) |
| `widgets/nav_rail.py` · `widgets/status_bar.py` · `widgets/audio_meter.py` · `widgets/model_card.py` | reusable pieces |
| `widgets/{general,dictation,models,advanced}_panel.py` | one file per panel; each exposes `load(s)` / `apply_to(s)`. Advanced also has the activity log + a Quit button |

Each panel reads/writes `config.ini` only through `config_store` and never
touches keys it doesn't own; the shell diffs the result so `Save` only restarts
the agent / ASR server process when a key that actually needs it changed.

## Dependencies

`requirements.txt` — `PySide6`, `sounddevice`, `numpy`, `pynput`.
`scripts\install.ps1` installs these into the shared `.venv`.

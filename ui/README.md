# ui/ — desktop app (PySide6)

Cross-platform (Windows / macOS / Linux) tray app + window. The **single face**
of Saykey: one tray icon, and a **Status panel** where all updates land (there
are no system notifications). Reuses the project's config and spawns the same
`recorder` / `server`; the headless `agent/saykey.ahk` reports to it via files in
`%TEMP%\saykey_ctl` (`activity`, `events.tsv`).

```
python -m ui            # Windows: ..\scripts\run.ps1
```

## Modules

| file | role |
|---|---|
| `app.py` | the one tray icon + menu, single-instance, start-hidden, status polling (fast file tick + off-thread server probe), orchestration wiring |
| `status.py` | reads the agent's `activity` / `events.tsv` and merges in UI-side events → one feed for the Status panel |
| `__main__.py` | entry point; also runnable as a bare file path (autostart) |
| `widgets/settings_window.py` | **Status panel** + **General** (shortcut / mode / mic / talk button) · **Advanced** (window prefs + Developer options) · **Models** (catalogue) |
| `config_store.py` | typed load/save over the root `config.ini` — preserves comments; the only writer the UI uses |
| `models_catalog.py` | the Models-tab list; each entry maps to `[server] engine` / `model` / `backend` (default: Parakeet Unified EN 0.6B) |
| `orchestrator.py` | `docker compose` up/down/restart (GPU auto-detect) + the Windows AHK agent lifecycle (graceful stop / reload / button signals via `%TEMP%\saykey_ctl`) |
| `autostart.py` | launch-on-startup: Windows registry · macOS LaunchAgent · Linux `.desktop` |
| `shortcuts.py` | `^!Space` ⇄ `Ctrl+Alt+Space` (config form ⇄ the capture widget) |
| `paths.py` | every project path, resolved from the package location |
| `icon.py` | tray icon drawn at runtime |

## Dependencies

`requirements.txt` — `PySide6`, `sounddevice`, `pynput`. `scripts\install.ps1`
always installs these into the shared `.venv`.

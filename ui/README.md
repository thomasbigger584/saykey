# ui/ — desktop app (PySide6)

Cross-platform (Windows / macOS / Linux) tray app + settings window. One Python
codebase; reuses the project's config and spawns the same `recorder` / `server`.

```
python -m ui            # or  ..\scripts\run-ui.ps1  /  ../scripts/run-ui.sh
```

## Modules

| file | role |
|---|---|
| `app.py` | tray icon, single-instance (relaunch reopens Settings), start-hidden, status polling, orchestration wiring |
| `__main__.py` | entry point; also runnable as a bare file path (autostart) |
| `widgets/settings_window.py` | **General** (shortcut / mode / mic) · **Advanced** (start hidden, launch on startup, tray icon, debugging, autostart toggles) · **Models** (catalogue) |
| `config_store.py` | typed load/save over the root `config.ini` — preserves comments; the only writer the UI uses |
| `models_catalog.py` | the Models-tab list; each entry maps to `[server] engine` / `model` / `backend` (default: Parakeet Unified EN 0.6B) |
| `orchestrator.py` | `docker compose` up/down/restart (GPU auto-detect) + the Windows AHK agent lifecycle |
| `autostart.py` | launch-on-startup: Windows registry · macOS LaunchAgent · Linux `.desktop` |
| `shortcuts.py` | `^!Space` ⇄ `Ctrl+Alt+Space` |
| `paths.py` | every project path, resolved from the package location |
| `icon.py` | tray icon drawn at runtime |

## Dependencies

`requirements.txt` — `PySide6`, `sounddevice`, `pynput`. Install into the shared
`.venv` with `scripts\install.ps1 -WithUI` or `pip install -r ui/requirements.txt`.

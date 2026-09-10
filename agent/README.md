# agent/ — host-side dictation agent

Owns the **global hotkey** and **types the transcript** into the focused window.
This is the only platform-specific part of the project.

## `saykey.ahk` (Windows, AutoHotkey v2)

- Registers the dictation key from `[hotkey]` in the project-root `config.ini`
  (`^Space` = Ctrl+Space, push-to-talk by default).
- Manages `recorder/record.py --serve` — the resident recorder — via signal
  files in `%TEMP%\saykey_ctl`. Press → `start`; release → `stop`.
- Shows the on-screen recording indicator (`[toast]`), then injects the returned
  text with `SendInput "{Raw}…"` (scan-codes, never the clipboard).
- Tray menu: config, audio devices, engine check, restart recorder, debug
  logging toggle, ASR Docker server submenu.

Run it: `..\scripts\run-agent.ps1`, or double-click the file, or via the UI.
`--test-toast` shows the indicator states and exits.

## macOS / Linux

Not implemented yet. Planned as a native Python agent using `pynput` (global
hotkey + `pynput.keyboard.Controller` for keystroke injection — Windows SendInput
/ macOS CGEvent / Linux XTEST under the hood). The recorder, server, config, and
UI are already cross-platform; only this folder needs a per-OS implementation.

# agent/ — host-side dictation agent

Owns the **global hotkey** and **types the transcript** into the focused window.
This is the only platform-specific part of the project.

## `saykey.ahk` (Windows, AutoHotkey v2)

**Headless** — no tray icon, no notifications. The desktop UI (`ui/`) is the one
face of the app; this process reports to it through `%TEMP%\saykey_ctl`:

| file | direction | meaning |
|---|---|---|
| `activity` | agent → UI | one word: `idle` / `preparing` / `recording` / `transcribing` / `stopped` |
| `events.tsv` | agent → UI | append-only `‹unix-seconds› TAB ‹level› TAB ‹message›` |
| `ui.quit` / `ui.reload` / `ui.button` | UI → agent | exit / reload / re-read `[button] enabled` |
| `ui.suspend` | UI → agent | pause every trigger (present = paused) while the UI captures a new shortcut |

- Registers the dictation key from `[hotkey]` in the project-root `config.ini`
  (`^Space` = Ctrl+Space, push-to-talk by default).
- Manages `recorder/record.py --serve` — the resident recorder — via signal
  files in the same directory. Press → `start`; release → `stop`.
- Shows the on-screen recording indicator (`[toast]`), then injects the returned
  text with `SendInput "{Raw}…"` (scan-codes, never the clipboard).
- Fires the dictation key from **two** independent paths so it still triggers when
  a remote-desktop client (Omnisa/VMware Horizon, Citrix, RDP) grabs the keyboard
  while its window has focus:
  1. a hook hotkey (`$` prefix — the keyboard hook, not `RegisterHotkey`);
  2. a **raw-input listener** (`RegisterRawInputDevices` + `RIDEV_INPUTSINK`,
     `WM_INPUT`) — a separate pipeline the client can't consume. It can't suppress
     the key, so the combo also reaches the guest; use a spare `[hotkey] key` if
     that matters.
  For clients that capture the keyboard so completely that neither path fires
  (e.g. Omnisa Cloud Desktop), enable the **floating mouse button**
  (`[button] enabled = true`) — a borderless `WS_EX_NOACTIVATE` always-on-top
  window: left-click-hold = talk, right-drag = move (position persisted to
  `config.ini`). All paths feed one idempotent `talkStart` / `talkStop` /
  `talkCancel` core, so whichever sees the input first does the work.

Normally the UI starts it (`..\scripts\run.ps1`) and controls it from its own
tray menu. To run just the agent, double-click `saykey.ahk` or point AutoHotkey
v2 at it (it works standalone; the status files simply go unread). `--test-toast`
shows the indicator states and exits.

## macOS / Linux

Not implemented yet. Planned as a native Python agent using `pynput` (global
hotkey + `pynput.keyboard.Controller` for keystroke injection — Windows SendInput
/ macOS CGEvent / Linux XTEST under the hood). The recorder, server, config, and
UI are already cross-platform; only this folder needs a per-OS implementation.

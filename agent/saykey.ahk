#Requires AutoHotkey v2.0
#SingleInstance Force
#MaxThreadsPerHotkey 1
#NoTrayIcon
Persistent

; ===========================================================================
;  Saykey -- local, offline speech-to-text for locked-down VDI clients
; ---------------------------------------------------------------------------
;  [hotkey] mode = hold   : HOLD Ctrl+Space to record, release to insert (PTT)
;  [hotkey] mode = toggle : press Ctrl+Space to start, press again to stop
;  Ctrl+Shift+Space       : cancel the current recording without transcribing
;
;  The transcript is injected as KEYSTROKES (SendInput / scan-codes), never via
;  the clipboard, so it works when host<->VDI clipboard sharing is disabled.
;  A resident recorder daemon (record.py --serve) keeps the microphone and the
;  transcription backend warm so a key press starts capture in ~100 ms.
;
;  This process is HEADLESS -- no tray icon, no notifications. It reports state
;  to the desktop UI by writing:
;    <ctl>\activity   one word: idle | preparing | recording | transcribing
;    <ctl>\events.tsv append-only: <unix-seconds> TAB <level> TAB <message>
;  and takes commands back from the UI via <ctl>\ui.quit / .reload / .button .
; ===========================================================================

global APP := "Saykey"
; agent/ lives one level under the project root; everything else is ROOT-relative.
global ROOT := RegExReplace(A_ScriptDir, "\\[^\\]+$")
global ConfigFile := ROOT "\config.ini"
global ExampleFile := ROOT "\config.example.ini"

if !FileExist(ConfigFile) {
    if FileExist(ExampleFile)
        FileCopy(ExampleFile, ConfigFile)
    else {
        MsgBox("Missing config.ini and config.example.ini.`nRun scripts\install.ps1 first.", APP, "Iconx")
        ExitApp()
    }
}

readCfg(sec, key, def) => IniRead(ConfigFile, sec, key, def)

global C := Map()
C["python"]            := readCfg("general", "python", ".venv\Scripts\pythonw.exe")
C["language"]          := readCfg("general", "language", "en")

global DEBUG := readCfg("general", "debug", "false") = "true"

C["backend"]           := readCfg("transcription", "backend", "server")
C["engine"]            := readCfg("server", "engine", "parakeet")

C["max_seconds"]       := readCfg("recording", "max_seconds", "60")
C["silence_timeout"]   := readCfg("recording", "silence_timeout", "2.0")
C["silence_threshold"] := readCfg("recording", "silence_threshold", "0.012")
C["device_index"]      := readCfg("recording", "device_index", "-1")

C["mode"]              := readCfg("injection", "mode", "Raw")
C["key_delay"]         := Integer(readCfg("injection", "key_delay", "10"))
C["chunk_size"]        := Integer(readCfg("injection", "chunk_size", "20"))
C["chunk_delay"]       := Integer(readCfg("injection", "chunk_delay", "15"))
C["trailing_space"]    := readCfg("injection", "trailing_space", "true") = "true"
C["trim"]              := readCfg("injection", "trim", "true") = "true"
C["normalize_ascii"]   := readCfg("injection", "normalize_ascii", "true") = "true"
C["collapse_newlines"] := readCfg("injection", "collapse_newlines", "true") = "true"

C["toast_enabled"]     := readCfg("toast", "enabled", "true") = "true"
C["toast_position"]    := StrLower(readCfg("toast", "position", "bottom"))
C["toast_margin"]      := Integer(readCfg("toast", "margin", "90"))
C["toast_font"]        := readCfg("toast", "font_size", "12")

; Floating mouse-driven trigger -- for VDI clients that capture the keyboard so
; completely that no hotkey (hook OR raw input) can fire while their window has
; focus. A borderless always-on-top button: left-click-hold to talk (or click to
; toggle), right-drag to move. Never takes keyboard focus off the VDI.
C["button_enabled"]    := readCfg("button", "enabled", "false") = "true"
C["button_position"]   := StrLower(readCfg("button", "position", "bottom-right"))
C["button_margin"]     := Integer(readCfg("button", "margin", "28"))
C["button_font"]       := readCfg("button", "font_size", "11")
C["button_x"]          := Integer(readCfg("button", "x", "-1"))
C["button_y"]          := Integer(readCfg("button", "y", "-1"))

global HK_MODE    := StrLower(readCfg("hotkey", "mode", "hold"))
global HK_KEY     := readCfg("hotkey", "key", "^Space")
global HK_CANCEL  := readCfg("hotkey", "cancel", "^+Space")
global HK_MINHOLD := Integer(readCfg("hotkey", "min_hold_ms", "250"))
global HK_BAREKEY := stripMods(HK_KEY)

global PY := resolvePath(C["python"])
if !FileExist(PY) {
    MsgBox("Python executable not found:`n" PY "`n`nRun scripts\install.ps1 first.", APP, "Iconx")
    ExitApp()
}
global RECORDER := ROOT "\recorder\record.py"
if !FileExist(RECORDER) {
    MsgBox("recorder\record.py not found under " ROOT, APP, "Iconx")
    ExitApp()
}

; ---- resident recorder: signal files in a %TEMP% control directory ---------
global CTLDIR        := A_Temp "\saykey_ctl"
global CTL_START     := CTLDIR "\start"
global CTL_STOP      := CTLDIR "\stop"
global CTL_CANCEL    := CTLDIR "\cancel"
global CTL_QUIT      := CTLDIR "\quit"
global CTL_READY     := CTLDIR "\ready"
global CTL_DONE      := CTLDIR "\done"
global CTL_RESULT    := CTLDIR "\result.txt"
global CTL_ERROR     := CTLDIR "\error"
global CTL_CANCELLED := CTLDIR "\cancelled"
global CTL_UP        := CTLDIR "\up"
global T_LOG         := A_Temp "\saykey.log"

; ---- headless status channel + commands from the UI ----------------------
global ST_ACTIVITY   := CTLDIR "\activity"          ; one word, overwritten
global ST_EVENTS     := CTLDIR "\events.tsv"        ; append-only feed
global UI_QUIT       := CTLDIR "\ui.quit"           ; UI -> exit the agent
global UI_RELOAD     := CTLDIR "\ui.reload"         ; UI -> reload the script
global UI_BUTTON     := CTLDIR "\ui.button"         ; UI -> re-read [button] enabled
global UI_SUSPEND    := CTLDIR "\ui.suspend"        ; UI -> pause triggers (recording a shortcut)
global gEmitCount    := 0
global gActivity     := ""
global gSuspended    := false

global gState := "idle"      ; idle | starting | recording | transcribing
global gPressTick := 0
global DAEMON_PID := 0

; ---- raw-input keyboard trigger (works when a VDI client eats the hook chain) --
global gTalkActive := false      ; a hold/toggle talk session is in progress
global RID_MAIN     := 0         ; parsed [hotkey] key   -> {ctrl,alt,shift,win,vk}
global RID_CANCEL   := 0         ; parsed [hotkey] cancel
global gRawOK       := false
global gRawCtrl := false, gRawAlt := false, gRawShift := false, gRawWin := false
global gRawMainDown := false, gRawCancelDown := false

global ToastGui := ""
global ToastLbl := ""
global BtnGui := "", BtnLbl := ""
global gBtnDrag := false, gBtnDX := 0, gBtnDY := 0

dbg(msg) {
    global DEBUG, T_LOG
    if DEBUG
        try FileAppend("[ahk " FormatTime(, "HH:mm:ss") "] " msg "`n", T_LOG)
}

; ------------------------------------------------------- status -> the UI
; The agent is headless; these two files are how the desktop UI learns what's
; happening. `emit` also mirrors to the debug log.
emit(level, msg) {
    global ST_EVENTS, gEmitCount
    msg := RegExReplace(msg, "[`t`r`n]+", " ")   ; keep the .tsv one line per event
    ts := DateDiff(A_NowUTC, "19700101000000", "Seconds")
    try FileAppend(ts "`t" level "`t" msg "`n", ST_EVENTS, "UTF-8-RAW")
    if (++gEmitCount >= 200)
        trimEvents()
    dbg("[" level "] " msg)
}
trimEvents() {
    global ST_EVENTS, gEmitCount
    gEmitCount := 0
    try {
        lines := StrSplit(FileRead(ST_EVENTS, "UTF-8"), "`n")
        if (lines.Length <= 130)
            return
        keep := ""
        loop 100
            keep .= lines[lines.Length - 101 + A_Index] "`n"
        f := FileOpen(ST_EVENTS, "w", "UTF-8-RAW"), f.Write(keep), f.Close()
    }
}
setActivity(word) {
    global ST_ACTIVITY, gActivity
    if (word = gActivity)
        return
    gActivity := word
    try {
        f := FileOpen(ST_ACTIVITY, "w", "UTF-8-RAW")
        f.Write(word), f.Close()
    }
}

try DirCreate(CTLDIR)                        ; status files land here from the first line
toastInit()
buttonInit()
if (A_Args.Length && A_Args[1] = "--test-toast") {
    DEBUG := true
    toastShow("prep"), Sleep(2500)
    toastShow("rec"), Sleep(2500)
    toastHide()
    ExitApp()
}

for f in [ST_EVENTS, ST_ACTIVITY, UI_QUIT, UI_RELOAD, UI_BUTTON, UI_SUSPEND]
    try FileDelete(f)
if (HK_MODE = "toggle")
    registerHotkey(HK_KEY, onToggle, "key")
else
    registerHotkey(HK_KEY, onKeyDown, "key")
registerHotkey(HK_CANCEL, onCancel, "cancel")
initRawInput()                               ; parallel trigger: raw HID input, not the hook chain
OnExit(onExitCleanup)
SetTimer(uiSignalWatch, 400)
setActivity("idle")
startDaemon()
emit("info", (HK_MODE = "hold" ? "Hold " HK_KEY " to talk" : "Press " HK_KEY " to dictate")
    . " -- warming up the recorder...")

; --------------------------------------------------------------- registration
registerHotkey(combo, handler, name) {
    global
    ; "$" forces the keyboard-hook implementation for this hotkey (more reliable
    ; for a Send-heavy script) rather than RegisterHotkey.
    hk := InStr(combo, "$") ? combo : "$" combo
    try {
        Hotkey(hk, (*) => handler())
    } catch as e {
        MsgBox("Invalid [hotkey] " name ' = "' combo '" in config.ini`n`n'
            . e.Message
            . "`n`nAutoHotkey syntax:  ^ = Ctrl   ! = Alt   + = Shift   # = Win"
            . "`nExample:  ^Space   (Ctrl+Space)", APP, "Icon!")
    }
}

; -------------------------------------------------------------- talk primitives
; Idempotent start / stop / cancel shared by BOTH trigger sources -- the forced
; keyboard hook (onKeyDown / onToggle / onCancel) and the raw-input listener
; (onTriggerDown / onTriggerUp). The gState / gTalkActive guards mean whichever
; source fires first does the work and the other is a harmless no-op.
talkStart() {
    global
    if (gSuspended)                             ; UI is capturing a new shortcut
        return
    prev := Critical("On")
    if (gState = "idle" && ensureDaemon()) {
        clearSignals()
        gPressTick := A_TickCount
        gState := "starting"
        gTalkActive := true
        setActivity("preparing")
        toastShow("prep")                       ; on screen immediately
        buttonState("prep")
        FileAppend("1", CTL_START)
        SetTimer(ctlWatch, 50)
        dbg("talk start")
    }
    Critical(prev)
}

; checkMinHold = true (push-to-talk): a sub-min_hold_ms press is an accidental
; tap and is cancelled instead of transcribed.
talkStop(checkMinHold := false) {
    global
    prev := Critical("On")
    if (gTalkActive) {
        gTalkActive := false
        toastHide()
        buttonState("prep")                     ; transcribing... back to idle in finishResult
        if (gState != "idle") {
            SendInput("{Ctrl up}{Shift up}{Alt up}{LWin up}{RWin up}")
            held := A_TickCount - gPressTick
            if (checkMinHold && held < HK_MINHOLD) {
                FileAppend("1", CTL_CANCEL)      ; accidental tap -> abort quietly
                setActivity("idle")
                dbg("talk stop held=" held "ms < " HK_MINHOLD " -> cancel")
            } else {
                FileAppend("1", CTL_STOP)
                gState := "transcribing"
                setActivity("transcribing")
                dbg("talk stop held=" held "ms -> transcribe")
            }
        }
    }
    Critical(prev)
}

talkCancel() {
    global
    prev := Critical("On")
    gTalkActive := false
    if (gState != "idle") {
        FileAppend("1", CTL_CANCEL)
        setActivity("idle")
        toastHide()
        dbg("talk cancel")
    }
    buttonState("idle")
    Critical(prev)
}

; -------------------------------------------------------------- hotkey actions
; (forced-hook path -- fires when a host window has focus, or when our hook wins
;  the chain over the VDI client's; the raw-input path covers the rest.)
onKeyDown() {                                   ; push-to-talk: hold to record
    global
    talkStart()
    if (gState = "idle")
        return
    KeyWait(HK_BAREKEY)                         ; block until the key is released
    talkStop(true)
}

onToggle() {                                    ; press to start, press again to stop
    global
    if (gState = "idle")
        talkStart()
    else if (gState = "recording")
        talkStop(false)
}

onCancel() => talkCancel()

; ------------------------------------------------------- raw-input keyboard trigger
; A VDI client (Omnisa/VMware Horizon, Citrix) can capture the keyboard below the
; Win32 hook chain, so the hook hotkeys above never fire while its window has
; focus. Raw input (WM_INPUT with RIDEV_INPUTSINK) is a separate pipeline: it is
; delivered to every registered window regardless of focus and cannot be consumed
; by another process, so it keeps working. It can't SUPPRESS the key, so the
; combo also reaches the guest -- harmless for Ctrl+Space; pick a spare key
; ([hotkey] key = SC152 / Pause / AppsKey ...) if that bothers a guest app.
parseTrigger(combo) {
    o := { ctrl: false, alt: false, shift: false, win: false, vk: 0 }
    i := 1, n := StrLen(combo)
    while (i <= n) {
        c := SubStr(combo, i, 1)
        if (c = "^")
            o.ctrl := true
        else if (c = "!")
            o.alt := true
        else if (c = "+")
            o.shift := true
        else if (c = "#")
            o.win := true
        else if (c = "<" || c = ">" || c = "*" || c = "~" || c = "$" || c = " " || c = "`t")
            i := i                               ; side / behaviour prefix -- ignore
        else
            break
        i += 1
    }
    name := Trim(SubStr(combo, i), " `t")
    try o.vk := GetKeyVK(name)
    return o
}

initRawInput() {
    global
    RID_MAIN   := parseTrigger(HK_KEY)
    RID_CANCEL := parseTrigger(HK_CANCEL)
    if !RID_MAIN.vk {
        dbg("raw-input: can't map [hotkey] key '" HK_KEY "' to a VK -- raw trigger off")
        return
    }
    ; RAWINPUTDEVICE: usUsagePage=1 (generic desktop), usUsage=6 (keyboard),
    ; dwFlags=RIDEV_INPUTSINK (0x100 -> receive even without focus), hwndTarget.
    rid := Buffer(A_PtrSize = 8 ? 16 : 12, 0)
    NumPut("ushort", 0x01, rid, 0)
    NumPut("ushort", 0x06, rid, 2)
    NumPut("uint", 0x00000100, rid, 4)
    NumPut("ptr", A_ScriptHwnd, rid, 8)
    if !DllCall("RegisterRawInputDevices", "ptr", rid, "uint", 1, "uint", rid.Size) {
        dbg("raw-input: RegisterRawInputDevices failed (err " A_LastError ")")
        return
    }
    OnMessage(0x00FF, onRawInput)               ; WM_INPUT
    gRawOK := true
    dbg("raw-input: listening (main vk=" RID_MAIN.vk " cancel vk=" RID_CANCEL.vk ")")
}

rawModsMatch(s) {
    global
    return (gRawCtrl = s.ctrl) && (gRawAlt = s.alt) && (gRawShift = s.shift) && (gRawWin = s.win)
}

onRawInput(wParam, lParam, *) {
    global
    static RID_INPUT := 0x10000003
    static HDRSIZE   := (A_PtrSize = 8 ? 24 : 16)   ; sizeof(RAWINPUTHEADER)
    static buf       := Buffer(64, 0)               ; RAWINPUT(keyboard) is 40 bytes

    size := buf.Size
    if (DllCall("GetRawInputData", "ptr", lParam, "uint", RID_INPUT, "ptr", buf,
                "uint*", &size, "uint", HDRSIZE, "int") < 1)
        return
    if (NumGet(buf, 0, "uint") != 1)            ; RIM_TYPEKEYBOARD
        return

    ; RAWKEYBOARD @ HDRSIZE:  MakeCode u16 | Flags u16 @+2 | Reserved u16 | VKey u16 @+6
    flags := NumGet(buf, HDRSIZE + 2, "ushort")
    vk    := NumGet(buf, HDRSIZE + 6, "ushort")
    if (vk = 0 || vk = 0xFF)                    ; overrun / escaped fake-shift
        return
    isUp := (flags & 0x01)                      ; RI_KEY_BREAK

    if (vk = 0x10 || vk = 0xA0 || vk = 0xA1)
        gRawShift := !isUp
    else if (vk = 0x11 || vk = 0xA2 || vk = 0xA3)
        gRawCtrl := !isUp
    else if (vk = 0x12 || vk = 0xA4 || vk = 0xA5)
        gRawAlt := !isUp
    else if (vk = 0x5B || vk = 0x5C)
        gRawWin := !isUp

    if (vk = RID_MAIN.vk) {
        if (!isUp && !gRawMainDown) {           ; down edge (ignore auto-repeat)
            gRawMainDown := true
            dbg("raw: trigger key down (ctrl=" gRawCtrl " shift=" gRawShift ")")
            onTriggerDown()
        } else if (isUp && gRawMainDown) {
            gRawMainDown := false
            onTriggerUp()
        }
    }
    if (RID_CANCEL.vk && RID_CANCEL.vk != RID_MAIN.vk && vk = RID_CANCEL.vk) {
        if (!isUp && !gRawCancelDown) {
            gRawCancelDown := true
            if rawModsMatch(RID_CANCEL)
                talkCancel()
        } else if (isUp) {
            gRawCancelDown := false
        }
    }
}

onTriggerDown() {
    global
    if (RID_CANCEL.vk = RID_MAIN.vk && rawModsMatch(RID_CANCEL)) {
        talkCancel()
        return
    }
    if !rawModsMatch(RID_MAIN)
        return
    if (HK_MODE = "toggle") {
        if (gState = "idle")
            talkStart()
        else if (gState = "recording")
            talkStop(false)
    } else {
        talkStart()
    }
}

onTriggerUp() {
    global
    if (HK_MODE != "toggle")
        talkStop(true)
}

; --------------------------------------------------------------- state machine
ctlWatch() {
    global
    if (gState = "starting" && FileExist(CTL_READY)) {
        gState := "recording"
        dbg("ready -> recording")
        if !FileExist(CTL_CANCEL) {
            setActivity("recording")
            toastShow("rec")                    ; now actually recording
            buttonState("rec")
            try SoundBeep(880, 80)
        }
    }
    if FileExist(CTL_DONE) {
        SetTimer(ctlWatch, 0)
        finishResult()
        return
    }
    if (DAEMON_PID && !ProcessExist(DAEMON_PID)) {
        SetTimer(ctlWatch, 0)
        gState := "idle"
        gTalkActive := false
        setActivity("idle")
        toastHide()
        buttonState("idle")
        emit("error", "Recorder stopped unexpectedly -- open the log for details")
    }
}

finishResult() {
    global
    SetTimer(ctlWatch, 0)
    toastHide()
    buttonState("idle")
    hadError := FileExist(CTL_ERROR)
    wasCancel := FileExist(CTL_CANCELLED)
    text := ""
    try text := FileRead(CTL_RESULT, "UTF-8")
    clearSignals()
    gState := "idle"
    gTalkActive := false
    gRawMainDown := false, gRawCancelDown := false   ; resync raw-input edge state
    setActivity("idle")

    if hadError {
        emit("error", "Transcription failed -- open the log for details")
        return
    }
    if wasCancel {
        emit("info", "Recording cancelled")
        return
    }
    text := Trim(text, " `t`r`n")
    dbg("result: " (text = "" ? "(empty)" : "'" text "'"))
    if (text = "") {
        emit("warn", "No speech detected")
        return
    }
    injectText(text)
}

; -------------------------------------------------------------- recorder daemon
ensureDaemon() {
    global
    if (DAEMON_PID && ProcessExist(DAEMON_PID))
        return true
    return startDaemon()
}

startDaemon() {
    global
    try DirCreate(CTLDIR)
    ; kill a stale daemon from a previous run / crash
    if FileExist(CTL_UP) {
        old := Trim(FileRead(CTL_UP))
        if (old != "" && ProcessExist(Integer(old)))
            try ProcessClose(Integer(old))
    }
    for f in [CTL_START, CTL_STOP, CTL_CANCEL, CTL_QUIT, CTL_READY, CTL_DONE
            , CTL_RESULT, CTL_ERROR, CTL_CANCELLED, CTL_UP]
        try FileDelete(f)

    silence := (HK_MODE = "hold") ? "0" : C["silence_timeout"]
    args := '"' PY '" "' RECORDER '" --serve'
        . ' --config="' ConfigFile '"'
        . ' --control-dir="' CTLDIR '"'
        . ' --parent-pid=' ProcessExist()
        . ' --language="' C["language"] '"'
        . ' --device-index=' C["device_index"]
        . ' --max-seconds=' C["max_seconds"]
        . ' --silence-timeout=' silence
        . ' --silence-threshold=' C["silence_threshold"]
        . ' --log-file="' T_LOG '"'
    if DEBUG
        args .= ' --debug'
    try {
        Run(args, ROOT, "Hide", &pid)
    } catch as e {
        emit("error", "Could not start the recorder: " e.Message)
        return false
    }
    DAEMON_PID := pid
    dbg("daemon spawned pid=" pid "  mode=" HK_MODE "  silence=" silence)
    SetTimer(warmWatch, 200)
    return true
}

restartDaemon() {
    global
    try FileAppend("1", CTL_QUIT)
    Sleep(300)
    if (DAEMON_PID && ProcessExist(DAEMON_PID))
        try ProcessClose(DAEMON_PID)
    DAEMON_PID := 0
    gState := "idle"
    gTalkActive := false
    startDaemon()
    emit("info", "Restarting the recorder...")
}

warmWatch() {
    global
    static waited := 0
    waited += 200
    if FileExist(CTL_UP) {
        SetTimer(warmWatch, 0)
        waited := 0
        setActivity("idle")
        emit("ok", "Ready -- "
            . (HK_MODE = "hold" ? "hold " HK_KEY " to talk" : "press " HK_KEY " to dictate"))
        return
    }
    if (DAEMON_PID && !ProcessExist(DAEMON_PID)) {
        SetTimer(warmWatch, 0)
        waited := 0
        emit("error", "The recorder failed to start -- open the log for details")
        return
    }
    if (waited = 8000)
        emit("info", "Still warming up the transcription backend...")
}

onExitCleanup(*) {
    global
    try FileAppend("1", CTL_QUIT)
    setActivity("stopped")
    Sleep(200)
    if (DAEMON_PID && ProcessExist(DAEMON_PID))
        try ProcessClose(DAEMON_PID)
}

; ---------------------------------------------------------- on-screen indicator
toastInit() {
    global
    if !C["toast_enabled"]
        return
    ; -Caption borderless; +ToolWindow no taskbar/alt-tab;
    ; -DPIScale: GUI coords are real pixels, matching A_ScreenWidth/Height (we
    ;   scale the font/size ourselves via A_ScreenDPI so it stays readable);
    ; +E0x08000000 WS_EX_NOACTIVATE so it never takes keyboard focus off the VDI.
    s := A_ScreenDPI / 96
    fs := Integer(C["toast_font"])
    ToastGui := Gui("-Caption +AlwaysOnTop +ToolWindow -DPIScale +E0x08000000")
    ToastGui.MarginX := Round(26 * s), ToastGui.MarginY := Round(14 * s)
    ToastGui.BackColor := "2B2B2B"
    ; Explicit control height (with room for descenders) + SS_CENTERIMAGE (0x200)
    ; so both states are the exact same size and "g"/"y" never clip.
    ToastLbl := ToastGui.AddText(
        "w" Round(210 * s) " h" Round(fs * 2.4 * s) " 0x200 Center cFFFFFF BackgroundTrans", "")
    ToastLbl.SetFont("s" fs " bold", "Segoe UI")
}

toastShow(mode) {
    global
    if !C["toast_enabled"] || !ToastGui
        return
    if (mode = "rec") {
        ToastGui.BackColor := "C0392B"                    ; red -> actually recording
        ToastLbl.Text := Chr(0x25CF) "  RECORDING"
    } else {
        ToastGui.BackColor := "2B2B2B"                    ; grey -> warming up
        ToastLbl.Text := Chr(0x25CB) "  starting" Chr(0x2026)
    }
    ToastGui.Show("NoActivate AutoSize")                  ; AHK shows it (autosized)
    hwnd := ToastGui.Hwnd

    ; measure + place in real screen pixels via raw Win32 (no DPI translation).
    rc := Buffer(16)
    DllCall("GetWindowRect", "ptr", hwnd, "ptr", rc)
    w := NumGet(rc, 8, "int") - NumGet(rc, 0, "int")
    h := NumGet(rc, 12, "int") - NumGet(rc, 4, "int")

    wa := Buffer(16)
    DllCall("SystemParametersInfo", "uint", 0x30, "uint", 0, "ptr", wa, "uint", 0)  ; SPI_GETWORKAREA
    l := NumGet(wa, 0, "int"), t := NumGet(wa, 4, "int")
    r := NumGet(wa, 8, "int"), b := NumGet(wa, 12, "int")

    m := Round(C["toast_margin"] * A_ScreenDPI / 96)
    cx := l + ((r - l) - w) // 2
    switch C["toast_position"] {
        case "top":          x := cx,           y := t + m
        case "center":       x := cx,           y := t + ((b - t) - h) // 2
        case "top-left":     x := l + m,        y := t + m
        case "top-right":    x := r - w - m,    y := t + m
        case "bottom-left":  x := l + m,        y := b - h - m
        case "bottom-right": x := r - w - m,    y := b - h - m
        default:             x := cx,           y := b - h - m
    }

    rgn := DllCall("CreateRoundRectRgn", "int", 0, "int", 0, "int", w, "int", h
        , "int", 14, "int", 14, "ptr")
    DllCall("SetWindowRgn", "ptr", hwnd, "ptr", rgn, "int", true)
    ; HWND_TOPMOST=-1  SWP_NOACTIVATE=0x10  SWP_NOZORDER not set (keep topmost)
    DllCall("SetWindowPos", "ptr", hwnd, "ptr", -1, "int", x, "int", y
        , "int", w, "int", h, "uint", 0x10)

    dbg("toast " mode ": size=" w "x" h " workarea=" l "," t "," r "," b " -> " x "," y
        " (dpi " A_ScreenDPI ")")
}

toastHide() {
    global
    if ToastGui
        try ToastGui.Hide()
}

; ------------------------------------------------------- floating mouse trigger
; Left-click-hold = talk (hold mode) / click = start-stop (toggle mode).
; Right-drag = move (position persisted to config.ini). +E0x08000000 = WS_EX_
; NOACTIVATE so clicking it never pulls keyboard focus off the VDI session.
buttonInit() {
    global
    if !C["button_enabled"]
        return
    s := A_ScreenDPI / 96
    fs := Integer(C["button_font"])
    BtnGui := Gui("-Caption +AlwaysOnTop +ToolWindow -DPIScale +E0x08000000")
    BtnGui.MarginX := Round(20 * s), BtnGui.MarginY := Round(11 * s)
    BtnGui.BackColor := "2B2B2B"
    ; +E0x20 = WS_EX_TRANSPARENT: the label is skipped in hit-testing, so every
    ; click lands on the GUI window itself (one place to handle press/hold).
    BtnLbl := BtnGui.AddText("h" Round(fs * 2.3 * s) " 0x200 Center cFFFFFF BackgroundTrans +E0x20", "")
    BtnLbl.SetFont("s" fs " bold", "Segoe UI")
    for m in [0x200, 0x201, 0x202, 0x204, 0x205]     ; MOUSEMOVE / L-down/up / R-down/up
        OnMessage(m, onBtnMouse)
    buttonState("idle")
    dbg("talk button: shown")
}

buttonState(st) {
    global
    if !BtnGui
        return
    if (st = "rec") {
        BtnGui.BackColor := "C0392B"
        BtnLbl.Text := Chr(0x25CF) "  REC"
    } else if (st = "prep") {
        BtnGui.BackColor := "3C3C3C"
        BtnLbl.Text := Chr(0x25CB) "  " Chr(0x2026)
    } else {
        BtnGui.BackColor := "2B2B2B"
        BtnLbl.Text := Chr(0x25CB) "  TALK"
    }
    buttonPlace()
}

buttonPlace() {
    global
    if !BtnGui
        return
    BtnGui.Show("NoActivate AutoSize")
    hwnd := BtnGui.Hwnd
    rc := Buffer(16)
    DllCall("GetWindowRect", "ptr", hwnd, "ptr", rc)
    w := NumGet(rc, 8, "int") - NumGet(rc, 0, "int")
    h := NumGet(rc, 12, "int") - NumGet(rc, 4, "int")

    wa := Buffer(16)
    DllCall("SystemParametersInfo", "uint", 0x30, "uint", 0, "ptr", wa, "uint", 0)  ; SPI_GETWORKAREA
    l := NumGet(wa, 0, "int"), t := NumGet(wa, 4, "int")
    r := NumGet(wa, 8, "int"), b := NumGet(wa, 12, "int")
    m := Round(C["button_margin"] * A_ScreenDPI / 96)

    if (C["button_x"] >= 0 && C["button_y"] >= 0) {
        x := C["button_x"], y := C["button_y"]
    } else {
        cx := l + ((r - l) - w) // 2
        switch C["button_position"] {
            case "top":          x := cx,           y := t + m
            case "center":       x := cx,           y := t + ((b - t) - h) // 2
            case "top-left":     x := l + m,        y := t + m
            case "top-right":    x := r - w - m,    y := t + m
            case "bottom":       x := cx,           y := b - h - m
            case "bottom-left":  x := l + m,        y := b - h - m
            default:             x := r - w - m,    y := b - h - m   ; bottom-right
        }
    }
    x := Max(l, Min(x, r - w)), y := Max(t, Min(y, b - h))          ; keep on screen

    rgn := DllCall("CreateRoundRectRgn", "int", 0, "int", 0, "int", w, "int", h
        , "int", 16, "int", 16, "ptr")
    DllCall("SetWindowRgn", "ptr", hwnd, "ptr", rgn, "int", true)
    DllCall("SetWindowPos", "ptr", hwnd, "ptr", -1, "int", x, "int", y
        , "int", w, "int", h, "uint", 0x10)                          ; HWND_TOPMOST, SWP_NOACTIVATE
}

onBtnMouse(wParam, lParam, msg, hwnd) {
    global
    if !BtnGui || (hwnd != BtnGui.Hwnd && (!BtnLbl || hwnd != BtnLbl.Hwnd))
        return
    if (DEBUG && msg != 0x200)
        dbg("btn msg=" Format("0x{:X}", msg) " on " (hwnd = BtnGui.Hwnd ? "gui" : "lbl"))
    switch msg {
        case 0x201:                                        ; WM_LBUTTONDOWN -> talk
            DllCall("SetCapture", "ptr", BtnGui.Hwnd)
            if (HK_MODE = "toggle") {
                if (gState = "idle")
                    talkStart()
                else if (gState = "recording")
                    talkStop(false)
            } else
                talkStart()
        case 0x202:                                        ; WM_LBUTTONUP
            DllCall("ReleaseCapture")
            if (HK_MODE != "toggle")
                talkStop(true)
        case 0x204:                                        ; WM_RBUTTONDOWN -> begin move
            DllCall("SetCapture", "ptr", BtnGui.Hwnd)
            pt := Buffer(8), DllCall("GetCursorPos", "ptr", pt)
            wr := Buffer(16), DllCall("GetWindowRect", "ptr", BtnGui.Hwnd, "ptr", wr)
            gBtnDrag := true
            gBtnDX := NumGet(pt, 0, "int") - NumGet(wr, 0, "int")
            gBtnDY := NumGet(pt, 4, "int") - NumGet(wr, 4, "int")
        case 0x200:                                        ; WM_MOUSEMOVE
            if gBtnDrag {
                pt := Buffer(8), DllCall("GetCursorPos", "ptr", pt)
                DllCall("SetWindowPos", "ptr", BtnGui.Hwnd, "ptr", -1
                    , "int", NumGet(pt, 0, "int") - gBtnDX
                    , "int", NumGet(pt, 4, "int") - gBtnDY
                    , "int", 0, "int", 0, "uint", 0x11)             ; SWP_NOSIZE|SWP_NOACTIVATE
            }
        case 0x205:                                        ; WM_RBUTTONUP -> save position
            if gBtnDrag {
                gBtnDrag := false
                DllCall("ReleaseCapture")
                wr := Buffer(16), DllCall("GetWindowRect", "ptr", BtnGui.Hwnd, "ptr", wr)
                C["button_x"] := NumGet(wr, 0, "int"), C["button_y"] := NumGet(wr, 4, "int")
                try {
                    IniWrite(C["button_x"], ConfigFile, "button", "x")
                    IniWrite(C["button_y"], ConfigFile, "button", "y")
                }
            }
    }
    return 0
}

; ----------------------------------------------------------------- injection
injectText(text) {
    global
    if C["collapse_newlines"]
        text := RegExReplace(text, "\s*\R\s*", " ")
    if C["normalize_ascii"]
        text := normalizeAscii(text)
    if C["trim"]
        text := Trim(text, " `t`r`n")
    if (text = "")
        return
    if C["trailing_space"]
        text .= " "

    SendInput("{Ctrl up}{Shift up}{Alt up}{LWin up}{RWin up}")
    Sleep(40)

    parts := splitChunks(text, C["chunk_size"])
    mode := C["mode"]

    if (mode = "Text") {
        for p in parts {
            SendText(p)
            Sleep(C["chunk_delay"])
        }
    } else if (mode = "Event") {
        SendMode("Event")
        SetKeyDelay(C["key_delay"], C["key_delay"])
        for p in parts {
            SendEvent("{Raw}" p)
            Sleep(C["chunk_delay"])
        }
        SendMode("Input")
    } else {                       ; "Raw" (default): SendInput scan-code stream
        for p in parts {
            SendInput("{Raw}" p)
            Sleep(C["chunk_delay"])
        }
    }
    shown := Trim(text, " `t`r`n")
    if (StrLen(shown) > 160)
        shown := SubStr(shown, 1, 157) "..."
    dbg("injected " StrLen(RTrim(text)) " chars in mode=" mode)
    emit("ok", 'Typed: "' shown '"')
}

splitChunks(s, n) {
    out := []
    if (n <= 0 || n >= StrLen(s)) {
        out.Push(s)
        return out
    }
    pos := 1
    while (pos <= StrLen(s)) {
        out.Push(SubStr(s, pos, n))
        pos += n
    }
    return out
}

normalizeAscii(s) {
    m := Map(
        Chr(0x2018), "'",  Chr(0x2019), "'",  Chr(0x201A), "'",  Chr(0x201B), "'",
        Chr(0x201C), '"',  Chr(0x201D), '"',  Chr(0x201E), '"',  Chr(0x2033), '"',
        Chr(0x2013), "-",  Chr(0x2014), "--", Chr(0x2015), "--", Chr(0x2212), "-",
        Chr(0x2026), "...", Chr(0x00A0), " ")
    for k, v in m
        s := StrReplace(s, k, v)
    return s
}

; ---------------------------------------------------------------------- misc
stripMods(combo) {
    return RegExReplace(combo, "^[\s\^\!\+\#\<\>\*\~\$]+", "")
}

resolvePath(p) {
    ; relative config paths (e.g. .venv\Scripts\pythonw.exe) are project-root relative
    if RegExMatch(p, "^([A-Za-z]:\\|\\\\)")
        return p
    return ROOT "\" p
}

clearSignals() {
    global
    for f in [CTL_START, CTL_STOP, CTL_CANCEL, CTL_READY, CTL_DONE, CTL_RESULT
            , CTL_ERROR, CTL_CANCELLED]
        try FileDelete(f)
}

; ------------------------------------------------------- commands from the UI
; The desktop UI owns the single tray icon; it drives the agent by dropping
; these flag files into the control directory (see uiSignalWatch's timer).
uiSignalWatch() {
    global
    if FileExist(UI_QUIT) {
        try FileDelete(UI_QUIT)
        ExitApp()                               ; runs onExitCleanup
    }
    if FileExist(UI_RELOAD) {
        try FileDelete(UI_RELOAD)
        Reload()
    }
    if FileExist(UI_BUTTON) {
        try FileDelete(UI_BUTTON)
        refreshButton()
    }

    ; While the UI captures a new shortcut, pause every trigger so the keys land
    ; in the UI's field instead of starting a recording. Self-heals if the file
    ; is left behind (UI crashed): drop it after 45 s.
    susp := FileExist(UI_SUSPEND) ? true : false
    if (susp && DateDiff(A_Now, FileGetTime(UI_SUSPEND, "M"), "Seconds") > 45) {
        try FileDelete(UI_SUSPEND)
        susp := false
    }
    if (susp != gSuspended) {
        gSuspended := susp
        emit("info", susp ? "Dictation paused -- recording a shortcut" : "Dictation resumed")
    }
}

; Re-read [button] enabled from config and show / hide the floating button
; live (no reload -- the recorder stays warm).
refreshButton() {
    global
    C["button_enabled"] := readCfg("button", "enabled", "false") = "true"
    if (C["button_enabled"]) {
        if BtnGui
            buttonState(gState = "recording" ? "rec" : "idle")
        else
            buttonInit()
    } else if BtnGui {
        try BtnGui.Hide()
    }
    emit("info", "Floating talk button " (C["button_enabled"] ? "on" : "off"))
}

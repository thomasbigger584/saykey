#Requires AutoHotkey v2.0
#SingleInstance Force
#MaxThreadsPerHotkey 1
Persistent

; ===========================================================================
;  VDI Dictate -- local, offline speech-to-text for locked-down VDI clients
; ---------------------------------------------------------------------------
;  [hotkey] mode = hold   : HOLD Ctrl+Space to record, release to insert (PTT)
;  [hotkey] mode = toggle : press Ctrl+Space to start, press again to stop
;  Ctrl+Shift+Space       : cancel the current recording without transcribing
;
;  The transcript is injected as KEYSTROKES (SendInput / scan-codes), never via
;  the clipboard, so it works when host<->VDI clipboard sharing is disabled.
;  A resident recorder daemon (record.py --serve) keeps the microphone and the
;  transcription backend warm so a key press starts capture in ~100 ms.
; ===========================================================================

global APP := "VDI Dictate"
global ScriptDir := A_ScriptDir
global ConfigFile := ScriptDir "\config.ini"
global ExampleFile := ScriptDir "\config.example.ini"

if !FileExist(ConfigFile) {
    if FileExist(ExampleFile)
        FileCopy(ExampleFile, ConfigFile)
    else {
        MsgBox("Missing config.ini and config.example.ini.`nRun install.ps1 first.", APP, "Iconx")
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

global HK_MODE    := StrLower(readCfg("hotkey", "mode", "hold"))
global HK_KEY     := readCfg("hotkey", "key", "^Space")
global HK_CANCEL  := readCfg("hotkey", "cancel", "^+Space")
global HK_MINHOLD := Integer(readCfg("hotkey", "min_hold_ms", "250"))
global HK_BAREKEY := stripMods(HK_KEY)

global PY := resolvePath(C["python"])
if !FileExist(PY) {
    MsgBox("Python executable not found:`n" PY "`n`nRun install.ps1 first.", APP, "Iconx")
    ExitApp()
}
global RECORDER := ScriptDir "\record.py"
if !FileExist(RECORDER) {
    MsgBox("record.py not found next to this script.", APP, "Iconx")
    ExitApp()
}
global SERVERPS1 := ScriptDir "\run-server.ps1"

; ---- resident recorder: signal files in a %TEMP% control directory ---------
global CTLDIR        := A_Temp "\vdi_dictate_ctl"
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
global T_LOG         := A_Temp "\vdi_dictate.log"
global T_DEV         := A_Temp "\vdi_dictate_devices.txt"

global gState := "idle"      ; idle | starting | recording | transcribing
global gPressTick := 0
global DAEMON_PID := 0

global ToastGui := ""
global ToastLbl := ""

dbg(msg) {
    global DEBUG, T_LOG
    if DEBUG
        try FileAppend("[ahk " FormatTime(, "HH:mm:ss") "] " msg "`n", T_LOG)
}

toastInit()
if (A_Args.Length && A_Args[1] = "--test-toast") {
    DEBUG := true
    toastShow("prep"), Sleep(2500)
    toastShow("rec"), Sleep(2500)
    toastHide()
    ExitApp()
}

buildTray()
if (HK_MODE = "toggle")
    registerHotkey(HK_KEY, onToggle, "key")
else
    registerHotkey(HK_KEY, onKeyDown, "key")
registerHotkey(HK_CANCEL, onCancel, "cancel")
OnExit(onExitCleanup)
setIdleTip()
startDaemon()
TrayTip((HK_MODE = "hold" ? "Hold " HK_KEY " to talk" : "Press " HK_KEY " to dictate")
    . " -- warming up...", APP, 0x1)

; --------------------------------------------------------------- registration
registerHotkey(combo, handler, name) {
    global
    try {
        Hotkey(combo, (*) => handler())
    } catch as e {
        MsgBox("Invalid [hotkey] " name ' = "' combo '" in config.ini`n`n'
            . e.Message
            . "`n`nAutoHotkey syntax:  ^ = Ctrl   ! = Alt   + = Shift   # = Win"
            . "`nExample:  ^Space   (Ctrl+Space)", APP, "Icon!")
    }
}

; -------------------------------------------------------------- hotkey actions
; push-to-talk: hold to record, release to transcribe + insert
onKeyDown() {
    global
    if (gState != "idle")
        return
    if !ensureDaemon()
        return

    clearSignals()
    gPressTick := A_TickCount
    gState := "starting"
    setTip("preparing...")
    toastShow("prep")                           ; on screen immediately
    FileAppend("1", CTL_START)
    SetTimer(ctlWatch, 50)
    dbg("key down -> start")

    KeyWait(HK_BAREKEY)                         ; block until the key is released

    toastHide()                                 ; keys lifted -> indicator gone
    if (gState = "idle") {                      ; watcher already finished it
        dbg("key up but already idle")
        return
    }
    SendInput("{Ctrl up}{Shift up}{Alt up}{LWin up}{RWin up}")
    held := A_TickCount - gPressTick
    if (held < HK_MINHOLD) {
        FileAppend("1", CTL_CANCEL)             ; accidental tap -> abort quietly
        setTip("tap ignored")
        dbg("key up held=" held "ms < " HK_MINHOLD " -> cancel")
    } else {
        FileAppend("1", CTL_STOP)
        gState := "transcribing"
        setTip("transcribing...")
        dbg("key up held=" held "ms -> stop")
    }
}

; toggle: press to start, press again to stop
onToggle() {
    global
    if (gState = "idle") {
        if !ensureDaemon()
            return
        clearSignals()
        gState := "starting"
        setTip("preparing...")
        toastShow("prep")
        FileAppend("1", CTL_START)
        SetTimer(ctlWatch, 50)
    } else if (gState = "recording") {
        FileAppend("1", CTL_STOP)
        gState := "transcribing"
        setTip("transcribing...")
        toastHide()
        SendInput("{Ctrl up}{Shift up}{Alt up}{LWin up}{RWin up}")
    }
}

onCancel() {
    global
    if (gState = "idle")
        return
    FileAppend("1", CTL_CANCEL)
    setTip("cancelling...")
    toastHide()
}

; --------------------------------------------------------------- state machine
ctlWatch() {
    global
    if (gState = "starting" && FileExist(CTL_READY)) {
        gState := "recording"
        dbg("ready -> recording")
        if !FileExist(CTL_CANCEL) {
            setTip(HK_MODE = "hold" ? "RECORDING -- release to insert"
                                    : "RECORDING -- " HK_KEY " to stop")
            toastShow("rec")                    ; now actually recording
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
        setIdleTip()
        toastHide()
        TrayTip("Recorder daemon stopped -- see log", APP, 0x3)
    }
}

finishResult() {
    global
    SetTimer(ctlWatch, 0)
    toastHide()
    hadError := FileExist(CTL_ERROR)
    wasCancel := FileExist(CTL_CANCELLED)
    text := ""
    try text := FileRead(CTL_RESULT, "UTF-8")
    clearSignals()
    gState := "idle"
    setIdleTip()

    if hadError {
        TrayTip("Transcription error -- opening log", APP, 0x3)
        if FileExist(T_LOG)
            try Run('notepad.exe "' T_LOG '"')
        return
    }
    if wasCancel {
        TrayTip("Cancelled", APP, 0x2)
        return
    }
    text := Trim(text, " `t`r`n")
    dbg("result: " (text = "" ? "(empty)" : "'" text "'"))
    if (text = "") {
        TrayTip("No speech detected", APP, 0x2)
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
        Run(args, ScriptDir, "Hide", &pid)
    } catch as e {
        TrayTip("Could not start recorder daemon: " e.Message, APP, 0x3)
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
    startDaemon()
    TrayTip("Recorder restarting...", APP, 0x1)
}

warmWatch() {
    global
    static waited := 0
    waited += 200
    if FileExist(CTL_UP) {
        SetTimer(warmWatch, 0)
        waited := 0
        setIdleTip()
        TrayTip("Recorder ready -- "
            . (HK_MODE = "hold" ? "hold " HK_KEY " to talk" : "press " HK_KEY " to dictate"),
            APP, 0x1)
        return
    }
    if (DAEMON_PID && !ProcessExist(DAEMON_PID)) {
        SetTimer(warmWatch, 0)
        waited := 0
        TrayTip("Recorder daemon failed to start -- check the log", APP, 0x3)
        return
    }
    if (waited = 8000)
        TrayTip("Warming up the transcription backend...", APP, 0x1)
}

onExitCleanup(*) {
    global
    try FileAppend("1", CTL_QUIT)
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
    dbg("injected " StrLen(RTrim(text)) " chars in mode=" mode)
    TrayTip("Inserted " StrLen(RTrim(text)) " characters", APP, 0x1)
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
    if RegExMatch(p, "^([A-Za-z]:\\|\\\\)")
        return p
    return A_ScriptDir "\" p
}

clearSignals() {
    global
    for f in [CTL_START, CTL_STOP, CTL_CANCEL, CTL_READY, CTL_DONE, CTL_RESULT
            , CTL_ERROR, CTL_CANCELLED]
        try FileDelete(f)
}

setIdleTip() => setTip(HK_MODE = "hold" ? "hold " HK_KEY " to talk" : HK_KEY " to dictate")
setTip(txt) => (A_IconTip := APP " -- " txt)

buildTray() {
    global
    try TraySetIcon("shell32.dll", 278)
    A_TrayMenu.Delete()
    label := APP "  [" HK_MODE " / " HK_KEY "]  "
        . (C["backend"] = "server" ? "server:" C["engine"] : "local whisper")
    A_TrayMenu.Add(label, (*) => "")
    A_TrayMenu.Disable(label)
    A_TrayMenu.Add()
    A_TrayMenu.Add("Edit config.ini", (*) => Run('notepad.exe "' ConfigFile '"'))
    A_TrayMenu.Add("List audio devices", (*) => showDevices())
    A_TrayMenu.Add("Check transcription engine", (*) => warmupEngine())
    A_TrayMenu.Add("Restart recorder", (*) => restartDaemon())
    A_TrayMenu.Add("Open log", (*) => (FileExist(T_LOG) ? Run('notepad.exe "' T_LOG '"') : TrayTip("No log yet", APP, 0x1)))
    A_TrayMenu.Add("Debug logging", (*) => toggleDebug())
    if DEBUG
        A_TrayMenu.Check("Debug logging")
    A_TrayMenu.Add()
    sub := Menu()
    sub.Add("Start / update ASR server", (*) => serverAction("up"))
    sub.Add("Server status", (*) => serverAction("status"))
    sub.Add("Server logs", (*) => serverAction("logs"))
    sub.Add("Stop ASR server", (*) => serverAction("down"))
    A_TrayMenu.Add("ASR Docker server", sub)
    A_TrayMenu.Add()
    A_TrayMenu.Add("Reload", (*) => Reload())
    A_TrayMenu.Add("Exit", (*) => ExitApp())
}

toggleDebug() {
    global
    IniWrite(DEBUG ? "false" : "true", ConfigFile, "general", "debug")
    TrayTip("Debug logging " (DEBUG ? "OFF" : "ON") " -- reloading", APP, 0x1)
    Reload()
}

showDevices() {
    global
    RunWait('"' PY '" "' RECORDER '" --list-devices --output="' T_DEV '"', ScriptDir, "Hide")
    if FileExist(T_DEV)
        Run('notepad.exe "' T_DEV '"')
}

warmupEngine() {
    global
    TrayTip("Checking transcription backend...", APP, 0x1)
    try FileDelete(T_DEV)
    RunWait('"' PY '" "' RECORDER '" --config="' ConfigFile '" --warmup --log-file="' T_DEV '"', ScriptDir, "Hide")
    if FileExist(T_DEV)
        Run('notepad.exe "' T_DEV '"')
    else
        TrayTip("Warmup produced no output -- see " T_LOG, APP, 0x2)
}

serverAction(act) {
    global
    if !FileExist(SERVERPS1) {
        TrayTip("run-server.ps1 not found", APP, 0x3)
        return
    }
    hide := (act = "up" || act = "status" || act = "logs") ? "" : "Hide"
    Run('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' SERVERPS1 '" ' act, ScriptDir, hide)
}

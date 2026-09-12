"""The unified bottom status bar shared by every view."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from .. import theme


def _server_glyph(text: str) -> tuple[str, str]:
    """(colour, glyph) from the server status string -- robust to wording."""
    t = text.lower()
    if "online" in t:
        return theme.ACCENT, "check"
    if "not reachable" in t or "unreachable" in t:
        return theme.RECORDING, "cross"
    if "stopped" in t:
        return theme.OFFLINE, "cross"
    if "not used" in t:
        return theme.OFFLINE, "dot"
    return theme.BUSY, "dot"          # starting… / checking…


class StatusBar(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusBar")
        self.setFixedHeight(34)
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 0, 14, 0)
        h.setSpacing(8)

        self._srv_icon = QLabel()
        self._srv_txt = QLabel("ASR Server: …")
        self._srv_txt.setProperty("dim", True)
        self._agent_icon = QLabel()
        self._agent_txt = QLabel("Dictation Agent: …")
        self._agent_txt.setProperty("dim", True)

        for w in (self._srv_icon, self._srv_txt):
            h.addWidget(w)
        h.addSpacing(14)
        for w in (self._agent_icon, self._agent_txt):
            h.addWidget(w)
        h.addStretch(1)

    def set_status(self, server_txt: str, agent_ok: bool, server_detail: str = "") -> None:
        colour, glyph = _server_glyph(server_txt)
        self._srv_icon.setPixmap(theme.glyph(glyph, 14, colour).pixmap(14, 14))
        text = f"ASR Server: {server_txt}"
        if server_detail:
            text += f"  —  {server_detail}"
        self._srv_txt.setText(text)

        a_colour = theme.ACCENT if agent_ok else theme.RECORDING
        self._agent_icon.setPixmap(
            theme.glyph("check" if agent_ok else "cross", 14, a_colour).pixmap(14, 14))
        self._agent_txt.setText(
            f"Dictation Agent: {'running' if agent_ok else 'stopped'}")

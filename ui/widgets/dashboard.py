"""The primary view: a big glowing state indicator + usage instructions."""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QRadialGradient
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import theme

# activity word -> (colour key, headline, sub-line)
_TEXT = {
    "idle":         ("idle", "Saykey is Ready to Dictate.", "hold"),
    "unknown":      ("unknown", "Starting up…", "wait"),
    "preparing":    ("preparing", "Getting ready…", "wait"),
    "recording":    ("recording", "Listening…", "release"),
    "transcribing": ("transcribing", "Transcribing…", "wait"),
    "stopped":      ("stopped", "Dictation agent is stopped.", "agent"),
    "error":        ("error", "Something went wrong.", "log"),
}


class GlowMic(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._colour = QColor(theme.ACCENT)
        self._phase = 0.0
        self.setMinimumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_colour(self, c: str) -> None:
        self._colour = QColor(c)
        self.update()

    def tick(self) -> None:
        self._phase = (self._phase + 0.06) % (2 * math.pi)
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height())
        cx, cy = self.width() / 2, self.height() / 2
        rad = side * 0.24

        # soft outer glow (kept inside the widget bounds so it stays circular)
        glow_r = min(rad * 2.0, side / 2)
        g = QRadialGradient(cx, cy, glow_r)
        c = QColor(self._colour)
        c.setAlpha(85)
        g.setColorAt(0.30, c)
        c2 = QColor(self._colour)
        c2.setAlpha(0)
        g.setColorAt(1.0, c2)
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(QRectF(cx - glow_r, cy - glow_r, 2 * glow_r, 2 * glow_r))

        # ring of dots
        dots = 32
        for i in range(dots):
            a = (i / dots) * 2 * math.pi
            pulse = 0.5 + 0.5 * math.sin(self._phase - a * 2)
            dr = rad * 1.55
            x = cx + math.cos(a) * dr
            y = cy + math.sin(a) * dr
            dc = QColor(self._colour)
            dc.setAlphaF(0.25 + 0.6 * pulse)
            p.setBrush(dc)
            s = side * 0.012 * (0.7 + 0.6 * pulse)
            p.drawEllipse(QRectF(x - s, y - s, 2 * s, 2 * s))

        # inner disc
        disc = QRadialGradient(cx, cy - rad * 0.3, rad * 1.4)
        disc.setColorAt(0.0, QColor(self._colour).lighter(150))
        disc.setColorAt(1.0, QColor(self._colour).darker(160))
        p.setBrush(disc)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(cx - rad, cy - rad, 2 * rad, 2 * rad))

        # white mic glyph
        theme._mic_path(p, QRectF(cx - rad * 0.7, cy - rad * 0.8, rad * 1.4, rad * 1.6),
                        QColor("#f4fff6"))
        p.end()


class DashboardView(QWidget):
    open_settings = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        header.setObjectName("DashHeader")
        header.setStyleSheet(
            f"QWidget#DashHeader {{ background: {theme.BG_ALT}; "
            f"border-bottom: 1px solid {theme.BORDER}; }}")
        hh = QHBoxLayout(header)
        hh.setContentsMargins(14, 12, 14, 12)
        hh.addStretch(1)
        logo = QLabel()
        logo.setPixmap(theme.glyph("mic", 26, theme.ACCENT).pixmap(26, 26))
        name = QLabel("Saykey")
        name.setProperty("h1", True)
        hh.addWidget(logo)
        hh.addWidget(name)
        hh.addStretch(1)
        outer.addWidget(header)

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(60)
        self._anim_timer.timeout.connect(lambda: self.glow.tick())

        body = QVBoxLayout()
        body.setContentsMargins(30, 20, 30, 20)
        outer.addLayout(body, 1)

        self.glow = GlowMic()
        body.addWidget(self.glow, 1, Qt.AlignHCenter)

        self.headline = QLabel("Saykey is Ready to Dictate.")
        self.headline.setProperty("h1", True)
        self.headline.setAlignment(Qt.AlignCenter)
        body.addWidget(self.headline)

        self.instruction = QLabel()
        self.instruction.setAlignment(Qt.AlignCenter)
        self.instruction.setTextFormat(Qt.RichText)
        self.instruction.setWordWrap(True)
        self.instruction.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 14px;")
        body.addWidget(self.instruction)

        body.addSpacing(10)
        actions = QHBoxLayout()
        actions.addStretch(1)
        btn_settings = QPushButton("Settings")
        btn_settings.setMinimumWidth(120)
        btn_settings.clicked.connect(self.open_settings)
        actions.addWidget(btn_settings)
        body.addLayout(actions)

    # ---- driven by the window ------------------------------------------
    def set_state(self, activity: str, shortcut_portable: str) -> None:
        key, headline, sub = _TEXT.get(activity, _TEXT["idle"])
        self.glow.set_colour(theme.STATE_COLOUR.get(key, theme.OFFLINE))
        self.headline.setText(headline)
        subs = {
            "hold": f"Hold <b>{shortcut_portable}</b> to talk.<br>"
                    "Your text will be typed into the active window.",
            "release": f"Release <b>{shortcut_portable}</b> to insert the text.",
            "wait": "One moment…",
            "agent": "Start it from Settings → Advanced &amp; Developer, or the tray menu.",
            "log": "Open the log from the tray menu for details.",
        }
        self.instruction.setText(subs.get(sub, subs["hold"]))

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._anim_timer.start()

    def hideEvent(self, e) -> None:  # noqa: N802
        self._anim_timer.stop()
        super().hideEvent(e)

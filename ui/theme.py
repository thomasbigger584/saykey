"""Shared dark theme (QSS + palette) and small runtime-drawn glyphs.

Applied to the main window's subtree only, so tray menus / native message
boxes keep the OS look. Cross-platform: pure Qt, no platform calls.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap

# ---- palette -------------------------------------------------------------
BG        = "#141517"   # window
BG_ALT    = "#1c1d21"   # panels / cards
BG_RAISE  = "#26272c"   # inputs / hover
BORDER    = "#33353b"
TEXT      = "#e8e9ec"
TEXT_DIM  = "#9aa0a6"
ACCENT    = "#3fb950"   # "ready" / selected green
ACCENT_DK = "#2ea043"
RECORDING = "#e5484d"
BUSY      = "#e0a92b"
OFFLINE   = "#7a7d85"

# activity word -> (colour, headline, sub-instruction key)
STATE_COLOUR = {
    "idle":         ACCENT,
    "ready":        ACCENT,
    "preparing":    BUSY,
    "recording":    RECORDING,
    "transcribing": BUSY,
    "stopped":      OFFLINE,
    "error":        RECORDING,
    "unknown":      OFFLINE,
}


STYLESHEET = f"""
* {{
    color: {TEXT};
    font-size: 13px;
}}
QWidget#Root, QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {{
    background: {BG};
}}
QLabel {{ background: transparent; }}
QLabel[dim="true"] {{ color: {TEXT_DIM}; }}
QLabel[h1="true"] {{ font-size: 22px; font-weight: 600; }}
QLabel[h2="true"] {{ font-size: 16px; font-weight: 600; }}
QLabel[section="true"] {{ color: {TEXT_DIM}; font-size: 11px;
                          font-weight: 700; letter-spacing: 1px; }}

QFrame#Card, QGroupBox {{
    background: {BG_ALT};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QGroupBox {{ margin-top: 14px; padding: 14px 12px 12px 12px; }}
QGroupBox::title {{
    subcontrol-origin: margin; left: 12px; padding: 0 4px;
    color: {TEXT_DIM}; font-weight: 700; font-size: 11px; letter-spacing: 1px;
}}

QPushButton {{
    background: {BG_RAISE};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 6px 14px;
}}
QPushButton:hover {{ background: #303138; border-color: #45474e; }}
QPushButton:pressed {{ background: #26272c; }}
QPushButton:disabled {{ color: {TEXT_DIM}; background: {BG_ALT}; }}
QPushButton[accent="true"] {{
    background: {ACCENT_DK}; border-color: {ACCENT}; color: #06210c; font-weight: 600;
}}
QPushButton[accent="true"]:hover {{ background: {ACCENT}; }}
QPushButton[segment="true"] {{ border-radius: 0; }}
QPushButton[segment="true"]:checked {{
    background: {ACCENT_DK}; border-color: {ACCENT}; color: #06210c; font-weight: 600;
}}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px;
                        border: 1px solid {BORDER}; background: {BG_RAISE}; }}
QCheckBox::indicator:checked {{ background: {ACCENT_DK}; border-color: {ACCENT}; }}

QComboBox, QLineEdit, QKeySequenceEdit, QSpinBox, QPlainTextEdit {{
    background: {BG_RAISE};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 6px 8px;
    selection-background-color: {ACCENT_DK};
}}
QComboBox:focus, QLineEdit:focus, QKeySequenceEdit:focus, QSpinBox:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{ border: 0; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {BG_RAISE}; border: 1px solid {BORDER};
    selection-background-color: {ACCENT_DK};
}}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #4a4d55; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}

/* left navigation rail */
QListWidget#Nav {{
    background: {BG_ALT}; border: 0; border-right: 1px solid {BORDER};
    outline: 0; padding: 6px;
}}
QListWidget#Nav::item {{
    padding: 12px 10px; border-radius: 8px; margin: 2px 0; color: {TEXT_DIM};
}}
QListWidget#Nav::item:selected {{
    background: {BG_RAISE}; color: {TEXT};
    border-left: 3px solid {ACCENT};
}}
QListWidget#Nav::item:hover:!selected {{ background: {BG_RAISE}; }}

QFrame#StatusBar {{ background: {BG_ALT}; border-top: 1px solid {BORDER}; }}
"""


def _mic_path(p: QPainter, r: QRectF, colour: QColor) -> None:
    """Draw a filled microphone glyph inside r."""
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(colour))
    w = r.width()
    cx = r.center().x()
    body = QRectF(cx - w * 0.16, r.top() + w * 0.12, w * 0.32, w * 0.42)
    p.drawRoundedRect(body, w * 0.16, w * 0.16)
    pen = QPen(colour, max(2.0, w * 0.05))
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(cx - w * 0.24, r.top() + w * 0.18, w * 0.48, w * 0.5),
              200 * 16, 140 * 16)
    p.drawLine(QPointF(cx, r.top() + w * 0.62), QPointF(cx, r.top() + w * 0.76))
    p.drawLine(QPointF(cx - w * 0.14, r.top() + w * 0.76),
               QPointF(cx + w * 0.14, r.top() + w * 0.76))


def glyph(kind: str, size: int = 22, colour: str = TEXT) -> QIcon:
    """kind: mic | nvidia | cpu | gear | wrench | check | cross | dot"""
    px = QPixmap(size, size)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    c = QColor(colour)
    r = QRectF(1, 1, size - 2, size - 2)

    if kind == "mic":
        _mic_path(p, r, c)
    elif kind == "nvidia":
        p.setPen(QPen(QColor("#76b900"), max(2.0, size * 0.08)))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(r.adjusted(size * 0.12, size * 0.2, -size * 0.12, -size * 0.2))
        p.drawEllipse(r.adjusted(size * 0.28, size * 0.32, -size * 0.05, -size * 0.1))
    elif kind == "cpu":
        p.setPen(QPen(c, max(1.6, size * 0.06)))
        p.setBrush(Qt.NoBrush)
        inner = r.adjusted(size * 0.22, size * 0.22, -size * 0.22, -size * 0.22)
        p.drawRoundedRect(inner, 3, 3)
        for i in range(3):
            x = inner.left() + inner.width() * (0.25 + 0.25 * i)
            p.drawLine(QPointF(x, r.top()), QPointF(x, inner.top()))
            p.drawLine(QPointF(x, inner.bottom()), QPointF(x, r.bottom()))
            y = inner.top() + inner.height() * (0.25 + 0.25 * i)
            p.drawLine(QPointF(r.left(), y), QPointF(inner.left(), y))
            p.drawLine(QPointF(inner.right(), y), QPointF(r.right(), y))
    elif kind in ("check", "dot", "cross"):
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(c))
        p.drawEllipse(r)
        if kind == "check":
            pen = QPen(QColor(BG), max(2.0, size * 0.11))
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawPolyline([QPointF(size * 0.3, size * 0.52),
                            QPointF(size * 0.44, size * 0.66),
                            QPointF(size * 0.72, size * 0.35)])
        elif kind == "cross":
            pen = QPen(QColor(BG), max(2.0, size * 0.11))
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(size * 0.34, size * 0.34), QPointF(size * 0.66, size * 0.66))
            p.drawLine(QPointF(size * 0.66, size * 0.34), QPointF(size * 0.34, size * 0.66))
    else:  # gear / wrench / fallback -> simple ring
        p.setPen(QPen(c, max(1.6, size * 0.08)))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(r.adjusted(3, 3, -3, -3))

    p.end()
    return QIcon(px)

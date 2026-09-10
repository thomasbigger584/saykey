"""Tray icon drawn at runtime (no binary asset to ship)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPixmap


def make_icon(state: str = "idle") -> QIcon:
    """state: idle | recording | busy | offline"""
    colours = {
        "idle": QColor("#3A7BD5"),
        "recording": QColor("#C0392B"),
        "busy": QColor("#E1A700"),
        "offline": QColor("#7A7A7A"),
    }
    bg = colours.get(state, colours["idle"])

    px = QPixmap(64, 64)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)

    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(bg))
    p.drawEllipse(QRectF(2, 2, 60, 60))

    # simple microphone glyph
    p.setBrush(QBrush(QColor("white")))
    p.drawRoundedRect(QRectF(26, 14, 12, 22), 6, 6)
    p.setPen(QColor("white"))
    pen = p.pen()
    pen.setWidth(3)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(20, 16, 24, 26), 200 * 16, 140 * 16)
    p.drawLine(QPointF(32, 42), QPointF(32, 50))
    p.drawLine(QPointF(25, 50), QPointF(39, 50))

    p.end()
    return QIcon(px)

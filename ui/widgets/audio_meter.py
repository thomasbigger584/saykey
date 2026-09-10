"""A horizontal VU meter drawn as a row of bars (see the mockups)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from .. import theme


class AudioMeter(QWidget):
    def __init__(self, bars: int = 14, parent=None) -> None:
        super().__init__(parent)
        self._bars = bars
        self._level = 0.0
        self.setMinimumSize(bars * 6 + 4, 22)
        self.setMaximumHeight(28)

    def set_level(self, level: float) -> None:
        level = max(0.0, min(1.0, float(level)))
        if abs(level - self._level) > 0.01:
            self._level = level
            self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        gap = 2.0
        bw = (w - gap * (self._bars - 1)) / self._bars
        lit = round(self._level * self._bars)
        base = QColor(theme.BORDER)
        for i in range(self._bars):
            frac = (i + 1) / self._bars
            bh = h * (0.35 + 0.65 * frac)
            x = i * (bw + gap)
            r = QRectF(x, (h - bh) / 2, bw, bh)
            p.setPen(Qt.NoPen)
            if i < lit:
                if frac > 0.85:
                    c = QColor(theme.RECORDING)
                elif frac > 0.65:
                    c = QColor(theme.BUSY)
                else:
                    c = QColor(theme.ACCENT)
            else:
                c = base
            p.setBrush(c)
            p.drawRoundedRect(r, 1.5, 1.5)
        p.end()

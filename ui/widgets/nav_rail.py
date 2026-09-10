"""Left-hand navigation rail (replaces the old top tabs)."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import theme

ITEMS = [
    ("General", "gear"),
    ("Dictation & Hotkeys", "mic"),
    ("ASR Engine & Models", "nvidia"),
    ("Advanced & Developer", "wrench"),
]


class NavRail(QWidget):
    navigate = Signal(int)   # panel index
    home = Signal()          # header clicked -> back to dashboard

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("NavRail")
        self.setFixedWidth(210)
        self.setStyleSheet(
            f"QWidget#NavRail {{ background: {theme.BG_ALT}; "
            f"border-right: 1px solid {theme.BORDER}; }}")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        header = QLabel("  ‹  Saykey")
        header.setObjectName("NavHeader")
        header.setProperty("h2", True)
        header.setToolTip("Back to the dashboard")
        header.setCursor(Qt.PointingHandCursor)
        header.setStyleSheet(
            f"QLabel#NavHeader {{ padding: 16px 12px; background: {theme.BG_ALT}; "
            f"border: 0; }}")
        header.mousePressEvent = lambda _e: self.home.emit()  # noqa: SLF001
        v.addWidget(header)

        self.list = QListWidget()
        self.list.setObjectName("Nav")
        self.list.setIconSize(QSize(18, 18))
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        for text, kind in ITEMS:
            it = QListWidgetItem(theme.glyph(kind, 18, theme.TEXT_DIM), text)
            it.setSizeHint(QSize(190, 46))
            self.list.addItem(it)
        self.list.currentRowChanged.connect(self.navigate)
        v.addWidget(self.list, 1)

    def set_index(self, i: int) -> None:
        self.list.blockSignals(True)
        self.list.setCurrentRow(i)
        self.list.blockSignals(False)

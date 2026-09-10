"""A single selectable model 'card' for the ASR Engine & Models panel."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from .. import theme
from ..models_catalog import ModelChoice


def _pill(text: str, fg: str, bg: str, border: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setFixedHeight(20)
    lbl.setStyleSheet(
        f"background: {bg}; border: 1px solid {border}; border-radius: 10px;"
        f"padding: 0 8px; color: {fg}; font-size: 10px; font-weight: 700;")
    return lbl


class ModelCard(QFrame):
    clicked = Signal(str)   # model id

    def __init__(self, model: ModelChoice, parent=None) -> None:
        super().__init__(parent)
        self.model = model
        self.setObjectName("Card")
        self.setCursor(Qt.PointingHandCursor)
        self._selected = False
        self._current = False

        h = QHBoxLayout(self)
        h.setContentsMargins(14, 12, 14, 12)
        h.setSpacing(12)

        is_local = model.backend != "server"
        kind = "cpu" if is_local else (
            "nvidia" if "canary" in model.id or "nvidia" in model.label.lower() else "mic")
        icon = QLabel()
        icon.setPixmap(theme.glyph(kind, 30,
                                   theme.TEXT_DIM if is_local else theme.ACCENT).pixmap(30, 30))
        icon.setFixedWidth(38)
        icon.setAlignment(Qt.AlignTop | Qt.AlignHCenter)

        col = QVBoxLayout()
        col.setSpacing(3)
        title = QLabel(model.label)
        title.setProperty("h2", True)
        title.setWordWrap(True)
        desc = QLabel(model.description)
        desc.setWordWrap(True)
        desc.setProperty("dim", True)
        self.status = QLabel()

        self._tier = _pill("GPU · Docker" if not is_local else "LOCAL · CPU",
                           theme.TEXT_DIM, theme.BG_RAISE, theme.BORDER)
        self._state = QLabel()          # APPLIED / SELECTED pill (hidden when neither)
        self._state.setAlignment(Qt.AlignCenter)
        self._state.setFixedHeight(20)

        top = QHBoxLayout()
        top.addWidget(title, 1)
        top.addWidget(self._state, 0, Qt.AlignTop)
        top.addWidget(self._tier, 0, Qt.AlignTop)
        col.addLayout(top)
        col.addWidget(desc)
        col.addWidget(self.status)

        h.addWidget(icon)
        h.addLayout(col, 1)
        self._restyle()

    def set_state(self, selected: bool, current: bool) -> None:
        self._selected, self._current = selected, current
        self._restyle()

    def _restyle(self) -> None:
        pending = self._selected and not self._current

        # --- top-right state pill ---
        if self._current:
            self._state.setText("✓ APPLIED")
            self._state.setStyleSheet(
                f"background: {theme.ACCENT_DK}; border-radius: 10px; padding: 0 9px;"
                f"color: #06210c; font-size: 10px; font-weight: 800;")
            self._state.show()
        elif pending:
            self._state.setText("SELECTED")
            self._state.setStyleSheet(
                f"background: transparent; border: 1px solid {theme.ACCENT};"
                f"border-radius: 10px; padding: 0 9px; color: {theme.ACCENT};"
                f"font-size: 10px; font-weight: 800;")
            self._state.show()
        else:
            self._state.hide()

        # --- description status line ---
        if pending:
            self.status.setText("Selected — click Save to switch to this model")
            self.status.setStyleSheet(f"color: {theme.ACCENT}; font-weight: 600;")
        elif self._current:
            self.status.setText("Active — currently transcribing your dictation")
            self.status.setStyleSheet(f"color: {theme.TEXT_DIM}; font-weight: 600;")
        elif self.model.backend == "server":
            self.status.setText(f"Download required ({self.model.size}) on first use")
            self.status.setStyleSheet(f"color: {theme.BUSY}; font-weight: 600;")
        else:
            self.status.setText(f"Local — runs on the CPU ({self.model.size})")
            self.status.setStyleSheet(f"color: {theme.TEXT_DIM}; font-weight: 600;")

        # --- frame: bold green border ONLY for the pending pick; the applied
        #     model is marked by its "✓ APPLIED" pill, not a border ---
        if pending:
            border, width, bg = theme.ACCENT, 2, "#1b2a1e"
        else:
            border, width, bg = theme.BORDER, 1, theme.BG_ALT
        self.setStyleSheet(
            f"QFrame#Card {{ background: {bg};"
            f" border: {width}px solid {border}; border-radius: 10px; }}")

    def mousePressEvent(self, _e) -> None:  # noqa: N802
        self.clicked.emit(self.model.id)

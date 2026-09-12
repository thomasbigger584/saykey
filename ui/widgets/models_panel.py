"""ASR Engine & Models panel -- curated model cards + hardware context."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import models_catalog
from .. import orchestrator as orch
from .. import theme
from ..config_store import Settings
from .model_card import ModelCard


def _card(text: str) -> QFrame:
    f = QFrame()
    f.setObjectName("Card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(14, 10, 14, 10)
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setProperty("dim", True)
    lay.addWidget(lbl)
    return f


class ModelsPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._model_dirty = False
        self._loaded_id: str | None = None
        self._current_id: str | None = None
        self._selected_id: str | None = None
        self._cards: dict[str, ModelCard] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(10)

        title = QLabel("Curated Models")
        title.setProperty("h1", True)
        outer.addWidget(title)
        outer.addWidget(_card("Recommended: GPU-accelerated (runs locally)   ·   "
                              "Fallback: CPU only (faster-whisper)"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        vb = QVBoxLayout(holder)
        vb.setContentsMargins(0, 0, 4, 0)
        vb.setSpacing(8)
        for m in models_catalog.CATALOG:
            c = ModelCard(m)
            c.clicked.connect(self._pick)
            self._cards[m.id] = c
            vb.addWidget(c)
        vb.addStretch(1)
        scroll.setWidget(holder)
        outer.addWidget(scroll, 1)

        self._hw = QLabel()
        self._hw.setWordWrap(True)
        self._hw.setAlignment(Qt.AlignCenter)
        self._hw.setProperty("dim", True)
        hw_card = QFrame()
        hw_card.setObjectName("Card")
        hwl = QVBoxLayout(hw_card)
        hwl.setContentsMargins(14, 10, 14, 10)
        hwl.addWidget(self._hw)
        outer.addWidget(hw_card)
        self._detect_hardware()

    # ---- hardware footer (async-ish: nvidia-smi is quick, run once) ----
    def _detect_hardware(self) -> None:
        gpu = orch.gpu_name()
        if gpu:
            self._hw.setText(f"GPU detected ({gpu}). Model selection optimised for "
                             "performance. Downloads may take several minutes.")
        else:
            self._hw.setText("No NVIDIA GPU detected — the local CPU model "
                             "(faster-whisper) is recommended; server models will be slow.")

    # ---- data <-> widgets ---------------------------------------------
    def load(self, s: Settings) -> None:
        cur = models_catalog.match(backend=s.backend, engine=s.engine,
                                   model_override=s.model_override)
        self._current_id = cur.id if cur else None
        self._loaded_id = self._current_id or models_catalog.DEFAULT_ID
        self._selected_id = self._loaded_id
        self._model_dirty = False
        self._restyle()

    def apply_to(self, s: Settings) -> None:
        if not self._model_dirty:
            return
        m = models_catalog.by_id(self._selected_id)
        if m:
            s.backend = m.backend
            s.engine = m.engine
            s.model_override = m.model_override

    def _pick(self, model_id: str) -> None:
        self._selected_id = model_id
        self._model_dirty = model_id != (self._current_id or self._loaded_id)
        self._restyle()

    def _restyle(self) -> None:
        for mid, card in self._cards.items():
            card.set_state(selected=(mid == self._selected_id),
                           current=(mid == self._current_id))

    # ---- driven by the window: ASR server launching/downloading/loading ----
    def set_server_state(self, phase: str | None, progress: int | None, detail: str) -> None:
        for mid, card in self._cards.items():
            if mid == self._current_id:
                card.set_loading(phase, progress, detail)
            else:
                card.set_loading(None, None, "")

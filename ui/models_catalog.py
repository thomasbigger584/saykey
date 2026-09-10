"""
The model picker's catalogue.

Each entry maps a friendly choice to the config.ini keys the ASR server /
local fallback actually read:  backend, engine, model_override.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelChoice:
    id: str
    label: str
    description: str
    backend: str          # "server" | "local"
    engine: str           # [server] engine  (ignored when backend == "local")
    model_override: str   # [server] model / [local] model
    size: str
    languages: str
    gpu_recommended: bool


CATALOG: list[ModelChoice] = [
    ModelChoice(
        id="parakeet-en",
        label="Parakeet Unified EN 0.6B  (recommended)",
        description="NVIDIA Parakeet TDT 0.6B v2. Best-in-class English accuracy, "
                    "punctuation + capitalisation built in, extremely fast on GPU.",
        backend="server", engine="parakeet", model_override="",
        size="~0.6 GB", languages="English", gpu_recommended=True,
    ),
    ModelChoice(
        id="parakeet-multi",
        label="Parakeet Multilingual 0.6B v3",
        description="NVIDIA Parakeet TDT 0.6B v3 — 25 European languages, same speed class.",
        backend="server", engine="parakeet-v3", model_override="",
        size="~0.6 GB", languages="25 languages", gpu_recommended=True,
    ),
    ModelChoice(
        id="parakeet-ctc",
        label="Parakeet CTC 0.6B  (lighter)",
        description="CTC decoder variant — a touch less accurate, lower latency and memory.",
        backend="server", engine="parakeet-ctc", model_override="",
        size="~0.6 GB", languages="English", gpu_recommended=False,
    ),
    ModelChoice(
        id="canary",
        label="NVIDIA Canary 1B v2",
        description="Larger multilingual model with strong punctuation. Needs a GPU to be snappy.",
        backend="server", engine="canary", model_override="",
        size="~1 GB", languages="multilingual", gpu_recommended=True,
    ),
    ModelChoice(
        id="whisper-large-v3",
        label="Whisper large-v3  (server)",
        description="OpenAI Whisper large-v3 via faster-whisper in the container. Very accurate, slower.",
        backend="server", engine="whisper", model_override="large-v3",
        size="~3 GB", languages="99 languages", gpu_recommended=True,
    ),
    ModelChoice(
        id="whisper-local",
        label="Whisper base.en  (local, CPU, no Docker)",
        description="Runs in-process on the CPU. No server needed — good for a laptop or a quick start.",
        backend="local", engine="whisper", model_override="base.en",
        size="~150 MB", languages="English", gpu_recommended=False,
    ),
]

DEFAULT_ID = "parakeet-en"

_BY_ID = {m.id: m for m in CATALOG}


def by_id(model_id: str) -> ModelChoice | None:
    return _BY_ID.get(model_id)


def match(*, backend: str, engine: str, model_override: str) -> ModelChoice | None:
    """Find the catalogue entry that corresponds to the current config values."""
    for m in CATALOG:
        if m.backend != backend:
            continue
        if backend == "local":
            if m.model_override == (model_override or "base.en"):
                return m
        elif m.engine == engine and m.model_override == (model_override or ""):
            return m
    return None

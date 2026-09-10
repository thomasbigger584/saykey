"""
Saykey ASR server.

An OpenAI-compatible speech-to-text endpoint in front of a swappable engine
(Parakeet via onnx-asr by default). Designed to run in the bundled Docker
container; the AHK client POSTs 16 kHz mono WAV audio and gets back text.

Endpoints
    GET  /health                     -> {status, engine, model, device}
    GET  /v1/models                  -> OpenAI-style model list
    POST /v1/audio/transcriptions    -> {text, ...}   (multipart 'file', or raw body)

Environment (set by docker compose from config.ini [server])
    ASR_ENGINE, ASR_MODEL, ASR_DEVICE, ASR_QUANTIZATION
    ASR_UPSTREAM_URL, ASR_UPSTREAM_KEY   (engine = openai)
    ASR_PRELOAD=1                        load the model at startup
"""

from __future__ import annotations

import io
import os
import threading
import time
from typing import Optional

import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

from engines import EngineError, load_engine

app = FastAPI(title="Saykey ASR", version="1.0.0")

_engine = None
_engine_err: Optional[str] = None
_lock = threading.Lock()


def get_engine():
    global _engine, _engine_err
    if _engine is not None:
        return _engine
    with _lock:
        if _engine is None:
            try:
                _engine = load_engine(
                    spec=os.getenv("ASR_ENGINE", "parakeet"),
                    model=os.getenv("ASR_MODEL") or None,
                    device=os.getenv("ASR_DEVICE", "auto"),
                    quantization=os.getenv("ASR_QUANTIZATION", "none"),
                    upstream_url=os.getenv("ASR_UPSTREAM_URL") or None,
                    upstream_key=os.getenv("ASR_UPSTREAM_KEY") or None,
                )
                _engine_err = None
            except Exception as exc:  # noqa: BLE001
                _engine_err = str(exc)
                raise
    return _engine


@app.on_event("startup")
def _preload():
    if os.getenv("ASR_PRELOAD", "1") != "1":
        return
    t0 = time.time()
    try:
        eng = get_engine()
        print(f"[startup] engine ready: {eng.describe()} in {time.time() - t0:.1f}s", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] engine load failed: {exc}", flush=True)


@app.get("/health")
def health():
    try:
        eng = get_engine()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=503,
                            content={"status": "error", "detail": str(exc)})
    return {"status": "ok", **eng.describe()}


@app.get("/v1/models")
def models():
    try:
        eng = get_engine()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc))
    return {"object": "list",
            "data": [{"id": eng.model, "object": "model", "owned_by": eng.name}]}


def _decode(raw: bytes):
    try:
        data, sr = sf.read(io.BytesIO(raw), dtype="float32", always_2d=False)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"could not decode audio: {exc}")
    if getattr(data, "ndim", 1) > 1:
        data = data.mean(axis=1)
    return np.ascontiguousarray(data, dtype="float32"), int(sr)


@app.post("/v1/audio/transcriptions")
async def transcriptions(
    request: Request,
    file: Optional[UploadFile] = File(default=None),
    model: str = Form(default=""),
    language: str = Form(default=""),
    response_format: str = Form(default="json"),
):
    raw = await file.read() if file is not None else await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="no audio provided")

    audio, sr = _decode(raw)
    t0 = time.time()
    try:
        text = get_engine().transcribe(audio, sr, language or None)
    except EngineError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"engine unavailable: {exc}")

    if response_format == "text":
        return PlainTextResponse(text)
    eng = get_engine()
    return {
        "text": text,
        "model": eng.model,
        "engine": eng.name,
        "device": eng.device,
        "audio_seconds": round(len(audio) / sr, 3) if sr else None,
        "processing_seconds": round(time.time() - t0, 3),
    }

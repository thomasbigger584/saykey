"""
Pluggable ASR engines for the vdi-dictate server.

The engine is chosen with the ASR_ENGINE env var (config.ini -> [server] engine):

    parakeet | parakeet-v3 | parakeet-ctc | parakeet-rnnt | canary | whisper
        friendly aliases (see _ALIASES below)
    onnx:<model-id>     any onnx-asr model id
    fw:<model-id>       any faster-whisper model id
    hf:<repo/id>        any HuggingFace ASR model (transformers pipeline)
    openai             forward requests to another OpenAI-compatible server

ASR_MODEL optionally overrides the checkpoint for the chosen engine.
"""

from __future__ import annotations

import io
import json
import os
import urllib.error
import urllib.request
import wave

import numpy as np

TARGET_SR = 16000


class EngineError(RuntimeError):
    pass


def _to_mono_16k(audio, sr):
    audio = np.asarray(audio, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr and sr != TARGET_SR and audio.size:
        n = int(round(audio.size * TARGET_SR / sr))
        audio = np.interp(np.linspace(0, audio.size - 1, n),
                          np.arange(audio.size), audio).astype("float32")
    return np.ascontiguousarray(audio, dtype="float32"), TARGET_SR


def _detect_device(pref):
    pref = (pref or "auto").strip().lower()
    if pref in ("cpu", "cuda"):
        return pref
    try:
        import onnxruntime as ort

        if "CUDAExecutionProvider" in ort.get_available_providers():
            return "cuda"
    except Exception:
        pass
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


class _Base:
    name = "base"

    def __init__(self):
        self.model = ""
        self.device = "cpu"

    def transcribe(self, audio, sr, language=None) -> str:
        raise NotImplementedError

    def warmup(self):
        pass

    def describe(self) -> dict:
        return {"engine": self.name, "model": self.model, "device": self.device}


class OnnxAsrEngine(_Base):
    name = "onnx-asr"

    def __init__(self, model_id, device="auto", quantization="none"):
        super().__init__()
        self.model = model_id
        self.device = _detect_device(device)
        try:
            import onnx_asr
        except Exception as exc:  # noqa: BLE001
            raise EngineError(f"onnx-asr is not installed: {exc}") from exc

        kwargs = {}
        if self.device == "cuda":
            kwargs["providers"] = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        if (quantization or "").strip().lower() == "int8":
            kwargs["quantization"] = "int8"
        try:
            self._m = onnx_asr.load_model(model_id, **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise EngineError(f"failed to load onnx-asr model '{model_id}': {exc}") from exc

    def transcribe(self, audio, sr, language=None) -> str:
        audio, sr = _to_mono_16k(audio, sr)
        if audio.size == 0:
            return ""
        try:
            out = self._m.recognize(audio, sample_rate=sr)
        except Exception as exc:  # noqa: BLE001
            raise EngineError(f"onnx-asr recognize failed: {exc}") from exc
        if isinstance(out, (list, tuple)):
            out = " ".join(str(x) for x in out)
        return str(out).strip()


class FasterWhisperEngine(_Base):
    name = "faster-whisper"

    def __init__(self, model_id="base.en", device="auto", quantization="none"):
        super().__init__()
        self.model = model_id
        self.device = _detect_device(device)
        if (quantization or "").strip().lower() == "int8":
            compute = "int8"
        else:
            compute = "float16" if self.device == "cuda" else "int8"
        try:
            from faster_whisper import WhisperModel

            self._m = WhisperModel(model_id, device=self.device, compute_type=compute,
                                   download_root=os.getenv("HF_HOME", "/models"))
        except Exception as exc:  # noqa: BLE001
            raise EngineError(f"failed to load faster-whisper model '{model_id}': {exc}") from exc

    def transcribe(self, audio, sr, language=None) -> str:
        audio, sr = _to_mono_16k(audio, sr)
        if audio.size == 0:
            return ""
        lang = None if (not language or language == "auto") else language
        segments, _info = self._m.transcribe(audio, language=lang, vad_filter=True,
                                             condition_on_previous_text=False)
        return " ".join(" ".join(s.text.split()) for s in segments).strip()


class TransformersEngine(_Base):
    name = "transformers"

    def __init__(self, model_id, device="auto", quantization="none"):
        super().__init__()
        self.model = model_id
        self.device = _detect_device(device)
        try:
            import torch
            from transformers import pipeline

            dtype = torch.float16 if self.device == "cuda" else torch.float32
            self._p = pipeline("automatic-speech-recognition", model=model_id,
                               device=0 if self.device == "cuda" else -1,
                               torch_dtype=dtype)
        except Exception as exc:  # noqa: BLE001
            raise EngineError(f"failed to load transformers model '{model_id}': {exc}") from exc

    def transcribe(self, audio, sr, language=None) -> str:
        audio, sr = _to_mono_16k(audio, sr)
        if audio.size == 0:
            return ""
        try:
            out = self._p({"array": audio, "sampling_rate": sr},
                          chunk_length_s=30, batch_size=8)
        except Exception as exc:  # noqa: BLE001
            raise EngineError(f"transformers inference failed: {exc}") from exc
        return (out.get("text") or "").strip()


class OpenAIProxyEngine(_Base):
    name = "openai-proxy"

    def __init__(self, upstream_url, upstream_key=None, model="whisper-1"):
        super().__init__()
        if not upstream_url:
            raise EngineError("engine 'openai' requires ASR_UPSTREAM_URL")
        self.base = upstream_url.rstrip("/")
        self.key = upstream_key or ""
        self.model = model
        self.device = "remote"

    def transcribe(self, audio, sr, language=None) -> str:
        audio, sr = _to_mono_16k(audio, sr)
        if audio.size == 0:
            return ""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
        wav = buf.getvalue()

        boundary = "----vdidictate" + os.urandom(8).hex()
        chunks = []
        for name, value in (("model", self.model), ("language", language or ""),
                            ("response_format", "json")):
            if not value:
                continue
            chunks.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                f"{value}\r\n".encode())
        chunks.append(
            (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
             f"filename=\"audio.wav\"\r\nContent-Type: audio/wav\r\n\r\n").encode()
            + wav + b"\r\n")
        chunks.append(f"--{boundary}--\r\n".encode())
        body = b"".join(chunks)

        req = urllib.request.Request(self.base + "/v1/audio/transcriptions",
                                     data=body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        if self.key:
            req.add_header("Authorization", "Bearer " + self.key)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise EngineError(f"upstream request failed: {exc}") from exc
        return (data.get("text") or "").strip()


_ALIASES = {
    "parakeet":      ("onnx", "nemo-parakeet-tdt-0.6b-v2"),
    "parakeet-v2":   ("onnx", "nemo-parakeet-tdt-0.6b-v2"),
    "parakeet-v3":   ("onnx", "nemo-parakeet-tdt-0.6b-v3"),
    "parakeet-ctc":  ("onnx", "nemo-parakeet-ctc-0.6b"),
    "parakeet-rnnt": ("onnx", "nemo-parakeet-rnnt-0.6b"),
    "canary":        ("onnx", "nemo-canary-1b-v2"),
    "canary-flash":  ("onnx", "istupakov/canary-1b-flash-onnx"),
    "whisper":       ("fw", "base.en"),
    "whisper-large": ("fw", "large-v3"),
}

_KIND_CLASS = {
    "onnx": OnnxAsrEngine, "onnx-asr": OnnxAsrEngine,
    "fw": FasterWhisperEngine, "faster-whisper": FasterWhisperEngine,
    "hf": TransformersEngine, "transformers": TransformersEngine,
}


def load_engine(spec, model=None, device="auto", quantization="none",
                upstream_url=None, upstream_key=None) -> _Base:
    spec = (spec or "parakeet").strip()
    low = spec.lower()

    if low == "openai":
        return OpenAIProxyEngine(upstream_url, upstream_key, model or "whisper-1")

    if low in _ALIASES:
        kind, default_model = _ALIASES[low]
    elif ":" in spec:
        kind, default_model = spec.split(":", 1)
        kind, default_model = kind.strip().lower(), default_model.strip()
    else:
        raise EngineError(f"unknown engine '{spec}' "
                          f"(try: {', '.join(sorted(_ALIASES))}, or onnx:/fw:/hf:/openai)")

    cls = _KIND_CLASS.get(kind)
    if cls is None:
        raise EngineError(f"unknown engine kind '{kind}'")
    return cls(model or default_model, device=device, quantization=quantization)

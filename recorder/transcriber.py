"""
Client-side transcription router used by record.py.

backend = server : POST a 16 kHz mono WAV to an OpenAI-compatible
                   /v1/audio/transcriptions endpoint -- the bundled Docker
                   ASR server, or speaches / an NVIDIA Riva gateway / a remote
                   box / a cloud API. Can fall back to local automatically.
backend = local  : transcribe in-process with faster-whisper (CPU).

Only the standard library is used for the server path (no extra client deps).
"""

from __future__ import annotations

import io
import json
import os
import socket
import urllib.error
import urllib.request
import wave
from pathlib import Path

SAMPLE_RATE = 16000


def _wav_bytes(audio, sample_rate: int = SAMPLE_RATE) -> bytes:
    import numpy as np

    pcm = np.clip(np.asarray(audio, dtype="float32"), -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def _multipart(fields: dict, filename: str, file_bytes: bytes):
    boundary = "----saykey" + os.urandom(8).hex()
    out = io.BytesIO()

    def w(s):
        out.write(s.encode("utf-8") if isinstance(s, str) else s)

    for key, val in fields.items():
        if val is None or val == "":
            continue
        w(f"--{boundary}\r\n")
        w(f'Content-Disposition: form-data; name="{key}"\r\n\r\n{val}\r\n')
    w(f"--{boundary}\r\n")
    w(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n')
    w("Content-Type: audio/wav\r\n\r\n")
    w(file_bytes)
    w(f"\r\n--{boundary}--\r\n")
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


class LocalWhisper:
    """In-process faster-whisper. Used as backend=local and as the server fallback."""

    def __init__(self, model="base.en", compute_type="int8", beam_size=1,
                 models_dir="models", language="en", offline=False):
        self.model = model
        self._opts = dict(compute_type=compute_type, beam_size=int(beam_size),
                          models_dir=models_dir, language=language, offline=offline)
        self._m = None

    def _load(self):
        if self._m is not None:
            return
        if self._opts["offline"]:
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
        from faster_whisper import WhisperModel

        md = Path(self._opts["models_dir"]).expanduser()
        md.mkdir(parents=True, exist_ok=True)
        self._m = WhisperModel(self.model, device="cpu",
                               compute_type=self._opts["compute_type"],
                               download_root=str(md))

    def warmup(self):
        self._load()

    def transcribe(self, audio, sample_rate=SAMPLE_RATE) -> str:
        import numpy as np

        if np.asarray(audio).size == 0:
            return ""
        self._load()
        lang = self._opts["language"]
        lang = None if (not lang or lang.lower() == "auto") else lang
        segments, _info = self._m.transcribe(
            np.asarray(audio, dtype="float32"), language=lang,
            beam_size=self._opts["beam_size"], vad_filter=True,
            condition_on_previous_text=False)
        return " ".join(" ".join(s.text.split()) for s in segments).strip()

    def describe(self) -> str:
        return f"local faster-whisper ({self.model}, cpu)"


class RemoteServer:
    """OpenAI-compatible ASR endpoint, with optional local fallback."""

    _NET_ERRORS = (urllib.error.URLError, socket.timeout, ConnectionError, TimeoutError, OSError)

    def __init__(self, base_url, model="parakeet", timeout=30, api_key="", fallback=None):
        self.base = base_url.rstrip("/")
        self.model = model or "parakeet"
        self.timeout = int(timeout)
        self.api_key = api_key or ""
        self.fallback = fallback

    def health(self):
        try:
            with urllib.request.urlopen(self.base + "/health", timeout=5) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            return None

    def warmup(self):
        h = self.health()
        if self.fallback is not None:
            try:
                self.fallback.warmup()
            except Exception:
                pass
        return h

    def _post(self, audio, sample_rate) -> str:
        body, ctype = _multipart(
            {"model": self.model, "response_format": "json"},
            "audio.wav", _wav_bytes(audio, sample_rate))
        req = urllib.request.Request(self.base + "/v1/audio/transcriptions", data=body)
        req.add_header("Content-Type", ctype)
        if self.api_key:
            req.add_header("Authorization", "Bearer " + self.api_key)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return (data.get("text") or "").strip()

    def transcribe(self, audio, sample_rate=SAMPLE_RATE) -> str:
        import numpy as np

        if np.asarray(audio).size == 0:
            return ""
        try:
            return self._post(audio, sample_rate)
        except self._NET_ERRORS as exc:
            if self.fallback is not None:
                return self.fallback.transcribe(audio, sample_rate)
            raise RuntimeError(f"ASR server unreachable at {self.base}: {exc}") from exc

    def describe(self) -> str:
        fb = f"  fallback={self.fallback.describe()}" if self.fallback else "  fallback=none"
        return f"server {self.base} (model={self.model}){fb}"


def make_transcriber(cfg: dict):
    backend = (cfg.get("backend") or "server").strip().lower()

    def _local():
        return LocalWhisper(
            model=cfg.get("local_model", "base.en"),
            compute_type=cfg.get("local_compute_type", "int8"),
            beam_size=cfg.get("local_beam_size", 1),
            models_dir=cfg.get("models_dir", "models"),
            language=cfg.get("language", "en"),
            offline=str(cfg.get("offline", "false")).strip().lower() == "true",
        )

    if backend == "local":
        return _local()

    fallback = None
    if str(cfg.get("fallback_to_local", "true")).strip().lower() == "true":
        fallback = _local()
    return RemoteServer(
        base_url=cfg.get("server_url", "http://127.0.0.1:9000"),
        model=cfg.get("server_model", "parakeet"),
        timeout=cfg.get("server_timeout", 30),
        api_key=cfg.get("server_api_key", ""),
        fallback=fallback,
    )

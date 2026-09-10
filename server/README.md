# Saykey ASR server

A small OpenAI-compatible speech-to-text service in front of a **swappable
engine**. Default engine: **NVIDIA Parakeet TDT 0.6B v2** (English) via
`onnx-asr` / ONNX Runtime, on the GPU.

Runs as a Docker container. The recorder (`backend = server` in `../config.ini`)
POSTs 16 kHz mono WAV and gets back text.

This folder is self-contained: the Docker **build context is `server/` itself**
(`docker-compose.yml` uses `context: .`), and the `../models` volume points at
the project-root model cache shared with the local fallback.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| `GET`  | `/health` | `{status, engine, model, device}` — `503` while the model loads |
| `GET`  | `/v1/models` | OpenAI-style listing |
| `POST` | `/v1/audio/transcriptions` | multipart `file=@audio.wav` (or a raw `audio/wav` body). Returns `{text, ...}`. `response_format=text` returns plain text. |

```bash
curl -F file=@sample.wav -F response_format=text http://localhost:9000/v1/audio/transcriptions
```

## Choosing the model

Set `[server] engine` in `../config.ini`, then `..\scripts\run-server.ps1 restart`
(or the UI's Models tab).

| `engine` | Loads | Backend |
|---|---|---|
| `parakeet` *(default)* | `nemo-parakeet-tdt-0.6b-v2` (English) | onnx-asr |
| `parakeet-v3` | `nemo-parakeet-tdt-0.6b-v3` (25 languages) | onnx-asr |
| `parakeet-ctc` | `nemo-parakeet-ctc-0.6b` | onnx-asr |
| `canary` | `nemo-canary-1b-v2` (multilingual) | onnx-asr |
| `whisper` | faster-whisper (`[server] model`, e.g. `large-v3`) | faster-whisper |
| `onnx:<id>` | any onnx-asr model id | onnx-asr |
| `fw:<id>` | any faster-whisper model id | faster-whisper |
| `hf:<repo/id>` | any HF ASR model (Parakeet, Whisper, wav2vec2, MMS…) | transformers — image must be built with `-Hf` |
| `openai` | forwards to `ASR_UPSTREAM_URL` | proxy |

`[server] model` overrides the checkpoint for any engine. `[server] quantization = int8`
gives a smaller/faster model for the onnx-asr and faster-whisper engines.

## Environment variables

`ASR_ENGINE`, `ASR_MODEL`, `ASR_DEVICE` (`auto|cuda|cpu`), `ASR_QUANTIZATION`
(`none|int8`), `ASR_UPSTREAM_URL`, `ASR_UPSTREAM_KEY`, `ASR_PRELOAD` (`1` = load
at startup). `HF_HOME=/models` — model cache, bind-mounted to `../models`.

## Build profiles

- GPU (default): `ORT_PACKAGE=onnxruntime-gpu`, CUDA 12 base image.
- CPU: `..\scripts\run-server.ps1 -Cpu` → `ORT_PACKAGE=onnxruntime`.
- `+transformers`: `..\scripts\run-server.ps1 -Hf` → also installs `requirements-hf.txt` (torch).

## Running without run-server.ps1

From this `server/` directory:

```bash
cp .env.example .env            # edit as needed
docker compose up -d --build                                   # CPU
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build   # GPU
```

## Local dev (no Docker, needs Python 3.10+)

```bash
pip install -r requirements.txt onnxruntime          # or onnxruntime-gpu
ASR_ENGINE=parakeet uvicorn app:app --port 9000      # run from this folder
```

## Notes

- First start downloads the model into `../models` (Parakeet v2 ONNX ≈ 0.6–2.4 GB
  depending on precision). `..\scripts\run-server.ps1 up` waits up to 5 min for it.
- If the CUDA execution provider fails to initialise (driver/cuDNN mismatch),
  ONNX Runtime falls back to CPU automatically. Force it with `[server] device = cpu`.

# GC Image Processing

Standalone FastAPI Processing Server for `gc-image-stream`.

The primary frame input is the Stream Server gRPC relay. FastAPI is used for
operational APIs such as health and buffer status.

## Quick Start

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py `
  --host 127.0.0.1 `
  --port 9000 `
  --grpc-bind 127.0.0.1:50051
```

Configure the Stream Server relay target with:

```env
STREAM_RELAY_ENABLED=true
STREAM_RELAY_TARGET=127.0.0.1:50051
STREAM_RELAY_TIMEOUT_SEC=
```

## APIs

- `GET /health`
- `GET /status`: gRPC receiver state and buffer state
- `GET /cameras`
- `GET /cameras/{device_id}`
- `GET /cameras/{device_id}/latest`
- `GET /cameras/{device_id}/latest/image`

## Debug Dump

Frame files are not written to disk by default. Enable debug dumps only when
you need local image files for inspection:

```env
PROCESSING_DEBUG_DUMP_ENABLED=true
PROCESSING_DEBUG_DUMP_DIR=debug_frames
PROCESSING_DEBUG_DUMP_MAX_PER_CAMERA=20
```

## ASGI

Deployment entrypoint:

```text
main:app
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

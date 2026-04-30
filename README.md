# GC Image Processing

Standalone FastAPI Processing Server for `gc-image-stream`.

The primary frame input is the Stream Server gRPC relay. FastAPI is used for
operational and debug APIs, not as the frame-processing trigger.

Relay contract:

```text
proto/processing_relay.proto
```

## Runtime Flow

```text
Stream Server gRPC relay
  -> GrpcRelayReceiver
  -> ProcessingService
  -> FrameBufferManager
  -> SyncMatcher
  -> ProcessingQueue
  -> MotionCaptureWorker
```

## Project Layout

```text
app/
  api/
  buffers/
  core/
  infrastructure/
  models/
  pipeline/
  schemas/
  services/
  sync/
```

See `docs/architecture.md` for module responsibilities.

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

## Sync Pipeline

Initial sync is optional. It matches frames by `timestamp_ms`, enqueues matched
frame sets, and lets a placeholder worker consume them.

```powershell
.\.venv\Scripts\python.exe main.py `
  --host 127.0.0.1 `
  --port 9000 `
  --grpc-bind 127.0.0.1:50051 `
  --sync-enabled `
  --sync-window-ms 50 `
  --expected-camera camera1 `
  --expected-camera camera2 `
  --expected-camera camera3 `
  --expected-camera camera4
```

## APIs

- `GET /health`
- `GET /status`
- `GET /cameras`
- `GET /cameras/{device_id}`
- `GET /cameras/{device_id}/latest`
- `GET /cameras/{device_id}/latest/image`
- `GET /pipeline/status`
- `GET /pipeline/recent-frame-sets`

`/pipeline/status` includes sync matcher counters, queue counters, and worker
state. The worker currently records a `placeholder_processed` result when it
consumes a synchronized frame set.

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

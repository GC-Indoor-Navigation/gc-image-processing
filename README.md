# GC Image Processing

Standalone FastAPI Processing Server for `gc-image-stream`.

The primary frame input is the Stream Server gRPC relay. FastAPI is used for
operational and debug APIs, not as the frame-processing trigger.

Relay contract:

```text
proto/processing_relay.proto
```

The receiver supports both raw frame relay and pre-synchronized frame-set relay:

- `StreamFrames`: raw camera frames, still handled by the local buffer/sync path.
- `StreamFrameSets`: synchronized frame sets, converted directly into the
  processing queue and deduplicated by `frame_set_id`.

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

## Environment

The server automatically loads `.env` from the project root.

Start from `.env.example`:

```powershell
Copy-Item .env.example .env
```

Key settings:

```env
PROCESSING_HTTP_HOST=127.0.0.1
PROCESSING_HTTP_PORT=9000
PROCESSING_GRPC_BIND=127.0.0.1:50051
PROCESSING_GRPC_ENABLED=true
PROCESSING_BUFFER_SIZE=120
PROCESSING_SYNC_ENABLED=true
PROCESSING_SYNC_WINDOW_MS=50
PROCESSING_EXPECTED_CAMERAS=camera1,camera2,camera3,camera4
PROCESSING_WORKER_ENABLED=true
```

## Quick Start

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

CLI flags can still override runtime settings:

```powershell
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

Initial sync matches frames by `timestamp_ms`, enqueues matched frame sets, and
lets a placeholder worker consume them.

Example `.env`:

```env
PROCESSING_SYNC_ENABLED=true
PROCESSING_SYNC_WINDOW_MS=50
PROCESSING_EXPECTED_CAMERAS=camera1,camera2,camera3,camera4
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
state. It also exposes relay frame-set accept/duplicate counters. The worker
currently records a `placeholder_processed` result when it consumes a
synchronized frame set.

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

# Real-Time Face Detection & Recognition Service

This document describes the architecture, request flow, and operational
details of the `inference/` service — the component responsible for
detecting faces, recognizing identities, and streaming results back to
clients in real time for the Staff Attendance System.

## 1. Goals

* **Ultra-fast detection**: a YOLOv8 model tuned specifically for faces,
  not a generic multi-class detector.
* **Accurate recognition**: a VGGFace2-pretrained ResNet (`InceptionResnetV1`)
  producing 512-D embeddings, matched via Qdrant's approximate nearest
  neighbor search instead of a linear scan over a flat JSON file.
* **Real-time, continuous streaming**: a WebSocket pipeline that always
  processes the *latest* frame from the client's camera, never a growing
  backlog — so the perceived latency stays low even if a frame takes longer
  to process than the camera's capture interval.
* **GPU optional**: every model runs on CPU by default if no usable CUDA
  device is found; GPU is purely an accelerator, never a hard requirement.
* **Decoupled, event-driven side effects**: recognition/attendance events are
  published to Redis pub/sub, so any number of dashboards or services can
  subscribe without being coupled to the connection that produced the event.

## 2. High-level architecture

```
                         ┌─────────────────────────────┐
                         │        Client (browser)      │
                         │  getUserMedia() -> canvas     │
                         │  -> base64 JPEG over WS       │
                         └───────────────┬──────────────┘
                                         │ WebSocket /ws/stream/{id}
                                         ▼
                         ┌─────────────────────────────┐
                         │         FastAPI app          │
                         │         (inference/api.py)   │
                         └───────────────┬──────────────┘
                                         │
                     ┌───────────────────┼─────────────────────┐
                     ▼                   ▼                     ▼
          ┌─────────────────┐  ┌──────────────────┐  ┌────────────────────┐
          │  ws_manager.py  │  │   pipeline.py     │  │  REST endpoints    │
          │  StreamSession  │─▶│ RealtimeFacePipe- │◀─│ (check-in/out,     │
          │ (1 frame mail-  │  │ line.process_frame│  │  register-staff,   │
          │  box per conn,  │  └─────────┬──────────┘  │  testing sandbox)  │
          │  worker thread) │            │             └────────────────────┘
          └─────────────────┘            │
                                         │
                ┌────────────────────────┼─────────────────────────┐
                ▼                        ▼                         ▼
     ┌────────────────────┐  ┌────────────────────────┐  ┌──────────────────┐
     │ face_detection_     │  │ face_recognition_       │  │ redis_service.py │
     │ service.py          │  │ service.py               │  │                  │
     │ YOLOv8-face model   │─▶│ InceptionResnetV1       │  │ de-dupe cache,   │
     │ (Ultralytics)       │  │ (facenet-pytorch) + ->   │  │ attendance       │
     └────────────────────┘  │ quadrant_service.py      │  │ debounce,        │
                              │ (Qdrant kNN lookup)      │  │ pub/sub events   │
                              └────────────────────────┘  └──────────────────┘
                                                                      │
                                                                      ▼
                                                          ┌──────────────────────┐
                                                          │  /ws/events           │
                                                          │  DashboardBroadcaster │
                                                          │  (fan-out to every    │
                                                          │  connected dashboard) │
                                                          └──────────────────────┘
```

External services (run via `docker-compose.yml`):

* **Qdrant** — vector database storing 512-D face embeddings with cosine
  similarity search. Collection: `face_embeddings` (configurable).
* **Redis** — pub/sub broadcast channel, short-TTL recognition de-dupe cache,
  and attendance debounce keys.

## 3. Module map

| File | Responsibility |
|---|---|
| [config.py](./config.py) | Single source of truth for all environment-driven settings: device selection (CPU/GPU), model paths/URLs, thresholds, Qdrant/Redis connection info, testing-sandbox paths. |
| [services/face_detection_service.py](./services/face_detection_service.py) | Wraps an Ultralytics YOLOv8 face model. Auto-downloads weights on first run. Thread-safe `detect()` returns sorted, capped bounding boxes. Process-wide singleton via `get_detector()`. |
| [services/face_recognition_service.py](./services/face_recognition_service.py) | Wraps `InceptionResnetV1` (facenet-pytorch, VGGFace2 weights). Preprocesses face crops, computes embeddings in batches, and delegates identity search/registration to `quadrant_service`. Singleton via `get_recognizer()`. |
| [services/quadrant_service.py](./services/quadrant_service.py) | Thin, reusable Qdrant client wrapper: `ingest_embedding`, `search_embedding`, `delete_embedding`/`delete_by_filter`, `count_embeddings`, `list_embeddings`, `health_check`, etc. Not face-specific — generic vector CRUD. |
| [services/redis_service.py](./services/redis_service.py) | Redis wrapper: recognition de-dupe cache (`was_recently_seen` / `mark_recently_seen`), attendance debounce (`can_mark_attendance` / `debounce_attendance`), and pub/sub (`publish_event` / `subscribe_events`). Singleton via `get_redis()`. |
| [pipeline.py](./pipeline.py) | `RealtimeFacePipeline` — the orchestrator. `process_frame()` runs detect → batch-embed → batch-identify → (optional) attendance marking, and publishes Redis events for recognized faces. Also contains direct sqlite helpers (`_lookup_employee`, `_save_attendance`) against the Django DB. Singleton via `get_pipeline()`. |
| [ws_manager.py](./ws_manager.py) | Real-time plumbing: `FrameMailbox` (always holds only the newest frame — old frames are dropped, not queued), `StreamSession` (one per WebSocket connection; owns a background worker thread that continuously pulls from the mailbox and runs the pipeline), and `DashboardBroadcaster` (bridges Redis pub/sub to every `/ws/events` WebSocket client). |
| [api.py](./api.py) | FastAPI app: startup/shutdown lifecycle (pre-loads all models so the first frame is fast), WebSocket endpoints, REST compatibility endpoints for the Django front-end, and the testing-sandbox endpoints. |

## 4. The real-time streaming pipeline, step by step

1. **Client connects**: browser opens `new WebSocket("ws://host/ws/stream/{connection_id}")`
   and sends an `{"type": "init", "mode": ..., "action": ...}` control message.
2. **Frame submission**: the client grabs a camera frame onto a `<canvas>`,
   encodes it as base64 JPEG, and sends `{"type": "frame", "image": "..."}`
   roughly every 100–150 ms (configurable by the client; the server has no
   fixed frame rate expectation).
3. **Mailbox, not a queue**: `StreamSession.submit_frame()` writes into a
   `FrameMailbox`, which holds exactly one frame. If the worker thread is
   still busy with the previous frame when a new one arrives, the new frame
   simply **overwrites** the old one — nothing is queued or backlogged. This
   is the key trick that keeps the system "real time": under load, the
   client always gets a result for the most recent reality, not a stale one
   from several frames ago.
4. **Background worker thread**: each `StreamSession` has one dedicated
   thread that loops: pull latest frame → `pipeline.process_frame()` → push
   result onto an `asyncio.Queue` via `run_coroutine_threadsafe`. Running the
   (blocking, CPU/GPU-bound) model inference in a thread — rather than the
   asyncio event loop — means the WebSocket's `receive()`/`send()` loop never
   blocks on inference.
5. **Pipeline execution** (`RealtimeFacePipeline.process_frame`):
   1. `detector.detect(frame)` → YOLOv8 face boxes, largest-first, capped at
      `MAX_FACES_PER_FRAME`.
   2. Each box is cropped out of the frame.
   3. `recognizer.embed_batch(crops)` — **all faces in the frame are embedded
      in a single batched forward pass** through the ResNet for efficiency.
   4. `recognizer.identify_batch(embeddings)` — each embedding is searched
      against Qdrant (`top_k=1`, cosine similarity, thresholded).
   5. For every recognized (non-"Unknown") identity:
      * Employee name/department is looked up from the Django sqlite DB.
      * `redis.was_recently_seen()` de-dupes repeat broadcasts of the exact
        same identity within `RECOGNITION_CACHE_TTL` seconds (per
        connection), then `redis.publish_event("face_recognized", ...)` is
        fired for subscribed dashboards.
      * If the session is in `"attendance"` mode, `redis.can_mark_attendance()`
        checks a debounce key (`ATTENDANCE_DEBOUNCE_TTL` seconds per
        emp_id+action) before writing to the attendance table and publishing
        an `"attendance_marked"` event.
6. **Result delivery**: the worker thread's result (`faces: [...]`,
   `processing_ms`) is pushed to the connection's own `asyncio.Queue`; a
   separate `sender()` coroutine drains that queue and calls
   `websocket.send_json()`. If the client is slow to read, the queue (max
   size 8) drops the oldest pending result rather than growing unbounded.
7. **Dashboard fan-out**: independently, any client can open
   `ws://host/ws/events`. `DashboardBroadcaster` subscribes once to the Redis
   channel and re-broadcasts every event to all currently-registered
   dashboard sockets — completely decoupled from which `/ws/stream`
   connection originally produced the event.

### Why this design is fast

* **No polling, no fixed-interval timers** on the server side — everything
  is event/queue driven.
* **Single batched recognition pass per frame** instead of one model call
  per detected face.
* **Dropped-frame backpressure** instead of buffering: latency never grows
  under load, only (rarely) a frame is skipped.
* **Models are loaded once** at process startup (FastAPI `lifespan`), not
  per-request or per-connection.
* **Half-precision (`fp16`) on GPU** when available (`USE_FP16=true`),
  automatically disabled on CPU.

## 5. REST endpoints (compatibility + management)

| Endpoint | Purpose |
|---|---|
| `GET /health` | Reports device, Qdrant reachability, Redis reachability. |
| `WS /ws/stream/{connection_id}` | Primary real-time detection/recognition stream described above. |
| `WS /ws/events` | Dashboard/monitoring feed of all recognition + attendance events, system-wide. |
| `POST /recognize-frame` | Single-shot recognize (used by legacy/manual callers). |
| `POST /check-in`, `POST /check-out` | Single base64-image attendance marking, used by the Django kiosk UI. |
| `POST /register-staff-faces/` | Detects+crops faces from uploaded images, saves to `media/`, and ingests embeddings into Qdrant — used by the staff signup flow. |
| `POST /store_embeddings/` | Bulk-ingest a dataset directory (one sub-folder per `emp_id`) into Qdrant. |
| `GET /load_embeddings/` | Returns Qdrant collection stats (point count, status) — replaces the old flat-file dump. |
| `POST /reload-embeddings` | Compatibility no-op that reports Qdrant connectivity (identities are always queried live now, nothing to "reload"). |

## 6. Testing sandbox

A fully isolated set of endpoints and a static HTML page let you upload
photos, train an identity, see it recognized live, and wipe everything —
without touching real Django media files or production Qdrant records.

* **UI**: served at `GET /testing-ui/` (static files from [`testing/`](../testing)),
  mounted directly on the FastAPI app via `StaticFiles`.
* **`POST /testing/register`** (multipart form: `emp_id`, `name`, `files[]`) —
  detects the face in each uploaded image, saves the crop under
  `testing/uploads/{emp_id}/`, and ingests the embedding into Qdrant tagged
  with payload `test_sandbox: true`.
* **`GET /testing/list`** — lists sandbox-tagged identities and their
  embedding counts (via `quadrant_service.list_embeddings(test_sandbox=True)`).
* **`POST /testing/clear`** — deletes every sandbox-tagged embedding from
  Qdrant (`delete_by_filter(test_sandbox=True)`) and removes the
  `testing/uploads/` directory contents. Real/production embeddings
  (registered through `/register-staff-faces/`) are never touched because
  they don't carry the `test_sandbox` payload flag.

Because sandbox data is tagged rather than stored in a separate collection,
it is recognized by the exact same live `/ws/stream` pipeline as real staff —
useful for verifying the whole detect → recognize → broadcast loop works
before registering real employees.

## 7. Configuration reference

All settings are environment-driven (see `.env.example`); the most relevant:

| Variable | Default | Purpose |
|---|---|---|
| `FORCE_CPU` | `false` | Force CPU even if CUDA is available. |
| `USE_FP16` | `true` | Use half precision on GPU only. |
| `YOLO_FACE_MODEL` / `YOLO_FACE_MODEL_URL` | `yolov8n-face.pt` | Which face-detection weights to auto-download/use. |
| `DETECTION_CONF_THRESHOLD` | `0.5` | YOLO confidence cutoff. |
| `DETECTION_IMG_SIZE` | `640` | YOLO inference resolution. |
| `MAX_FACES_PER_FRAME` | `5` | Cap on faces processed per frame (bounds recognition cost). |
| `RECOGNITION_SCORE_THRESHOLD` | `0.55` | Minimum cosine similarity to accept a Qdrant match as a known identity. |
| `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION` | — | Qdrant connection. |
| `REDIS_URL`, `REDIS_EVENTS_CHANNEL` | — | Redis connection + pub/sub channel name. |
| `RECOGNITION_CACHE_TTL` | `5`s | How long a connection suppresses repeat `face_recognized` broadcasts for the same identity. |
| `ATTENDANCE_DEBOUNCE_TTL` | `60`s | Minimum gap between repeat check-in/out writes for the same employee+action. |

## 8. Running it

```powershell
# 1. Start Qdrant + Redis
docker compose up -d

# 2. Start the inference service (GPU used automatically if available)
uv run uvicorn inference.api:app --host 0.0.0.0 --port 5600

# 3. Open the testing sandbox
#    http://localhost:5600/testing-ui/
```

`GET /health` should report `{"status": "ok", "qdrant": true, "redis": true}`
once both containers are reachable.

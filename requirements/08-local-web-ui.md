# Requirement 08: Local-Only Web UI (Live Preview, Parameter Tuning & Manual Capture)

## Goal
Provide a lightweight, locally accessible web interface where the user can inspect a live camera preview, adjust all IMX362 UVC camera parameters in real time, persist tuned settings for future automated captures, and trigger manual 12MP captures that enter the same archive/backup/publish pipeline.

## Access & Security Model
- **Local-Only Access:**
  - Server binds to configurable `web.host` (default `127.0.0.1` for SSH port-forwarding, or `0.0.0.0` on home LAN) and `web.port` (default `8080`).
  - Enforce a configurable **`allowed_cidrs`** IP allowlist (default: loopback + private RFC1918 LAN ranges `127.0.0.0/8`, `::1/128`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) so non-local requests are rejected with `403 Forbidden`.
- **No Heavy Framework Dependencies:** Built using Python stdlib `http.server.ThreadingHTTPServer` + static HTML/CSS/vanilla JS (zero Flask/FastAPI/Node dependencies, ~15–20 MB RAM on Pi Zero W v1).

## Live Video Preview & Camera Locking
- **Preview Format:** Stream `multipart/x-mixed-replace` MJPEG from the camera at a preview resolution (configurable `preview_width = 1280`, `preview_height = 720`, `preview_fps = 10`) using `ffmpeg` (`-c:v copy` from camera hardware MJPG so CPU load on Pi Zero W v1 stays minimal).
  - Alternatively/fallback: single-frame JPEG snapshot polling if stream is paused.
- **Exclusive V4L2 Device Coordination (`camera.lock`):**
  - Linux V4L2 allows only one streaming reader on `/dev/videoX` at a time.
  - Both the Web UI preview streamer and `daily-shot capture` / `tick` coordinate via an `fcntl.flock` lock (`data/camera.lock`) plus a `data/capture.requested` signal file:
    1. When a scheduled `tick` capture needs the camera, it touches `data/capture.requested` and waits on `camera.lock`.
    2. The Web UI MJPEG stream checks `capture.requested` on each frame, promptly closes its `ffmpeg` preview process, releases `camera.lock`, and shows a `"Scheduled capture in progress..."` status in the browser.
    3. Once the scheduled capture finishes and removes `capture.requested`, the Web UI automatically resumes live streaming.

## Exposed Camera Controls (All 18 IMX362 UVC Controls)
The backend parses live ranges/defaults/current values via `v4l2-ctl -d <dev> --list-ctrls-menus` (falling back to the verified IMX362 control schema) and renders controls dynamically in two groups:

### User Controls
| Control | Type | Range / Options | Default | Notes |
|---|---|---|---|---|
| `brightness` | int | `1..64` (step 1) | `27` | Slider + numeric input |
| `contrast` | int | `1..64` (step 1) | `32` | Slider + numeric input |
| `saturation` | int | `1..64` (step 1) | `32` | Slider + numeric input |
| `hue` | int | `1..64` (step 1) | `32` | Slider + numeric input |
| `white_balance_automatic` | bool | `0 / 1` | `1` | Toggle; disables `white_balance_temperature` when `1` |
| `white_balance_temperature` | int | `1800..10000` (step 1) | `5000` | Active only when `white_balance_automatic = 0` |
| `gamma` | int | `0..64` (step 1) | `32` | Slider + numeric input |
| `gain` | int | `0..15` (step 1) | `0` | Slider + numeric input |
| `power_line_frequency` | menu | `0: Disabled`, `1: 50 Hz`, `2: 60 Hz` | `1` (override `2`) | Dropdown |
| `sharpness` | int | `1..64` (step 1) | `38` | Slider + numeric input |
| `backlight_compensation` | int | `0..15` (step 1) | `0` | Slider + numeric input |

### Camera Controls
| Control | Type | Range / Options | Default | Notes |
|---|---|---|---|---|
| `auto_exposure` | menu | `1: Manual Mode`, `3: Aperture Priority Mode` | `3` | Dropdown; disables `exposure_time_absolute` when `3` |
| `exposure_time_absolute` | int | `1..5000` (step 1) | `100` | Active only when `auto_exposure = 1` |
| `focus_automatic_continuous` | bool | `0 / 1` | `1` (override `0`) | Toggle; disables `focus_absolute` when `1` |
| `focus_absolute` | int | `0..1023` (step 1) | `192` | Active only when `focus_automatic_continuous = 0` |
| `pan_absolute` | int | `-576000..576000` (step 3600) | `0` | Slider + numeric input |
| `tilt_absolute` | int | `-576000..576000` (step 3600) | `0` | Slider + numeric input |
| `zoom_absolute` | int | `0..100` (step 1) | `0` | Slider + numeric input |

## Web UI Actions & API Endpoints
- `GET /`: Single-page local web dashboard (live MJPEG preview, parameter controls, action buttons, latest capture thumbnail & system status summary).
- `GET /stream.mjpg`: Live MJPEG preview stream (yields automatically when `capture.requested` is set).
- `GET /api/controls`: Returns current values, min/max/step/default/menu items, active/inactive flags, and supported `MJPG` capture resolutions from the camera.
- `POST /api/controls`: Applies one or more V4L2 controls immediately (`v4l2-ctl --set-ctrl`, using two-phase mode-before-value ordering) so changes appear live in the video stream.
- `POST /api/controls/save`: Persists current tuned `[camera.controls]` to a camera-settings override file (e.g., `data/camera_controls.json` or `config.toml`) so future automated `daily-shot capture` runs use the saved parameters.
- `POST /api/controls/reset`: Restores V4L2 hardware default values for all controls.
- `POST /api/capture`: Accepts optional JSON body `{"watermark": true|false, "resolution": "WxH"}` (defaults to `web.manual_capture_watermark_default` and `camera.width x camera.height`). Pauses preview stream, acquires `camera.lock`, runs the standard `capture_photo(..., width=..., height=..., watermark=...)` MJPEG pipeline (using the current active controls), saves `YYYY-MM-DD_HH-MM-SS.jpg` into `<archive_dir>/YYYY-MM-DD/`, resumes preview stream, and returns the saved file metadata (including `width`, `height`, `watermarked: bool`) & preview URL.
- **Web UI Controls for Manual Capture:** Displays a **Resolution** selector (populated from all discrete `MJPG` resolutions supported by the camera, e.g. `4000x3000`, `3840x2160`, `1920x1080`, etc.) and a **"Timestamp Watermark"** toggle/checkbox right next to the **"Take Photo"** button.
- `GET /shots/latest` (or `/shots/<path>`): Serves captured photos safely within `archive_dir` for inspection in the UI.

## Manual Capture Parity with Automatic Captures
Manual photos taken from the Web UI use the **exact same storage path and naming convention** (`<archive_dir>/YYYY-MM-DD/YYYY-MM-DD_HH-MM-SS.jpg`), meaning:
- They are automatically included in the daily `rclone` cloud backup.
- They follow the same sync-gated 14-day local retention cleanup.
- They are visible to the daily Bluesky selector (which picks the frame closest to exact sunset time).

## Acceptance Criteria
- Unit tests verify CIDR local-only access enforcement, V4L2 `--list-ctrls-menus` parsing into JSON schema, two-phase control updates, `camera.lock` / `capture.requested` coordination, control persistence, and manual capture endpoint behavior.
- Live test on dev machine confirms live MJPEG preview updates immediately when sliders (e.g., `brightness`, `focus_absolute`, `saturation`) are adjusted, and manual capture saves a `4000x3000` JPEG cleanly without device-busy errors.

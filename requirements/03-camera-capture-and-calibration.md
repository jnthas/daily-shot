# Requirement 03: Camera Discovery, Calibration & 12MP Capture

## Goal
Reliably discover the IMX362 USB UVC camera after any reboot/replug, lock manual infinity focus and anti-flicker controls, allow exposure/AWB stabilization before capture, and provide a focus calibration CLI.

## Verified Hardware Details
Observed on `Fic760xUsbCamera` (`/dev/video2` during local inspection):
- Capture format: `MJPG` at `4000x3000` (15 fps / 10 fps)
- Controls:
  - `focus_automatic_continuous` (bool, default `1`)
  - `focus_absolute` (`0..1023`, default `192`, inactive until `focus_automatic_continuous=0`)
  - `auto_exposure` (`1` = Manual Mode, `3` = Aperture Priority Mode)
  - `exposure_time_absolute` (`1..5000`, active in Manual Mode)
  - `white_balance_automatic` (bool), `white_balance_temperature` (`1800..10000`)
  - `power_line_frequency` (`0` = Disabled, `1` = 50 Hz, `2` = 60 Hz)
  - `brightness` (`1..64`), `contrast` (`1..64`), `saturation` (`1..64`), `hue` (`1..64`), `sharpness` (`1..64`), `gain` (`0..15`)

## Device Resolution & Control Locking (`camera.py`)
- **Discovery:** If `camera.device == "auto"`, parse `v4l2-ctl --list-devices` to locate the first `/dev/video*` node under `device_name_match` (`Fic760xUsbCamera`). Also support explicit paths like `/dev/v4l/by-id/...`.
- **Two-Phase Control Application:** Apply mode switches (`focus_automatic_continuous`, `auto_exposure`, `white_balance_automatic`) in a first `v4l2-ctl --set-ctrl` call, then apply numeric values (`focus_absolute`, `power_line_frequency`, `brightness`, `contrast`, `saturation`, `sharpness`, `exposure_time_absolute`, etc.) in a second call so inactive-flag guards never reject manual values.
- Reapply all configured controls **before every capture** to recover cleanly from power cycles or USB resets. If `controls_override_file` (saved via the Web UI) exists, merge it over `[camera.controls]` so live-tuned settings automatically apply to scheduled shots.
- Coordinate exclusive access to `/dev/videoX` via `data/camera.lock` (`fcntl.flock`) and `data/capture.requested` so scheduled captures pause any active Web UI preview stream cleanly without `Device or resource busy` errors.

## Warmup, Capture & Watermark Pipeline
- Run `ffmpeg` capturing from `v4l2` with `-input_format mjpeg -video_size 4000x3000`:
  - Discard initial frames for `warmup_seconds` (`-ss <warmup_seconds>`) so auto-exposure and auto-white-balance adjust to outdoor sunset lighting.
  - Capture 1 frame with `-frames:v 1 -c:v copy -update 1` (zero CPU re-encoding on capture).
- **Timestamp Watermarking:**
  - Applied by default to all **automatic scheduled captures** (and configurable via `--watermark` / `--no-watermark` on `daily-shot capture`, or the Web UI flag for manual captures).
  - Opens the captured frame in Pillow (`ImageOps.exif_transpose`), renders the capture date and time (`watermark.format`, default `%Y-%m-%d %H:%M:%S`) onto an `RGBA` overlay with a semi-transparent dark backdrop for high contrast across bright sunset skies and dark twilight, composites onto `RGB`, and saves at `watermark.jpeg_quality` (`95`).
  - Uses system TrueType fonts (`DejaVuSansMono.ttf` / `DejaVuSans.ttf`) safely compatible with both Pillow 9.4 (Raspberry Pi OS Bookworm) and Pillow 10+.
- Write to a hidden `.tmp` file, verify size > 0 bytes, and atomically rename to `<archive_dir>/YYYY-MM-DD/YYYY-MM-DD_HH-MM-SS.jpg` (one subfolder per day, e.g., `2026-11-23/2026-11-23_18-30-00.jpg`).

## Calibration CLI (`daily-shot calibrate`)
- Print V4L2 device path and current control list (`v4l2-ctl --list-ctrls-menus`).
- Capture labeled full-res test images across a configurable list of `focus_absolute` values (default e.g. `0, 50, 100, 150, 200, 300`) into a calibration folder (`focus_0000.jpg`, ...) so the user can visually confirm infinity focus outdoors.

## Acceptance Criteria
- Unit tests verify `parse_v4l2_devices()`, two-phase control ordering, `ffmpeg` command args, and atomic temp-file cleanup on failure.
- Live hardware test against `/dev/video2` produces a valid `4000x3000` JPEG and applies `focus_automatic_continuous=0` + `focus_absolute`.

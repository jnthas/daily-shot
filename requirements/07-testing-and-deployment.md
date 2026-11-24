# Requirement 07: Testing & Raspberry Pi Zero W v1 Deployment

## Goal
Ensure all logic is verified with automated tests and provide a reproducible, low-friction deployment guide for Raspberry Pi OS on the Pi Zero W v1.

## Automated Test Suite (`tests/`)
- Run via `pytest` with mocked `subprocess.run` and `requests.Session`:
  - `test_config.py`: TOML parsing, `.env` loading, path resolution, defaults.
  - `test_solar.py`: NOAA sunset calculation accuracy across solstices/equinoxes, `sunset_offset_minutes`, and burst slot counts (`before`, `after`, `include_sunset_shot`).
  - `test_camera.py`: `v4l2-ctl --list-devices` parsing, two-phase `--set-ctrl`, `ffmpeg` args, atomic `.tmp` cleanup.
  - `test_image_prep.py`: resizing, EXIF transpose, strict `max_bytes` compliance on noisy/complex synthetic images, and timestamp watermark rendering (with/without background, corner positions, font fallback).
  - `test_backup.py`: `rclone copy` command, retry backoff, sync-gated 14-day local pruning.
  - `test_bluesky.py`: exact-sunset shot selection, post & `aspectRatio` payload, same-day catch-up / next-day skip, DM chat XRPC calls & cooldown/offline queue.
  - `test_scheduler.py`: `tick` state transitions across capture slots, backup, publish, failure alerts, and `boot_id` power-outage recovery + boot DM queuing.
  - `test_web.py`: local-CIDR allowlist enforcement, V4L2 control schema parsing, live control updates, `camera.lock` stream yielding, and manual capture endpoint.

## Live Hardware Smoke Verification (Dev Machine)
All features and tests are developed and verified locally on the dev machine with the IMX362 camera attached before deploying to the Raspberry Pi Zero W v1:
1. `daily-shot camera-info` — verify auto-discovery of `Fic760xUsbCamera` and live V4L2 controls.
2. `daily-shot schedule` — verify sunset and burst slots for configured coordinates.
3. `daily-shot calibrate` — verify focus sweep captures `4000x3000` JPEGs.
4. `daily-shot capture --output /tmp/debug.jpg` — verify full single-frame capture with locked focus and timestamp watermark.
5. `daily-shot web` — verify live MJPEG preview, live V4L2 control sliders, and manual 12MP capture (with/without watermark).
6. `daily-shot publish --dry-run` — verify compression `< 950 KB` and rendered text/alt.

## Raspberry Pi Zero W v1 Deployment Checklist (`README.md` + `systemd/`)
- Install OS packages: `sudo apt install -y python3 python3-pil python3-requests v4l-utils ffmpeg rclone fonts-dejavu-core`
- Configure OS timezone (`America/Sao_Paulo`) and confirm `systemd-timesyncd` NTP sync.
- Install package (`pip install --no-deps -e .` or `PYTHONPATH=src` in `systemd` unit so no PyPI network builds are needed on ARMv6).
- Configure `rclone config` for cloud remote and test `daily-shot sync`.
- Create Bluesky App Password (with **Direct Messages** enabled), populate `.env` and `config.toml`, and run `daily-shot test-alert` + `daily-shot publish --dry-run`.
- Run `daily-shot calibrate` at mounting site and set optimal `focus_absolute` in `config.toml`.
- Enable `systemd` units:
  - `sudo cp systemd/daily-shot-tick.* /etc/systemd/system/`
  - Optional local Web UI service: `sudo cp systemd/daily-shot-web.service /etc/systemd/system/` (or launch on demand via `daily-shot web`)
  - `sudo systemctl daemon-reload && sudo systemctl enable --now daily-shot-tick.timer`
  - Verify with `systemctl status daily-shot-tick.timer` and `daily-shot status`.
- **Power-Outage Hardening (Pi Zero W v1):**
  - Enable `systemd-time-wait-sync.service` and confirm Wi-Fi auto-reconnect (`nmcli` / `wpa_supplicant` autoconnect priority).
  - Enable the Raspberry Pi hardware watchdog (`RuntimeWatchdogSec=15` in `/etc/systemd/system.conf`) so kernel/system hangs auto-reboot.
  - Ensure `fsck.repair=yes` in `/boot/firmware/cmdline.txt` for automatic filesystem repair after dirty power cuts.
- **SSH Access & Remote Terminal Debugging (`README.md`):**
  - Enable OpenSSH (`sudo systemctl enable --now ssh`) + key-based authentication (`~/.ssh/authorized_keys`) + mDNS hostname (`daily-shot.local` via `avahi-daemon`).
  - Document SSH tunneling for the local-only Web UI when bound to `127.0.0.1` (`ssh -L 8080:127.0.0.1:8080 <user>@daily-shot.local`).
  - Include a terminal troubleshooting cheat sheet (`journalctl -u daily-shot-tick -f`, `systemctl list-timers daily-shot*`, `daily-shot status`, `daily-shot camera-info`, `v4l2-ctl --list-devices`, `lsusb`, `timedatectl status`, `df -h`, `rclone lsd`).

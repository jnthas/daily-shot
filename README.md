# `daily-shot`

Automated outdoor sunset photography system built for **Raspberry Pi Zero W v1 (32-bit ARMv6)** and a **12MP IMX362 USB UVC camera** (`Fic760xUsbCamera`).

## Features
- **Solar-Aware Sunset Burst:** Offline pure-Python NOAA sunset calculator (e.g. `-19.9167, -43.9345`, `America/Sao_Paulo`), capturing configurable bursts before/after sunset (default: `6` before + `1` at sunset + `6` after, every `15` min).
- **Locked 12MP UVC Capture + Watermark:** Two-phase `v4l2-ctl` control locking (disables autofocus, sets manual infinity `focus_absolute` and 60 Hz anti-flicker), `ffmpeg` warmup + zero-re-encode MJPEG capture (`4000×3000`), and legible date/time watermark stamping saved as `YYYY-MM-DD/YYYY-MM-DD_HH-MM-SS.jpg`.
- **Offline Autonomy & Resumable Google Drive Sync:** Captures continue uninterrupted during Wi-Fi outages; `rclone copy` syncs daily `YYYY-MM-DD/` folders directly into your configured Google Drive folder (`backup.drive_root_folder_id`) and resumes from where it stopped when connectivity returns. Local 14-day cleanup only runs after a clean sync.
- **Daily Bluesky Bot (`dailyshot.bsky.social`):** Picks the frame closest to exact sunset, optimizes JPEG size (`<= 950 KB`, preserving `aspectRatio`), and posts with templated caption and alt text.
- **Unattended Power-Outage Recovery & DM Alerts:** Detects new Linux boots via `/proc/sys/kernel/random/boot_id`, cleans stale signals/temp files, re-applies camera controls, and queues a Bluesky DM log + failure alerts (retried automatically if offline).
- **Local-Only Web UI:** Live MJPEG preview, real-time sliders/toggles for all 18 IMX362 V4L2 controls, save/reset defaults, and manual 12MP capture (with watermark toggle). Coordinates exclusively with scheduled captures via `camera.lock`.

---

## 1. Local Development & Hardware Testing (Linux Laptop / Dev Machine)

### Prerequisites
```bash
sudo apt update && sudo apt install -y python3 python3-pil python3-requests v4l-utils ffmpeg rclone fonts-dejavu-core
```

### Configure
```bash
cp config.example.toml config.toml
cp .env.example .env
# Edit .env with BLUESKY_HANDLE=dailyshot.bsky.social and BLUESKY_APP_PASSWORD
# Edit config.toml (e.g., set alerts.recipient_handle to your personal Bluesky handle)
```

### Useful Local Commands
```bash
# Run unit tests
uv run --with pytest pytest

# Inspect connected IMX362 camera & V4L2 controls
PYTHONPATH=src python3 -m daily_shot.cli camera-info

# Preview today's sunset & burst schedule
PYTHONPATH=src python3 -m daily_shot.cli schedule

# Sweep focus_absolute values to verify infinity focus outdoors
PYTHONPATH=src python3 -m daily_shot.cli calibrate --values 0,50,100,150,200

# Isolated test capture (does not modify archive sync state)
PYTHONPATH=src python3 -m daily_shot.cli capture --output /tmp/debug.jpg

# Launch local-only Web UI (http://127.0.0.1:8080)
PYTHONPATH=src python3 -m daily_shot.cli web

# Dry-run Bluesky post & Google Drive sync
PYTHONPATH=src python3 -m daily_shot.cli publish --dry-run
PYTHONPATH=src python3 -m daily_shot.cli sync --dry-run
```

---

## 2. Raspberry Pi Zero W v1 (32-bit ARMv6) Deployment

### 2.1 System Packages & Timezone
```bash
sudo apt update && sudo apt install -y \
  python3 python3-pil python3-requests v4l-utils ffmpeg rclone fonts-dejavu-core avahi-daemon

sudo timedatectl set-timezone America/Sao_Paulo
sudo systemctl enable --now systemd-time-wait-sync.service
```

### 2.2 Install Project
No `pip` compilation is needed on ARMv6—everything uses `apt` packages + stdlib:
```bash
git clone <repo-url> /home/pi/daily-shot
cd /home/pi/daily-shot
cp config.example.toml config.toml
cp .env.example .env
chmod 600 .env
```

### 2.3 Google Drive (`rclone`) Setup
Run `rclone config`, create a `drive` remote named `gdrive` (matching `backup.rclone_remote = "gdrive"`), and test:
```bash
PYTHONPATH=src python3 -m daily_shot.cli sync --dry-run
```
Photos will sync into your configured `drive_root_folder_id` inside daily `YYYY-MM-DD/` subfolders.

### 2.4 Bluesky App Password (with DM Permission)
1. In Bluesky **Settings → Privacy and Security → App Passwords**, create a new App Password for `dailyshot.bsky.social` and **enable Direct Messages** permission.
2. Put `BLUESKY_HANDLE` and `BLUESKY_APP_PASSWORD` in `.env`, set `alerts.recipient_handle` in `config.toml`, and test:
```bash
PYTHONPATH=src python3 -m daily_shot.cli test-alert
```

### 2.5 Enable `systemd` Timers & Optional Web UI
Edit `User=` and paths inside `systemd/*.service` if your username is not `pi`, then:
```bash
sudo cp systemd/daily-shot-tick.service systemd/daily-shot-tick.timer /etc/systemd/system/
# Optional: keep local Web UI running on boot
sudo cp systemd/daily-shot-web.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now daily-shot-tick.timer
# Optional: sudo systemctl enable --now daily-shot-web.service
```

### 2.6 Power-Outage & SSH Hardening
- **Hardware Watchdog:** In `/etc/systemd/system.conf`, set `RuntimeWatchdogSec=15` and reboot so kernel hangs auto-reset.
- **Filesystem Auto-Repair:** Ensure `fsck.repair=yes` is present in `/boot/firmware/cmdline.txt`.
- **Wi-Fi Auto-Reconnect:** Verify NetworkManager (`nmcli connection modify <wifi-name> connection.autoconnect yes connection.autoconnect-retries 0`).
- **SSH & Tunneling:**
  ```bash
  sudo systemctl enable --now ssh
  # Forward local-only Web UI safely over SSH:
  ssh -L 8080:127.0.0.1:8080 pi@daily-shot.local
  ```

---

## 3. Terminal Troubleshooting Cheat Sheet
```bash
PYTHONPATH=src python3 -m daily_shot.cli status            # System, slots, sync, failures, disk free
PYTHONPATH=src python3 -m daily_shot.cli status --json     # Machine-readable status
PYTHONPATH=src python3 -m daily_shot.cli -v tick           # Run one tick with DEBUG output
PYTHONPATH=src python3 -m daily_shot.cli camera-info       # Resolved /dev/video* & live V4L2 ctrls
journalctl -u daily-shot-tick -n 100 -f                    # Live scheduler logs
journalctl -u daily-shot-web -n 100 -f                     # Live Web UI logs
systemctl list-timers daily-shot*                          # Timer next/last run
v4l2-ctl --list-devices && lsusb                           # USB camera enumeration
timedatectl status && df -h                                # NTP sync & SD card space
rclone lsd gdrive: --drive-root-folder-id <your-drive-folder-id>
```

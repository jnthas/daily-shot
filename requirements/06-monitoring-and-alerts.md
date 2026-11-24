# Requirement 06: Observability, Health Status & Bluesky DM Alerts

## Goal
Provide clear operational visibility via `journald` and a CLI status check, and automatically notify the owner via **Bluesky Direct Message** when persistent failures occur.

## Logging & Diagnostics
- Use Python's `logging` module writing timestamped structured messages to `stderr` so `systemd` captures everything in `journald` (`journalctl -u daily-shot-tick`). Support a global `-v` / `--verbose` CLI flag for `DEBUG`-level terminal troubleshooting.
- **`daily-shot status` CLI (with `--json` option):**
  - Displays boot info (`boot_id` / `last_boot_at`), NTP sync state, resolved camera device, configured coordinates, today's calculated sunset time, each scheduled burst slot and whether it was captured, `sync_pending`, `last_synced_at`, `last_published_date` + post URI, SD card free space (MB), and consecutive failure counts.
- **Terminal Debug Subcommands (for SSH diagnostics):**
  - `daily-shot camera-info`: prints resolved `/dev/video*` path and live `v4l2-ctl --list-ctrls-menus` + `--list-formats-ext` output.
  - `daily-shot capture --output /tmp/debug.jpg`: runs a one-off debug capture without altering archive state or triggering cloud sync.
  - `daily-shot sync --dry-run` and `daily-shot publish --dry-run`: validate `rclone` connectivity/diff and Bluesky image prep/caption without side effects.

## Failure Tracking (`state.py`)
Track consecutive failures per subsystem in `state.json`:
- `capture`: incremented on camera/capture error; reset to `0` on next successful capture.
- `backup`: incremented on `rclone` error; reset to `0` on next successful sync.
- `bluesky`: incremented on publish failure (excluding normal "no photos found" if configured, or tracked distinctly); reset to `0` on successful post.

## Bluesky DM Alerting (`bluesky.py`)
- When a subsystem's failure count reaches `alerts.alert_after_failures` (default `2`):
  - Check `cooldown_hours` (default `12`) so repeated ticks do not spam DMs.
  - Format a concise alert message with subsystem name, failure count, timestamp, and latest error summary.
- **ATProto Chat XRPC Flow:**
  1. Authenticate via `com.atproto.server.createSession` (reusing `BLUESKY_HANDLE` / `BLUESKY_APP_PASSWORD`).
  2. Resolve `alerts.recipient_handle` to `did` via `com.atproto.identity.resolveHandle` (if not already a `did:` string).
  3. Call `GET /xrpc/chat.bsky.convo.getConvoForMembers?members=<sender_did>&members=<recipient_did>` with header `Atproto-Proxy: did:web:api.bsky.chat#bsky_chat` to obtain `convo.id`.
  4. Call `POST /xrpc/chat.bsky.convo.sendMessage` with header `Atproto-Proxy: did:web:api.bsky.chat#bsky_chat` and `convoId` + `message: {"text": ...}`.
- **Offline Alert Queue:** If sending the DM itself fails (e.g., Pi has no Wi-Fi), store the pending alert in `state.json` and automatically deliver it on the next `tick` once connectivity returns.
- Provide `daily-shot test-alert` CLI subcommand so the user can verify DM permissions and delivery during setup.
- **Prerequisite:** Document clearly that the Bluesky App Password must have **Direct Messages** access enabled in Bluesky account settings.

## Power-Outage & Reboot Auto-Recovery Logging
- **Boot Detection (`boot_id`):** On startup / first `tick` after boot, compare `/proc/sys/kernel/random/boot_id` against `state.json`'s `last_boot_id`.
- **Automatic Recovery Sequence (zero manual intervention):**
  1. Clean up stale `.tmp` files and any orphaned `data/capture.requested` signal left by an unclean power cut.
  2. Immediately re-apply all UVC camera controls (`[camera.controls]` merged with `data/camera_controls.json`) to restore locked infinity focus and anti-flicker after USB power loss.
  3. Wait/check for NTP synchronization (`timedatectl show -p NTPSynchronized`) before evaluating time-sensitive capture slots or recording the boot timestamp.
  4. When `alerts.notify_on_boot = true`, queue a boot-recovery Bluesky DM (`"Recovered after reboot/power cycle at <timestamp> (boot <short_id>). Camera controls reapplied."`) into `pending_alerts` and deliver it as soon as Wi-Fi connects (retrying across `tick` runs if Wi-Fi takes longer to reconnect).
  5. Update `last_boot_id` and `last_boot_at` in `state.json`.

## Acceptance Criteria
- Unit tests verify `Atproto-Proxy` header usage, `resolveHandle` -> `getConvoForMembers` -> `sendMessage` call sequence, alert cooldown suppression, pending-alert retry on network recovery, and `boot_id` change detection + recovery DM queuing.

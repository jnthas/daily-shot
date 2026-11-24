# Requirement 04: Daily Cloud Backup (`rclone`) & Local Retention

## Goal
Archive all full-resolution 12MP photos to cloud storage once daily after the sunset burst completes, while keeping the Pi Zero SD card from filling up without risking unbacked-up data loss.

## Cloud Sync Behavior (`backup.py`)
- **Destination Layout (Google Drive):**
  - Target Google Drive root folder ID configured via `backup.drive_root_folder_id`.
  - Inside that root folder, photos are organized into **one subfolder per day** (`YYYY-MM-DD/`), matching `<archive_dir>/YYYY-MM-DD/YYYY-MM-DD_HH-MM-SS.jpg` (e.g., `2026-11-23/2026-11-23_18-30-00.jpg`).
- **Offline Autonomy:** Capture and local archiving (`YYYY-MM-DD/YYYY-MM-DD_HH-MM-SS.jpg`) have zero network dependency. If Wi-Fi is down for hours or days, the Pi keeps capturing every scheduled sunset burst (and manual Web UI photos) to the SD card.
- **Resumable Incremental Sync Command:**
  `rclone copy <archive_dir> <rclone_remote>:<rclone_path> --drive-root-folder-id <drive_root_folder_id> --transfers 2 --checkers 2 --retries 2 --low-level-retries 5 --contimeout 15s --timeout 60s --create-empty-src-dirs=false`
  - Uses `rclone copy` across the entire `<archive_dir>` tree (not `rclone sync`), so local cleanup never deletes archived cloud photos.
  - Passing `--drive-root-folder-id` (when `drive_root_folder_id` is configured) ensures `gdrive:` uploads directly into the specified Google Drive folder even without setting `root_folder_id` manually in `rclone.conf`.
  - **Resumes from where it stopped:** `rclone copy` skips all already-uploaded files by size/modtime/checksum and uploads only missing or previously interrupted files (multi-day backlogs catch up automatically once Wi-Fi returns).
  - Low `--transfers 2 --checkers 2` limits memory and CPU pressure on Pi Zero W v1.
- **Pending-Sync Flag (`sync_pending` in `state.json`):**
  - Every successful capture (automatic or manual via Web UI) sets `sync_pending = true`.
  - After the sunset burst ends (or on subsequent `tick` runs whenever `sync_pending == true` or `last_synced_date != today`), `daily-shot tick` runs `rclone copy` (respecting a short retry cooldown so offline ticks don't hammer `rclone` every single minute).
  - Only when `rclone copy` exits cleanly with code `0` are `sync_pending = false`, `last_synced_date = today`, and `last_synced_at` updated. If Wi-Fi drops mid-transfer, `sync_pending` stays `true` so the next attempt picks up cleanly from the remaining files.
  - Lock/singleton guard prevents overlapping `rclone` processes if a large backlog takes longer than 1 minute to upload.

## Sync-Gated Local Retention Pruning
- Delete local `.jpg` files older than `local_retention_days` (default `14`; `0` disables pruning) and remove empty `YYYY-MM-DD` directories.
- **Safety Gate:** Local pruning MUST NOT run unless `sync_pending == false` and `last_synced_date` succeeded cleanly. During prolonged Wi-Fi outages, all local photos remain safely on the SD card until cloud sync completes.
- Check remaining SD card free space (`shutil.disk_usage`) and queue a low-disk warning Bluesky DM if free space drops below `storage.min_free_mb` (default `500` MB).

## Acceptance Criteria
- Unit tests verify `rclone copy` command construction, retry behavior on non-zero exit codes, and `BackupError` reporting.
- Unit tests verify `prune_old_shots()` deletes files strictly older than `retention_days`, cleans empty date directories, and skips deletion when sync verification gate is unmet.

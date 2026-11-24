# Requirement 05: Daily Bluesky Publishing & Image Optimization

## Goal
Automatically publish one daily photo (the exact sunset frame) to Bluesky shortly after the sunset burst completes, sized within Bluesky's upload limit with crisp quality, proper aspect ratio, and templated caption/alt text.

## Daily Photo Selection (`bluesky.py`)
- Triggered once per day after the sunset window completes (`last_slot + publish_offset_after_window_minutes`), or manually via `daily-shot publish`.
- Scans `<archive_dir>/YYYY-MM-DD/*.jpg` for today's local date.
- Parses timestamp from `YYYY-MM-DD_HH-MM-SS.jpg` (falling back to file `mtime`) and selects the frame with the **smallest absolute time difference to today's calculated exact sunset time** (`sunset_dt`).

## Image Optimization (`image_prep.py`)
- Open the selected 12MP (4000×3000) JPEG with Pillow:
  - Apply `ImageOps.exif_transpose` and convert to `RGB`.
  - Downscale proportionally (`Image.Resampling.LANCZOS`) so `max(width, height) <= max_dimension` (default `2048`).
  - Re-encode JPEG (`optimize=True`) stepping quality down from `initial_quality` (`92`) to `min_quality` (`70`) in steps of `5` until `len(bytes) <= max_bytes`.
  - If still over `max_bytes`, iteratively scale dimensions by `0.85` and retry until under `max_bytes`.
- Default `max_bytes = 950_000` (safe margin under Bluesky's standard `1,000,000`-byte `app.bsky.embed.images` blob limit, configurable up to `1,950,000` / 2 MB if supported by PDS).
- Return `(jpeg_bytes, width, height, quality)`.

## ATProto Publishing Flow (`BlueskyClient`)
1. Authenticate via `POST /xrpc/com.atproto.server.createSession` using `BLUESKY_HANDLE` (default `dailyshot.bsky.social`) and `BLUESKY_APP_PASSWORD`.
2. Upload JPEG via `POST /xrpc/com.atproto.repo.uploadBlob` (`Content-Type: image/jpeg`).
3. Create post via `POST /xrpc/com.atproto.repo.createRecord` (`app.bsky.feed.post`) including:
   - Rendered `post_template` text
   - `app.bsky.embed.images` with uploaded `blob`, rendered `alt_template`, and `aspectRatio: {"width": w, "height": h}`
   - UTC ISO-8601 `createdAt`
- Retry transient network errors and HTTP `429` / `5xx` responses with backoff.
- Template variables: `{date}`, `{time}`, `{datetime}`, `{sunset_time}`, `{location}`.

## Catch-Up & Deduplication Policy
- Record `last_published_date`, `last_published_photo`, and `last_post_uri` in `state.json` on success (never double-post on the same day unless `--force`).
- If offline during the initial publish attempt, subsequent `tick` runs retry for the remainder of the same local calendar day.
- Once the local date advances past midnight, missed days are skipped.
- Support `--dry-run` to test photo selection, compression size, and rendered text/alt without calling Bluesky.

## Acceptance Criteria
- Unit tests verify exact-sunset frame selection, JPEG compression strictly `<= max_bytes`, `aspectRatio` inclusion in `createRecord`, and same-day retry / next-day skip rules.

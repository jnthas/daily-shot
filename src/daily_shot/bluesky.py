"""Bluesky ATProto publishing, sunset photo selector, and Bluesky DM alerts."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .camera import parse_shot_timestamp
from .config import BlueskyConfig
from .image_prep import prepare_for_bluesky
from .state import load_state, save_state

logger = logging.getLogger(__name__)

_CHAT_PROXY_HEADER = "did:web:api.bsky.chat#bsky_chat"


class BlueskyError(RuntimeError):
    """Raised when Bluesky authentication, upload, post, or DM fails."""


def select_daily_photo(
    archive_dir: Path,
    target_date: date,
    sunset_dt: datetime,
) -> Path | None:
    """Pick the photo in YYYY-MM-DD/ closest in time to exact sunset_dt."""
    day_dir = archive_dir / target_date.strftime("%Y-%m-%d")
    if not day_dir.is_dir():
        return None

    candidates = sorted(p for p in day_dir.glob("*.jpg") if not p.name.startswith("."))
    if not candidates:
        return None

    sunset_naive = sunset_dt.replace(tzinfo=None)

    def _distance_seconds(path: Path) -> float:
        ts = parse_shot_timestamp(path)
        if ts is None:
            ts = datetime.fromtimestamp(path.stat().st_mtime)
        return abs((ts.replace(tzinfo=None) - sunset_naive).total_seconds())

    return min(candidates, key=_distance_seconds)


def format_templates(
    config: BlueskyConfig,
    photo_dt: datetime,
    sunset_dt: datetime,
) -> tuple[str, str]:
    """Render post caption and alt text templates."""
    ctx = {
        "date": photo_dt.strftime("%Y-%m-%d"),
        "time": photo_dt.strftime("%H:%M"),
        "datetime": photo_dt.strftime("%Y-%m-%d %H:%M"),
        "sunset_time": sunset_dt.strftime("%H:%M"),
        "location": config.location_name,
    }
    return config.post_template.format(**ctx), config.alt_template.format(**ctx)


class BlueskyClient:
    """Minimal ATProto XRPC client for posts and chat.bsky.convo DMs."""

    def __init__(
        self,
        pds_url: str = "https://bsky.social",
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.pds_url = pds_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.access_jwt: str | None = None
        self.did: str | None = None

    def _request_with_retry(
        self,
        method: str,
        endpoint: str,
        *,
        retries: int = 3,
        **kwargs: Any,
    ) -> dict[str, Any]:
        url = f"{self.pds_url}/xrpc/{endpoint}"
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                    time.sleep(2 * attempt)
                    continue
                if resp.status_code >= 400:
                    detail = getattr(resp, "text", "")[:300]
                    raise BlueskyError(
                        f"Bluesky API request {endpoint} returned HTTP {resp.status_code}: {detail}"
                    )
                return resp.json()
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < retries:
                    time.sleep(2 * attempt)
                    continue
                raise BlueskyError(f"Bluesky API request {endpoint} failed: {exc}") from exc
        raise BlueskyError(f"Bluesky API request {endpoint} failed: {last_exc}")

    def create_session(self, handle: str, app_password: str) -> None:
        if not handle or not app_password:
            raise BlueskyError("BLUESKY_HANDLE and BLUESKY_APP_PASSWORD must be configured.")
        data = self._request_with_retry(
            "POST",
            "com.atproto.server.createSession",
            json={"identifier": handle, "password": app_password},
        )
        self.access_jwt = data["accessJwt"]
        self.did = data["did"]
        for svc in data.get("didDoc", {}).get("service", []):
            if svc.get("type") == "AtprotoPersonalDataServer" and svc.get("serviceEndpoint"):
                self.pds_url = str(svc["serviceEndpoint"]).rstrip("/")
                break

    def resolve_handle(self, handle_or_did: str) -> str:
        if not handle_or_did:
            raise BlueskyError("Recipient handle or DID must not be empty.")
        if handle_or_did.startswith("did:"):
            return handle_or_did
        data = self._request_with_retry(
            "GET",
            "com.atproto.identity.resolveHandle",
            params={"handle": handle_or_did},
        )
        return data["did"]

    def upload_blob(self, jpeg_bytes: bytes) -> dict[str, Any]:
        if not self.access_jwt:
            raise BlueskyError("Not authenticated; call create_session first.")
        data = self._request_with_retry(
            "POST",
            "com.atproto.repo.uploadBlob",
            headers={
                "Authorization": f"Bearer {self.access_jwt}",
                "Content-Type": "image/jpeg",
            },
            data=jpeg_bytes,
        )
        return data["blob"]

    def create_image_post(
        self,
        *,
        text: str,
        blob: dict[str, Any],
        alt_text: str,
        width: int,
        height: int,
        created_at: datetime | None = None,
    ) -> dict[str, Any]:
        if not self.access_jwt or not self.did:
            raise BlueskyError("Not authenticated; call create_session first.")

        ts = (created_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        record = {
            "$type": "app.bsky.feed.post",
            "text": text,
            "createdAt": ts.isoformat().replace("+00:00", "Z"),
            "embed": {
                "$type": "app.bsky.embed.images",
                "images": [
                    {
                        "image": blob,
                        "alt": alt_text,
                        "aspectRatio": {"width": width, "height": height},
                    }
                ],
            },
        }
        return self._request_with_retry(
            "POST",
            "com.atproto.repo.createRecord",
            headers={"Authorization": f"Bearer {self.access_jwt}"},
            json={
                "repo": self.did,
                "collection": "app.bsky.feed.post",
                "record": record,
            },
        )

    def send_dm(self, recipient_handle_or_did: str, text: str) -> dict[str, Any]:
        """Send a Bluesky DM via chat.bsky.convo using Atproto-Proxy header."""
        if not self.access_jwt or not self.did:
            raise BlueskyError("Not authenticated; call create_session first.")

        recipient_did = self.resolve_handle(recipient_handle_or_did)
        headers = {
            "Authorization": f"Bearer {self.access_jwt}",
            "Atproto-Proxy": _CHAT_PROXY_HEADER,
        }
        convo_data = self._request_with_retry(
            "GET",
            "chat.bsky.convo.getConvoForMembers",
            headers=headers,
            params=[("members", self.did), ("members", recipient_did)],
        )
        convo_id = convo_data["convo"]["id"]
        return self._request_with_retry(
            "POST",
            "chat.bsky.convo.sendMessage",
            headers=headers,
            json={"convoId": convo_id, "message": {"text": text}},
        )


def publish_daily_photo(
    config: BlueskyConfig,
    archive_dir: Path,
    state_file: Path,
    sunset_dt: datetime,
    *,
    target_date: date | None = None,
    force: bool = False,
    dry_run: bool = False,
    state: dict[str, Any] | None = None,
    client: BlueskyClient | None = None,
) -> str | None:
    """Select the sunset frame for target_date, optimize it, and post to Bluesky."""
    if not config.enabled:
        logger.info("Bluesky publishing is disabled; skipping.")
        return None

    day = target_date or sunset_dt.date()
    day_str = day.isoformat()
    owned_state = state is None
    st = state if state is not None else load_state(state_file)

    if not force and st.get("last_published_date") == day_str:
        logger.info("Already published for %s (%s); skipping.", day_str, st.get("last_post_uri"))
        return None

    photo_path = select_daily_photo(archive_dir, day, sunset_dt)
    if photo_path is None:
        logger.warning("No photos found for %s in %s; skipping publish.", day_str, archive_dir)
        return None

    photo_dt = parse_shot_timestamp(photo_path) or datetime.fromtimestamp(photo_path.stat().st_mtime)
    text, alt_text = format_templates(config, photo_dt, sunset_dt)
    prepared = prepare_for_bluesky(
        photo_path,
        max_bytes=config.max_bytes,
        max_dimension=config.max_dimension,
        initial_quality=config.initial_quality,
        min_quality=config.min_quality,
    )

    logger.info(
        "Prepared %s -> %dx%d JPEG (%d bytes, q=%d)",
        photo_path.name,
        prepared.width,
        prepared.height,
        len(prepared.data),
        prepared.quality,
    )
    if dry_run:
        logger.info("Dry run: would post %r with alt %r", text, alt_text)
        return "dry-run"

    bsky = client or BlueskyClient(pds_url=config.pds_url)
    bsky.create_session(config.handle, config.app_password)
    blob = bsky.upload_blob(prepared.data)
    res = bsky.create_image_post(
        text=text,
        blob=blob,
        alt_text=alt_text,
        width=prepared.width,
        height=prepared.height,
    )
    uri = res.get("uri", "")
    st["last_published_date"] = day_str
    st["last_published_photo"] = str(photo_path)
    st["last_post_uri"] = uri
    if owned_state:
        save_state(state_file, st)
    logger.info("Published daily photo to Bluesky: %s", uri)
    return uri

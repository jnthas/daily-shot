from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from PIL import Image

from daily_shot.bluesky import BlueskyClient, publish_daily_photo, select_daily_photo
from daily_shot.config import BlueskyConfig
from daily_shot.state import load_state


class _FakeResp:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> _FakeResp:
        self.calls.append({"method": method, "url": url, **kwargs})
        if url.endswith("createSession"):
            return _FakeResp(
                {
                    "accessJwt": "jwt-123",
                    "did": "did:plc:bot",
                    "didDoc": {
                        "service": [
                            {
                                "id": "#atproto_pds",
                                "type": "AtprotoPersonalDataServer",
                                "serviceEndpoint": "https://pds.example.bsky.network",
                            }
                        ]
                    },
                }
            )
        if url.endswith("resolveHandle"):
            return _FakeResp({"did": "did:plc:owner"})
        if url.endswith("uploadBlob"):
            return _FakeResp({"blob": {"$type": "blob", "ref": {"$link": "bafy123"}}})
        if url.endswith("createRecord"):
            return _FakeResp({"uri": "at://did:plc:bot/app.bsky.feed.post/abc"})
        if url.endswith("getConvoForMembers"):
            return _FakeResp({"convo": {"id": "convo-42"}})
        if url.endswith("sendMessage"):
            return _FakeResp({"id": "msg-99"})
        raise AssertionError(f"Unexpected URL {url}")


def test_select_daily_photo_picks_closest_to_sunset(tmp_path: Path) -> None:
    day_dir = tmp_path / "2026-11-23"
    day_dir.mkdir()
    for name in ("2026-11-23_18-00-00.jpg", "2026-11-23_18-16-00.jpg", "2026-11-23_18-30-00.jpg"):
        Image.new("RGB", (640, 480), (100, 120, 140)).save(day_dir / name)

    sunset_dt = datetime(2026, 11, 23, 18, 17, 0)
    picked = select_daily_photo(tmp_path, date(2026, 11, 23), sunset_dt)
    assert picked is not None
    assert picked.name == "2026-11-23_18-16-00.jpg"


def test_publish_daily_photo_and_dedup(tmp_path: Path) -> None:
    archive = tmp_path / "shots"
    day_dir = archive / "2026-11-23"
    day_dir.mkdir(parents=True)
    Image.new("RGB", (800, 600), (200, 100, 50)).save(day_dir / "2026-11-23_18-17-00.jpg")

    state_file = tmp_path / "state.json"
    cfg = BlueskyConfig(
        handle="dailyshot.bsky.social",
        app_password="app-password",
        location_name="Belo Horizonte",
    )
    fake_sess = _FakeSession()
    client = BlueskyClient(session=fake_sess)
    sunset_dt = datetime(2026, 11, 23, 18, 17, 0)

    uri = publish_daily_photo(
        cfg,
        archive,
        state_file,
        sunset_dt,
        target_date=date(2026, 11, 23),
        client=client,
    )
    assert uri == "at://did:plc:bot/app.bsky.feed.post/abc"
    create_call = [c for c in fake_sess.calls if c["url"].endswith("createRecord")][0]
    embed_img = create_call["json"]["record"]["embed"]["images"][0]
    assert embed_img["aspectRatio"] == {"width": 800, "height": 600}
    assert "Belo Horizonte" in embed_img["alt"]

    # Calling again same day skips without duplicate post
    assert (
        publish_daily_photo(
            cfg, archive, state_file, sunset_dt, target_date=date(2026, 11, 23), client=client
        )
        is None
    )
    assert load_state(state_file)["last_published_date"] == "2026-11-23"


def test_send_dm_uses_chat_proxy_header() -> None:
    fake_sess = _FakeSession()
    client = BlueskyClient(session=fake_sess)
    client.create_session("dailyshot.bsky.social", "pw")
    res = client.send_dm("owner.bsky.social", "System recovered")
    assert res["id"] == "msg-99"

    dm_calls = [c for c in fake_sess.calls if "chat.bsky.convo" in c["url"]]
    assert len(dm_calls) == 2
    for c in dm_calls:
        assert c["url"].startswith("https://pds.example.bsky.network/xrpc/")
        assert c["headers"]["Atproto-Proxy"] == "did:web:api.bsky.chat#bsky_chat"

from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path

from PIL import Image

from daily_shot.config import WatermarkConfig
from daily_shot.image_prep import apply_timestamp_watermark, prepare_for_bluesky


def _make_noisy_image(path: Path, width: int = 4000, height: int = 3000) -> None:
    rng = random.Random(42)
    # Build noisy RGB blocks so JPEG isn't trivially 20 KB
    small = Image.frombytes(
        "RGB",
        (1000, 750),
        bytes(rng.getrandbits(8) for _ in range(1000 * 750 * 3)),
    )
    img = small.resize((width, height), Image.Resampling.NEAREST)
    img.save(path, format="JPEG", quality=92)


def test_prepare_for_bluesky_respects_max_bytes_and_aspect(tmp_path: Path) -> None:
    src = tmp_path / "raw_12mp.jpg"
    _make_noisy_image(src, 4000, 3000)

    prepared = prepare_for_bluesky(
        src,
        max_bytes=400_000,
        max_dimension=2048,
        initial_quality=92,
        min_quality=70,
    )
    assert len(prepared.data) <= 400_000
    assert prepared.width <= 2048
    assert prepared.height <= 2048
    assert abs((prepared.width / prepared.height) - (4000 / 3000)) < 0.02


def test_apply_timestamp_watermark_preserves_dimensions_and_modifies_pixels(tmp_path: Path) -> None:
    src = tmp_path / "photo.jpg"
    Image.new("RGB", (1280, 720), color=(240, 240, 240)).save(src, format="JPEG", quality=95)
    before_bytes = src.read_bytes()

    cfg = WatermarkConfig(
        enabled=True,
        format="%Y-%m-%d %H:%M:%S",
        position="bottom-right",
        show_background=True,
        jpeg_quality=95,
    )
    apply_timestamp_watermark(src, datetime(2026, 11, 23, 18, 17, 45), cfg)

    with Image.open(src) as out:
        assert out.size == (1280, 720)
        # Bottom-right corner region must contain the darkened backdrop pill
        min_luma = min(
            sum(out.getpixel((x, y)))
            for x in range(1050, 1270, 5)
            for y in range(660, 715, 5)
        )
        assert min_luma < 200 * 3
    assert src.read_bytes() != before_bytes

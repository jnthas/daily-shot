"""Image resizing, JPEG byte-budget optimization, and timestamp watermarking."""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .config import WatermarkConfig

logger = logging.getLogger(__name__)

_SYSTEM_FONT_CANDIDATES: tuple[str, ...] = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
)


@dataclass(slots=True)
class PreparedImage:
    data: bytes
    width: int
    height: int
    quality: int


def _encode_jpeg(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _load_font(font_path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates: list[str] = []
    if font_path and font_path.lower() != "auto":
        candidates.append(font_path)
    candidates.extend(_SYSTEM_FONT_CANDIDATES)

    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue

    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        # Pillow < 10.1 (e.g. Raspberry Pi OS Bookworm 9.4) has no `size` kwarg on load_default()
        return ImageFont.load_default()


def apply_timestamp_watermark(
    image_path: Path,
    timestamp: datetime,
    cfg: WatermarkConfig,
) -> None:
    """Stamp a legible date/time watermark onto image_path atomically."""
    label = timestamp.strftime(cfg.format)

    with Image.open(image_path) as raw_img:
        base = ImageOps.exif_transpose(raw_img).convert("RGBA")

    font_size = max(16, int(round(base.height * (cfg.font_size_percent / 100.0))))
    font = _load_font(cfg.font_path, font_size)

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    bbox = draw.textbbox((0, 0), label, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad_x = max(10, int(round(font_size * 0.45)))
    pad_y = max(6, int(round(font_size * 0.30)))
    margin = max(16, int(round(base.height * 0.015)))

    box_w = text_w + 2 * pad_x
    box_h = text_h + 2 * pad_y

    pos = cfg.position.lower()
    if "left" in pos:
        box_x = margin
    else:
        box_x = max(margin, base.width - box_w - margin)

    if "top" in pos:
        box_y = margin
    else:
        box_y = max(margin, base.height - box_h - margin)

    if cfg.show_background:
        radius = max(4, int(round(font_size * 0.2)))
        draw.rounded_rectangle(
            [box_x, box_y, box_x + box_w, box_y + box_h],
            radius=radius,
            fill=(0, 0, 0, 150),
        )
    else:
        # Subtle dark drop shadow for legibility without a box
        shadow_offset = max(2, int(round(font_size * 0.06)))
        draw.text(
            (box_x + pad_x - bbox[0] + shadow_offset, box_y + pad_y - bbox[1] + shadow_offset),
            label,
            font=font,
            fill=(0, 0, 0, 200),
        )

    draw.text(
        (box_x + pad_x - bbox[0], box_y + pad_y - bbox[1]),
        label,
        font=font,
        fill=(255, 255, 255, 242),
    )

    composited = Image.alpha_composite(base, overlay).convert("RGB")
    tmp_path = image_path.with_name(f".{image_path.name}.wm.tmp")
    try:
        composited.save(tmp_path, format="JPEG", quality=cfg.jpeg_quality, optimize=True)
        tmp_path.replace(image_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def prepare_for_bluesky(
    source_path: Path,
    max_bytes: int = 950_000,
    max_dimension: int = 2048,
    initial_quality: int = 92,
    min_quality: int = 70,
) -> PreparedImage:
    """Resize and re-encode a photo as JPEG so it fits within max_bytes."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if min_quality > initial_quality:
        raise ValueError("min_quality cannot exceed initial_quality")

    with Image.open(source_path) as raw_img:
        img = ImageOps.exif_transpose(raw_img)
        if img.mode != "RGB":
            img = img.convert("RGB")

        w, h = img.size
        if max(w, h) > max_dimension:
            scale = max_dimension / float(max(w, h))
            img = img.resize(
                (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
                Image.Resampling.LANCZOS,
            )

        current = img
        last_len = 0
        for _ in range(8):
            for quality in range(initial_quality, min_quality - 1, -5):
                data = _encode_jpeg(current, quality)
                last_len = len(data)
                if last_len <= max_bytes:
                    return PreparedImage(
                        data=data,
                        width=current.width,
                        height=current.height,
                        quality=quality,
                    )

            next_w = max(1, int(round(current.width * 0.85)))
            next_h = max(1, int(round(current.height * 0.85)))
            if next_w == current.width and next_h == current.height:
                break
            current = current.resize((next_w, next_h), Image.Resampling.LANCZOS)

    raise ValueError(
        f"Unable to compress {source_path} under {max_bytes} bytes "
        f"(smallest attempt was {last_len} bytes at {current.width}x{current.height})"
    )

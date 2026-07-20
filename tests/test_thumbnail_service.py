from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw

from openmediadl.core.thumbnail_service import (
    ThumbnailDownloadError,
    ThumbnailService,
    transform_thumbnail,
)


def test_center_crops_to_a_square_with_high_quality_resize(tmp_path: Path) -> None:
    source = tmp_path / "wide.png"
    destination = tmp_path / "cover.jpg"
    image = Image.new("RGB", (1_200, 600), "red")
    draw = ImageDraw.Draw(image)
    draw.rectangle((300, 0, 899, 599), fill="green")
    draw.rectangle((900, 0, 1_199, 599), fill="blue")
    image.save(source)

    result = transform_thumbnail(source, destination, crop_square=True)

    assert (result.width, result.height) == (600, 600)
    with Image.open(destination) as transformed:
        assert transformed.format == "JPEG"
        red, green, blue = transformed.getpixel((300, 300))
        assert green > red * 2
        assert green > blue * 2


def test_preserves_aspect_ratio_and_caps_oversized_images(tmp_path: Path) -> None:
    source = tmp_path / "huge.webp"
    destination = tmp_path / "bounded.jpg"
    Image.new("RGB", (2_800, 1_400), "purple").save(source)

    result = transform_thumbnail(source, destination)

    assert (result.width, result.height) == (1_400, 700)
    with Image.open(destination) as transformed:
        assert transformed.size == (1_400, 700)


def test_transparency_is_flattened_onto_white_for_jpeg(tmp_path: Path) -> None:
    source = tmp_path / "transparent.png"
    destination = tmp_path / "opaque.jpg"
    Image.new("RGBA", (100, 100), (255, 0, 0, 0)).save(source)

    transform_thumbnail(source, destination, min_size=100, max_size=100)

    with Image.open(destination) as transformed:
        assert transformed.mode == "RGB"
        pixel = transformed.getpixel((50, 50))
        assert all(channel >= 250 for channel in pixel)


class _Response(io.BytesIO):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (800, 400), "orange").save(output, format="PNG")
    return output.getvalue()


def test_downloads_bounded_thumbnail_and_reuses_cached_variant(tmp_path: Path) -> None:
    service = ThumbnailService(tmp_path / "cache")
    data = _png_bytes()

    with patch(
        "openmediadl.core.thumbnail_service.urlopen",
        return_value=_Response(data),
    ) as opener:
        first = service.download(
            "https://example.test/thumb.png",
            "video-1",
            min_size=200,
            max_size=640,
        )
        second = service.download(
            "https://example.test/thumb.png",
            "video-1",
            min_size=200,
            max_size=640,
        )

    assert opener.call_count == 1
    assert first == second
    assert (first.width, first.height) == (640, 320)


def test_download_rejects_oversized_response_before_processing(tmp_path: Path) -> None:
    service = ThumbnailService(tmp_path / "cache")
    data = _png_bytes()

    with (
        patch(
            "openmediadl.core.thumbnail_service.urlopen",
            return_value=_Response(data),
        ),
        pytest.raises(ThumbnailDownloadError, match="download-size limit"),
    ):
        service.download(
            "https://example.test/thumb.png",
            "video-1",
            max_download_bytes=4,
        )

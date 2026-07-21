"""Bounded, high-quality JPEG thumbnail processing and disk caching."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PIL import Image, ImageOps, UnidentifiedImageError

PathLike = str | os.PathLike[str]


class ThumbnailProcessingError(RuntimeError):
    """Raised when a thumbnail cannot be decoded or transformed."""


class ThumbnailDownloadError(ThumbnailProcessingError):
    """Raised when a remote thumbnail cannot be downloaded safely."""


@dataclass(frozen=True, slots=True)
class ThumbnailResult:
    path: Path
    width: int
    height: int


def _validate_sizes(min_size: int, max_size: int) -> None:
    if min_size < 1 or max_size < min_size:
        raise ValueError("Require 1 <= min_size <= max_size")


def _target_size(
    width: int,
    height: int,
    *,
    crop_square: bool,
    min_size: int,
    max_size: int,
) -> tuple[int, int]:
    if crop_square:
        side = min(max(min(width, height), min_size), max_size)
        return side, side

    longest = max(width, height)
    target_longest = min(max(longest, min_size), max_size)
    scale = target_longest / longest
    return max(1, round(width * scale)), max(1, round(height * scale))


def _rgb_image(image: Image.Image) -> Image.Image:
    if image.mode == "RGB":
        return image
    if "A" in image.getbands() or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        return Image.alpha_composite(background, rgba).convert("RGB")
    return image.convert("RGB")


def transform_thumbnail(
    source_path: PathLike,
    destination_path: PathLike,
    *,
    crop_square: bool = False,
    min_size: int = 600,
    max_size: int = 1_400,
    quality: int = 90,
) -> ThumbnailResult:
    """Convert an image to an oriented, bounded JPEG using an atomic replace."""

    _validate_sizes(min_size, max_size)
    if not 1 <= quality <= 100:
        raise ValueError("quality must be between 1 and 100")

    source = Path(source_path)
    destination = Path(destination_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)

    temporary: Path | None = None
    try:
        with Image.open(source) as opened:
            oriented = ImageOps.exif_transpose(opened)
            size = _target_size(
                oriented.width,
                oriented.height,
                crop_square=crop_square,
                min_size=min_size,
                max_size=max_size,
            )
            if crop_square:
                transformed = ImageOps.fit(
                    oriented,
                    size,
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )
            elif oriented.size != size:
                transformed = oriented.resize(size, resample=Image.Resampling.LANCZOS)
            else:
                transformed = oriented.copy()
            rgb = _rgb_image(transformed)

            with tempfile.NamedTemporaryFile(
                dir=destination.parent,
                prefix=f".{destination.stem}-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
            rgb.save(temporary, format="JPEG", quality=quality, optimize=True, progressive=True)
            output_size = rgb.size

        os.replace(temporary, destination)
        temporary = None
        return ThumbnailResult(path=destination, width=output_size[0], height=output_size[1])
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise ThumbnailProcessingError(f"Could not process thumbnail {source}: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepare_cover_image(
    source_path: PathLike,
    destination_path: PathLike,
    *,
    crop_square: bool = False,
    min_size: int = 600,
    max_size: int = 1_400,
) -> Path:
    """Return the JPEG path after applying cover-art defaults."""

    return transform_thumbnail(
        source_path,
        destination_path,
        crop_square=crop_square,
        min_size=min_size,
        max_size=max_size,
    ).path


class ThumbnailService:
    """Manage deterministic, disk-backed processed thumbnail files."""

    def __init__(self, cache_directory: PathLike) -> None:
        self.cache_directory = Path(cache_directory)

    def cache_path(
        self,
        cache_key: str,
        *,
        crop_square: bool = False,
        min_size: int = 600,
        max_size: int = 1_400,
    ) -> Path:
        _validate_sizes(min_size, max_size)
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        shape = "square" if crop_square else "fit"
        return self.cache_directory / f"{digest}-{shape}-{min_size}-{max_size}.jpg"

    def prepare(
        self,
        source_path: PathLike,
        cache_key: str,
        *,
        crop_square: bool = False,
        min_size: int = 600,
        max_size: int = 1_400,
    ) -> ThumbnailResult:
        destination = self.cache_path(
            cache_key,
            crop_square=crop_square,
            min_size=min_size,
            max_size=max_size,
        )
        return transform_thumbnail(
            source_path,
            destination,
            crop_square=crop_square,
            min_size=min_size,
            max_size=max_size,
        )

    def download(
        self,
        url: str,
        cache_key: str,
        *,
        crop_square: bool = False,
        min_size: int = 600,
        max_size: int = 1_400,
        timeout: float = 20.0,
        max_download_bytes: int = 25 * 1024 * 1024,
        refresh: bool = False,
    ) -> ThumbnailResult:
        """Download and process a bounded HTTP(S) image into the disk cache."""

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Thumbnail URL must be an absolute HTTP(S) URL")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_download_bytes < 1:
            raise ValueError("max_download_bytes must be positive")

        destination = self.cache_path(
            cache_key,
            crop_square=crop_square,
            min_size=min_size,
            max_size=max_size,
        )
        if destination.is_file() and not refresh:
            try:
                with Image.open(destination) as cached:
                    cached.load()
                    return ThumbnailResult(destination, cached.width, cached.height)
            except (OSError, UnidentifiedImageError):
                destination.unlink(missing_ok=True)

        self.cache_directory.mkdir(parents=True, exist_ok=True)
        raw_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.cache_directory,
                prefix=".thumbnail-download-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                raw_path = Path(handle.name)
                request = Request(
                    url,
                    headers={
                        "Accept": "image/*",
                        "User-Agent": "YT-DW/0.1 (local desktop application)",
                    },
                )
                with urlopen(request, timeout=timeout) as response:  # noqa: S310
                    content_length = response.headers.get("Content-Length")
                    if content_length is not None and int(content_length) > max_download_bytes:
                        raise ThumbnailDownloadError("Thumbnail exceeds the download-size limit")

                    downloaded = 0
                    while chunk := response.read(
                        min(64 * 1024, max_download_bytes + 1 - downloaded)
                    ):
                        downloaded += len(chunk)
                        if downloaded > max_download_bytes:
                            raise ThumbnailDownloadError(
                                "Thumbnail exceeds the download-size limit"
                            )
                        handle.write(chunk)

            return transform_thumbnail(
                raw_path,
                destination,
                crop_square=crop_square,
                min_size=min_size,
                max_size=max_size,
            )
        except ThumbnailProcessingError:
            raise
        except (HTTPError, URLError, OSError, ValueError) as exc:
            raise ThumbnailDownloadError(f"Could not download thumbnail {url}: {exc}") from exc
        finally:
            if raw_path is not None:
                raw_path.unlink(missing_ok=True)

    def remove(self, cache_key: str) -> None:
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        if not self.cache_directory.is_dir():
            return
        for cached_path in self.cache_directory.glob(f"{digest}-*.jpg"):
            cached_path.unlink(missing_ok=True)

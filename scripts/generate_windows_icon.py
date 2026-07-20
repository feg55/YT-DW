"""Generate the multi-resolution Windows icon from the approved PNG source."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PNG_PATH = PROJECT_ROOT / "src" / "openmediadl" / "resources" / "icons" / "openmediadl.png"
ICO_PATH = PNG_PATH.with_suffix(".ico")
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def main() -> None:
    with Image.open(PNG_PATH) as source:
        icon = source.convert("RGBA").resize((1024, 1024), Image.Resampling.LANCZOS)
        icon.save(PNG_PATH, format="PNG", optimize=True)
        icon.save(ICO_PATH, format="ICO", sizes=[(size, size) for size in ICON_SIZES])


if __name__ == "__main__":
    main()

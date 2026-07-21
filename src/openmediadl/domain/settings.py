"""Application settings with conservative, typed defaults."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from enum import StrEnum
from typing import Any, Self


class VideoQuality(StrEnum):
    BEST = "best"
    UHD_2160 = "2160p"
    QHD_1440 = "1440p"
    FULL_HD_1080 = "1080p"
    HD_720 = "720p"
    SD_480 = "480p"


class CookieBrowser(StrEnum):
    SYSTEM = "system"
    DISABLED = "none"
    CHROME = "chrome"
    CHROMIUM = "chromium"
    EDGE = "edge"
    FIREFOX = "firefox"
    BRAVE = "brave"
    OPERA = "opera"
    SAFARI = "safari"
    VIVALDI = "vivaldi"
    WHALE = "whale"


class ThemePreference(StrEnum):
    DARK = "dark"
    ORIGINAL = "original"
    LIGHT = "light"
    SYSTEM = "system"


class LanguagePreference(StrEnum):
    SYSTEM = "system"
    RUSSIAN = "ru"
    ENGLISH = "en"


def _known_values(cls: Any, values: dict[str, Any]) -> dict[str, Any]:
    names = {item.name for item in fields(cls)}
    return {key: value for key, value in values.items() if key in names}


@dataclass(slots=True)
class MetadataSettings:
    """Rules used when automatically deriving editable metadata."""

    use_channel_name_as_artist: bool = True
    remove_channel_name_from_title: bool = True
    remove_labels: bool = True
    use_playlist_title_as_album: bool = False
    use_playlist_index_as_track_number: bool = True
    use_cleaned_title_as_filename: bool = True
    embed_thumbnail_as_cover: bool = True
    crop_cover_to_square: bool = True
    save_cover_as_separate_jpeg: bool = False
    store_original_url_in_comment: bool = True
    store_upload_year: bool = True
    use_channel_name_as_album_artist: bool = True

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> Self:
        return cls(**_known_values(cls, values))

    # Small compatibility aliases matching shorter labels used by some callers.
    @property
    def use_channel_as_artist(self) -> bool:
        return self.use_channel_name_as_artist

    @use_channel_as_artist.setter
    def use_channel_as_artist(self, value: bool) -> None:
        self.use_channel_name_as_artist = value

    @property
    def remove_channel_from_title(self) -> bool:
        return self.remove_channel_name_from_title

    @remove_channel_from_title.setter
    def remove_channel_from_title(self, value: bool) -> None:
        self.remove_channel_name_from_title = value


@dataclass(slots=True)
class AppearanceSettings:
    """Persistent interface preferences."""

    theme: ThemePreference = ThemePreference.DARK
    language: LanguagePreference = LanguagePreference.SYSTEM
    remember_last_tab: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.theme, str):
            try:
                self.theme = ThemePreference(self.theme)
            except ValueError:
                self.theme = ThemePreference.DARK
        if isinstance(self.language, str):
            try:
                self.language = LanguagePreference(self.language)
            except ValueError:
                self.language = LanguagePreference.SYSTEM

    def to_dict(self) -> dict[str, Any]:
        return {
            "theme": self.theme.value,
            "language": self.language.value,
            "remember_last_tab": self.remember_last_tab,
        }

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> Self:
        return cls(**_known_values(cls, values))


@dataclass(slots=True)
class DownloadSettings:
    """Queue, network, and output behavior."""

    destination_directory: str = ""
    quality: VideoQuality = VideoQuality.BEST
    max_concurrent_downloads: int = 1
    retry_count: int = 5
    fragment_retry_count: int = 5
    continue_partial_downloads: bool = True
    delay_between_items: float = 0.0
    socket_timeout: float = 30.0
    bandwidth_limit: int | None = None
    cookie_browser: CookieBrowser | None = CookieBrowser.SYSTEM
    cookie_profile: str | None = None
    ffmpeg_directory: str | None = None
    skip_download_archive: bool = True

    def __post_init__(self) -> None:
        self.max_concurrent_downloads = min(3, max(1, int(self.max_concurrent_downloads)))
        self.retry_count = max(0, int(self.retry_count))
        self.fragment_retry_count = max(0, int(self.fragment_retry_count))
        self.delay_between_items = max(0.0, float(self.delay_between_items))
        self.socket_timeout = max(1.0, float(self.socket_timeout))
        if isinstance(self.quality, str):
            self.quality = VideoQuality(self.quality)
        if isinstance(self.cookie_browser, str):
            try:
                self.cookie_browser = CookieBrowser(self.cookie_browser)
            except ValueError:
                self.cookie_browser = CookieBrowser.SYSTEM

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["quality"] = self.quality.value
        result["cookie_browser"] = (
            self.cookie_browser.value
            if self.cookie_browser is not None
            else CookieBrowser.DISABLED.value
        )
        return result

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> Self:
        normalized = dict(values)
        # Older settings omitted this field or stored JSON null. Migrate both
        # to automatic browser detection; the explicit "none" value remains
        # the persistent opt-out.
        if normalized.get("cookie_browser") is None:
            normalized["cookie_browser"] = CookieBrowser.SYSTEM.value
        return cls(**_known_values(cls, normalized))

    @property
    def video_quality(self) -> VideoQuality:
        return self.quality

    @video_quality.setter
    def video_quality(self, value: VideoQuality) -> None:
        self.quality = value

    @property
    def maximum_concurrent_downloads(self) -> int:
        return self.max_concurrent_downloads

    @maximum_concurrent_downloads.setter
    def maximum_concurrent_downloads(self, value: int) -> None:
        self.max_concurrent_downloads = min(3, max(1, int(value)))


@dataclass(slots=True)
class AppSettings:
    """Serializable top-level settings document."""

    metadata: MetadataSettings | None = None
    downloads: DownloadSettings | None = None
    appearance: AppearanceSettings | None = None

    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = MetadataSettings()
        elif isinstance(self.metadata, dict):
            self.metadata = MetadataSettings.from_dict(self.metadata)
        if self.downloads is None:
            self.downloads = DownloadSettings()
        elif isinstance(self.downloads, dict):
            self.downloads = DownloadSettings.from_dict(self.downloads)
        if self.appearance is None:
            self.appearance = AppearanceSettings()
        elif isinstance(self.appearance, dict):
            self.appearance = AppearanceSettings.from_dict(self.appearance)

    def to_dict(self) -> dict[str, Any]:
        assert self.metadata is not None
        assert self.downloads is not None
        assert self.appearance is not None
        return {
            "metadata": self.metadata.to_dict(),
            "downloads": self.downloads.to_dict(),
            "appearance": self.appearance.to_dict(),
        }

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> Self:
        metadata = values.get("metadata", {})
        downloads = values.get("downloads", {})
        appearance = values.get("appearance", {})
        return cls(
            metadata=MetadataSettings.from_dict(metadata)
            if isinstance(metadata, dict)
            else MetadataSettings(),
            downloads=DownloadSettings.from_dict(downloads)
            if isinstance(downloads, dict)
            else DownloadSettings.from_dict({}),
            appearance=AppearanceSettings.from_dict(appearance)
            if isinstance(appearance, dict)
            else AppearanceSettings(),
        )


ApplicationSettings = AppSettings

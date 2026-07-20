"""Domain types for OpenMediaDL."""

from openmediadl.domain.download_item import DownloadItem
from openmediadl.domain.download_status import DownloadMode, DownloadStatus
from openmediadl.domain.media_metadata import MediaMetadata, MetadataEditFlags
from openmediadl.domain.settings import (
    AppearanceSettings,
    ApplicationSettings,
    AppSettings,
    CookieBrowser,
    DownloadSettings,
    LanguagePreference,
    MetadataSettings,
    ThemePreference,
    VideoQuality,
)

__all__ = [
    "AppSettings",
    "AppearanceSettings",
    "ApplicationSettings",
    "CookieBrowser",
    "DownloadItem",
    "DownloadMode",
    "DownloadSettings",
    "DownloadStatus",
    "LanguagePreference",
    "MediaMetadata",
    "MetadataEditFlags",
    "MetadataSettings",
    "ThemePreference",
    "VideoQuality",
]

"""Small runtime-switchable Russian and English string catalog."""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import Final

from PySide6.QtCore import QLocale

from openmediadl.domain.settings import LanguagePreference

Catalog = MappingProxyType[str, str]

_ENGLISH: Final = MappingProxyType(
    {
        "app.title": "OpenMediaDL",
        "action.analyze": "Analyze",
        "action.browse": "Browse…",
        "action.cancel": "Cancel",
        "action.cancel_all_pending": "Cancel all pending",
        "action.cancel_analysis": "Cancel analysis",
        "action.cancel_selected": "Cancel selected",
        "action.close": "Close",
        "action.continue_downloads": "Continue to downloads →",
        "action.download": "Download",
        "action.download_selected": "Download selected",
        "action.open_logs": "Open logs",
        "action.open_output": "Open output directory",
        "action.pause_queue": "Pause queue",
        "action.recalculate_metadata": "Recalculate metadata for selected rows",
        "action.remove_completed": "Remove completed",
        "action.resume_queue": "Resume queue",
        "action.retry_failed": "Retry failed",
        "action.save": "Save",
        "action.settings": "Advanced settings…",
        "common.none": "None",
        "group.media_urls": "Media URLs",
        "group.metadata_rules": "Metadata and cover rules",
        "group.output": "Output",
        "label.destination": "Destination",
        "label.language": "Language",
        "label.mode": "Mode",
        "label.theme": "Theme",
        "label.video_quality": "Video quality",
        "language.en": "English",
        "language.ru": "Русский",
        "language.system": "System language",
        "log.analysis_cancelled": "Analysis cancelled: {count} item(s).",
        "log.analysis_completed": "Analysis completed: {count} item(s).",
        "log.analysis_error": "Analysis error: {message}",
        "log.analyzing_urls": "Analyzing {count} URL(s)…",
        "log.queue_cancelled": "Queue cancelled.",
        "log.queue_finished": "Queue finished.",
        "log.recalculated": "Recalculated metadata for {count} item(s).",
        "log.retry_ready": "Marked {count} failed item(s) ready to retry.",
        "log.starting_queue": "Starting {count} queued item(s).",
        "mode.audio": "Audio only — M4A",
        "mode.video": "Video",
        "metadata.channel_album_artist": "Use channel name as album artist",
        "metadata.channel_artist": "Use channel name as artist",
        "metadata.cleaned_filename": "Use cleaned title as file name",
        "metadata.crop_cover": "Crop embedded cover to square",
        "metadata.embed_cover": "Embed thumbnail as cover",
        "metadata.playlist_album": "Use playlist title as album",
        "metadata.playlist_track": "Use playlist index as track number",
        "metadata.remove_channel_title": "Remove channel name from track title",
        "metadata.remove_labels": "Remove Official Video / Lyrics labels",
        "metadata.separate_jpeg": "Save cover as separate JPEG",
        "metadata.upload_year": "Store upload year",
        "metadata.url_comment": "Store original URL in comment",
        "dialog.cancel_queue.message": (
            "Cancel all pending and active tasks? Partial files will be retained when yt-dlp "
            "supports continuation."
        ),
        "dialog.cancel_queue.title": "Cancel queue",
        "dialog.checking_ffmpeg.message": (
            "FFmpeg is being checked in the background. Start the queue again when the check "
            "finishes."
        ),
        "dialog.checking_ffmpeg.title": "Checking FFmpeg",
        "dialog.choose_destination": "Choose download destination",
        "dialog.destination_required.message": "Choose an output directory first.",
        "dialog.destination_required.title": "Destination required",
        "dialog.destination_unavailable.title": "Destination unavailable",
        "dialog.ffmpeg_required.message": (
            "Downloads require both FFmpeg and FFprobe for extraction, merging, and metadata. "
            "Install them or select their directory in Settings."
        ),
        "dialog.ffmpeg_required.title": "FFmpeg required",
        "dialog.invalid_url.title": "Invalid URL",
        "dialog.no_urls.message": ("Paste at least one video, playlist, or channel URL."),
        "dialog.no_urls.title": "No URLs",
        "dialog.nothing_selected.message": "Select at least one unfinished item.",
        "dialog.nothing_selected.title": "Nothing selected",
        "dialog.remove_completed.message": ("Remove {count} completed rows from the queue?"),
        "dialog.remove_completed.title": "Remove completed",
        "dialog.tasks_active.message": (
            "Cancel active tasks and close? Partial downloads will be retained when possible."
        ),
        "dialog.tasks_active.title": "Tasks are active",
        "dialog.worker_stopping.message": (
            "A network operation is still stopping. Try closing again shortly."
        ),
        "dialog.worker_stopping.title": "Still stopping",
        "dialog.ffmpeg_stopping.message": (
            "The FFmpeg check is still stopping. Try closing again shortly."
        ),
        "dialog.ffmpeg_stopping.title": "Still checking FFmpeg",
        "notice.legal_full": (
            "Download only content you are permitted to access and save. "
            "OpenMediaDL does not bypass DRM, paywalls, or access controls."
        ),
        "placeholder.activity_log": (
            "Activity and concise errors appear here. Full tracebacks are saved in the "
            "log directory."
        ),
        "placeholder.media_urls": (
            "Paste one or more video, playlist, or channel URLs — one per line"
        ),
        "phase.analyzing": "Analyzing",
        "phase.analyzing_found": "Analyzing — {count} found",
        "phase.completed": "Completed",
        "phase.converting": "Converting",
        "phase.downloading_audio": "Downloading audio",
        "phase.downloading_media": "Downloading media",
        "phase.downloading_thumbnail": "Downloading thumbnail",
        "phase.downloading_video": "Downloading video",
        "phase.embedding_cover": "Embedding cover",
        "phase.failed": "Failed",
        "phase.finishing_thumbnails": "Finishing thumbnails",
        "phase.merging": "Merging",
        "phase.processing": "Processing",
        "phase.verifying_output": "Verifying output",
        "phase.writing_metadata": "Writing metadata",
        "quality.best": "Best",
        "queue.summary.active": " · {count} active",
        "queue.summary.base": "{total} items · {selected} selected",
        "queue.summary.completed": " · {count} completed",
        "queue.summary.failed": " · {count} failed",
        "review.intro": (
            "Review thumbnails and proposed file names. Edit title, artist and track values "
            "directly in the table; edits are saved immediately."
        ),
        "settings.appearance_note": (
            "Theme and language are applied after saving. System language uses Russian when "
            "the operating-system locale is Russian and English otherwise."
        ),
        "settings.bandwidth": "Bandwidth limit",
        "settings.browser_profile": "Browser profile",
        "settings.browser_profile_placeholder": "Optional browser profile identifier",
        "settings.continue_parts": "Continue supported partial downloads",
        "settings.cookies_browser": "Cookies from browser",
        "settings.cookies_notice": (
            "OpenMediaDL stores only the selected browser and optional profile name. "
            "It never reads or logs raw cookie contents itself."
        ),
        "settings.delay": "Delay between items",
        "settings.ffmpeg_check_after_save": (
            "FFmpeg will be checked in the background after saving."
        ),
        "settings.ffmpeg_directory": "FFmpeg directory",
        "settings.ffmpeg_selected_check_after_save": (
            "The selected directory will be checked in the background after saving."
        ),
        "settings.ffmpeg_status": "FFmpeg status",
        "settings.fragment_retries": "Fragment retries",
        "settings.parallel_downloads": "Parallel downloads",
        "settings.remember_last_tab": "Restore the last opened main tab",
        "settings.retries": "Retries",
        "settings.select_ffmpeg_directory": "Select directory containing FFmpeg and FFprobe",
        "settings.skip_archive": "Skip media already present in the download archive",
        "settings.socket_timeout": "Socket timeout",
        "settings.tab.downloads": "Downloads",
        "settings.tab.interface": "Interface",
        "settings.tab.network_tools": "Network & tools",
        "settings.title": "Settings",
        "source.intro": (
            "Paste source URLs, choose the output format and destination, then analyze. "
            "A new destination rebases proposed paths; the format is the default for the next "
            "analysis. Neither action clears manual metadata edits."
        ),
        "status.items_found": "Items found: {count}",
        "status.no_analyzed_items": "No analyzed items yet.",
        "status.no_queue_items": "No items in the queue.",
        "status.ready_analyze": "Ready to analyze.",
        "status.ready": "Ready",
        "status.analysis_cancelled": "Analysis cancelled",
        "status.analysis_complete": "Analysis complete — {count} item(s)",
        "status.analysis_error": "Analysis error: {message}",
        "status.analyzed": "Analyzed {current}",
        "status.analyzed_total": "Analyzed {current} / {total}",
        "status.checking_ffmpeg": "Checking FFmpeg/FFprobe…",
        "status.destination_updated": (
            "Destination updated; manual metadata edits were preserved."
        ),
        "status.ffmpeg_available": "FFmpeg available",
        "status.ffmpeg_unavailable": (
            "FFmpeg/FFprobe unavailable — configure them before downloading"
        ),
        "status.format_next_analysis": (
            "Format saved for the next analysis; reviewed rows were not changed."
        ),
        "status.queue": "Queue: {completed} / {total}",
        "status.queue_cancelled": "Queue cancelled",
        "status.queue_finished": "Queue finished",
        "status.starting_analysis": "Starting analysis…",
        "download_status.analyzing": "Analyzing",
        "download_status.cancelled": "Cancelled",
        "download_status.completed": "Completed",
        "download_status.downloading": "Downloading",
        "download_status.failed": "Failed",
        "download_status.pending": "Pending",
        "download_status.processing": "Processing",
        "download_status.ready": "Ready",
        "download_status.skipped": "Skipped",
        "table.album": "Album",
        "table.artist": "Artist",
        "table.cover": "Cover",
        "table.duration": "Duration",
        "table.error": "Error",
        "table.final_file": "Final file",
        "table.include_download": "Include in download",
        "table.original_title": "Original title",
        "table.cleaned_title": "Cleaned title",
        "table.progress": "Progress",
        "table.selected": "✓",
        "table.status": "Status",
        "table.track": "Track",
        "tab.analyze": "1. Analyze",
        "tab.download": "3. Download",
        "tab.download_count": "3. Download ({count})",
        "tab.review": "2. Review & edit",
        "tab.review_count": "2. Review & edit ({count})",
        "theme.dark": "Dark",
        "theme.light": "Light",
        "theme.system": "System",
        "unit.kib_unlimited": " KiB/s (0 = unlimited)",
        "unit.seconds": " s",
        "notice.legal": "Download only content you are permitted to access and save.",
    }
)

_RUSSIAN: Final = MappingProxyType(
    {
        "app.title": "OpenMediaDL",
        "action.analyze": "Анализировать",
        "action.browse": "Обзор…",
        "action.cancel": "Отмена",
        "action.cancel_all_pending": "Отменить все ожидающие",
        "action.cancel_analysis": "Отменить анализ",
        "action.cancel_selected": "Отменить выбранные",
        "action.close": "Закрыть",
        "action.continue_downloads": "Перейти к скачиванию →",
        "action.download": "Скачать",
        "action.download_selected": "Скачать выбранные",
        "action.open_logs": "Открыть журналы",
        "action.open_output": "Открыть папку загрузок",
        "action.pause_queue": "Приостановить очередь",
        "action.recalculate_metadata": "Пересчитать метаданные выбранных строк",
        "action.remove_completed": "Удалить завершённые",
        "action.resume_queue": "Продолжить очередь",
        "action.retry_failed": "Повторить ошибки",
        "action.save": "Сохранить",
        "action.settings": "Расширенные настройки…",
        "common.none": "Нет",
        "group.media_urls": "Ссылки на медиа",
        "group.metadata_rules": "Правила метаданных и обложек",
        "group.output": "Результат",
        "label.destination": "Папка назначения",
        "label.language": "Язык",
        "label.mode": "Режим",
        "label.theme": "Тема",
        "label.video_quality": "Качество видео",
        "language.en": "English",
        "language.ru": "Русский",
        "language.system": "Язык системы",
        "log.analysis_cancelled": "Анализ отменён. Элементов: {count}.",
        "log.analysis_completed": "Анализ завершён. Элементов: {count}.",
        "log.analysis_error": "Ошибка анализа: {message}",
        "log.analyzing_urls": "Анализ ссылок: {count}…",
        "log.queue_cancelled": "Очередь отменена.",
        "log.queue_finished": "Очередь завершена.",
        "log.recalculated": "Метаданные пересчитаны. Элементов: {count}.",
        "log.retry_ready": "Готовы к повтору после ошибки: {count}.",
        "log.starting_queue": "Запуск очереди. Элементов: {count}.",
        "mode.audio": "Только аудио — M4A",
        "mode.video": "Видео",
        "metadata.channel_album_artist": "Использовать канал как исполнителя альбома",
        "metadata.channel_artist": "Использовать название канала как исполнителя",
        "metadata.cleaned_filename": "Использовать итоговое название как имя файла",
        "metadata.crop_cover": "Обрезать встроенную обложку до квадрата",
        "metadata.embed_cover": "Встроить миниатюру как обложку",
        "metadata.playlist_album": "Использовать название плейлиста как альбом",
        "metadata.playlist_track": "Использовать позицию в плейлисте как номер трека",
        "metadata.remove_channel_title": "Удалять название канала из названия трека",
        "metadata.remove_labels": "Удалять пометки Official Video / Lyrics",
        "metadata.separate_jpeg": "Сохранять обложку отдельным JPEG",
        "metadata.upload_year": "Сохранять год публикации",
        "metadata.url_comment": "Сохранять исходную ссылку в комментарии",
        "dialog.cancel_queue.message": (
            "Отменить все ожидающие и активные задачи? Частичные файлы сохранятся, если "
            "yt-dlp поддерживает продолжение."
        ),
        "dialog.cancel_queue.title": "Отмена очереди",
        "dialog.checking_ffmpeg.message": (
            "FFmpeg проверяется в фоне. Запустите очередь ещё раз после завершения проверки."
        ),
        "dialog.checking_ffmpeg.title": "Проверка FFmpeg",
        "dialog.choose_destination": "Выберите папку загрузок",
        "dialog.destination_required.message": "Сначала выберите папку назначения.",
        "dialog.destination_required.title": "Не выбрана папка",
        "dialog.destination_unavailable.title": "Папка недоступна",
        "dialog.ffmpeg_required.message": (
            "Для извлечения, объединения и метаданных нужны FFmpeg и FFprobe. Установите их "
            "или выберите папку в настройках."
        ),
        "dialog.ffmpeg_required.title": "Требуется FFmpeg",
        "dialog.invalid_url.title": "Некорректная ссылка",
        "dialog.no_urls.message": "Вставьте хотя бы одну ссылку на видео, плейлист или канал.",
        "dialog.no_urls.title": "Нет ссылок",
        "dialog.nothing_selected.message": "Выберите хотя бы один незавершённый элемент.",
        "dialog.nothing_selected.title": "Ничего не выбрано",
        "dialog.remove_completed.message": ("Удалить завершённые строки из очереди: {count}?"),
        "dialog.remove_completed.title": "Удаление завершённых",
        "dialog.tasks_active.message": (
            "Отменить активные задачи и закрыть приложение? Частичные загрузки будут "
            "сохранены, когда это возможно."
        ),
        "dialog.tasks_active.title": "Есть активные задачи",
        "dialog.worker_stopping.message": (
            "Сетевая операция ещё завершается. Попробуйте закрыть приложение чуть позже."
        ),
        "dialog.worker_stopping.title": "Остановка ещё выполняется",
        "dialog.ffmpeg_stopping.message": (
            "Проверка FFmpeg ещё завершается. Попробуйте закрыть приложение чуть позже."
        ),
        "dialog.ffmpeg_stopping.title": "FFmpeg ещё проверяется",
        "notice.legal_full": (
            "Скачивайте только контент, который вам разрешено сохранять. OpenMediaDL не "
            "обходит DRM, платный доступ и ограничения доступа."
        ),
        "placeholder.activity_log": (
            "Здесь отображаются действия и краткие ошибки. Полная диагностика сохраняется "
            "в папке журналов."
        ),
        "placeholder.media_urls": (
            "Вставьте ссылки на видео, плейлисты или каналы — по одной на строку"
        ),
        "phase.analyzing": "Анализ",
        "phase.analyzing_found": "Анализ — найдено: {count}",
        "phase.completed": "Завершено",
        "phase.converting": "Конвертация",
        "phase.downloading_audio": "Скачивание аудио",
        "phase.downloading_media": "Скачивание медиа",
        "phase.downloading_thumbnail": "Скачивание миниатюры",
        "phase.downloading_video": "Скачивание видео",
        "phase.embedding_cover": "Встраивание обложки",
        "phase.failed": "Ошибка",
        "phase.finishing_thumbnails": "Завершение обработки миниатюр",
        "phase.merging": "Объединение потоков",
        "phase.processing": "Обработка",
        "phase.verifying_output": "Проверка результата",
        "phase.writing_metadata": "Запись метаданных",
        "quality.best": "Лучшее",
        "queue.summary.active": " · активно: {count}",
        "queue.summary.base": "Элементов: {total} · выбрано: {selected}",
        "queue.summary.completed": " · завершено: {count}",
        "queue.summary.failed": " · ошибок: {count}",
        "review.intro": (
            "Проверьте миниатюры и будущие имена файлов. Название, исполнителя и номер трека "
            "можно менять прямо в таблице; правки сохраняются сразу."
        ),
        "settings.appearance_note": (
            "Тема и язык применяются после сохранения. Язык системы выбирает русский для "
            "русской локали ОС, для остальных локалей — английский."
        ),
        "settings.bandwidth": "Ограничение скорости",
        "settings.browser_profile": "Профиль браузера",
        "settings.browser_profile_placeholder": "Необязательное имя профиля браузера",
        "settings.continue_parts": "Продолжать поддерживаемые незавершённые загрузки",
        "settings.cookies_browser": "Cookies из браузера",
        "settings.cookies_notice": (
            "OpenMediaDL хранит только выбранный браузер и необязательное имя профиля. "
            "Содержимое cookies не сохраняется и не записывается в журнал."
        ),
        "settings.delay": "Задержка между файлами",
        "settings.ffmpeg_check_after_save": ("FFmpeg будет проверен в фоне после сохранения."),
        "settings.ffmpeg_directory": "Папка FFmpeg",
        "settings.ffmpeg_selected_check_after_save": (
            "Выбранная папка будет проверена в фоне после сохранения."
        ),
        "settings.ffmpeg_status": "Состояние FFmpeg",
        "settings.fragment_retries": "Повторы фрагментов",
        "settings.parallel_downloads": "Одновременные загрузки",
        "settings.remember_last_tab": "Восстанавливать последнюю открытую вкладку",
        "settings.retries": "Повторы",
        "settings.select_ffmpeg_directory": "Выберите папку с FFmpeg и FFprobe",
        "settings.skip_archive": "Пропускать медиа, уже записанные в архив загрузок",
        "settings.socket_timeout": "Тайм-аут соединения",
        "settings.tab.downloads": "Скачивание",
        "settings.tab.interface": "Интерфейс",
        "settings.tab.network_tools": "Сеть и инструменты",
        "settings.title": "Настройки",
        "source.intro": (
            "Вставьте ссылки, выберите формат и папку, затем запустите анализ. Новая папка "
            "изменяет только будущие пути, а формат применяется к следующему анализу. Ручные "
            "правки при этом сохраняются."
        ),
        "status.items_found": "Найдено элементов: {count}",
        "status.no_analyzed_items": "Проанализированных элементов пока нет.",
        "status.no_queue_items": "Очередь пока пуста.",
        "status.ready_analyze": "Готово к анализу.",
        "status.ready": "Готово",
        "status.analysis_cancelled": "Анализ отменён",
        "status.analysis_complete": "Анализ завершён — элементов: {count}",
        "status.analysis_error": "Ошибка анализа: {message}",
        "status.analyzed": "Проанализировано: {current}",
        "status.analyzed_total": "Проанализировано: {current} из {total}",
        "status.checking_ffmpeg": "Проверка FFmpeg/FFprobe…",
        "status.destination_updated": ("Папка обновлена; ручные изменения метаданных сохранены."),
        "status.ffmpeg_available": "FFmpeg доступен",
        "status.ffmpeg_unavailable": ("FFmpeg/FFprobe недоступны — настройте их перед скачиванием"),
        "status.format_next_analysis": (
            "Формат сохранён для следующего анализа; проверенные строки не изменены."
        ),
        "status.queue": "Очередь: {completed} / {total}",
        "status.queue_cancelled": "Очередь отменена",
        "status.queue_finished": "Очередь завершена",
        "status.starting_analysis": "Запуск анализа…",
        "download_status.analyzing": "Анализ",
        "download_status.cancelled": "Отменено",
        "download_status.completed": "Завершено",
        "download_status.downloading": "Скачивание",
        "download_status.failed": "Ошибка",
        "download_status.pending": "Ожидание",
        "download_status.processing": "Обработка",
        "download_status.ready": "Готово",
        "download_status.skipped": "Пропущено",
        "table.album": "Альбом",
        "table.artist": "Исполнитель",
        "table.cover": "Обложка",
        "table.duration": "Длительность",
        "table.error": "Ошибка",
        "table.final_file": "Итоговый файл",
        "table.include_download": "Включить в скачивание",
        "table.original_title": "Исходное название",
        "table.cleaned_title": "Итоговое название",
        "table.progress": "Прогресс",
        "table.selected": "✓",
        "table.status": "Состояние",
        "table.track": "Трек",
        "tab.analyze": "1. Анализ",
        "tab.download": "3. Скачивание",
        "tab.download_count": "3. Скачивание ({count})",
        "tab.review": "2. Проверка и правка",
        "tab.review_count": "2. Проверка и правка ({count})",
        "theme.dark": "Тёмная",
        "theme.light": "Светлая",
        "theme.system": "Системная",
        "unit.kib_unlimited": " КиБ/с (0 = без ограничений)",
        "unit.seconds": " с",
        "notice.legal": "Скачивайте только контент, который вам разрешено сохранять.",
    }
)

_CATALOGS: Final[MappingProxyType[LanguagePreference, Catalog]] = MappingProxyType(
    {
        LanguagePreference.ENGLISH: _ENGLISH,
        LanguagePreference.RUSSIAN: _RUSSIAN,
    }
)
_LOCALE_SEPARATOR_RE: Final = re.compile(r"[-_.@]")


def resolve_language(
    preference: LanguagePreference = LanguagePreference.SYSTEM,
    *,
    locale_name: str | None = None,
) -> LanguagePreference:
    """Resolve ``system`` to Russian for ``ru`` locales and English otherwise."""

    if preference is not LanguagePreference.SYSTEM:
        return preference

    system_name = QLocale.system().name() if locale_name is None else locale_name
    language_code = _LOCALE_SEPARATOR_RE.split(system_name.strip(), maxsplit=1)[0].casefold()
    if language_code == LanguagePreference.RUSSIAN.value:
        return LanguagePreference.RUSSIAN
    return LanguagePreference.ENGLISH


class TranslationCatalog:
    """An instance-local catalog that can be switched while the app is running."""

    def __init__(
        self,
        preference: LanguagePreference = LanguagePreference.SYSTEM,
        *,
        locale_name: str | None = None,
    ) -> None:
        self._preference = preference
        self._language = resolve_language(preference, locale_name=locale_name)

    @property
    def preference(self) -> LanguagePreference:
        return self._preference

    @property
    def language(self) -> LanguagePreference:
        return self._language

    def set_language(
        self,
        preference: LanguagePreference,
        *,
        locale_name: str | None = None,
    ) -> bool:
        """Switch language and report whether the resolved language changed."""

        resolved = resolve_language(preference, locale_name=locale_name)
        changed = resolved is not self._language
        self._preference = preference
        self._language = resolved
        return changed

    def tr(self, key: str, **format_values: object) -> str:
        """Translate a key, falling back to English and then to the key itself."""

        catalog = _CATALOGS[self._language]
        template = catalog.get(key, _ENGLISH.get(key, key))
        if not format_values:
            return template
        return template.format(**format_values)


Translator = TranslationCatalog

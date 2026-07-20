# OpenMediaDL

OpenMediaDL is a local desktop application for inspecting and downloading media with
[yt-dlp](https://github.com/yt-dlp/yt-dlp). It provides a reviewable workflow for individual
videos, playlists, and channels: analyze the source, edit the proposed metadata, then run and
monitor the download queue.

The primary audio workflow produces M4A files with deterministic file names, editable metadata,
and optional embedded cover art. Video downloads and quality selection are also supported. There
is no application-level 100-item playlist limit.

> [!IMPORTANT]
> Download only media that you own or are authorized to save. OpenMediaDL does not bypass DRM,
> paywalls, authentication, or access controls. You are responsible for the source service's terms
> and applicable law.

## Workflow

The main window is divided into three state-preserving tabs:

| Tab | Purpose |
| --- | --- |
| **1. Analyze** | Paste one or more URLs, choose audio or video, select video quality, set the destination, and start analysis. |
| **2. Review & edit** | Inspect thumbnails and source data, choose queue items, edit titles and artist metadata, and apply optional metadata and cover rules. |
| **3. Download** | Start or pause the queue, follow per-item progress, cancel work, retry failures, and open the output or log directory. |

All three tabs use the same in-memory queue model. Switching tabs never recreates the queue or
discards edits. Changing the destination rebases only the proposed final paths and preserves
manually edited metadata. Changing the download mode sets the default for the next analysis and
does not rewrite reviewed rows. Queue items, settings, the URL draft, and the active tab are also
stored in SQLite so unfinished work can be restored after an application restart.

The interface defaults to the dark theme. Language defaults to the operating-system locale:
Russian locales use the Russian interface and all other locales fall back to English. Settings
allow switching at runtime among Dark, Light, and System themes and among System, Русский, and
English languages without rebuilding the tabs or queue model.

## Artwork

No banners, social previews, placeholder covers, or packaged screenshots are required. The only
project artwork is the application icon described in [docs/visual-assets.md](docs/visual-assets.md).

## Features

- Analyze individual videos, playlists, channels, or several URLs in one request without blocking
  the Qt event loop.
- Add large collections incrementally instead of waiting for an entire playlist to finish before
  showing results.
- Review a compact queue with thumbnails, wrapped titles, editable metadata, selection state,
  status, progress, and proposed output files.
- Download video at a selected quality or extract audio to M4A, reusing a suitable M4A/AAC source
  stream when practical.
- Derive clean titles and file names with conservative, opt-in metadata rules.
- Use the channel as artist or album artist, the playlist index as track number, and optionally
  the playlist title as album. Playlist album tagging is disabled by default.
- Embed a thumbnail as cover art, optionally crop it to a square, or retain a separate JPEG.
- Write and verify M4A title, artist, album artist, album, track, year, source URL, and cover tags
  with Mutagen.
- Persist queue items, settings, completed-download history, and window state in SQLite.
- Switch theme and language at runtime; optionally start on the last opened workflow tab.
- Skip media already recorded in the download history only after processing and metadata
  verification have succeeded.
- Report yt-dlp progress through structured hooks and show concise failures in the progress area;
  full diagnostic details remain available in rotating log files.
- Detect FFmpeg and FFprobe from `PATH`, an optional local tools directory, or a directory selected
  in advanced settings.
- Keep browser-cookie data private: only the selected browser and optional profile identifier are
  stored, never the cookie contents.

## Requirements

- Python 3.12 or newer
- FFmpeg and FFprobe for audio extraction, conversion, merging, and most metadata operations
- Windows, Linux, or macOS; Windows is the first packaged target
- Network access to the requested media source

Some yt-dlp extractors also require a supported JavaScript runtime such as Deno or Node. Install
one separately if yt-dlp requests it. OpenMediaDL does not fetch runtimes, FFmpeg, or other
third-party executables.

## Install and run from source

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m openmediadl.main
```

### Linux or macOS

```bash
python3.12 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements-lock.txt
./.venv/bin/python -m pip install --no-deps -e .
./.venv/bin/python -m openmediadl.main
```

`requirements-lock.txt` contains the reviewed, universal dependency graph used by development and
CI. Compatible package ranges are declared separately in `pyproject.toml`.

## FFmpeg setup

1. Obtain a build through the [official FFmpeg download page](https://ffmpeg.org/download.html) or
   your operating system's trusted package manager.
2. Keep the executables outside this repository.
3. Add the directory containing `ffmpeg` and `ffprobe` to `PATH`, or select it in OpenMediaDL's
   advanced settings.
4. Verify both commands in a terminal:

```text
ffmpeg -version
ffprobe -version
```

OpenMediaDL validates both executables before starting operations that depend on them. FFmpeg is
not bundled in the repository, source distribution, or default PyInstaller build.

## Metadata and cover behavior

Title cleaning first normalizes Unicode and whitespace. It removes a channel name only when that
name is a distinct prefix, suffix, or bracketed prefix; a matching word in the middle of a title is
preserved. Optional trailing labels such as `Official Video`, `Official Audio`, `Lyrics`, and
`Visualizer` are removed case-insensitively. If a rule would produce an empty title, the normalized
original title is restored.

For audio downloads, OpenMediaDL writes the following MP4 atoms when their corresponding values
are available:

| Atom | Value |
| --- | --- |
| `©nam` | Cleaned or manually edited title |
| `©ART` | Channel-derived or manually edited artist |
| `aART` | Album artist, normally derived from the channel |
| `©alb` | Optional playlist-derived or manually edited album |
| `trkn` | Playlist track index and total |
| `©day` | Upload year |
| `©cmt` | Original source URL |
| `covr` | JPEG or PNG cover bytes |

After writing tags, the application reopens the M4A with Mutagen and verifies readability plus the
required title and artist. It verifies the cover when embedding is enabled. A missing optional tag
does not delete an otherwise successful download.

Analysis caches bounded display previews instead of downloading full-size artwork for every row.
A larger cover candidate is fetched only for a selected audio download. Pillow converts supported
source images to JPEG, limits excessive dimensions, and can apply a high-quality center crop.
Temporary cover files are removed after successful embedding unless **Save cover as separate
JPEG** is enabled.

## Local data and privacy

OpenMediaDL has no companion server. Analysis, downloads, queue state, thumbnails, settings, and
logs remain on the user's computer.

| Platform | Persistent data | Thumbnail cache |
| --- | --- | --- |
| Windows | `%LOCALAPPDATA%\OpenMediaDL` | `%LOCALAPPDATA%\OpenMediaDL\cache` |
| macOS | `~/Library/Application Support/OpenMediaDL` | `~/Library/Caches/OpenMediaDL` |
| Linux | `$XDG_DATA_HOME/openmediadl` or `~/.local/share/openmediadl` | `$XDG_CACHE_HOME/openmediadl` or `~/.cache/openmediadl` |

The persistent data directory contains the SQLite database and rotating logs. Raw cookies,
complete sensitive request headers, downloaded media, and cover images are not copied into the
database or logs.

## Project structure

```text
src/openmediadl/
├── application.py    # application composition, storage paths, and logging
├── core/             # analysis, downloads, metadata, filenames, covers, and FFmpeg
├── database/         # SQLite connection, migrations, and repositories
├── domain/           # typed queue items, statuses, metadata, and settings
├── ui/               # PySide6 window, tabs, table model, dialogs, and delegates
└── workers/          # background analysis, download, and FFmpeg checks
```

The project uses a `src/` layout. Business rules belong in `core` or `domain`; persistent storage
belongs in `database`; widgets and presentation models belong in `ui`. Long-running work must run
outside the GUI thread and communicate through Qt signals.

## Development checks

Install the locked dependencies as shown above, then run:

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy src/openmediadl
.\.venv\Scripts\python.exe -m pytest
```

Apply Python formatting with:

```powershell
.\.venv\Scripts\python.exe -m ruff format .
```

Ordinary tests are deterministic and do not download live media. Network-dependent tests must use
the `integration` marker and are excluded from the default run. Execute them only in an appropriate
environment:

```powershell
.\.venv\Scripts\python.exe -m pytest -o addopts="-ra --strict-config --strict-markers" -m integration
```

Maintainers can regenerate the dependency lock with
[`uv`](https://docs.astral.sh/uv/) after reviewing upstream releases. Advance the cutoff date
deliberately:

```powershell
uv pip compile pyproject.toml --extra dev --universal --python-version 3.12 --exclude-newer 2026-07-20 --output-file requirements-lock.txt --upgrade
```

## Build the Windows application

PyInstaller builds for its current operating system. Create the Windows single-file executable on
Windows:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm OpenMediaDL.spec
```

The result is the standalone `dist\OpenMediaDL.exe`. It does not require a neighboring `_internal`
directory or a separately installed Python runtime. At launch, PyInstaller extracts its bundled
runtime into a temporary directory, so the first start can be slightly slower. The GitHub Actions
workflow runs the same spec and publishes the executable in its Windows artifact. Build Linux and
macOS packages natively on those platforms.

## Known limitations

- Source sites and their formats change independently, so yt-dlp updates may be required.
- DRM-protected, paid, private, or otherwise unauthorized media is unsupported.
- Browser-cookie extraction depends on browser and operating-system behavior.
- **Pause queue** prevents new items from starting; an active yt-dlp or FFmpeg operation must
  finish or be cancelled. Byte-perfect pause and resume are not promised.
- Video-container metadata and thumbnails are best effort because container capabilities vary.
  M4A is the primary supported metadata workflow.
- Large channels can take considerable time to analyze despite incremental batches and the absence
  of an artificial item limit.
- Windows packaging is automated. Linux and macOS currently run from source and require native
  packages built on their respective platforms.

## Visual assets

OpenMediaDL needs only one supplied image: the square application icon. The PNG is used by the Qt
window and a derived multi-resolution ICO is embedded in the Windows executable. No README banner,
social preview, placeholder cover, or screenshot artwork is part of the application bundle. See
[docs/visual-assets.md](docs/visual-assets.md) for paths and export details. A Russian icon-only
regeneration prompt is kept in [docs/image-generation-prompts.ru.md](docs/image-generation-prompts.ru.md).

## Contributing

1. Open an issue before starting a large behavioral change.
2. Keep pull requests focused and preserve separation between business logic and Qt presentation.
3. Add or update deterministic tests; ordinary unit tests must not download live media.
4. Run Ruff, mypy, and pytest using the commands above.
5. Describe user-visible changes, migrations, and platform-specific behavior in the pull request.

Do not commit downloaded media, cookie exports, logs, databases, local FFmpeg binaries, or other
third-party executables.

## License and acknowledgements

OpenMediaDL source code is available under the [MIT License](LICENSE). Dependencies and external
tools retain their own licenses:

- [Qt for Python / PySide6](https://doc.qt.io/qtforpython-6/) — LGPLv3, GPLv2/GPLv3, or commercial,
  depending on the selected terms
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — The Unlicense
- [yt-dlp-ejs](https://github.com/yt-dlp/ejs) — The Unlicense, with ISC- and MIT-licensed components
- [Pillow](https://python-pillow.github.io/) — HPND License
- [Mutagen](https://mutagen.readthedocs.io/) — GPL-2.0-or-later
- [FFmpeg](https://ffmpeg.org/legal.html) — LGPL or GPL, depending on the external build
- [PyInstaller](https://pyinstaller.org/) — GPL-2.0-or-later with an application-distribution
  exception

Distributors are responsible for reviewing and satisfying the licenses of the exact libraries and
binaries they ship, including Qt, Mutagen, and their chosen FFmpeg build.

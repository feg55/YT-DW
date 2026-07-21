# ruff: noqa: F821 - PyInstaller injects SPECPATH and build classes at runtime.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

project_root = Path(SPECPATH)

datas = collect_data_files("openmediadl", includes=["resources/*", "resources/**/*"])
datas += copy_metadata("yt-dw")
datas += copy_metadata("yt-dlp")
datas += copy_metadata("yt-dlp-ejs")
datas += copy_metadata("mutagen")
datas += collect_data_files("yt_dlp_ejs")

# yt-dlp discovers extractors dynamically and Mutagen discovers format handlers.
hidden_imports = collect_submodules("yt_dlp")
hidden_imports += collect_submodules("yt_dlp_ejs")
hidden_imports += collect_submodules("mutagen")

analysis = Analysis(
    [str(project_root / "src" / "openmediadl" / "main.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["mypy", "pytest", "ruff", "tkinter"],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="YT-DW",
    icon=str(project_root / "src" / "openmediadl" / "resources" / "icons" / "yt-dw.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

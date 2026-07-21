# YT-DW application icon

YT-DW uses one visual asset: the supplied square application icon. No README banner, social
preview, placeholder cover, promotional art, or packaged screenshots are required.

## Repository files

| File | Purpose |
| --- | --- |
| `src/openmediadl/resources/icons/yt-dw.png` | Source icon used by the Qt window and application resources |
| `src/openmediadl/resources/icons/yt-dw.ico` | Multi-resolution Windows icon embedded in `YT-DW.exe` |

The PNG is the only image that needs to be supplied. The ICO should be generated from that same
master so the application and executable keep one identity. Do not generate a visually different
ICO.

After replacing the PNG, regenerate the ICO with:

```powershell
.\.venv\Scripts\python.exe scripts\generate_windows_icon.py
```

## Export requirements

- Square sRGB PNG, preferably 512×512 or larger, with transparency preserved when present.
- ICO layers: 16, 24, 32, 48, 64, 128, and 256 px.
- Keep the main silhouette readable at 16 and 24 px.
- Do not add text, third-party platform logos, watermarks, or copyrighted media artwork.

The Windows release is a single `dist\YT-DW.exe`; the icon is embedded in that file. Users do
not need to keep the PNG, ICO, an `_internal` directory, or any other image beside the executable.

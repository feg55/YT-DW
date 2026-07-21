# Иконка YT-DW

Для программы нужна только одна картинка — квадратная иконка приложения. Присланного PNG достаточно:
из него создаётся многоразмерный `yt-dw.ico` для Windows. Баннер README, превью для соцсетей,
заглушки обложек и дополнительные изображения не нужны.

## Куда сохранить

- `src/openmediadl/resources/icons/yt-dw.png` — исходная иконка для интерфейса.
- `src/openmediadl/resources/icons/yt-dw.ico` — производная иконка внутри Windows EXE.

Рекомендуемый исходник: квадратный PNG 512×512 или больше, sRGB. Для ICO нужны слои 16, 24, 32,
48, 64, 128 и 256 px. В готовой сборке оба файла встроены в `dist\YT-DW.exe`; хранить их рядом
с EXE не требуется.

## Промпт на случай будущей перегенерации

```text
Create one original square desktop application icon for YT-DW. Show a bold downward transfer
arrow entering an open tray, with a small abstract audio-wave detail near the bottom. Use a dark
charcoal rounded-square body, restrained crimson-to-coral arrow, cool gray highlights, clean
geometric shapes, strong contrast, and a silhouette readable at 16 pixels. Transparent background
outside the icon. No text, letters, play-button symbol, platform branding, third-party logos,
album art, faces, watermarks, or copyrighted symbols. Output a 1024×1024 sRGB PNG.
```

Этот промпт нужен только для замены существующей иконки в будущем. Дополнительные картинки по нему
создавать не нужно.

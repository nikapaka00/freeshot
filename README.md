# FreeShot 🔴

A lightweight screenshot and annotation tool for Windows — built with Python.

Trigger a capture with **PrtScrn** or **Alt + Home**, draw a selection, annotate if needed, and the result is instantly on your clipboard and saved as a lossless PNG.

---

## Features

- **Rectangle & Freehand selection** — switch modes with `R` / `F` keys; last used mode is remembered
- **Annotation tools** — Arrow, Line, Rectangle, Pen, Highlighter, Text, Blur, Eraser
- **True transparency** — freehand selections copy as RGBA PNG (no black or white fill)
- **Auto-save PNG** — every screenshot is saved losslessly to `~/Pictures/FreeShot/` (configurable)
- **Auto-copy to clipboard** — raw selection is copied the moment you enter annotation mode
- **Auto copy & close** — skip annotation entirely; copy + save and done
- **PrtScrn intercept** — low-level keyboard hook suppresses Windows Snipping Tool
- **System tray** — all options accessible from the tray icon; no window clutter
- **Low RAM** — full-screen grab is freed as soon as the selection is made (~30 MB during annotation)

---

## Hotkeys

| Key | Action |
|-----|--------|
| `PrtScrn` | Trigger capture |
| `Alt + Home` | Trigger capture (alternative) |
| `R` | Switch to Rectangle mode |
| `F` | Switch to Freehand mode |
| `Ctrl + C` | Copy annotated screenshot |
| `Ctrl + S` | Save annotated screenshot |
| `Ctrl + Z` | Undo last annotation |
| `Esc` | Cancel / close |

---

## Tray Menu

| Option | Description |
|--------|-------------|
| ✔ Auto-copy on capture | Copies raw selection to clipboard when entering annotation |
| ✔ Auto copy & close | Copies + saves immediately, skips annotation |
| ✔ Auto-save PNG | Saves every screenshot as PNG to the save folder |
| 📁 Save folder | Click to change the auto-save destination |

---

## Requirements

```
pip install pillow pynput pystray pywin32
```

Requires **Python 3.9+** and **Windows** (uses Win32 APIs for clipboard and keyboard hook).

---

## Run from source

```bash
git clone https://github.com/nikapaka00/freeshot.git
cd freeshot
pip install -r requirements.txt
python freeshot.py
```

---

## Build executable

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --icon=freeshot.ico --version-file=version_info.txt --name=FreeShot freeshot.py
```

The built EXE will be in `dist/FreeShot.exe`.

---

## Files

| File | Description |
|------|-------------|
| `freeshot.py` | Main application |
| `freeshot_debug.py` | Debug build — logs everything to `debug.log` |
| `freeshot.ico` | Application icon |
| `version_info.txt` | Windows EXE version/metadata for PyInstaller |
| `requirements.txt` | Python dependencies |

---

## Author

**nikapaka00**

---

## License

MIT License — free to use, modify, and distribute.

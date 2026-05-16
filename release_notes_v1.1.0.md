## What's new in v1.1.0

### New: Settings window
Right-click the tray icon -> Settings... opens a proper settings dialog with everything in one place:
- Capture behaviour: Auto-copy on capture, Auto copy & close, Auto-save PNG
- Capture mode: Rectangle / Freehand radio buttons
- Save folder: path display + Browse button
- Hotkeys: choose the Capture key (Print Screen, Scroll Lock, Pause, F13, F14) and Fullscreen key (None, Alt+PrtScrn, Alt+Scroll Lock, Ctrl+PrtScrn, F15, F16)
- System: Start with Windows

The tray menu is now minimal: Capture | Settings... | Exit.

### New: Start with Windows
Registers FreeShot in HKCU\Software\Microsoft\Windows\CurrentVersion\Run so it launches automatically on login. Toggle it on/off from Settings at any time.

### New: Configurable hotkeys
- **Capture key** — the key that triggers the selection overlay. Default: Print Screen.
- **Fullscreen key** — instantly copies the full screen without any selection UI. Default: Alt + Print Screen.
Both keys are changeable from the Hotkeys section in Settings. The alternative Alt+Home shortcut has been removed.

### New: Fullscreen capture
Press the configured fullscreen key to silently grab the entire screen, copy it to the clipboard, and (if Auto-save is on) save it to the save folder — no overlay, no clicks.

### Smoother annotation tools
All drawing tools (arrow, line, rect, pen, highlighter, text, blur, eraser) now render at 2× resolution internally and are downscaled with Lanczos for smooth, anti-aliased edges on output and clipboard copies.

---

### Security & stability fixes (since v1.0.0)
- Settings window can be closed and reopened correctly
- Security hardening: input validation, path traversal guard, TOCTOU-free file creation, font path resolution, hex colour validation
- Error handling: all exception paths now reset _active flag; save failures show error dialogs instead of silently failing
- Dependency versions pinned in requirements.txt
- Code signing + SHA-256 checksums added

---

## SHA-256 checksums

| File | SHA-256 |
|------|---------|
| FreeShot.exe | ad851869a76dc10573aa456640644b9e70db861383a3a6bdc082a535f1acd3cd |
| FreeShot_debug.exe | 9d94fb396440a45aca4e4c074b73b8e903105c295d2aafea46974b3aaf1478b0 |

Both EXEs are Authenticode-signed. See the README for verification instructions.

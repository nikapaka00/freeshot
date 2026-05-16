## What's fixed in v1.1.1

### Critical fix: keyboard freeze on Windows 11

In v1.1.0, some users (including on Windows 11 build 26200) experienced a system-wide keyboard freeze the moment FreeShot launched — the keyboard only started working again when the capture overlay was open, or after closing the app entirely.

**Root cause.** The hotkey listener installed a low-level keyboard hook (`WH_KEYBOARD_LL`) that ran a Python callback on every keystroke. On recent Windows 11 builds, the per-callback GIL acquisition was enough to wedge the system input queue, blocking all keyboard input globally.

**Fix.** The low-level hook has been removed entirely. Hotkeys are now registered with the standard Win32 `RegisterHotKey` API, which delivers `WM_HOTKEY` messages without sitting in the keyboard input chain — no global side effects, no input queue interference.

### Trade-off you should know about

Because `RegisterHotKey` does not swallow the key, pressing **Print Screen** may also trigger the built-in Windows 11 Snipping Tool overlay alongside FreeShot. If this bothers you, disable it in:

> **Settings → Accessibility → Keyboard → "Use the Print Screen key to open screen capture"** (turn off)

Alternatively, change FreeShot's capture key to **Scroll Lock**, **Pause**, **F13**, or **F14** in Settings — none of those are claimed by Windows.

### Other notes
- All v1.1.0 features (Settings window, Start with Windows, configurable hotkeys, fullscreen capture, smoother annotations) are unchanged.
- Overlay shortcuts (Esc / R / F) still work normally.
- Both EXEs are Authenticode-signed.

---

## SHA-256 checksums

| File | SHA-256 |
|------|---------|
| FreeShot.exe | ad851869a76dc10573aa456640644b9e70db861383a3a6bdc082a535f1acd3cd |
| FreeShot_debug.exe | 9d94fb396440a45aca4e4c074b73b8e903105c295d2aafea46974b3aaf1478b0 |

See the README for verification instructions.

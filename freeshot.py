#!/usr/bin/env python3
"""
FreeShot — Screenshot + Annotation Tool for Windows 11
═══════════════════════════════════════════════════════
INSTALL:  pip install pillow pynput pystray pywin32
HOTKEY:   PrtScrn  or  Alt + Home  (or tray icon)
"""

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk
import threading, sys, math, time, json, os, gc, queue, re, stat, ctypes, ctypes.wintypes as _wt, winreg
from pathlib import Path
from io import BytesIO
from PIL import Image, ImageDraw, ImageFilter, ImageGrab, ImageTk
import pystray
from pystray import MenuItem as item


# ── Config ────────────────────────────────────────────────────────────────────
# Store config in %APPDATA%\FreeShot\ so it works even when the exe lives in
# a write-protected location such as C:\Program Files\.
_APP_DIR    = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "FreeShot")
_CONFIG_PATH = os.path.join(_APP_DIR, "config.json")
_ERROR_LOG   = os.path.join(_APP_DIR, "error.log")   # fatal crash log (production)

class Config:
    def __init__(self):
        self.auto_copy        = False
        self.auto_copy_close  = False
        self.capture_mode     = "rect"             # "rect" or "free"
        self.auto_save        = True
        self.save_folder      = ""                 # empty = ~/Pictures/FreeShot
        self.capture_key      = "Print Screen"     # key name from _CAPTURE_KEYS
        self.fullscreen_key   = "Alt + Print Screen"  # key name from _FULLSCREEN_KEYS
        self._load()

    def _load(self):
        try:
            with open(_CONFIG_PATH) as f:
                d = json.load(f)
            self.auto_copy       = bool(d.get("auto_copy", False))
            self.auto_copy_close = bool(d.get("auto_copy_close", False))
            self.capture_mode    = d.get("capture_mode", "rect")
            self.auto_save       = bool(d.get("auto_save", True))
            self.save_folder     = _validate_save_folder(d.get("save_folder", ""))
            ck = str(d.get("capture_key",    self.capture_key))
            fk = str(d.get("fullscreen_key", self.fullscreen_key))
            self.capture_key    = ck if ck in _CAPTURE_KEYS   else "Print Screen"
            self.fullscreen_key = fk if fk in _FULLSCREEN_KEYS else "Alt + Print Screen"
        except Exception as e:
            print(f"[FreeShot] config load: {e}", file=sys.stderr)

    def save(self):
        try:
            os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
            with open(_CONFIG_PATH, "w") as f:
                json.dump({"auto_copy":       self.auto_copy,
                           "auto_copy_close": self.auto_copy_close,
                           "capture_mode":    self.capture_mode,
                           "auto_save":       self.auto_save,
                           "save_folder":     self.save_folder,
                           "capture_key":     self.capture_key,
                           "fullscreen_key":  self.fullscreen_key}, f, indent=2)
        except Exception as e:
            print(f"[FreeShot] config save: {e}", file=sys.stderr)


# ── Icon ──────────────────────────────────────────────────────────────────────
def _make_pentagram_icon(size=64):
    # ImageFilter already imported at the top — no duplicate import needed
    S  = size * 4
    cx = cy = S / 2
    pad      = S * 0.06
    circle_r = S / 2 - pad
    lw       = max(4, S // 12)
    RED      = (160, 0, 0, 255)

    img = Image.new("RGBA", (S, S), (18, 18, 18, 255))
    d   = ImageDraw.Draw(img)

    pts = []
    for i in range(5):
        a = math.radians(-90 + i * 72)
        pts.append((cx + circle_r * math.cos(a),
                    cy + circle_r * math.sin(a)))

    d.ellipse([pad, pad, S - pad, S - pad], outline=RED, width=lw)
    for i, j in zip([0, 2, 4, 1, 3], [2, 4, 1, 3, 0]):
        d.line([pts[i], pts[j]], fill=RED, width=lw)

    out  = img.resize((size, size), Image.LANCZOS)
    glow = out.copy().filter(ImageFilter.GaussianBlur(radius=size * 0.03))
    return Image.alpha_composite(glow, out)


# ── Helpers ───────────────────────────────────────────────────────────────────
_HEX_RE = re.compile(r'^#?([0-9a-fA-F]{6})$')

def hex_rgba(h: str, a: int = 255) -> tuple:
    """Convert a #RRGGBB hex string to an RGBA tuple. Never raises."""
    m = _HEX_RE.match(h.strip())
    if not m:
        return (255, 0, 0, a)   # fall back to red on bad input
    c = m.group(1)
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), a)

def copy_to_clipboard(img: Image.Image) -> bool:
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:                                    # ← fix: always close clipboard
            win32clipboard.EmptyClipboard()
            if img.mode == "RGBA":
                # PNG format — preserves transparency in apps that support it
                png_fmt = win32clipboard.RegisterClipboardFormat("PNG")
                png_buf = BytesIO()
                img.save(png_buf, "PNG")
                win32clipboard.SetClipboardData(png_fmt, png_buf.getvalue())
                # CF_DIB fallback — white background for apps that only read BMP
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[3])
                bmp_buf = BytesIO()
                bg.save(bmp_buf, "BMP")
                win32clipboard.SetClipboardData(win32clipboard.CF_DIB,
                                                bmp_buf.getvalue()[14:])
            else:
                bmp_buf = BytesIO()
                img.convert("RGB").save(bmp_buf, "BMP")
                win32clipboard.SetClipboardData(win32clipboard.CF_DIB,
                                                bmp_buf.getvalue()[14:])
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception as e:
        print(f"[FreeShot] clipboard: {e}", file=sys.stderr)
        return False


def _validate_save_folder(raw: str) -> str:
    """Return *raw* if it is a safe local path under the user home dir; empty string otherwise."""
    if not raw:
        return ""
    try:
        p = Path(raw).resolve()
    except (ValueError, OSError):
        return ""
    s = str(p)
    # Reject UNC / network paths (\\server\share or //server/share)
    if s.startswith("\\\\") or raw.lstrip().startswith("//"):
        return ""
    # Path must reside under the current user's home directory
    try:
        p.relative_to(Path.home().resolve())
        return s
    except ValueError:
        return ""


# ── Windows startup registry helpers ─────────────────────────────────────────
_RUN_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "FreeShot"


def _get_exe_path() -> str:
    """Path to register: sys.executable when frozen (PyInstaller), else abspath(argv[0])."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.abspath(sys.argv[0])


def _read_startup() -> bool:
    """Return True if the HKCU Run key exists and points to the current EXE."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, _RUN_NAME)
            return val == _get_exe_path()
    except (FileNotFoundError, OSError):
        return False


def _write_startup(enable: bool) -> bool:
    """Add or remove the FreeShot Run entry.  Returns True on success."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY,
                            access=winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, _RUN_NAME, 0, winreg.REG_SZ, _get_exe_path())
            else:
                try:
                    winreg.DeleteValue(k, _RUN_NAME)
                except FileNotFoundError:
                    pass   # already absent — not an error
        return True
    except OSError as e:
        print(f"[FreeShot] registry write failed: {e}", file=sys.stderr)
        return False


_SAVE_EXTS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".bmp": "BMP"}
_SSAA = 2   # supersampling scale — draw at 2× then downscale for smooth edges

# Supported hotkeys for selection-capture (plain key, no modifier required)
_CAPTURE_KEYS: dict[str, int] = {
    "Print Screen":  0x2C,
    "Scroll Lock":   0x91,
    "Pause":         0x13,
    "F13":           0x7C,
    "F14":           0x7D,
}
# Supported hotkeys for fullscreen-capture.
# Value: (modifier, vk) or None to disable.
# modifier is "alt", "ctrl", or None (no modifier).
_FULLSCREEN_KEYS: dict[str, object] = {
    "None":                 None,
    "Alt + Print Screen":   ("alt",  0x2C),
    "Alt + Scroll Lock":    ("alt",  0x91),
    "Ctrl + Print Screen":  ("ctrl", 0x2C),
    "F15":                  (None,   0x7E),
    "F16":                  (None,   0x7F),
}

# RegisterHotKey modifier flags (Windows SDK)
_MOD_NOREPEAT = 0x4000          # prevents repeated WM_HOTKEY on key hold
_FS_MOD_FLAGS = {               # fullscreen-key modifier → MOD_* flag
    "alt":  0x0001 | 0x4000,    # MOD_ALT  | MOD_NOREPEAT
    "ctrl": 0x0002 | 0x4000,    # MOD_CTRL | MOD_NOREPEAT
    None:   0x4000,             # no modifier
}


def _load_font(size: int):
    """Return a TrueType font at *size* pts, or None. Tries multiple Windows fonts; never raises."""
    from PIL import ImageFont
    candidates = [
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "segoeui.ttf"),
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arial.ttf"),
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "tahoma.ttf"),
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return None


def save_png(img: Image.Image, folder: str = "") -> str:
    """Save *img* as a lossless PNG using atomic exclusive-create; returns the saved path."""
    if not folder:
        folder = os.path.join(os.path.expanduser("~"), "Pictures", "FreeShot")
    os.makedirs(folder, exist_ok=True)
    ts   = time.strftime("%Y%m%d_%H%M%S")
    base = os.path.join(folder, f"freeshot_{ts}")
    n = 0
    while True:
        suffix = "" if n == 0 else f"_{n}"
        path   = f"{base}{suffix}.png"
        try:
            with open(path, "xb") as f:   # 'x' = exclusive create — no TOCTOU race
                img.save(f, "PNG")
            return path
        except FileExistsError:
            n += 1
            if n > 999:
                raise RuntimeError("save_png: cannot find a unique filename")


# ── Selection + Inline Annotation Overlay ────────────────────────────────────
class SelectionOverlay:
    RECT, FREE = "rect", "free"

    # ── Phase 1 : Selection ───────────────────────────────────────────────────

    def __init__(self, root, shot: Image.Image, done_cb, config: Config):
        self.root    = root
        self.shot    = shot
        self.done_cb = done_cb
        self.cfg     = config
        self.sw, self.sh = shot.size

        self.mode      = self.FREE if config.capture_mode == "free" else self.RECT
        self.selecting = False
        self.sx = self.sy = 0
        self.fpts: list = []

        dim        = Image.new("RGBA", shot.size, (0, 0, 0, 140))
        self._dark = Image.alpha_composite(
            shot.convert("RGBA"), dim).convert("RGB")

        self.win = root
        self.win.geometry(f"{self.sw}x{self.sh}+0+0")
        self.win.attributes("-topmost", True)
        self.win.deiconify()

        self.cv = tk.Canvas(self.win, highlightthickness=0, bd=0,
                            cursor="crosshair")
        self.cv.pack(fill="both", expand=True)

        self._ph_dark = ImageTk.PhotoImage(self._dark)
        self.cv.create_image(0, 0, anchor="nw", image=self._ph_dark, tags="bg")
        self._draw_hints()

        self.cv.bind("<ButtonPress-1>",   self._press)
        self.cv.bind("<B1-Motion>",       self._drag)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.win.bind("<Escape>", self._cancel)
        self.win.bind("<r>",  lambda _: self._set_mode(self.RECT))
        self.win.bind("<R>",  lambda _: self._set_mode(self.RECT))
        self.win.bind("<f>",  lambda _: self._set_mode(self.FREE))
        self.win.bind("<F>",  lambda _: self._set_mode(self.FREE))
        self.win.lift()
        self.win.focus_force()

    def _draw_hints(self):
        self.cv.delete("ui")
        self.cv.create_text(
            self.sw // 2, 28,
            text="Drag to select  ·  R = Rectangle  ·  F = Freehand  ·  Esc = Cancel",
            fill="white", font=("Segoe UI", 11, "bold"), tags="ui"
        )
        bx, by = 14, 60
        for label, m in [("▭  Rectangle  (R)", self.RECT),
                         ("✏  Freehand   (F)", self.FREE)]:
            bg = "#2979FF" if m == self.mode else "#333"
            self.cv.create_rectangle(bx, by, bx + 172, by + 30,
                                     fill=bg, outline="#555555", tags="ui")
            self.cv.create_text(bx + 86, by + 15, text=label,
                                fill="white", font=("Segoe UI", 9), tags="ui")
            by += 38

    def _set_mode(self, mode):
        self.mode = mode
        self.cfg.capture_mode = mode
        self.cfg.save()
        self._draw_hints()

    def _press(self, e):
        self.selecting = True
        self.sx, self.sy = e.x, e.y
        self.fpts = [(e.x, e.y)]
        self.cv.delete("sel")

    def _drag(self, e):
        if not self.selecting:
            return
        self.cv.delete("sel")
        if self.mode == self.RECT:
            x0, y0 = min(self.sx, e.x), min(self.sy, e.y)
            x1, y1 = max(self.sx, e.x), max(self.sy, e.y)
            if x1 - x0 > 0 and y1 - y0 > 0:
                ph = ImageTk.PhotoImage(self.shot.crop((x0, y0, x1, y1)))
                self.cv._si = ph
                self.cv.create_image(x0, y0, anchor="nw",
                                     image=ph, tags="sel")
            self.cv.create_rectangle(x0, y0, x1, y1,
                                     outline="#2979FF", width=2, tags="sel")
            self.cv.create_text(
                (x0 + x1) // 2, max(y0 - 12, 12),
                text=f"{x1 - x0} × {y1 - y0}",
                fill="white", font=("Segoe UI", 9, "bold"), tags="sel")
        else:
            self.fpts.append((e.x, e.y))
            if len(self.fpts) >= 2:
                self.cv.create_line(
                    [c for p in self.fpts for c in p],
                    fill="#2979FF", width=2, smooth=True, tags="sel")

    def _release(self, e):
        if not self.selecting:
            return
        self.selecting = False
        if self.mode == self.RECT:
            x0, y0 = min(self.sx, e.x), min(self.sy, e.y)
            x1, y1 = max(self.sx, e.x), max(self.sy, e.y)
            if x1 - x0 < 5 or y1 - y0 < 5:
                return
            region = self.shot.crop((x0, y0, x1, y1))
        else:
            if len(self.fpts) < 3:
                return
            xs = [p[0] for p in self.fpts]
            ys = [p[1] for p in self.fpts]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
            if x1 - x0 < 5 or y1 - y0 < 5:
                return
            mask = Image.new("L", self.shot.size, 0)
            ImageDraw.Draw(mask).polygon(self.fpts, fill=255)
            out = Image.new("RGBA", self.shot.size)
            out.paste(self.shot.convert("RGBA"), mask=mask)
            region = out.crop((x0, y0, x1, y1))
        if self.cfg.auto_copy_close:
            copy_to_clipboard(region)
            if self.cfg.auto_save:
                try:
                    save_png(region, self.cfg.save_folder)
                except Exception as e:
                    messagebox.showerror("FreeShot – Save Error",
                                         f"Auto-save failed:\n{e}")
            self._close_overlay()
            self.done_cb()
            return
        self._enter_annotation(region, x0, y0, x1, y1)

    def _cancel(self, _=None):
        self._close_overlay()
        self.done_cb()

    def _close_overlay(self):
        for w in self.win.winfo_children():
            w.destroy()
        self.win.withdraw()
        self.shot = self._dark = self._ph_dark = None
        for attr in ("ann_base", "ann_current", "ann_history",
                     "_ann_dark_patch", "_ann_ph"):
            if hasattr(self, attr):
                setattr(self, attr, None)
        gc.collect()

    # ── Phase 2 : Inline annotation ───────────────────────────────────────────

    def _enter_annotation(self, region, x0, y0, x1, y1):
        self.ann_x0, self.ann_y0 = x0, y0
        self.ann_x1, self.ann_y1 = x1, y1
        self.ann_w = x1 - x0
        self.ann_h = y1 - y0

        base = region.convert("RGBA") if region.mode != "RGBA" else region.copy()
        # Upscale to 2× for supersampled drawing; output downscales back to 1×
        base_ss = base.resize(
            (self.ann_w * _SSAA, self.ann_h * _SSAA), Image.LANCZOS)
        self.ann_base    = base_ss
        self.ann_current = base_ss.copy()
        self.ann_history = [base_ss.copy()]

        self.ann_tool      = None
        self.ann_color     = "#ff0000"
        self.ann_thickness = 2
        self.ann_drawing   = False
        self.ann_sx = self.ann_sy = 0
        self.ann_pts: list = []

        # Auto-copy raw selection to clipboard if enabled.
        # No save_png here — the final save happens in _ann_copy so we don't
        # produce a duplicate file when auto_copy + auto_save are both on.
        if self.cfg.auto_copy:
            copy_to_clipboard(base)

        # Crop the dark background to just the selection area, then free the
        # full-screen images — saves ~22 MB during annotation
        self._ann_dark_patch = self._dark.crop(
            (x0, y0, x1, y1)).convert("RGBA")
        self.shot  = None
        self._dark = None
        gc.collect()

        self._ann_region_id = None

        self.cv.unbind("<ButtonPress-1>")
        self.cv.unbind("<B1-Motion>")
        self.cv.unbind("<ButtonRelease-1>")
        self.win.unbind("<r>"); self.win.unbind("<R>")
        self.win.unbind("<f>"); self.win.unbind("<F>")

        self.cv.bind("<ButtonPress-1>",   self._ann_press)
        self.cv.bind("<B1-Motion>",       self._ann_drag)
        self.cv.bind("<ButtonRelease-1>", self._ann_release)
        self.win.bind("<Escape>",    lambda _: self._ann_cancel())
        self.win.bind("<Control-c>", lambda _: self._ann_copy())
        self.win.bind("<Control-s>", lambda _: self._ann_save())
        self.win.bind("<Control-z>", lambda _: self._ann_undo())

        self.cv.delete("ui")
        self.cv.delete("sel")
        self._ann_build_toolbar()
        self._ann_refresh()

    def _ann_build_toolbar(self):
        tools = [
            ("→", "arrow"), ("╱", "line"), ("▭", "rect"),
            ("✏", "pen"),  ("▬", "hl"),   ("T", "text"),
            ("⬛","blur"),  ("⌫", "eraser"),
        ]

        self._tb_frame = tk.Frame(self.win, bg="#1e1e1e", pady=2)
        self._ann_tbtn = {}

        for icon, t in tools:
            b = tk.Button(
                self._tb_frame, text=icon, width=2,
                command=lambda t=t: self._ann_toggle_tool(t),
                bg="#1e1e1e", fg="white", activebackground="#2979FF",
                relief="flat", font=("Segoe UI", 12), cursor="hand2", bd=0
            )
            b.pack(side="left", padx=1)
            self._ann_tbtn[t] = b

        tk.Label(self._tb_frame, text="│",
                 bg="#1e1e1e", fg="#555").pack(side="left", padx=3)

        self._ann_color_btn = tk.Button(
            self._tb_frame, bg=self.ann_color, width=2,
            command=self._ann_pick_color,
            relief="solid", cursor="hand2", bd=2
        )
        self._ann_color_btn.pack(side="left", padx=3, pady=10)

        self._ann_size_v = tk.IntVar(value=2)
        tk.Scale(
            self._tb_frame, from_=1, to=20, orient="horizontal",
            variable=self._ann_size_v, bg="#1e1e1e", fg="white",
            troughcolor="#444", highlightthickness=0,
            length=64, width=8, showvalue=False,
            command=lambda v: setattr(self, "ann_thickness", int(v))
        ).pack(side="left", padx=2)

        tk.Label(self._tb_frame, text="│",
                 bg="#1e1e1e", fg="#555").pack(side="left", padx=3)

        tk.Button(
            self._tb_frame, text="↩", width=2, command=self._ann_undo,
            bg="#1e1e1e", fg="white", activebackground="#444",
            relief="flat", font=("Segoe UI", 12), cursor="hand2", bd=0
        ).pack(side="left", padx=1)

        tk.Label(self._tb_frame, text="│",
                 bg="#1e1e1e", fg="#555").pack(side="left", padx=3)

        tk.Button(
            self._tb_frame, text="📋 Copy", command=self._ann_copy,
            bg="#1565C0", fg="white", activebackground="#1976D2",
            relief="flat", font=("Segoe UI", 9, "bold"),
            cursor="hand2", padx=8
        ).pack(side="left", padx=2, pady=8)

        tk.Button(
            self._tb_frame, text="💾 Save", command=self._ann_save,
            bg="#2e7d32", fg="white", activebackground="#388e3c",
            relief="flat", font=("Segoe UI", 9, "bold"),
            cursor="hand2", padx=8
        ).pack(side="left", padx=2, pady=8)

        tk.Button(
            self._tb_frame, text="✕", command=self._ann_cancel,
            bg="#1e1e1e", fg="#ff5555", activebackground="#333",
            relief="flat", font=("Segoe UI", 12, "bold"),
            cursor="hand2", padx=4
        ).pack(side="left", padx=4)

        tb_y = self.ann_y1 + 6
        if tb_y + 44 > self.sh:
            tb_y = self.ann_y0 - 50
        tb_x = max(0, min(self.ann_x0, self.sw - 480))
        self.cv.create_window(tb_x, tb_y, anchor="nw",
                              window=self._tb_frame, tags="toolbar")
        self._ann_refresh_toolbar()

    # ── Tool toggle ───────────────────────────────────────────────────────────

    def _ann_toggle_tool(self, t):
        if self.ann_tool == t:
            self.ann_tool = None
            self.cv.configure(cursor="arrow")
        else:
            self.ann_tool = t
            cur = {"text": "xterm", "blur": "sizing", "eraser": "dotbox"}
            self.cv.configure(cursor=cur.get(t, "crosshair"))
        self._ann_refresh_toolbar()

    def _ann_refresh_toolbar(self):
        for t, b in self._ann_tbtn.items():
            b.configure(bg="#2979FF" if t == self.ann_tool else "#1e1e1e")

    def _ann_pick_color(self):
        c = colorchooser.askcolor(color=self.ann_color, parent=self.win)
        if c[1]:
            self.ann_color = c[1]
            self._ann_color_btn.configure(bg=self.ann_color)

    def _ann_refresh(self, tmp=None):
        img = (tmp or self.ann_current)
        # Downscale 2× supersampled canvas to screen resolution for display
        img = img.resize((self.ann_w, self.ann_h), Image.LANCZOS)
        bg  = self._ann_dark_patch.copy()
        if img.mode == "RGBA":
            bg.paste(img, (0, 0), img.split()[3])
        else:
            bg.paste(img.convert("RGBA"), (0, 0))
        ImageDraw.Draw(bg).rectangle(
            [0, 0, self.ann_w - 1, self.ann_h - 1],
            outline="#2979FF", width=2)
        self._ann_ph = ImageTk.PhotoImage(bg.convert("RGB"))
        if self._ann_region_id is None:
            self._ann_region_id = self.cv.create_image(
                self.ann_x0, self.ann_y0, anchor="nw",
                image=self._ann_ph, tags="ann_region")
        else:
            self.cv.itemconfig(self._ann_region_id, image=self._ann_ph)

    # ── Annotation mouse events ───────────────────────────────────────────────

    def _ann_press(self, e):
        if self.ann_tool is None:
            return
        x = e.x - self.ann_x0
        y = e.y - self.ann_y0
        if not (0 <= x <= self.ann_w and 0 <= y <= self.ann_h):
            return
        if self.ann_tool == "text":
            self._ann_place_text(x, y)
            return
        self.ann_drawing = True
        self.ann_sx, self.ann_sy = x, y
        self.ann_pts = [(x, y)]

    def _ann_drag(self, e):
        if not self.ann_drawing:
            return
        x = e.x - self.ann_x0
        y = e.y - self.ann_y0
        self.ann_pts.append((x, y))

        if self.ann_tool in ("arrow", "line", "rect", "blur"):
            # Shape tools: redraw preview on a temp copy each event
            tmp = self.ann_current.copy()
            self._ann_draw_shape(ImageDraw.Draw(tmp), x, y)
            self._ann_refresh(tmp)
        elif self.ann_tool == "hl":
            # Highlight needs full redraw on tmp so opacity doesn't stack
            tmp = self.ann_current.copy()
            self._ann_draw_stroke(tmp)
            self._ann_refresh(tmp)
        elif self.ann_tool == "pen":
            # O(1): draw only the last segment directly on ann_current
            if len(self.ann_pts) >= 2:
                ImageDraw.Draw(self.ann_current).line(
                    [(int(px * _SSAA), int(py * _SSAA))
                     for px, py in self.ann_pts[-2:]],
                    fill=self.ann_color,
                    width=self.ann_thickness * _SSAA, joint="curve")
            self._ann_refresh()
        elif self.ann_tool == "eraser":
            # O(1): erase only at the current point
            r = max(6, self.ann_thickness * 4)
            bx0 = max(0, x - r); by0 = max(0, y - r)
            bx1 = min(self.ann_w, x + r); by1 = min(self.ann_h, y + r)
            s = _SSAA
            self.ann_current.paste(
                self.ann_base.crop((bx0*s, by0*s, bx1*s, by1*s)),
                (bx0 * s, by0 * s))
            self._ann_refresh()

    def _ann_release(self, e):
        if not self.ann_drawing:
            return
        self.ann_drawing = False
        x = e.x - self.ann_x0
        y = e.y - self.ann_y0
        changed = True   # assume changed; set False for detected no-ops

        if self.ann_tool in ("arrow", "line", "rect"):
            self._ann_draw_shape(ImageDraw.Draw(self.ann_current), x, y)
            # Zero-length strokes are visual no-ops
            changed = not (self.ann_sx == x and self.ann_sy == y)
        elif self.ann_tool == "pen":
            changed = len(self.ann_pts) >= 2   # drawn incrementally in _ann_drag
        elif self.ann_tool == "hl":
            if len(self.ann_pts) >= 2:
                self._ann_commit_highlight()
            else:
                changed = False
        elif self.ann_tool == "blur":
            bx0 = min(self.ann_sx, x); bx1 = max(self.ann_sx, x)
            by0 = min(self.ann_sy, y); by1 = max(self.ann_sy, y)
            if bx1 - bx0 >= 2 and by1 - by0 >= 2:
                self._ann_commit_blur(x, y)
            else:
                changed = False
        elif self.ann_tool == "eraser":
            changed = len(self.ann_pts) >= 2   # drawn incrementally in _ann_drag

        if changed:
            self.ann_history.append(self.ann_current.copy())
            if len(self.ann_history) > 10:
                self.ann_history.pop(0)
        self.ann_pts = []
        self._ann_refresh()

    # ── Drawing tools ─────────────────────────────────────────────────────────

    def _ann_draw_shape(self, d: ImageDraw.ImageDraw, cx, cy):
        c, w = self.ann_color, self.ann_thickness * _SSAA
        s = _SSAA
        sx, sy   = int(self.ann_sx * s), int(self.ann_sy * s)
        cx2, cy2 = int(cx * s), int(cy * s)
        if self.ann_tool == "arrow":
            if self.ann_sx == cx and self.ann_sy == cy:
                return
            d.line([(sx, sy), (cx2, cy2)], fill=c, width=w)
            ang = math.atan2(cy - self.ann_sy, cx - self.ann_sx)
            hs  = max(12 * s, w * 5)
            for da in (0.42, -0.42):
                ax = cx2 + hs * math.cos(ang + math.pi + da)
                ay = cy2 + hs * math.sin(ang + math.pi + da)
                d.line([(cx2, cy2), (int(ax), int(ay))], fill=c, width=w)
        elif self.ann_tool == "line":
            d.line([(sx, sy), (cx2, cy2)], fill=c, width=w)
        elif self.ann_tool == "rect":
            x0, y0 = min(sx, cx2), min(sy, cy2)
            x1, y1 = max(sx, cx2), max(sy, cy2)
            d.rectangle([x0, y0, x1, y1], outline=c, width=w)
        elif self.ann_tool == "blur":
            # Live preview: show the selection area with a highlight outline
            x0, y0 = min(sx, cx2), min(sy, cy2)
            x1, y1 = max(sx, cx2), max(sy, cy2)
            d.rectangle([x0, y0, x1, y1], outline="#00BFFF", width=2 * s)

    def _ann_draw_stroke(self, img: Image.Image):
        if len(self.ann_pts) < 2:
            return
        d    = ImageDraw.Draw(img)
        pts2 = [(int(px * _SSAA), int(py * _SSAA)) for px, py in self.ann_pts]
        if self.ann_tool == "pen":
            d.line(pts2, fill=self.ann_color,
                   width=self.ann_thickness * _SSAA, joint="curve")
        elif self.ann_tool == "hl":
            d.line(pts2,
                   fill=hex_rgba(self.ann_color, 90),
                   width=max(14 * _SSAA, self.ann_thickness * 7 * _SSAA))

    def _ann_commit_highlight(self):
        if len(self.ann_pts) < 2:
            return
        ov   = Image.new("RGBA", self.ann_current.size, (0, 0, 0, 0))
        pts2 = [(int(px * _SSAA), int(py * _SSAA)) for px, py in self.ann_pts]
        ImageDraw.Draw(ov).line(
            pts2,
            fill=hex_rgba(self.ann_color, 90),
            width=max(14 * _SSAA, self.ann_thickness * 7 * _SSAA))
        self.ann_current = Image.alpha_composite(
            self.ann_current.convert("RGBA"), ov)

    def _ann_commit_blur(self, cx, cy):
        x0, y0 = min(self.ann_sx, cx), min(self.ann_sy, cy)
        x1, y1 = max(self.ann_sx, cx), max(self.ann_sy, cy)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return
        s = _SSAA
        patch = self.ann_current.crop((x0*s, y0*s, x1*s, y1*s))
        self.ann_current.paste(
            patch.filter(ImageFilter.GaussianBlur(radius=10 * s)), (x0*s, y0*s))

    def _ann_place_text(self, x, y):
        dlg = tk.Toplevel(self.win)
        dlg.overrideredirect(True)
        dlg.attributes("-topmost", True)
        dlg.configure(bg="#111")
        rx = self.win.winfo_rootx() + self.ann_x0 + x
        ry = self.win.winfo_rooty() + self.ann_y0 + y
        dlg.geometry(f"+{rx}+{ry}")
        fs = max(12, self.ann_thickness * 4)
        e  = tk.Entry(dlg, font=("Segoe UI", fs), fg=self.ann_color,
                      bg="#111", insertbackground=self.ann_color,
                      relief="flat", width=22, bd=4)
        e.pack()
        e.focus_set()

        def commit(_=None):
            txt = e.get().strip()
            if txt:
                d = ImageDraw.Draw(self.ann_current)
                fnt = _load_font(fs * _SSAA)
                d.text((int(x * _SSAA), int(y * _SSAA)), txt, fill=self.ann_color, font=fnt)
                self.ann_history.append(self.ann_current.copy())
                if len(self.ann_history) > 10:       # ← fix: apply same cap
                    self.ann_history.pop(0)
                self._ann_refresh()
            dlg.destroy()

        e.bind("<Return>", commit)
        e.bind("<Escape>", lambda _: dlg.destroy())

    def _ann_undo(self):
        if len(self.ann_history) > 1:
            self.ann_history.pop()
            self.ann_current = self.ann_history[-1].copy()
            self._ann_refresh()

    # ── Actions ───────────────────────────────────────────────────────────────

    def _ann_copy(self):
        final = self.ann_current.resize((self.ann_w, self.ann_h), Image.LANCZOS)
        copy_to_clipboard(final)
        if self.cfg.auto_save:
            try:
                save_png(final, self.cfg.save_folder)
            except Exception as e:
                # Tell the user — don't silently discard a failed save
                messagebox.showerror(
                    "FreeShot — Save Failed",
                    f"Could not auto-save screenshot:\n{e}",
                    parent=self.win)
        self._close_overlay()
        self.done_cb()

    def _ann_save(self):
        path = filedialog.asksaveasfilename(
            parent=self.win, defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg *.jpeg"), ("BMP", "*.bmp")],
            initialfile=f"freeshot_{int(time.time())}.png"
        )
        if not path:
            return
        ext = os.path.splitext(path)[1].lower()
        fmt = _SAVE_EXTS.get(ext, "PNG")        # explicit format — never inferred
        if ext not in _SAVE_EXTS:               # user typed unknown extension
            path += ".png"
            fmt   = "PNG"
        img = self.ann_current.resize((self.ann_w, self.ann_h), Image.LANCZOS)
        if fmt == "JPEG" and img.mode == "RGBA":
            img = img.convert("RGB")
        try:
            img.save(path, fmt)
        except Exception as e:
            print(f"[FreeShot] save error: {e}", file=sys.stderr)
            messagebox.showerror(
                "FreeShot — Save Failed",
                f"Could not save to:\n{path}\n\n{e}",
                parent=self.win)
            return   # keep overlay open so user can try a different path
        self._close_overlay()
        self.done_cb()

    def _ann_cancel(self):
        self._close_overlay()
        self.done_cb()


# ── Settings window ───────────────────────────────────────────────────────────
class SettingsWindow:
    """Non-modal settings dialog.  All changes apply immediately."""

    def __init__(self, parent: tk.Tk, cfg, on_change):
        self.cfg       = cfg
        self.on_change = on_change

        win = tk.Toplevel(parent)
        win.title("FreeShot Settings")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = 380, 400
        win.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
        self.win = win

        pad = {"padx": 16, "pady": 4}

        # ── Capture behaviour ────────────────────────────────────────────────
        tk.Label(win, text="Capture behaviour",
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16, pady=(12, 2))

        self._var_ac  = tk.BooleanVar(value=cfg.auto_copy)
        self._var_acc = tk.BooleanVar(value=cfg.auto_copy_close)
        self._var_as  = tk.BooleanVar(value=cfg.auto_save)

        tk.Checkbutton(win, text="Auto-copy on capture",
                       variable=self._var_ac,
                       command=self._toggle_auto_copy).pack(anchor="w", **pad)
        tk.Checkbutton(win, text="Auto copy & close",
                       variable=self._var_acc,
                       command=self._toggle_auto_copy_close).pack(anchor="w", **pad)
        tk.Checkbutton(win, text="Auto-save PNG",
                       variable=self._var_as,
                       command=self._toggle_auto_save).pack(anchor="w", **pad)

        # ── Capture mode ─────────────────────────────────────────────────────
        tk.Label(win, text="Capture mode",
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16, pady=(10, 2))

        self._var_mode = tk.StringVar(value=cfg.capture_mode)
        mf = tk.Frame(win)
        mf.pack(anchor="w", padx=16)
        tk.Radiobutton(mf, text="Rectangle", variable=self._var_mode, value="rect",
                       command=self._change_mode).pack(side="left", padx=(0, 16))
        tk.Radiobutton(mf, text="Freehand",  variable=self._var_mode, value="free",
                       command=self._change_mode).pack(side="left")

        # ── Save folder ───────────────────────────────────────────────────────
        tk.Label(win, text="Save folder",
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16, pady=(10, 2))

        ff = tk.Frame(win)
        ff.pack(fill="x", padx=16, pady=(0, 4))
        self._var_folder = tk.StringVar(
            value=cfg.save_folder or os.path.join(
                os.path.expanduser("~"), "Pictures", "FreeShot"))
        tk.Entry(ff, textvariable=self._var_folder,
                 state="readonly", width=32).pack(side="left", fill="x", expand=True)
        tk.Button(ff, text="Browse…",
                  command=self._pick_folder).pack(side="left", padx=(6, 0))

        # ── Hotkeys ───────────────────────────────────────────────────────────
        tk.Label(win, text="Hotkeys",
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16, pady=(10, 2))

        hf = tk.Frame(win)
        hf.pack(fill="x", padx=16, pady=(0, 4))

        tk.Label(hf, text="Capture:").grid(row=0, column=0, sticky="w", pady=2)
        self._var_cap_key = tk.StringVar(value=cfg.capture_key)
        cap_cb = ttk.Combobox(hf, textvariable=self._var_cap_key,
                              values=list(_CAPTURE_KEYS.keys()),
                              state="readonly", width=20)
        cap_cb.grid(row=0, column=1, sticky="w", padx=(8, 0), pady=2)
        cap_cb.bind("<<ComboboxSelected>>", lambda _: self._change_capture_key())

        tk.Label(hf, text="Fullscreen:").grid(row=1, column=0, sticky="w", pady=2)
        self._var_fs_key = tk.StringVar(value=cfg.fullscreen_key)
        fs_cb = ttk.Combobox(hf, textvariable=self._var_fs_key,
                             values=list(_FULLSCREEN_KEYS.keys()),
                             state="readonly", width=20)
        fs_cb.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=2)
        fs_cb.bind("<<ComboboxSelected>>", lambda _: self._change_fullscreen_key())

        # ── System ────────────────────────────────────────────────────────────
        tk.Label(win, text="System",
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16, pady=(10, 2))

        self._var_startup = tk.BooleanVar(value=_read_startup())
        tk.Checkbutton(win, text="Start with Windows",
                       variable=self._var_startup,
                       command=self._toggle_startup).pack(anchor="w", **pad)

        # ── Close ─────────────────────────────────────────────────────────────
        tk.Button(win, text="Close", command=win.destroy,
                  width=10).pack(pady=(12, 14))
        win.protocol("WM_DELETE_WINDOW", win.destroy)

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _toggle_auto_copy(self):
        self.cfg.auto_copy = self._var_ac.get()
        if self.cfg.auto_copy:
            self.cfg.auto_copy_close = False
            self._var_acc.set(False)
        self.cfg.save()
        self.on_change()

    def _toggle_auto_copy_close(self):
        self.cfg.auto_copy_close = self._var_acc.get()
        if self.cfg.auto_copy_close:
            self.cfg.auto_copy = False
            self._var_ac.set(False)
        self.cfg.save()
        self.on_change()

    def _toggle_auto_save(self):
        self.cfg.auto_save = self._var_as.get()
        self.cfg.save()
        self.on_change()

    def _change_capture_key(self):
        self.cfg.capture_key = self._var_cap_key.get()
        self.cfg.save()
        self.on_change()   # rebuilds tray label

    def _change_fullscreen_key(self):
        self.cfg.fullscreen_key = self._var_fs_key.get()
        self.cfg.save()

    def _change_mode(self):
        self.cfg.capture_mode = self._var_mode.get()
        self.cfg.save()
        self.on_change()

    def _pick_folder(self):
        init = self.cfg.save_folder or os.path.join(os.path.expanduser("~"), "Pictures")
        folder = filedialog.askdirectory(
            title="FreeShot — choose save folder", initialdir=init, parent=self.win)
        if folder:
            validated = _validate_save_folder(folder)
            if not validated:
                messagebox.showerror(
                    "FreeShot — Invalid Folder",
                    "Please choose a folder inside your home directory.\n"
                    "Network and system paths are not supported.",
                    parent=self.win)
                return
            self.cfg.save_folder = validated
            self._var_folder.set(validated)
            self.cfg.save()
            self.on_change()

    def _toggle_startup(self):
        desired = self._var_startup.get()
        if not _write_startup(desired):
            self._var_startup.set(not desired)   # revert on failure
            messagebox.showerror(
                "FreeShot — Registry Error",
                "Could not update the Windows startup entry.\n"
                "Try running FreeShot as administrator once to set this option.",
                parent=self.win)


# ── Main App ──────────────────────────────────────────────────────────────────
class FreeShotApp:

    def __init__(self):
        # Make the process DPI-aware so Tkinter's winfo_screenwidth/height()
        # returns physical pixels — matching ImageGrab.grab() exactly,
        # which eliminates the quality-degrading resize on HiDPI displays.
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)  # PER_MONITOR_V2
        except Exception:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)      # PER_MONITOR
            except Exception:
                pass

        self.cfg  = Config()
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.withdraw()
        self._active    = False
        self._capture_q = queue.Queue(maxsize=1)   # bounded: drop if a capture is already pending

        self._hotkey_tid = 0
        self._setup_tray()
        self._setup_hotkeys()
        # Poll the capture queue every 50 ms — safe alternative to
        # event_generate() which is not guaranteed across foreign threads
        self.root.after(50, self._poll_capture_queue)

    def _poll_capture_queue(self):
        try:
            while not self._capture_q.empty():
                val = self._capture_q.get_nowait()
                if val == 2:
                    self._trigger_fullscreen()
                else:
                    self._trigger()
        except Exception as e:
            # Log but never crash — the after() below must always re-arm
            print(f"[FreeShot] poll_capture_queue: {e}", file=sys.stderr)
        self.root.after(50, self._poll_capture_queue)

    def _setup_tray(self):
        self._rebuild_tray(_make_pentagram_icon(64))

    def _rebuild_tray(self, img=None):
        if img is None:
            img = self._icon.icon
        menu = pystray.Menu(
            item(f"📷  Capture  ({self.cfg.capture_key})", self._tray_trigger, default=True),
            pystray.Menu.SEPARATOR,
            item("⚙  Settings…", self._open_settings),
            item("Exit", self._quit)
        )
        if hasattr(self, "_icon"):
            self._icon.menu = menu
        else:
            self._icon = pystray.Icon("FreeShot", img, "FreeShot", menu)
            threading.Thread(target=self._icon.run, daemon=True).start()

    def _toggle_auto_copy(self, *_):
        self.cfg.auto_copy = not self.cfg.auto_copy
        if self.cfg.auto_copy:
            self.cfg.auto_copy_close = False   # mutually exclusive
        self.cfg.save()
        self._rebuild_tray()

    def _toggle_auto_copy_close(self, *_):
        self.cfg.auto_copy_close = not self.cfg.auto_copy_close
        if self.cfg.auto_copy_close:
            self.cfg.auto_copy = False         # mutually exclusive
        self.cfg.save()
        self._rebuild_tray()

    def _toggle_auto_save(self, *_):
        self.cfg.auto_save = not self.cfg.auto_save
        self.cfg.save()
        self._rebuild_tray()

    def _pick_save_folder(self, *_):
        self.root.after(0, self._do_pick_save_folder)

    def _do_pick_save_folder(self):
        init = self.cfg.save_folder or os.path.join(
            os.path.expanduser("~"), "Pictures")
        folder = filedialog.askdirectory(
            title="FreeShot — choose save folder", initialdir=init)
        if folder:
            self.cfg.save_folder = folder
            self.cfg.save()
            self._rebuild_tray()

    def _open_settings(self, *_):
        self.root.after(0, self._do_open_settings)

    def _do_open_settings(self):
        if hasattr(self, "_settings_win") and self._settings_win.win.winfo_exists():
            self._settings_win.win.lift()
            self._settings_win.win.focus_force()
            return
        self._settings_win = SettingsWindow(self.root, self.cfg, self._on_settings_change)

    def _setup_hotkeys(self):
        """Register system-wide hotkeys via RegisterHotKey.
        Unlike WH_KEYBOARD_LL there is no per-keypress Python callback, so the
        Python GIL is never held across normal typing — no keyboard freeze.
        Queue values: 1 = selection capture, 2 = fullscreen capture."""
        _q   = self._capture_q
        _cfg = self.cfg

        def _thread():
            try:
                import ctypes as ct
                import ctypes.wintypes as wt

                WM_HOTKEY = 0x0312
                u32       = ct.windll.user32
                u32.GetMessageW.argtypes = [ct.c_void_p, ct.c_void_p,
                                            ct.c_uint, ct.c_uint]

                # Store thread-id so _update_hotkeys can send WM_QUIT
                self._hotkey_tid = ct.windll.kernel32.GetCurrentThreadId()

                cap_vk   = _CAPTURE_KEYS.get(_cfg.capture_key, 0x2C)
                fs_entry = _FULLSCREEN_KEYS.get(_cfg.fullscreen_key)

                registered = []
                if u32.RegisterHotKey(None, 1, _MOD_NOREPEAT, cap_vk):
                    registered.append(1)
                else:
                    print(f"[FreeShot] RegisterHotKey(capture) failed "
                          f"err={ct.windll.kernel32.GetLastError()}", file=sys.stderr)

                if fs_entry is not None:
                    fs_mod, fs_vk = fs_entry
                    mod = _FS_MOD_FLAGS.get(fs_mod, _MOD_NOREPEAT)
                    if u32.RegisterHotKey(None, 2, mod, fs_vk):
                        registered.append(2)

                msg = wt.MSG()
                while u32.GetMessageW(ct.byref(msg), None, 0, 0) != 0:
                    if msg.message == WM_HOTKEY:
                        try:
                            if msg.wParam == 1:
                                _q.put_nowait(1)
                            elif msg.wParam == 2:
                                _q.put_nowait(2)
                        except queue.Full:
                            pass

                for hid in registered:
                    u32.UnregisterHotKey(None, hid)
            except Exception as e:
                print(f"[FreeShot] hotkey thread: {e}", file=sys.stderr)
            finally:
                self._hotkey_tid = 0

        threading.Thread(target=_thread, daemon=True).start()

    def _update_hotkeys(self):
        """Stop the current hotkey thread and restart with updated key config."""
        tid = getattr(self, '_hotkey_tid', 0)
        if tid:
            ctypes.windll.user32.PostThreadMessageW(tid, 0x0012, 0, 0)  # WM_QUIT
        # Brief delay lets the old thread unregister before the new one registers
        self.root.after(150, self._setup_hotkeys)

    def _on_settings_change(self, img=None):
        """Tray rebuild + hotkey re-registration, called on any settings save."""
        self._rebuild_tray(img)
        self._update_hotkeys()

    def _tray_trigger(self, icon=None, item=None):
        self.root.after(150, self._trigger)

    def _trigger(self):
        if self._active:
            return
        self._active = True
        self.root.after(400, self._capture)

    def _capture(self):
        try:
            shot = ImageGrab.grab()
            lw = self.root.winfo_screenwidth()
            lh = self.root.winfo_screenheight()
            if shot.size != (lw, lh):
                # Fallback only — should not be needed after SetProcessDpiAwarenessContext
                shot = shot.resize((lw, lh), Image.LANCZOS)
            SelectionOverlay(self.root, shot, self._on_done, self.cfg)
        except Exception as e:
            # Always release the lock — without this, every subsequent
            # hotkey press is silently ignored until the app is restarted
            self._active = False
            print(f"[FreeShot] capture failed: {e}", file=sys.stderr)

    def _trigger_fullscreen(self):
        if self._active:
            return
        self._active = True
        self.root.after(200, self._capture_fullscreen)

    def _capture_fullscreen(self):
        try:
            shot = ImageGrab.grab()
            lw = self.root.winfo_screenwidth()
            lh = self.root.winfo_screenheight()
            if shot.size != (lw, lh):
                shot = shot.resize((lw, lh), Image.LANCZOS)
            copy_to_clipboard(shot)
            if self.cfg.auto_save:
                try:
                    save_png(shot, self.cfg.save_folder)
                except Exception as e:
                    print(f"[FreeShot] fullscreen save error: {e}", file=sys.stderr)
        except Exception as e:
            print(f"[FreeShot] fullscreen capture failed: {e}", file=sys.stderr)
        finally:
            self._active = False

    def _on_done(self):
        self._active = False
        self.root.withdraw()

    def _quit(self, *_):
        self._icon.stop()
        self.root.quit()
        sys.exit(0)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    import traceback as _tb
    try:
        FreeShotApp().run()
    except Exception:
        # In --windowed EXE builds stderr is suppressed; write the crash
        # to %APPDATA%\FreeShot\error.log so it is not silently discarded
        try:
            os.makedirs(_APP_DIR, exist_ok=True)
            with open(_ERROR_LOG, "a", encoding="utf-8") as _f:
                import datetime as _dt
                _f.write(f"\n=== {_dt.datetime.now().isoformat()} ===\n")
                _f.write(_tb.format_exc())
        except Exception:
            pass   # last-resort: if we can't even write the log, give up quietly

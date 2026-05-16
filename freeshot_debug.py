#!/usr/bin/env python3
"""FreeShot — DEBUG BUILD  (logs to %APPDATA%\\FreeShot\\freeshot_debug.log)"""

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox
import threading, sys, math, time, traceback, queue, re, stat, ctypes, ctypes.wintypes as _wt, json, os, gc, winreg
from pathlib import Path
from io import BytesIO
from PIL import Image, ImageDraw, ImageFilter, ImageGrab, ImageTk
import pystray
from pystray import MenuItem as item
from pynput import keyboard as kb

# ── Logger ────────────────────────────────────────────────────────────────────
import logging
_LOG_DIR  = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "FreeShot")
_LOG_PATH = os.path.join(_LOG_DIR, "freeshot_debug.log")
os.makedirs(_LOG_DIR, exist_ok=True)
# %APPDATA% NTFS ACLs already restrict access to the current user.
# os.chmod adds belt-and-suspenders on systems that honour Unix modes.
if not os.path.exists(_LOG_PATH):
    open(_LOG_PATH, "w").close()
try:
    os.chmod(_LOG_PATH, stat.S_IRUSR | stat.S_IWUSR)   # 0o600 — owner read/write only
except OSError:
    pass
logging.basicConfig(
    filename=_LOG_PATH, filemode="w", level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S"
)
def log(msg):
    logging.info(msg)
    print(msg, flush=True)

log("=== FreeShot DEBUG START ===")
log(f"Python {sys.version}")
log(f"PID {os.getpid()}")

# ── Config ────────────────────────────────────────────────────────────────────
# %APPDATA%\FreeShot\ so it works even from write-protected locations
_APP_DIR     = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "FreeShot")
_CONFIG_PATH = os.path.join(_APP_DIR, "config.json")
log(f"config path = {_CONFIG_PATH}")

class Config:
    def __init__(self):
        self.auto_copy        = False
        self.auto_copy_close  = False
        self.capture_mode     = "rect"  # "rect" or "free"
        self.auto_save        = True
        self.save_folder      = ""      # empty = ~/Pictures/FreeShot
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
            log(f"  config loaded  auto_copy={self.auto_copy}  auto_copy_close={self.auto_copy_close}  "
                f"capture_mode={self.capture_mode}  auto_save={self.auto_save}  "
                f"save_folder={self.save_folder!r}")
        except Exception as e:
            log(f"  config load failed ({e}), using defaults")

    def save(self):
        try:
            os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
            with open(_CONFIG_PATH, "w") as f:
                json.dump({"auto_copy":       self.auto_copy,
                           "auto_copy_close": self.auto_copy_close,
                           "capture_mode":    self.capture_mode,
                           "auto_save":       self.auto_save,
                           "save_folder":     self.save_folder}, f, indent=2)
            log(f"  config saved  auto_save={self.auto_save}  save_folder={self.save_folder!r}")
        except Exception as e:
            log(f"  config save failed: {e}")


# ── Icon ──────────────────────────────────────────────────────────────────────
def _make_pentagram_icon(size=64):
    # ImageFilter already imported at top — no duplicate import needed
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
        try:                                    # fix: always close clipboard
            win32clipboard.EmptyClipboard()
            if img.mode == "RGBA":
                png_fmt = win32clipboard.RegisterClipboardFormat("PNG")
                png_buf = BytesIO()
                img.save(png_buf, "PNG")
                win32clipboard.SetClipboardData(png_fmt, png_buf.getvalue())
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[3])
                bmp_buf = BytesIO()
                bg.save(bmp_buf, "BMP")
                win32clipboard.SetClipboardData(win32clipboard.CF_DIB,
                                                bmp_buf.getvalue()[14:])
                log("  clipboard: copied OK (RGBA — PNG + BMP fallback)")
            else:
                bmp_buf = BytesIO()
                img.convert("RGB").save(bmp_buf, "BMP")
                win32clipboard.SetClipboardData(win32clipboard.CF_DIB,
                                                bmp_buf.getvalue()[14:])
                log("  clipboard: copied OK (RGB — BMP)")
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception as e:
        log(f"  clipboard error: {e}")
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
        log(f"  registry write failed: {e}")
        print(f"[FreeShot] registry write failed: {e}", file=sys.stderr)
        return False


_SAVE_EXTS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".bmp": "BMP"}


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
            log(f"  saved PNG → {path}")
            return path
        except FileExistsError:
            n += 1
            if n > 999:
                raise RuntimeError("save_png: cannot find a unique filename")


# ── Selection + Inline Annotation Overlay ────────────────────────────────────
class SelectionOverlay:
    RECT, FREE = "rect", "free"

    def __init__(self, root, shot: Image.Image, done_cb, config: Config):
        log(f"SelectionOverlay.__init__  shot={shot.size}")
        self.root    = root
        self.shot    = shot
        self.done_cb = done_cb
        self.cfg     = config
        self.sw, self.sh = shot.size

        self.mode      = self.FREE if config.capture_mode == "free" else self.RECT
        self.selecting = False
        self.sx = self.sy = self.cx = self.cy = 0
        self.fpts: list = []

        dim        = Image.new("RGBA", shot.size, (0, 0, 0, 140))
        self._dark = Image.alpha_composite(
            shot.convert("RGBA"), dim).convert("RGB")

        self.win = root
        log(f"  using root window  id={root.winfo_id()}")
        self.win.geometry(f"{self.sw}x{self.sh}+0+0")
        log(f"  geometry set to {self.sw}x{self.sh}+0+0")
        self.win.attributes("-topmost", True)
        self.win.deiconify()
        log("  deiconify()")

        self.cv = tk.Canvas(self.win, highlightthickness=0, bd=0, cursor="crosshair")
        self.cv.pack(fill="both", expand=True)
        log(f"  canvas packed  cv_id={self.cv.winfo_id()}")

        self._ph_dark = ImageTk.PhotoImage(self._dark)
        self.cv.create_image(0, 0, anchor="nw", image=self._ph_dark, tags="bg")
        self._draw_hints()

        self.cv.bind("<ButtonPress-1>",   self._press)
        self.cv.bind("<B1-Motion>",       self._drag)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.win.bind("<Escape>", self._cancel)
        self.win.bind("<r>",      lambda _: self._set_mode(self.RECT))
        self.win.bind("<R>",      lambda _: self._set_mode(self.RECT))
        self.win.bind("<f>",      lambda _: self._set_mode(self.FREE))
        self.win.bind("<F>",      lambda _: self._set_mode(self.FREE))
        self.win.bind("<Key>",         lambda e: log(f"  KEY   keysym={e.keysym}"))
        self.win.bind("<ButtonPress>", lambda e: log(f"  BTN   x={e.x} y={e.y} num={e.num}"))
        log("  bindings registered")

        self.win.update()
        log(f"  after update  winfo_viewable={self.win.winfo_viewable()}  "
            f"winfo_ismapped={self.win.winfo_ismapped()}")

        self._report_focus("before focus_force")
        self.win.lift()
        self.win.focus_force()
        self.win.update()
        self._report_focus("after focus_force")

    def _report_focus(self, label):
        try:
            u32 = ctypes.windll.user32
            fg_hwnd  = u32.GetForegroundWindow()
            win_hwnd = self.win.winfo_id()
            cv_hwnd  = self.cv.winfo_id()
            focus_wid = self.win.focus_get()
            log(f"  [{label}]  fg_hwnd={fg_hwnd:#x}  win_hwnd={win_hwnd:#x}  "
                f"cv_hwnd={cv_hwnd:#x}  tk_focus={focus_wid}")
        except Exception as e:
            log(f"  _report_focus error: {e}")

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
        log(f"  mode → {mode}")
        self.mode = mode
        self.cfg.capture_mode = mode
        self.cfg.save()
        self._draw_hints()

    def _press(self, e):
        log(f"  _press  x={e.x} y={e.y}")
        self.selecting = True
        self.sx, self.sy = e.x, e.y
        self.cx, self.cy = e.x, e.y
        self.fpts = [(e.x, e.y)]
        self.cv.delete("sel")

    def _drag(self, e):
        if not self.selecting:
            return
        self.cx, self.cy = e.x, e.y
        self.cv.delete("sel")
        if self.mode == self.RECT:
            x0, y0 = min(self.sx, e.x), min(self.sy, e.y)
            x1, y1 = max(self.sx, e.x), max(self.sy, e.y)
            if x1 - x0 > 0 and y1 - y0 > 0:
                ph = ImageTk.PhotoImage(self.shot.crop((x0, y0, x1, y1)))
                self.cv._si = ph
                self.cv.create_image(x0, y0, anchor="nw", image=ph, tags="sel")
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
        log(f"  _release  x={e.x} y={e.y}  selecting={self.selecting}")
        if not self.selecting:
            return
        self.selecting = False
        if self.mode == self.RECT:
            x0, y0 = min(self.sx, e.x), min(self.sy, e.y)
            x1, y1 = max(self.sx, e.x), max(self.sy, e.y)
            log(f"  rect region {x0},{y0} → {x1},{y1}  size={x1-x0}×{y1-y0}")
            if x1 - x0 < 5 or y1 - y0 < 5:
                log("  region too small, ignoring")
                return
            region = self.shot.crop((x0, y0, x1, y1))
        else:
            if len(self.fpts) < 3:
                return
            xs = [p[0] for p in self.fpts]
            ys = [p[1] for p in self.fpts]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
            log(f"  freehand region {x0},{y0} → {x1},{y1}  size={x1-x0}×{y1-y0}  pts={len(self.fpts)}")
            if x1 - x0 < 5 or y1 - y0 < 5:
                return
            mask = Image.new("L", self.shot.size, 0)
            ImageDraw.Draw(mask).polygon(self.fpts, fill=255)
            out = Image.new("RGBA", self.shot.size)
            out.paste(self.shot.convert("RGBA"), mask=mask)
            region = out.crop((x0, y0, x1, y1))
        log("  entering annotation phase")
        if self.cfg.auto_copy_close:
            log("  auto_copy_close: copying and closing immediately")
            copy_to_clipboard(region)
            if self.cfg.auto_save:
                try:
                    save_png(region, self.cfg.save_folder)
                    log("  auto_copy_close save: OK")
                except Exception as e:
                    log(f"  auto_copy_close save error: {e}")
                    messagebox.showerror("FreeShot – Save Error",
                                         f"Auto-save failed:\n{e}")
            self._close_overlay()
            self.done_cb()
            return
        self._enter_annotation(region, x0, y0, x1, y1)

    def _cancel(self, _=None):
        log("  _cancel (Esc)")
        self._close_overlay()
        self.done_cb()

    def _close_overlay(self):
        log("SelectionOverlay._close_overlay")
        for w in self.win.winfo_children():
            w.destroy()
        self.win.withdraw()
        self.shot = self._dark = self._ph_dark = None
        for attr in ("ann_base", "ann_current", "ann_history",
                     "_ann_dark_patch", "_ann_ph"):
            if hasattr(self, attr):
                setattr(self, attr, None)
        gc.collect()
        log("  memory released + gc.collect() done")

    # ── Phase 2 : Inline annotation ───────────────────────────────────────────

    def _enter_annotation(self, region, x0, y0, x1, y1):
        log(f"  _enter_annotation  region_size={region.size}  pos=({x0},{y0})-({x1},{y1})")
        self.ann_x0, self.ann_y0 = x0, y0
        self.ann_x1, self.ann_y1 = x1, y1
        self.ann_w = x1 - x0
        self.ann_h = y1 - y0

        base = region.convert("RGBA") if region.mode != "RGBA" else region.copy()
        self.ann_base    = base
        self.ann_current = base.copy()
        self.ann_history = [base.copy()]

        self.ann_tool      = None
        self.ann_color     = "#ff0000"
        self.ann_thickness = 2
        self.ann_drawing   = False
        self.ann_sx = self.ann_sy = 0
        self.ann_pts: list = []
        log("  ann_tool initialized to None (no tool active until user clicks)")

        # Auto-copy raw selection — no save_png here to avoid duplicate file
        # when auto_copy + auto_save are both on (final save happens in _ann_copy)
        if self.cfg.auto_copy:
            log("  auto_copy enabled — copying raw selection to clipboard")
            copy_to_clipboard(base)

        self._ann_dark_patch = self._dark.crop(
            (x0, y0, x1, y1)).convert("RGBA")
        self.shot  = None
        self._dark = None
        gc.collect()
        log(f"  full-screen images freed, dark patch cropped {self.ann_w}x{self.ann_h}")

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
        log(f"  toolbar placed at ({tb_x},{tb_y})")

    def _ann_toggle_tool(self, t):
        if self.ann_tool == t:
            log(f"  tool toggle: deactivating '{t}'")
            self.ann_tool = None
            self.cv.configure(cursor="arrow")
        else:
            log(f"  tool toggle: activating '{t}'")
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
            log(f"  color changed to {self.ann_color}")

    def _ann_refresh(self, tmp=None):
        img = (tmp or self.ann_current)
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
            log(f"  ann_region canvas item created at ({self.ann_x0},{self.ann_y0})")
        else:
            self.cv.itemconfig(self._ann_region_id, image=self._ann_ph)

    def _ann_press(self, e):
        if self.ann_tool is None:
            log("  ann_press ignored (no tool active)")
            return
        x = e.x - self.ann_x0
        y = e.y - self.ann_y0
        if not (0 <= x <= self.ann_w and 0 <= y <= self.ann_h):
            return
        log(f"  ann_press  tool={self.ann_tool}  x={x} y={y}")
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
            tmp = self.ann_current.copy()
            self._ann_draw_shape(ImageDraw.Draw(tmp), x, y)
            self._ann_refresh(tmp)
        elif self.ann_tool == "hl":
            tmp = self.ann_current.copy()
            self._ann_draw_stroke(tmp)
            self._ann_refresh(tmp)
        elif self.ann_tool == "pen":
            # O(1): draw only the last segment directly on ann_current
            if len(self.ann_pts) >= 2:
                ImageDraw.Draw(self.ann_current).line(
                    self.ann_pts[-2:], fill=self.ann_color,
                    width=self.ann_thickness, joint="curve")
            self._ann_refresh()
        elif self.ann_tool == "eraser":
            # O(1): erase only at the current point
            r = max(6, self.ann_thickness * 4)
            bx0 = max(0, x - r); by0 = max(0, y - r)
            bx1 = min(self.ann_w, x + r); by1 = min(self.ann_h, y + r)
            self.ann_current.paste(
                self.ann_base.crop((bx0, by0, bx1, by1)), (bx0, by0))
            self._ann_refresh()

    def _ann_release(self, e):
        if not self.ann_drawing:
            return
        self.ann_drawing = False
        x = e.x - self.ann_x0
        y = e.y - self.ann_y0
        log(f"  ann_release  tool={self.ann_tool}  x={x} y={y}")
        changed = True

        if self.ann_tool in ("arrow", "line", "rect"):
            self._ann_draw_shape(ImageDraw.Draw(self.ann_current), x, y)
            changed = not (self.ann_sx == x and self.ann_sy == y)
        elif self.ann_tool == "pen":
            changed = len(self.ann_pts) >= 2
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
                log("  blur area too small — skipping undo entry")
        elif self.ann_tool == "eraser":
            changed = len(self.ann_pts) >= 2

        if changed:
            self.ann_history.append(self.ann_current.copy())
            if len(self.ann_history) > 10:
                self.ann_history.pop(0)
            log(f"  history appended  len={len(self.ann_history)}")
        else:
            log("  no-op — undo history unchanged")
        self.ann_pts = []
        self._ann_refresh()

    # ── Drawing tools ─────────────────────────────────────────────────────────

    def _ann_draw_shape(self, d: ImageDraw.ImageDraw, cx, cy):
        c, w = self.ann_color, self.ann_thickness
        if self.ann_tool == "arrow":
            if self.ann_sx == cx and self.ann_sy == cy:
                return
            d.line([(self.ann_sx, self.ann_sy), (cx, cy)], fill=c, width=w)
            ang = math.atan2(cy - self.ann_sy, cx - self.ann_sx)
            hs  = max(12, w * 5)
            for da in (0.42, -0.42):
                ax = cx + hs * math.cos(ang + math.pi + da)
                ay = cy + hs * math.sin(ang + math.pi + da)
                d.line([(cx, cy), (int(ax), int(ay))], fill=c, width=w)
        elif self.ann_tool == "line":
            d.line([(self.ann_sx, self.ann_sy), (cx, cy)], fill=c, width=w)
        elif self.ann_tool == "rect":
            x0, y0 = min(self.ann_sx, cx), min(self.ann_sy, cy)
            x1, y1 = max(self.ann_sx, cx), max(self.ann_sy, cy)
            d.rectangle([x0, y0, x1, y1], outline=c, width=w)
        elif self.ann_tool == "blur":
            # Live preview: cyan outline of the area to be blurred
            x0, y0 = min(self.ann_sx, cx), min(self.ann_sy, cy)
            x1, y1 = max(self.ann_sx, cx), max(self.ann_sy, cy)
            d.rectangle([x0, y0, x1, y1], outline="#00BFFF", width=2)

    def _ann_draw_stroke(self, img: Image.Image):
        if len(self.ann_pts) < 2:
            return
        d = ImageDraw.Draw(img)
        if self.ann_tool == "pen":
            d.line(self.ann_pts, fill=self.ann_color,
                   width=self.ann_thickness, joint="curve")
        elif self.ann_tool == "hl":
            d.line(self.ann_pts,
                   fill=hex_rgba(self.ann_color, 90),
                   width=max(14, self.ann_thickness * 7))

    def _ann_commit_highlight(self):
        if len(self.ann_pts) < 2:
            return
        ov = Image.new("RGBA", self.ann_current.size, (0, 0, 0, 0))
        ImageDraw.Draw(ov).line(
            self.ann_pts,
            fill=hex_rgba(self.ann_color, 90),
            width=max(14, self.ann_thickness * 7))
        self.ann_current = Image.alpha_composite(
            self.ann_current.convert("RGBA"), ov)

    def _ann_commit_blur(self, cx, cy):
        x0, y0 = min(self.ann_sx, cx), min(self.ann_sy, cy)
        x1, y1 = max(self.ann_sx, cx), max(self.ann_sy, cy)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return
        patch = self.ann_current.crop((x0, y0, x1, y1))
        self.ann_current.paste(
            patch.filter(ImageFilter.GaussianBlur(radius=10)), (x0, y0))
        log(f"  blur applied  region=({x0},{y0})-({x1},{y1})")

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
                fnt = _load_font(fs)
                d.text((x, y), txt, fill=self.ann_color, font=fnt)
                self.ann_history.append(self.ann_current.copy())
                if len(self.ann_history) > 10:        # fix: apply cap
                    self.ann_history.pop(0)
                self._ann_refresh()
                log(f"  text placed: <{len(txt)} chars>  pos=({x},{y})  history_len={len(self.ann_history)}")
            dlg.destroy()

        e.bind("<Return>", commit)
        e.bind("<Escape>", lambda _: dlg.destroy())

    def _ann_undo(self):
        if len(self.ann_history) > 1:
            self.ann_history.pop()
            self.ann_current = self.ann_history[-1].copy()
            self._ann_refresh()
            log(f"  undo  history len={len(self.ann_history)}")

    # ── Actions ───────────────────────────────────────────────────────────────

    def _ann_copy(self):
        log("  ann_copy")
        copy_to_clipboard(self.ann_current)
        if self.cfg.auto_save:
            try:
                save_png(self.ann_current, self.cfg.save_folder)
            except Exception as e:
                log(f"  ann_copy: auto-save failed: {e}")
                messagebox.showerror(
                    "FreeShot — Save Failed",
                    f"Could not auto-save screenshot:\n{e}",
                    parent=self.win)
        self._close_overlay()
        self.done_cb()

    def _ann_save(self):
        log("  ann_save dialog")
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
        img = self.ann_current
        if fmt == "JPEG" and img.mode == "RGBA":
            img = img.convert("RGB")
        try:
            img.save(path, fmt)
        except Exception as e:
            log(f"  save error: {e}")
            messagebox.showerror(
                "FreeShot — Save Failed",
                f"Could not save to:\n{path}\n\n{e}",
                parent=self.win)
            return   # keep overlay open so user can try a different path
        log(f"  saved to {path}  fmt={fmt}")
        self._close_overlay()
        self.done_cb()

    def _ann_cancel(self):
        log("  ann_cancel")
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
        w, h = 380, 320
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
        log("SettingsWindow opened")

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _toggle_auto_copy(self):
        self.cfg.auto_copy = self._var_ac.get()
        if self.cfg.auto_copy:
            self.cfg.auto_copy_close = False
            self._var_acc.set(False)
        self.cfg.save()
        log(f"  settings: auto_copy → {self.cfg.auto_copy}")
        self.on_change()

    def _toggle_auto_copy_close(self):
        self.cfg.auto_copy_close = self._var_acc.get()
        if self.cfg.auto_copy_close:
            self.cfg.auto_copy = False
            self._var_ac.set(False)
        self.cfg.save()
        log(f"  settings: auto_copy_close → {self.cfg.auto_copy_close}")
        self.on_change()

    def _toggle_auto_save(self):
        self.cfg.auto_save = self._var_as.get()
        self.cfg.save()
        log(f"  settings: auto_save → {self.cfg.auto_save}")
        self.on_change()

    def _change_mode(self):
        self.cfg.capture_mode = self._var_mode.get()
        self.cfg.save()
        log(f"  settings: capture_mode → {self.cfg.capture_mode}")
        self.on_change()

    def _pick_folder(self):
        init = self.cfg.save_folder or os.path.join(os.path.expanduser("~"), "Pictures")
        folder = filedialog.askdirectory(
            title="FreeShot — choose save folder", initialdir=init, parent=self.win)
        if folder:
            validated = _validate_save_folder(folder)
            if not validated:
                log(f"  settings: folder rejected (unsafe path): {folder}")
                messagebox.showerror(
                    "FreeShot — Invalid Folder",
                    "Please choose a folder inside your home directory.\n"
                    "Network and system paths are not supported.",
                    parent=self.win)
                return
            self.cfg.save_folder = validated
            self._var_folder.set(validated)
            self.cfg.save()
            log(f"  settings: save_folder → {validated}")
            self.on_change()

    def _toggle_startup(self):
        desired = self._var_startup.get()
        log(f"  settings: startup toggle requested → {desired}")
        if not _write_startup(desired):
            self._var_startup.set(not desired)   # revert on failure
            log(f"  settings: startup FAILED — reverted to {not desired}")
            messagebox.showerror(
                "FreeShot — Registry Error",
                "Could not update the Windows startup entry.\n"
                "Try running FreeShot as administrator once to set this option.",
                parent=self.win)
            return
        log(f"  settings: startup OK → {desired}")


# ── Main App ──────────────────────────────────────────────────────────────────
class FreeShotApp:
    _ALT_KEYS = {kb.Key.alt, kb.Key.alt_l, kb.Key.alt_r}

    def __init__(self):
        log("FreeShotApp.__init__")

        # DPI awareness — winfo_screenwidth() returns physical pixels
        # so ImageGrab.grab() and Tkinter agree without a resize
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
            log("  DPI awareness set (PER_MONITOR_V2)")
        except Exception:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
                log("  DPI awareness set (PER_MONITOR fallback)")
            except Exception as e:
                log(f"  DPI awareness failed: {e}")

        self.cfg  = Config()
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.withdraw()
        self._down: set = set()
        self._active    = False
        self._capture_q = queue.Queue(maxsize=1)   # bounded: drop if a capture is already pending

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        log(f"  logical screen = {sw}x{sh}")

        try:
            import ctypes as _ct
            sm_cx = _ct.windll.user32.GetSystemMetrics(0)
            sm_cy = _ct.windll.user32.GetSystemMetrics(1)
            log(f"  GetSystemMetrics(SM_CX/CY) = {sm_cx}x{sm_cy}")
        except Exception as e:
            log(f"  GetSystemMetrics error: {e}")

        self._setup_tray()
        self._setup_hotkey()
        self._setup_prtscr_hook()
        self.root.after(50, self._poll_capture_queue)
        log("FreeShotApp ready — waiting for hotkey")

    def _poll_capture_queue(self):
        try:
            while not self._capture_q.empty():
                self._capture_q.get_nowait()
                log("  capture queue → _trigger")
                self._trigger()
        except Exception as e:
            log(f"  poll_capture_queue error: {e}")
        self.root.after(50, self._poll_capture_queue)

    def _setup_tray(self):
        self._rebuild_tray(_make_pentagram_icon(64))

    def _rebuild_tray(self, img=None):
        if img is None:
            img = self._icon.icon
        menu = pystray.Menu(
            item("📷  Capture  (PrtScrn / Alt+Home)", self._tray_trigger, default=True),
            pystray.Menu.SEPARATOR,
            item("⚙  Settings…", self._open_settings),
            item("Exit", self._quit)
        )
        if hasattr(self, "_icon"):
            self._icon.menu = menu
            log("  tray menu rebuilt")
        else:
            self._icon = pystray.Icon("FreeShot", img, "FreeShot", menu)
            threading.Thread(target=self._icon.run, daemon=True).start()
            log("  tray icon running")

    def _toggle_auto_copy(self, *_):
        self.cfg.auto_copy = not self.cfg.auto_copy
        if self.cfg.auto_copy:
            self.cfg.auto_copy_close = False
        self.cfg.save()
        self._rebuild_tray()
        log(f"  auto_copy toggled → {self.cfg.auto_copy}")

    def _toggle_auto_copy_close(self, *_):
        self.cfg.auto_copy_close = not self.cfg.auto_copy_close
        if self.cfg.auto_copy_close:
            self.cfg.auto_copy = False
        self.cfg.save()
        self._rebuild_tray()
        log(f"  auto_copy_close toggled → {self.cfg.auto_copy_close}")

    def _toggle_auto_save(self, *_):
        self.cfg.auto_save = not self.cfg.auto_save
        self.cfg.save()
        self._rebuild_tray()
        log(f"  auto_save toggled → {self.cfg.auto_save}")

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
            log(f"  save_folder changed → {folder!r}")

    def _open_settings(self, *_):
        log("  open_settings (tray)")
        self.root.after(0, self._do_open_settings)

    def _do_open_settings(self):
        if hasattr(self, "_settings_win") and self._settings_win.win.winfo_exists():
            log("  settings window already open — focusing")
            self._settings_win.win.lift()
            self._settings_win.win.focus_force()
            return
        log("  creating SettingsWindow")
        self._settings_win = SettingsWindow(self.root, self.cfg, self._rebuild_tray)

    def _setup_hotkey(self):
        def on_press(key):
            self._down.add(key)
            if self._down & self._ALT_KEYS and kb.Key.home in self._down:
                log(f"  hotkey fired  down={self._down}")
                self.root.after(0, self._trigger)
        def on_release(key):
            self._down.discard(key)
        l = kb.Listener(on_press=on_press, on_release=on_release)
        l.daemon = True
        l.start()
        log("  keyboard listener started")

    def _setup_prtscr_hook(self):
        """WH_KEYBOARD_LL hook — signals via Queue, polled every 50 ms."""
        _q = self._capture_q

        def _thread():
            log("  PrtScrn hook thread started")
            try:
                import ctypes as ct
                import ctypes.wintypes as wt
                log(f"  ctypes id={id(ct)}  wt id={id(wt)}")

                WH_KEYBOARD_LL = 13
                WM_KEYDOWN     = 0x0100
                WM_SYSKEYDOWN  = 0x0104
                VK_SNAPSHOT    = 0x2C

                HOOKPROC = ct.WINFUNCTYPE(ct.c_longlong, ct.c_int,
                                          wt.WPARAM, wt.LPARAM)
                log("  HOOKPROC type created")

                @HOOKPROC
                def _proc(nCode, wParam, lParam):
                    if nCode >= 0 and lParam:           # null-pointer guard
                        try:
                            vk = ct.c_uint32.from_address(lParam).value
                        except (ValueError, OSError):   # invalid address — pass through
                            return ct.windll.user32.CallNextHookEx(
                                None, nCode, wParam, lParam)
                        if vk == VK_SNAPSHOT:
                            if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                                log("  PrtScrn DOWN intercepted → queue.put_nowait")
                                try:
                                    _q.put_nowait(1)    # drop silently if already full
                                except queue.Full:
                                    pass
                            return 1
                    return ct.windll.user32.CallNextHookEx(
                        None, nCode, wParam, lParam)

                log("  _proc callback created")

                u32 = ct.windll.user32
                u32.SetWindowsHookExW.restype  = ct.c_void_p
                u32.SetWindowsHookExW.argtypes = [ct.c_int, ct.c_void_p,
                                                  ct.c_void_p, ct.c_uint]
                u32.CallNextHookEx.restype      = ct.c_longlong
                u32.UnhookWindowsHookEx.restype = ct.c_bool
                u32.GetMessageW.argtypes        = [ct.c_void_p, ct.c_void_p,
                                                   ct.c_uint, ct.c_uint]

                hhook = u32.SetWindowsHookExW(WH_KEYBOARD_LL, _proc, None, 0)
                if not hhook:
                    err = ct.windll.kernel32.GetLastError()
                    log(f"  SetWindowsHookExW FAILED  GetLastError={err}")
                    return

                self._prtscr_proc  = _proc
                self._prtscr_hhook = hhook
                log(f"  PrtScrn hook installed  hhook={hhook}")

                msg = wt.MSG()
                while u32.GetMessageW(ct.byref(msg), None, 0, 0) > 0:
                    u32.TranslateMessage(ct.byref(msg))
                    u32.DispatchMessageW(ct.byref(msg))

                u32.UnhookWindowsHookEx(hhook)
                log("  PrtScrn hook thread exiting")
            except Exception:
                log(f"  PrtScrn hook thread EXCEPTION:\n{traceback.format_exc()}")

        threading.Thread(target=_thread, daemon=True).start()
        log("  PrtScrn hook thread spawned")

    def _tray_trigger(self, icon=None, item=None):
        log("  tray_trigger")
        self.root.after(150, self._trigger)

    def _trigger(self):
        log(f"  _trigger  active={self._active}")
        if self._active:
            return
        self._active = True
        self._down.clear()   # clear stale modifier-key state
        self.root.after(400, self._capture)

    def _capture(self):
        log("  _capture start")
        try:
            shot = ImageGrab.grab()
            log(f"  ImageGrab.grab() → {shot.size}")
            lw = self.root.winfo_screenwidth()
            lh = self.root.winfo_screenheight()
            if shot.size != (lw, lh):
                log(f"  resizing {shot.size} → {lw}x{lh}  (DPI awareness fallback)")
                shot = shot.resize((lw, lh), Image.LANCZOS)
            SelectionOverlay(self.root, shot, self._on_done, self.cfg)
        except Exception:
            # Always release the lock so subsequent hotkey presses still work
            self._active = False
            log(f"  _capture EXCEPTION:\n{traceback.format_exc()}")

    def _on_done(self):
        log("  _on_capture_done")
        self._active = False
        self.root.withdraw()

    def _quit(self, *_):
        log("  quit")
        self._icon.stop()
        self.root.quit()
        sys.exit(0)

    def run(self):
        log("mainloop start")
        self.root.mainloop()


if __name__ == "__main__":
    try:
        FreeShotApp().run()
    except Exception:
        log(f"FATAL:\n{traceback.format_exc()}")

#!/usr/bin/env python3
"""
FreeShot — Screenshot + Annotation Tool for Windows 11
═══════════════════════════════════════════════════════
INSTALL:  pip install pillow pynput pystray pywin32
HOTKEY:   Alt + Home  (or tray icon)
"""

import tkinter as tk
from tkinter import colorchooser, filedialog
import threading, sys, math, time, json, os, gc, ctypes, ctypes.wintypes as _wt
from io import BytesIO
from PIL import Image, ImageDraw, ImageFilter, ImageGrab, ImageTk
import pystray
from pystray import MenuItem as item
from pynput import keyboard as kb

# ── Config ────────────────────────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    _APP_DIR = os.path.dirname(sys.executable)
else:
    _APP_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_APP_DIR, "config.json")

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
            self.save_folder     = d.get("save_folder", "")
        except Exception:
            pass

    def save(self):
        try:
            with open(_CONFIG_PATH, "w") as f:
                json.dump({"auto_copy":       self.auto_copy,
                           "auto_copy_close": self.auto_copy_close,
                           "capture_mode":    self.capture_mode,
                           "auto_save":       self.auto_save,
                           "save_folder":     self.save_folder}, f, indent=2)
        except Exception:
            pass


# ── Icon ──────────────────────────────────────────────────────────────────────
def _make_pentagram_icon(size=64):
    from PIL import ImageFilter
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
def hex_rgba(h, a=255):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)

def copy_to_clipboard(img: Image.Image) -> bool:
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
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

        win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False


def save_png(img: Image.Image, folder: str = "") -> str:
    """Save *img* as a lossless PNG; returns the saved file path."""
    if not folder:
        folder = os.path.join(os.path.expanduser("~"), "Pictures", "FreeShot")
    os.makedirs(folder, exist_ok=True)
    ts   = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(folder, f"freeshot_{ts}.png")
    n = 1
    while os.path.exists(path):
        path = os.path.join(folder, f"freeshot_{ts}_{n}.png")
        n += 1
    img.save(path, "PNG")
    return path


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
                save_png(region, self.cfg.save_folder)
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
        # Release large image objects so GC can reclaim memory
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
        self.ann_base    = base
        self.ann_current = base.copy()
        self.ann_history = [base.copy()]

        self.ann_tool      = None
        self.ann_color     = "#ff0000"
        self.ann_thickness = 2
        self.ann_drawing   = False
        self.ann_sx = self.ann_sy = 0
        self.ann_pts: list = []

        # Auto-copy the raw selection immediately if enabled
        if self.cfg.auto_copy:
            copy_to_clipboard(base)
            if self.cfg.auto_save:
                save_png(base, self.cfg.save_folder)

        # Crop the dark background to just the selection area, then free
        # the full-screen images — saves ~22 MB during annotation
        self._ann_dark_patch = self._dark.crop(
            (x0, y0, x1, y1)).convert("RGBA")
        self.shot  = None
        self._dark = None
        gc.collect()

        self._ann_region_id = None  # created lazily in _ann_refresh

        # Unbind selection phase events
        self.cv.unbind("<ButtonPress-1>")
        self.cv.unbind("<B1-Motion>")
        self.cv.unbind("<ButtonRelease-1>")
        self.win.unbind("<r>"); self.win.unbind("<R>")
        self.win.unbind("<f>"); self.win.unbind("<F>")

        # Bind annotation phase events
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
        # Click active tool → deactivate; click other → activate
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
        # Composite annotation onto the pre-cropped dark patch (small, not full-screen)
        bg = self._ann_dark_patch.copy()
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
            tmp = self.ann_current.copy()
            self._ann_draw_shape(ImageDraw.Draw(tmp), x, y)
            self._ann_refresh(tmp)
        elif self.ann_tool in ("pen", "hl", "eraser"):
            tmp = self.ann_current.copy()
            self._ann_draw_stroke(tmp)
            self._ann_refresh(tmp)

    def _ann_release(self, e):
        if not self.ann_drawing:
            return
        self.ann_drawing = False
        x = e.x - self.ann_x0
        y = e.y - self.ann_y0
        if self.ann_tool in ("arrow", "line", "rect"):
            self._ann_draw_shape(ImageDraw.Draw(self.ann_current), x, y)
        elif self.ann_tool == "pen":
            self._ann_draw_stroke(self.ann_current)
        elif self.ann_tool == "hl":
            self._ann_commit_highlight()
        elif self.ann_tool == "blur":
            self._ann_commit_blur(x, y)
        elif self.ann_tool == "eraser":
            self._ann_commit_eraser()
        self.ann_history.append(self.ann_current.copy())
        if len(self.ann_history) > 10:
            self.ann_history.pop(0)
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

    def _ann_draw_stroke(self, img: Image.Image):
        if len(self.ann_pts) < 2:
            return
        d = ImageDraw.Draw(img)
        if self.ann_tool == "pen":
            d.line(self.ann_pts, fill=self.ann_color,
                   width=self.ann_thickness, joint="curve")
        elif self.ann_tool == "eraser":
            r = max(6, self.ann_thickness * 4)
            for px, py in self.ann_pts:
                bx0 = max(0, px - r); by0 = max(0, py - r)
                bx1 = min(self.ann_w, px + r)
                by1 = min(self.ann_h, py + r)
                img.paste(self.ann_base.crop((bx0, by0, bx1, by1)), (bx0, by0))

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

    def _ann_commit_eraser(self):
        self._ann_draw_stroke(self.ann_current)

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
                try:
                    from PIL import ImageFont
                    fnt = ImageFont.truetype(
                        "C:/Windows/Fonts/segoeui.ttf", fs)
                except Exception:
                    fnt = None
                d.text((x, y), txt, fill=self.ann_color, font=fnt)
                self.ann_history.append(self.ann_current.copy())
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
        copy_to_clipboard(self.ann_current)
        if self.cfg.auto_save:
            save_png(self.ann_current, self.cfg.save_folder)
        self._close_overlay()
        self.done_cb()

    def _ann_save(self):
        path = filedialog.asksaveasfilename(
            parent=self.win, defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("BMP", "*.bmp")],
            initialfile=f"freeshot_{int(time.time())}.png"
        )
        if path:
            img = self.ann_current
            if path.lower().endswith((".jpg", ".jpeg")) and img.mode == "RGBA":
                img = img.convert("RGB")
            img.save(path)
            self._close_overlay()
            self.done_cb()

    def _ann_cancel(self):
        self._close_overlay()
        self.done_cb()


# ── Main App ──────────────────────────────────────────────────────────────────
class FreeShotApp:
    _ALT_KEYS = {kb.Key.alt, kb.Key.alt_l, kb.Key.alt_r}

    def __init__(self):
        self.cfg  = Config()
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.withdraw()
        self._down: set = set()
        self._active = False
        self._setup_tray()
        self._setup_hotkey()
        self._setup_prtscr_hook()

    def _setup_tray(self):
        self._tray_menu_ref = None
        self._rebuild_tray(_make_pentagram_icon(64))

    def _rebuild_tray(self, img=None):
        if img is None:
            img = self._icon.icon
        lbl_ac  = ("✔  Auto-copy on capture"
                   if self.cfg.auto_copy else
                   "    Auto-copy on capture")
        lbl_acc = ("✔  Auto copy & close"
                   if self.cfg.auto_copy_close else
                   "    Auto copy & close")
        lbl_as  = ("✔  Auto-save PNG"
                   if self.cfg.auto_save else
                   "    Auto-save PNG")
        folder  = self.cfg.save_folder or os.path.join(
                      os.path.expanduser("~"), "Pictures", "FreeShot")
        menu = pystray.Menu(
            item("📷  Capture  (PrtScrn / Alt+Home)", self._tray_trigger, default=True),
            pystray.Menu.SEPARATOR,
            item(lbl_ac,  self._toggle_auto_copy),
            item(lbl_acc, self._toggle_auto_copy_close),
            item(lbl_as,  self._toggle_auto_save),
            item(f"📁  {folder}", self._pick_save_folder),
            pystray.Menu.SEPARATOR,
            item("Exit", self._quit)
        )
        if hasattr(self, "_icon"):
            self._icon.menu = menu
        else:
            self._icon = pystray.Icon("FreeShot", img, "FreeShot", menu)
            threading.Thread(target=self._icon.run, daemon=True).start()

    def _toggle_auto_copy(self, *_):
        self.cfg.auto_copy = not self.cfg.auto_copy
        self.cfg.save()
        self._rebuild_tray()

    def _toggle_auto_copy_close(self, *_):
        self.cfg.auto_copy_close = not self.cfg.auto_copy_close
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

    def _setup_hotkey(self):
        def on_press(key):
            self._down.add(key)
            if self._down & self._ALT_KEYS and kb.Key.home in self._down:
                self.root.after(0, self._trigger)
        def on_release(key):
            self._down.discard(key)
        l = kb.Listener(on_press=on_press, on_release=on_release)
        l.daemon = True
        l.start()

    def _setup_prtscr_hook(self):
        """Low-level WH_KEYBOARD_LL hook that intercepts Print Screen before
        Windows Snipping Tool can grab it, then triggers our capture."""
        self.root.bind("<<Capture>>", lambda _: self._trigger())
        _root = self.root

        def _thread():
            try:
                import ctypes as ct
                import ctypes.wintypes as wt

                WH_KEYBOARD_LL = 13
                WM_KEYDOWN     = 0x0100
                WM_SYSKEYDOWN  = 0x0104
                VK_SNAPSHOT    = 0x2C

                # c_longlong matches LRESULT (64-bit) on x64 Windows
                HOOKPROC = ct.WINFUNCTYPE(ct.c_longlong, ct.c_int, wt.WPARAM, wt.LPARAM)

                @HOOKPROC
                def _proc(nCode, wParam, lParam):
                    if nCode >= 0:
                        vk = ct.c_uint32.from_address(lParam).value
                        if vk == VK_SNAPSHOT:
                            if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                                try:
                                    _root.event_generate("<<Capture>>", when="tail")
                                except Exception:
                                    pass
                            return 1   # suppress all PrtScrn events
                    return ct.windll.user32.CallNextHookEx(None, nCode, wParam, lParam)

                # Set correct restype — critical on 64-bit Windows
                u32 = ct.windll.user32
                u32.SetWindowsHookExW.restype  = ct.c_void_p
                u32.CallNextHookEx.restype     = ct.c_longlong
                u32.UnhookWindowsHookEx.restype = ct.c_bool
                u32.GetMessageW.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_uint, ct.c_uint]

                hhook = u32.SetWindowsHookExW(WH_KEYBOARD_LL, _proc, None, 0)
                if not hhook:
                    return   # hook failed (no elevated rights needed, so rare)

                self._prtscr_proc  = _proc   # keep alive (GC guard)
                self._prtscr_hhook = hhook

                msg = wt.MSG()
                while u32.GetMessageW(ct.byref(msg), None, 0, 0) > 0:
                    u32.TranslateMessage(ct.byref(msg))
                    u32.DispatchMessageW(ct.byref(msg))

                u32.UnhookWindowsHookEx(hhook)
            except Exception:
                pass   # hook unavailable; Alt+Home still works

        threading.Thread(target=_thread, daemon=True).start()

    def _tray_trigger(self, icon=None, item=None):
        self.root.after(150, self._trigger)

    def _trigger(self):
        if self._active:
            return
        self._active = True
        self.root.after(400, self._capture)

    def _capture(self):
        shot = ImageGrab.grab()
        lw = self.root.winfo_screenwidth()
        lh = self.root.winfo_screenheight()
        if shot.size != (lw, lh):
            shot = shot.resize((lw, lh), Image.LANCZOS)
        SelectionOverlay(self.root, shot, self._on_done, self.cfg)

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
    FreeShotApp().run()

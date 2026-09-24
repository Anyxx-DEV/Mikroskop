"""
Mikroskop-Capture
-----------------
Live-Bild von der Capture-Card (Vision Engineering Makrolite 4K via HDMI),
Aufnahme per Button / Taste (F9 oder Leertaste - z.B. vom Contour Shuttle Pro V2),
Speichern im Zielordner (auch Netzlaufwerk / UNC-Pfad) und Eintrag in eine Excel-Liste.
"""

import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

import cv2
import numpy as np
import customtkinter as ctk
from PIL import Image, ImageTk

try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Font, PatternFill, Alignment
except ImportError:
    Workbook = None

APP_NAME = "Mikroskop-Capture"
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).parent
CONFIG_FILE = APP_DIR / "config.json"

RESOLUTIONS = ["3840x2160", "2560x1440", "1920x1080", "1280x720"]
EXCEL_HEADERS = ["Datum", "Uhrzeit", "Hersteller", "Leiterplatte / Artikel-Nr.",
                 "Serien-/Auftrags-Nr.", "Schadensbeschreibung", "Bearbeiter",
                 "Dateiname", "Link", "Vorschau"]
THUMB_HEIGHT = 90   # px, Vorschaubild in Excel
STRIP_COUNT = 8     # letzte Aufnahmen in der Leiste
IMAGE_EXTS = (".png", ".jpg", ".jpeg")

# Farben (hell, dunkel)
ACCENT = ("#2563eb", "#3b82f6")
ACCENT_HOVER = ("#1d4ed8", "#2563eb")
CARD = ("#ffffff", "#1f2329")
BG = ("#eef1f5", "#14171b")
MUTED = ("#6b7280", "#9ca3af")
PREVIEW_BG = ("#d9dee5", "#0b0d10")
OK_GREEN = ("#15803d", "#22c55e")
WARN = ("#b45309", "#f59e0b")
ERR = ("#b91c1c", "#ef4444")

DEFAULT_CONFIG = {
    "camera_index": 0,
    "resolution": "3840x2160",
    "save_dir": str(Path.home() / "Pictures" / "Mikroskop"),
    "excel_name": "Schadensdokumentation.xlsx",
    "excel_enabled": True,
    "excel_thumbnail": True,
    "image_format": "png",
    "bearbeiter": os.environ.get("USERNAME", ""),
    "hersteller_history": [],
    "appearance": "Dark",
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print("Config konnte nicht gespeichert werden:", e)


def list_cameras():
    """Liefert [(index, name)]. Namen über DirectShow (pygrabber), sonst generisch."""
    try:
        from pygrabber.dshow_graph import FilterGraph
        names = FilterGraph().get_input_devices()
        return list(enumerate(names))
    except Exception:
        found = []
        for i in range(5):
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            if cap.isOpened():
                found.append((i, f"Gerät {i}"))
            cap.release()
        return found


def safe_name(text):
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text.strip())
    return re.sub(r"\s+", "_", text)[:40]


def mode_color(pair):
    return pair[1] if ctk.get_appearance_mode() == "Dark" else pair[0]


class CameraThread(threading.Thread):
    """Liest fortlaufend Frames, damit immer das aktuellste Bild in voller Auflösung bereitliegt."""

    def __init__(self, index, width, height):
        super().__init__(daemon=True)
        self.cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        # MJPG erlaubt bei den meisten Capture-Cards hohe Auflösung mit brauchbarer Bildrate
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.lock = threading.Lock()
        self.frame = None
        self.fps = 0.0
        self.running = self.cap.isOpened()

    @property
    def actual_size(self):
        return (int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))

    def run(self):
        count, t0 = 0, time.time()
        while self.running:
            ok, frame = self.cap.read()
            if ok:
                with self.lock:
                    self.frame = frame
                count += 1
                if time.time() - t0 >= 1.0:
                    self.fps = count / (time.time() - t0)
                    count, t0 = 0, time.time()
            else:
                time.sleep(0.05)
        self.cap.release()

    def get_frame(self):
        with self.lock:
            return None if self.frame is None else self.frame.copy()

    def stop(self):
        self.running = False


class Card(ctk.CTkFrame):
    """Abgerundete Karte mit Überschrift."""

    def __init__(self, master, title, icon=""):
        super().__init__(master, fg_color=CARD, corner_radius=12)
        ctk.CTkLabel(self, text=f"{icon}  {title}".strip(), anchor="w",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(fill="x", padx=14, pady=(12, 6))
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="x", padx=14, pady=(0, 14))


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.cam = None
        self.cameras = []
        self._photo = None
        self._flash_until = 0
        self._strip_images = []
        self._strip_items = []
        self.session_count = 0

        root.title(APP_NAME)
        root.geometry("1440x880")
        root.minsize(1100, 700)
        root.configure(fg_color=BG)
        self._build_ui()
        self.refresh_cameras()
        self.start_camera()
        self.load_recent()

        # Tastenkürzel: F9 / Leertaste = Aufnahme (Shuttle-Taste auf F9 legen)
        root.bind_all("<F9>", lambda e: self.capture())
        root.bind("<space>", self._space_capture)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.update_preview()

    # ---------------- UI ----------------
    def _build_ui(self):
        r = self.root
        r.grid_columnconfigure(0, weight=1)
        r.grid_rowconfigure(1, weight=1)

        # Kopfzeile
        header = ctk.CTkFrame(r, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=18, pady=(14, 6))
        ctk.CTkLabel(header, text="🔬  Mikroskop-Capture",
                     font=ctk.CTkFont(size=22, weight="bold")).pack(side="left")
        ctk.CTkLabel(header, text="Leiterplatten-Schadensdokumentation", text_color=MUTED,
                     font=ctk.CTkFont(size=13)).pack(side="left", padx=(14, 0), pady=(6, 0))
        self.mode_switch = ctk.CTkSegmentedButton(header, values=["☀ Hell", "🌙 Dunkel"],
                                                  command=self.set_appearance, width=160)
        self.mode_switch.set("🌙 Dunkel" if self.cfg["appearance"] == "Dark" else "☀ Hell")
        self.mode_switch.pack(side="right")
        self.count_label = ctk.CTkLabel(header, text="", text_color=MUTED)
        self.count_label.pack(side="right", padx=16)

        # Linke Seite: Vorschau + Leiste
        left = ctk.CTkFrame(r, fg_color="transparent")
        left.grid(row=1, column=0, sticky="nsew", padx=(18, 9), pady=(6, 0))
        left.grid_rowconfigure(0, weight=1)
        left.grid_columnconfigure(0, weight=1)

        pv = ctk.CTkFrame(left, fg_color=PREVIEW_BG, corner_radius=14)
        pv.grid(row=0, column=0, sticky="nsew")
        self.canvas = tk.Canvas(pv, highlightthickness=0, bd=0, bg=mode_color(PREVIEW_BG))
        self.canvas.pack(fill="both", expand=True, padx=6, pady=6)

        strip_card = ctk.CTkFrame(left, fg_color=CARD, corner_radius=12)
        strip_card.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        top = ctk.CTkFrame(strip_card, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(top, text="🖼  Letzte Aufnahmen",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        ctk.CTkButton(top, text="Ordner öffnen", width=110, height=26, fg_color="transparent",
                      border_width=1, text_color=("gray10", "gray90"),
                      command=self.open_dir).pack(side="right")
        self.strip = ctk.CTkFrame(strip_card, fg_color="transparent", height=96)
        self.strip.pack(fill="x", padx=10, pady=(6, 10))
        self.strip_empty = ctk.CTkLabel(self.strip, text="Noch keine Aufnahmen in diesem Ordner.",
                                        text_color=MUTED)
        self.strip_empty.pack(pady=30)

        # Rechte Seite: Einstellungen
        side = ctk.CTkScrollableFrame(r, width=370, fg_color="transparent")
        side.grid(row=1, column=1, sticky="ns", padx=(9, 12), pady=(6, 0))

        # Kamera
        card = Card(side, "Kamera / Capture-Card", "🎥")
        card.pack(fill="x", pady=(0, 10))
        b = card.body
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x")
        self.cam_menu = ctk.CTkOptionMenu(row, values=["—"], command=lambda _: self.start_camera(),
                                          dynamic_resizing=False)
        self.cam_menu.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="↻", width=34, command=self.refresh_cameras).pack(side="left", padx=(6, 0))
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(row, text="Auflösung", text_color=MUTED).pack(side="left")
        self.res_menu = ctk.CTkOptionMenu(row, values=RESOLUTIONS, width=130,
                                          command=lambda _: self.start_camera())
        self.res_menu.set(self.cfg["resolution"])
        self.res_menu.pack(side="right")

        # Speicherort
        card = Card(side, "Speicherort", "📁")
        card.pack(fill="x", pady=(0, 10))
        b = card.body
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x")
        self.dir_var = tk.StringVar(value=self.cfg["save_dir"])
        ent = ctk.CTkEntry(row, textvariable=self.dir_var)
        ent.pack(side="left", fill="x", expand=True)
        ent.bind("<FocusOut>", lambda e: (self.persist(), self.load_recent()))
        ctk.CTkButton(row, text="…", width=34, command=self.choose_dir).pack(side="left", padx=(6, 0))

        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(row, text="Format", text_color=MUTED).pack(side="left")
        self.fmt_seg = ctk.CTkSegmentedButton(row, values=["PNG", "JPG"], width=130)
        self.fmt_seg.set(self.cfg["image_format"].upper())
        self.fmt_seg.pack(side="right")

        self.excel_var = tk.BooleanVar(value=self.cfg["excel_enabled"])
        self.thumb_var = tk.BooleanVar(value=self.cfg["excel_thumbnail"])
        ctk.CTkSwitch(b, text="In Excel-Liste eintragen", variable=self.excel_var).pack(anchor="w", pady=(12, 0))
        ctk.CTkSwitch(b, text="Vorschaubild in Excel", variable=self.thumb_var).pack(anchor="w", pady=(6, 0))
        self.excel_name_var = tk.StringVar(value=self.cfg["excel_name"])
        ctk.CTkEntry(b, textvariable=self.excel_name_var,
                     placeholder_text="Excel-Dateiname").pack(fill="x", pady=(8, 0))

        # Angaben
        card = Card(side, "Angaben zur Leiterplatte", "🧾")
        card.pack(fill="x", pady=(0, 10))
        b = card.body
        self.hersteller_var = tk.StringVar()
        self.artikel_var = tk.StringVar()
        self.serie_var = tk.StringVar()
        self.bearbeiter_var = tk.StringVar(value=self.cfg["bearbeiter"])

        def label(text):
            ctk.CTkLabel(b, text=text, text_color=MUTED, anchor="w",
                         font=ctk.CTkFont(size=12)).pack(fill="x", pady=(6, 0))

        label("Hersteller")
        self.hersteller_combo = ctk.CTkComboBox(b, variable=self.hersteller_var,
                                                values=self.cfg["hersteller_history"] or [""])
        self.hersteller_combo.pack(fill="x")
        self.hersteller_var.set("")
        for text, var in (("Leiterplatte / Artikel-Nr.", self.artikel_var),
                          ("Serien- / Auftrags-Nr.", self.serie_var),
                          ("Bearbeiter", self.bearbeiter_var)):
            label(text)
            ctk.CTkEntry(b, textvariable=var).pack(fill="x")
        label("Schadensbeschreibung")
        self.desc_text = ctk.CTkTextbox(b, height=90, wrap="word", border_width=2)
        self.desc_text.pack(fill="x")
        ctk.CTkButton(b, text="Felder leeren", fg_color="transparent", border_width=1,
                      text_color=("gray10", "gray90"), command=self.clear_fields).pack(fill="x", pady=(10, 0))

        # Großer Aufnahme-Button (unter der Scrollfläche, immer sichtbar)
        cap_frame = ctk.CTkFrame(r, fg_color="transparent")
        cap_frame.grid(row=2, column=1, sticky="ew", padx=(9, 18), pady=(8, 0))
        self.cap_btn = ctk.CTkButton(cap_frame, text="📷   Aufnehmen", height=62, corner_radius=14,
                                     fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                     font=ctk.CTkFont(size=19, weight="bold"), command=self.capture)
        self.cap_btn.pack(fill="x")
        ctk.CTkLabel(cap_frame, text="Tastenkürzel: F9  ·  Leertaste  ·  Shuttle-Taste",
                     text_color=MUTED, font=ctk.CTkFont(size=11)).pack(pady=(4, 0))

        # Statusleiste
        self.status = ctk.CTkLabel(r, text="Bereit", anchor="w", text_color=MUTED,
                                   font=ctk.CTkFont(size=12))
        self.status.grid(row=2, column=0, sticky="ew", padx=22, pady=(8, 0))
        ctk.CTkFrame(r, height=10, fg_color="transparent").grid(row=3, column=0)

    def set_appearance(self, value):
        mode = "Dark" if "Dunkel" in value else "Light"
        ctk.set_appearance_mode(mode)
        self.cfg["appearance"] = mode
        self.canvas.configure(bg=mode_color(PREVIEW_BG))
        save_config(self.cfg)

    def _space_capture(self, event):
        # Leertaste nur auslösen, wenn nicht gerade in ein Textfeld geschrieben wird
        if not isinstance(event.widget, (tk.Entry, tk.Text)):
            self.capture()

    def set_status(self, text, color=MUTED):
        self.status.configure(text=text, text_color=color)

    def toast(self, text, color=OK_GREEN, ms=2500):
        """Kurze Einblendung oben im Vorschaubild."""
        self._toast = (text, color, time.time() + ms / 1000)

    # ---------------- Kamera ----------------
    def refresh_cameras(self):
        self.cameras = list_cameras()
        labels = [f"{i}: {n}" for i, n in self.cameras]
        if not labels:
            self.cam_menu.configure(values=["Keine Kamera gefunden"])
            self.cam_menu.set("Keine Kamera gefunden")
            self.set_status("Keine Kamera / Capture-Card gefunden!", ERR)
            return
        self.cam_menu.configure(values=labels)
        want = self.cfg["camera_index"]
        self.cam_menu.set(next((l for (i, _), l in zip(self.cameras, labels) if i == want), labels[0]))

    def selected_index(self):
        v = self.cam_menu.get()
        return int(v.split(":")[0]) if v and v[0].isdigit() else None

    def start_camera(self):
        if self.cam:
            self.cam.stop()
            self.cam.join(timeout=2)
            self.cam = None
        idx = self.selected_index()
        if idx is None:
            return
        w, h = map(int, self.res_menu.get().split("x"))
        self.set_status("Starte Kamera…")
        self.root.update_idletasks()
        cam = CameraThread(idx, w, h)
        if not cam.running:
            self.set_status(f"Kamera {idx} konnte nicht geöffnet werden.", ERR)
            return
        cam.start()
        self.cam = cam
        self.set_status("Kamera läuft – bereit für Aufnahmen.")

    def update_preview(self):
        c = self.canvas
        cw, ch = max(c.winfo_width(), 10), max(c.winfo_height(), 10)
        c.delete("all")
        frame = self.cam.get_frame() if self.cam else None
        if frame is not None:
            fh, fw = frame.shape[:2]
            scale = min(cw / fw, ch / fh)
            small = cv2.resize(frame, (max(int(fw * scale), 1), max(int(fh * scale), 1)),
                               interpolation=cv2.INTER_AREA)
            if time.time() < self._flash_until:   # Blitz-Effekt nach Aufnahme
                small = cv2.addWeighted(small, 0.35, np.full_like(small, 255), 0.65, 0)
            self._photo = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB)))
            c.create_image(cw // 2, ch // 2, image=self._photo)

            # Info-Badges
            aw, ah = self.cam.actual_size
            x = self._badge(16, 14, "●  LIVE", "#dc2626")
            self._badge(x + 22, 14, f"{aw} × {ah}   ·   {self.cam.fps:4.1f} fps", "#111827")
        else:
            txt = "Warte auf Bild…" if self.cam else "Keine Kamera aktiv\nCapture-Card wählen und ↻ drücken"
            c.create_text(cw // 2, ch // 2, text=txt, fill=mode_color(MUTED),
                          font=("Segoe UI", 16), justify="center")

        toast = getattr(self, "_toast", None)
        if toast and time.time() < toast[2]:
            self._badge(cw // 2, ch - 40, toast[0], mode_color(toast[1]), center=True, size=13)
        self.root.after(33, self.update_preview)

    def _badge(self, x, y, text, color, center=False, size=10):
        c = self.canvas
        t = c.create_text(x, y, text=text, fill="white", anchor="center" if center else "nw",
                          font=("Segoe UI Semibold", size))
        x1, y1, x2, y2 = c.bbox(t)
        pad = 8
        r = c.create_rectangle(x1 - pad, y1 - pad // 2, x2 + pad, y2 + pad // 2,
                               fill=color, outline="")
        c.tag_lower(r, t)
        return x2

    # ---------------- Letzte Aufnahmen ----------------
    def load_recent(self):
        d = Path(self.dir_var.get().strip())

        def work():
            try:
                files = sorted((p for p in d.iterdir() if p.suffix.lower() in IMAGE_EXTS),
                               key=lambda p: p.stat().st_mtime, reverse=True)[:STRIP_COUNT]
            except Exception:
                files = []
            items = []
            for p in files:
                t = d / ".thumbs" / (p.stem + ".jpg")
                try:
                    img = Image.open(t if t.exists() else p)
                    img.thumbnail((160, 90))
                    items.append((p, img.copy()))
                except Exception:
                    pass
            self.root.after(0, lambda: self._fill_strip(items))

        threading.Thread(target=work, daemon=True).start()

    def _fill_strip(self, items):
        for w in self.strip.winfo_children():
            if w is not self.strip_empty:
                w.destroy()
        self._strip_images = []
        self._strip_items = items
        if not items:
            self.strip_empty.pack(pady=30)
            return
        self.strip_empty.pack_forget()
        for path, img in items:
            ci = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self._strip_images.append(ci)
            ctk.CTkButton(self.strip, image=ci, text="", width=img.size[0] + 8, height=img.size[1] + 8,
                          fg_color="transparent", hover_color=("gray80", "gray25"), corner_radius=8,
                          command=lambda p=path: os.startfile(p)).pack(side="left", padx=2)

    def add_to_strip(self, path, frame):
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (int(w * 90 / h), 90), interpolation=cv2.INTER_AREA)
        img = Image.fromarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
        img.thumbnail((160, 90))
        self._fill_strip([(path, img)] + self._strip_items[:STRIP_COUNT - 1])

    # ---------------- Speichern ----------------
    def choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.dir_var.get() or None, title="Zielordner wählen")
        if d:
            self.dir_var.set(os.path.normpath(d))
            self.persist()
            self.load_recent()

    def open_dir(self):
        d = self.dir_var.get()
        if os.path.isdir(d):
            os.startfile(d)

    def clear_fields(self):
        self.artikel_var.set("")
        self.serie_var.set("")
        self.desc_text.delete("1.0", "end")

    def persist(self):
        self.cfg.update(
            camera_index=self.selected_index() or 0,
            resolution=self.res_menu.get(),
            save_dir=self.dir_var.get(),
            excel_name=self.excel_name_var.get().strip() or DEFAULT_CONFIG["excel_name"],
            excel_enabled=self.excel_var.get(),
            excel_thumbnail=self.thumb_var.get(),
            image_format=self.fmt_seg.get().lower(),
            bearbeiter=self.bearbeiter_var.get(),
        )
        h = self.hersteller_var.get().strip()
        if h:
            hist = [h] + [x for x in self.cfg["hersteller_history"] if x != h]
            self.cfg["hersteller_history"] = hist[:30]
            self.hersteller_combo.configure(values=self.cfg["hersteller_history"])
        save_config(self.cfg)

    def capture(self):
        if not self.cam:
            self.set_status("Keine Kamera aktiv.", ERR)
            return
        frame = self.cam.get_frame()
        if frame is None:
            self.set_status("Noch kein Bild von der Kamera.", ERR)
            return

        save_dir = Path(self.dir_var.get().strip())
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Zielordner nicht erreichbar:\n{save_dir}\n\n{e}")
            return

        now = datetime.now()
        parts = [now.strftime("%Y-%m-%d_%H-%M-%S")]
        for v in (self.hersteller_var.get(), self.artikel_var.get(), self.serie_var.get()):
            if v.strip():
                parts.append(safe_name(v))
        ext = self.fmt_seg.get().lower()
        path = save_dir / ("_".join(parts) + f".{ext}")
        n = 2
        while path.exists():
            path = save_dir / ("_".join(parts) + f"_{n}.{ext}")
            n += 1

        params = [cv2.IMWRITE_JPEG_QUALITY, 95] if ext == "jpg" else [cv2.IMWRITE_PNG_COMPRESSION, 3]
        ok, buf = cv2.imencode(f".{ext}", frame, params)
        try:
            if not ok:
                raise RuntimeError("Bild konnte nicht kodiert werden")
            path.write_bytes(buf.tobytes())  # funktioniert auch mit Umlauten / UNC-Pfaden
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Bild konnte nicht gespeichert werden:\n{e}")
            return

        self._flash_until = time.time() + 0.15
        self.persist()
        self.session_count += 1
        self.count_label.configure(text=f"{self.session_count} Aufnahme(n) in dieser Sitzung")
        msg, color, toast = f"✓ Gespeichert: {path}", OK_GREEN, "✓  Bild gespeichert"

        if self.excel_var.get():
            try:
                self.append_excel(save_dir, path, now, frame)
                msg += "   ·   Excel aktualisiert"
                toast += "  ·  Excel aktualisiert"
            except PermissionError:
                msg += "   ·   Excel GESPERRT"
                toast, color = "⚠  Bild gespeichert – Excel gesperrt", WARN
                messagebox.showwarning(
                    APP_NAME,
                    "Das Bild wurde gespeichert, aber die Excel-Liste ist gerade geöffnet "
                    "(evtl. von einem Kollegen) und konnte nicht beschrieben werden.\n\n"
                    "Bitte Excel schließen und die Aufnahme ggf. nachtragen.")
            except Exception as e:
                msg += "   ·   Excel-Fehler"
                toast, color = "⚠  Bild gespeichert – Excel-Fehler", WARN
                messagebox.showwarning(APP_NAME, f"Excel-Eintrag fehlgeschlagen:\n{e}")
        self.set_status(msg, color)
        self.toast(toast, color)
        self.add_to_strip(path, frame)

    def append_excel(self, save_dir, img_path, now, frame):
        if Workbook is None:
            raise RuntimeError("openpyxl ist nicht installiert (pip install openpyxl)")
        xlsx = save_dir / (self.excel_name_var.get().strip() or DEFAULT_CONFIG["excel_name"])
        if xlsx.exists():
            wb = load_workbook(xlsx)
            ws = wb.active
        else:
            wb = Workbook()
            ws = wb.active
            ws.title = "Schäden"
            ws.append(EXCEL_HEADERS)
            for c in ws[1]:
                c.font = Font(bold=True, color="FFFFFF")
                c.fill = PatternFill("solid", fgColor="305496")
            for col, w in zip("ABCDEFGHIJ", (11, 9, 18, 24, 20, 40, 14, 40, 10, 22)):
                ws.column_dimensions[col].width = w
            ws.freeze_panes = "A2"

        r = ws.max_row + 1
        values = [now.strftime("%d.%m.%Y"), now.strftime("%H:%M:%S"),
                  self.hersteller_var.get().strip(), self.artikel_var.get().strip(),
                  self.serie_var.get().strip(), self.desc_text.get("1.0", "end").strip(),
                  self.bearbeiter_var.get().strip(), img_path.name]
        for c, v in enumerate(values, start=1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.alignment = Alignment(vertical="top", wrap_text=(c == 6))
        link = ws.cell(row=r, column=9, value="Bild öffnen")
        link.hyperlink = str(img_path)
        link.font = Font(color="0563C1", underline="single")
        link.alignment = Alignment(vertical="top")

        # openpyxl verliert beim Laden vorhandene Bilder -> Vorschaubilder neu einfügen
        thumb_dir = save_dir / ".thumbs"
        if self.thumb_var.get():
            if not thumb_dir.exists():
                thumb_dir.mkdir()
                try:  # Ordner unter Windows verstecken
                    import ctypes
                    ctypes.windll.kernel32.SetFileAttributesW(str(thumb_dir), 0x02)
                except Exception:
                    pass
            h, w = frame.shape[:2]
            tw = int(w * THUMB_HEIGHT / h)
            cv2.imencode(".jpg", cv2.resize(frame, (tw, THUMB_HEIGHT), interpolation=cv2.INTER_AREA),
                         [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tofile(str(thumb_dir / (img_path.stem + ".jpg")))
        if thumb_dir.is_dir():
            for row in range(2, r + 1):
                name = ws.cell(row=row, column=8).value
                t = thumb_dir / (Path(str(name)).stem + ".jpg") if name else None
                if t and t.exists():
                    xi = XLImage(str(t))
                    ws.add_image(xi, f"J{row}")
                    ws.row_dimensions[row].height = THUMB_HEIGHT * 0.78

        wb.save(xlsx)

    def on_close(self):
        self.persist()
        if self.cam:
            self.cam.stop()
            self.cam.join(timeout=2)
        self.root.destroy()


if __name__ == "__main__":
    cfg = load_config()
    ctk.set_appearance_mode(cfg.get("appearance", "Dark"))
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    App(root)
    root.mainloop()

"""
Mikroskop-Capture
-----------------
Live-Bild von der Capture-Card (Vision Engineering Makrolite 4K via HDMI),
Aufnahme per Button / Taste (F9, Leertaste oder Contour Shuttle Pro V2),
Markieren, Messen, Info-Leiste und Maßstab im Foto, Speichern im Serverordner
und Eintrag in eine Excel-Liste.
"""

import json
import math
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

# Media Foundation öffnet Capture-Cards sonst teils sehr langsam / gar nicht
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")
import cv2
import numpy as np
import customtkinter as ctk
from PIL import Image, ImageTk

import ui
import overlay
import netdrive
from annotate import AnnotateDialog
from icons import ctk_icon, pil_icon
from ui import (BG, CARD, FIELD, BORDER, BORDER_STRONG, FG, MUTED, SECONDARY, SECONDARY_HOVER, ACCENT,
                ACCENT_HOVER, ON_ACCENT, PREVIEW_BG, OK_GREEN, WARN, ERR, F, Card, button, entry, option,
                switch, field_label, keycap, focus_ring, set_error, error_label, set_toggle, mode_color, ask_form)

try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
except ImportError:
    Workbook = None

APP_NAME = "Mikroskop-Capture"
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).parent
CONFIG_FILE = APP_DIR / "config.json"

RESOLUTIONS = ["3840x2160", "2560x1440", "1920x1080", "1280x720"]
# Videomodi: (Backend, FourCC). "Auto" probiert der Reihe nach, bis ein echtes Bild kommt.
MODES = {
    "DirectShow · MJPG": (cv2.CAP_DSHOW, "MJPG"),
    "DirectShow · YUY2": (cv2.CAP_DSHOW, "YUY2"),
    "DirectShow · NV12": (cv2.CAP_DSHOW, "NV12"),
    "DirectShow · Standard": (cv2.CAP_DSHOW, None),
    "Media Foundation": (cv2.CAP_MSMF, None),
}
MODE_AUTO = "Auto"
AUTO_CHECK_MS = 5000     # so lange wird pro Modus auf ein Bild gewartet
LOST_AFTER_S = 3.0       # so lange ohne Bild -> Verbindung gilt als unterbrochen
OVERRIDE_S = 6.0         # Pflichtfeld-Warnung: erneutes F9 innerhalb dieser Zeit speichert trotzdem

# Excel-Spalten (Name, Breite). Vorhandene Listen werden anhand der Überschriften zugeordnet,
# fehlende Spalten werden hinten angefügt.
EXCEL_COLUMNS = [("Datum", 11), ("Uhrzeit", 9), ("Fall-Nr.", 16), ("Bild-Nr.", 8), ("Hersteller", 18),
                 ("Leiterplatte / Artikel-Nr.", 22), ("Serien-/Auftrags-Nr.", 18), ("Fehlerart", 22),
                 ("Schadensbeschreibung", 40), ("Vergrößerung", 14), ("Bearbeiter", 14), ("Dateiname", 44),
                 ("Link", 11), ("Vorschau", 22)]
THUMB_HEIGHT = 90   # px, Vorschaubild in Excel
STRIP_COUNT = 8     # letzte Aufnahmen in der Leiste
IMAGE_EXTS = (".png", ".jpg", ".jpeg")
NO_FEHLERART = "– keine Angabe –"
NOT_CALIBRATED = "Nicht kalibriert"

DEFAULT_FEHLERARTEN = [
    "Kratzer", "Lötfehler", "Lötbrücke / Kurzschluss", "Lifted Lead", "Tombstone",
    "Fehlendes Bauteil", "Falsches / verdrehtes Bauteil", "Verfärbung", "Delamination",
    "Riss / Bruch", "Verschmutzung", "Sonstiges",
]

# Serverordner (N: = \\w2k12-srv12\SE$) - als UNC-Pfad, damit es auch ohne verbundenes Laufwerk N: geht
SERVER_DIR = r"\\w2k12-srv12\SE$\08_Produktion_Fertigung\11_SMD\Bilder\Bilder Mikroskop"
OLD_DEFAULT_DIR = str(Path.home() / "Pictures" / "Mikroskop")

DEFAULT_CONFIG = {
    "camera_index": 0,
    "camera_name": "",
    "resolution": "3840x2160",
    "save_dir": SERVER_DIR,
    "excel_name": "Schadensdokumentation.xlsx",
    "excel_enabled": True,
    "excel_thumbnail": True,
    "image_format": "png",
    "bearbeiter": os.environ.get("USERNAME", ""),
    "hersteller_history": [],
    "appearance": "Dark",
    "video_mode": MODE_AUTO,
    "fehlerarten": DEFAULT_FEHLERARTEN,
    "calibrations": {},          # Name -> {"ppm": Pixel pro mm, "ref_w": Bildbreite bei Kalibrierung}
    "calibration": "",
    "annotate_after": True,
    "overlay_info": True,
    "overlay_scale": True,
    "crosshair": False,
    "live_scale": True,
    "server_user": r"se-elektronic.local\smd",   # Domänen-Benutzer; das Passwort wird nie hier gespeichert
    "server_drive": "N:",
}


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    if os.path.normcase(cfg["save_dir"]) == os.path.normcase(OLD_DEFAULT_DIR):
        cfg["save_dir"] = SERVER_DIR   # alte Standard-Einstellung auf den Server umstellen
    if cfg.get("server_user") == "smd":
        cfg["server_user"] = DEFAULT_CONFIG["server_user"]   # Anmeldung braucht den Domänen-Benutzer
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


def is_black(frame):
    """True, wenn das Bild praktisch komplett schwarz/einfarbig ist (kein Signal)."""
    small = cv2.resize(frame, (64, 36), interpolation=cv2.INTER_AREA)
    return small.mean() < 6 or small.std() < 2


def hide_dir(path):
    try:
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)
    except Exception:
        pass


def new_case_id():
    return datetime.now().strftime("%y%m%d-%H%M%S")


class CameraThread(threading.Thread):
    """Liest fortlaufend Frames, damit immer das aktuellste Bild in voller Auflösung bereitliegt."""

    def __init__(self, index, width, height, backend=cv2.CAP_DSHOW, fourcc="MJPG"):
        super().__init__(daemon=True)
        self.cap = cv2.VideoCapture(index, backend)
        if fourcc:
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        self.mode_name = ""
        self.got_signal = False  # mindestens ein echtes (nicht schwarzes) Bild empfangen
        self.last_ok = time.time()
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
            if ok and frame is not None:
                with self.lock:
                    self.frame = frame
                self.last_ok = time.time()
                if not self.got_signal and not is_black(frame):
                    self.got_signal = True
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


class App:
    def __init__(self, root):
        ui.init_fonts()
        self.root = root
        self.cfg = load_config()
        self._canvas_icons = {}
        self.cam = None
        self.cameras = []
        self._photo = None
        self._flash_until = 0
        self._strip_images = []
        self._strip_items = []
        self.session_count = 0
        self._gen = 0
        self._busy = False
        self._reconnecting = False
        self._override_until = 0
        self._fullscreen = False
        self._server_ok = None
        # Live-Ansicht
        self.zoom = 1.0
        self.center = None
        self._view = None            # (x0, y0, scale, ox, oy, fw, fh)
        self._measure = None         # [p1, p2] in Bildkoordinaten
        self._calib_mode = False
        self._pan_start = None
        # Fall / Rückgängig
        self.case_id = new_case_id()
        self.case_img = 0
        self.undo_stack = []

        root.title(APP_NAME)
        root.geometry("1480x920")
        root.minsize(1150, 760)
        root.configure(fg_color=BG)
        self._set_window_icon()
        self._build_ui()
        self._bind_keys()
        self.refresh_cameras()
        self.start_camera()
        self.check_server()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.update_preview()
        self.root.after(1000, self._watchdog)

    # ================= UI =================
    def _set_window_icon(self):
        try:
            import tempfile
            from PIL import ImageDraw
            ico = Path(tempfile.gettempdir()) / "mikroskop_capture.ico"
            img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
            ImageDraw.Draw(img).rounded_rectangle((0, 0, 255, 255), 56, fill="#16A34A")
            glyph = pil_icon("microscope", 192, "#FFFFFF", 2.0)
            img.paste(glyph, (32, 32), glyph)
            img.save(ico, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
            self.root.iconbitmap(str(ico))
        except Exception:
            pass

    def _build_ui(self):
        r = self.root
        r.grid_columnconfigure(0, weight=1)
        r.grid_rowconfigure(1, weight=1)

        # ---- Kopfzeile ----
        self.header = header = ctk.CTkFrame(r, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=20, pady=(16, 8))
        logo = ctk.CTkFrame(header, width=44, height=44, corner_radius=10, fg_color=ACCENT)
        logo.pack(side="left")
        logo.pack_propagate(False)
        ctk.CTkLabel(logo, text="", image=ctk_icon("microscope", 26, ON_ACCENT, 2.0)).pack(expand=True)
        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.pack(side="left", padx=(12, 0))
        ctk.CTkLabel(titles, text="Mikroskop-Capture", text_color=FG, anchor="w",
                     font=F(20, "bold", ui.FONT_DISPLAY)).pack(anchor="w")
        ctk.CTkLabel(titles, text="Leiterplatten-Schadensdokumentation · SMD", text_color=MUTED,
                     anchor="w", font=F(12)).pack(anchor="w")

        self.theme_btn = button(header, "", self.toggle_appearance, kind="ghost", width=110)
        self.theme_btn.pack(side="right")
        self._update_theme_button()
        button(header, " Tasten", self.show_help, icon="help", kind="ghost", width=100).pack(side="right", padx=(0, 8))
        self.count_label = ctk.CTkLabel(header, text="  0 Aufnahmen", image=ctk_icon("camera", 16, MUTED),
                                        compound="left", fg_color=SECONDARY, corner_radius=16, height=32,
                                        text_color=FG, font=F(12, "bold"), padx=12)
        self.count_label.pack(side="right", padx=12)

        # ---- Links: Hinweis-Banner + Vorschau + letzte Aufnahmen ----
        self.left = left = ctk.CTkFrame(r, fg_color="transparent")
        left.grid(row=1, column=0, sticky="nsew", padx=(20, 10), pady=(4, 0))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)

        self.banner = ctk.CTkFrame(left, fg_color=("#FEF2F2", "#3B1219"), corner_radius=10,
                                   border_width=1, border_color=ERR)
        ctk.CTkLabel(self.banner, text="", image=ctk_icon("warning", 20, ERR)).pack(side="left", padx=(14, 8), pady=10)
        self.banner_text = ctk.CTkLabel(self.banner, text="", text_color=FG, font=F(13), anchor="w",
                                        justify="left", wraplength=700)
        self.banner_text.pack(side="left", fill="x", expand=True, pady=10)
        button(self.banner, "Ordner wählen", self.choose_dir, kind="ghost", height=32).pack(side="right", padx=(6, 12))
        button(self.banner, " Erneut prüfen", self.check_server, icon="refresh", height=32).pack(side="right")
        button(self.banner, " Am Server anmelden", self.server_login, icon="folder", kind="primary",
               height=32, width=200).pack(side="right", padx=(0, 6))

        self.pv = pv = ctk.CTkFrame(left, fg_color=PREVIEW_BG, corner_radius=12, border_width=1, border_color=BORDER)
        pv.grid(row=1, column=0, sticky="nsew")
        self.canvas = tk.Canvas(pv, highlightthickness=0, bd=0, bg=mode_color(PREVIEW_BG), cursor="crosshair")
        self.canvas.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self._bind_canvas()

        # Werkzeugleiste unter dem Live-Bild
        tb = ctk.CTkFrame(pv, fg_color="transparent")
        tb.pack(fill="x", padx=8, pady=8)
        button(tb, "", lambda: self.zoom_by(1 / 1.25), icon="zoom_out", width=36, height=32).pack(side="left")
        self.zoom_btn = button(tb, "100 %", self.zoom_reset, width=70, height=32, font=F(12, family=ui.FONT_MONO))
        self.zoom_btn.pack(side="left", padx=4)
        button(tb, "", lambda: self.zoom_by(1.25), icon="zoom_in", width=36, height=32).pack(side="left")
        ctk.CTkFrame(tb, width=1, height=24, fg_color=BORDER_STRONG).pack(side="left", padx=10)
        self.cross_btn = button(tb, " Fadenkreuz", self.toggle_crosshair, icon="crosshair", height=32)
        self.cross_btn.pack(side="left")
        self.scale_btn = button(tb, " Maßstab", self.toggle_live_scale, icon="ruler", height=32)
        self.scale_btn.pack(side="left", padx=6)
        self.full_btn = button(tb, " Vollbild", self.toggle_fullscreen, icon="fullscreen", height=32)
        self.full_btn.pack(side="left")
        ctk.CTkLabel(tb, text="Ziehen = messen  ·  Rechts ziehen = verschieben  ·  Mausrad = Zoom",
                     text_color=MUTED, font=F(12)).pack(side="right", padx=6)
        set_toggle(self.cross_btn, self.cfg["crosshair"])
        set_toggle(self.scale_btn, self.cfg["live_scale"])

        self.strip_card = Card(left, "Letzte Aufnahmen", "image")
        self.strip_card.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        button(self.strip_card.head, "Ordner öffnen", self.open_dir, icon="folder_open", kind="ghost",
               height=32).pack(side="right")
        button(self.strip_card.head, " Rückgängig (F8)", self.undo_last, icon="undo", kind="ghost",
               height=32).pack(side="right", padx=(0, 8))
        self.strip = ctk.CTkFrame(self.strip_card.body, fg_color="transparent", height=104)
        self.strip.pack(fill="x", pady=(8, 0))
        self.strip_empty = ctk.CTkLabel(self.strip, text="  Noch keine Aufnahmen in diesem Ordner",
                                        image=ctk_icon("image", 20, MUTED), compound="left",
                                        text_color=MUTED, font=F(13))
        self.strip_empty.pack(pady=34)

        # ---- Rechts: Einstellungen ----
        self.side = side = ctk.CTkScrollableFrame(r, width=390, fg_color="transparent",
                                                  scrollbar_button_color=SECONDARY,
                                                  scrollbar_button_hover_color=SECONDARY_HOVER)
        side.grid(row=1, column=1, sticky="ns", padx=(10, 14), pady=(4, 0))
        self._build_board_card(side)
        self._build_measure_card(side)
        self._build_camera_card(side)
        self._build_storage_card(side)

        # ---- Aufnahme-Button (immer sichtbar) ----
        self.cap_frame = cap = ctk.CTkFrame(r, fg_color="transparent")
        cap.grid(row=2, column=1, sticky="ew", padx=(10, 20), pady=(10, 0))
        self.cap_btn = ctk.CTkButton(cap, text="  Aufnehmen", image=ctk_icon("camera", 26, ON_ACCENT, 2.0),
                                     compound="left", height=64, corner_radius=12, cursor="hand2",
                                     fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ON_ACCENT,
                                     text_color_disabled=ON_ACCENT, font=F(18, "bold", ui.FONT_DISPLAY),
                                     command=self.capture)
        self.cap_btn.pack(fill="x")
        keys = ctk.CTkFrame(cap, fg_color="transparent")
        keys.pack(pady=(8, 0))
        for k, t in (("F9", "Aufnahme"), ("F8", "rückgängig"), ("F7", "neuer Fall")):
            keycap(keys, k).pack(side="left", padx=(6, 3))
            ctk.CTkLabel(keys, text=t, text_color=MUTED, font=F(12)).pack(side="left")

        # ---- Statusleiste ----
        self.status_bar = bar = ctk.CTkFrame(r, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=24, pady=(10, 0))
        self.status_dot = ctk.CTkFrame(bar, width=10, height=10, corner_radius=5, fg_color=MUTED)
        self.status_dot.pack(side="left")
        self.status = ctk.CTkLabel(bar, text="Bereit", anchor="w", text_color=MUTED, font=F(12))
        self.status.pack(side="left", padx=(10, 0), fill="x", expand=True)
        self.bottom_pad = ctk.CTkFrame(r, height=14, fg_color="transparent")
        self.bottom_pad.grid(row=3, column=0)

    def _build_board_card(self, side):
        card = Card(side, "Angaben zur Leiterplatte", "clipboard")
        card.pack(fill="x", pady=(0, 12))
        b = card.body
        self.case_label = ctk.CTkLabel(b, text="", fg_color=SECONDARY, corner_radius=12, height=26,
                                       text_color=FG, font=F(12, "bold", ui.FONT_MONO), padx=10)
        self.case_label.pack(anchor="w", pady=(8, 0))
        self.hersteller_var = tk.StringVar()
        self.artikel_var = tk.StringVar()
        self.serie_var = tk.StringVar()
        self.bearbeiter_var = tk.StringVar(value=self.cfg["bearbeiter"])

        field_label(b, "Hersteller", required=True)
        self.hersteller_combo = focus_ring(ctk.CTkComboBox(
            b, variable=self.hersteller_var, values=self.cfg["hersteller_history"] or [""], height=36,
            corner_radius=8, fg_color=FIELD, border_color=BORDER_STRONG, border_width=1,
            button_color=SECONDARY, button_hover_color=SECONDARY_HOVER, text_color=FG,
            dropdown_fg_color=CARD, dropdown_hover_color=SECONDARY, dropdown_text_color=FG,
            font=F(13), dropdown_font=F(13)))
        self.hersteller_combo.pack(fill="x")
        self.hersteller_err = error_label(b)
        self.hersteller_var.set("")

        grid = ctk.CTkFrame(b, fg_color="transparent")
        grid.pack(fill="x")
        grid.grid_columnconfigure((0, 1), weight=1, uniform="c")
        cell = ctk.CTkFrame(grid, fg_color="transparent")
        cell.grid(row=0, column=0, sticky="new", padx=(0, 6))
        field_label(cell, "Artikel-Nr.", required=True)
        self.artikel_entry = entry(cell, self.artikel_var)
        self.artikel_entry.pack(fill="x")
        self.artikel_err = error_label(cell)
        cell = ctk.CTkFrame(grid, fg_color="transparent")
        cell.grid(row=0, column=1, sticky="new", padx=(6, 0))
        field_label(cell, "Serien-/Auftrags-Nr.")
        entry(cell, self.serie_var).pack(fill="x")

        self._required = [(self.hersteller_var, self.hersteller_combo, self.hersteller_err, "Hersteller"),
                          (self.artikel_var, self.artikel_entry, self.artikel_err, "Artikel-Nr.")]
        for var, widget, lbl, _ in self._required:
            var.trace_add("write", lambda *a, v=var, w=widget, l=lbl: v.get().strip() and set_error(w, l, None))

        field_label(b, "Fehlerart  (F6 wechselt)")
        self.fehler_menu = option(b, [NO_FEHLERART] + self.cfg["fehlerarten"], None, dynamic_resizing=False)
        self.fehler_menu.pack(fill="x")
        self.fehler_menu.set(NO_FEHLERART)

        field_label(b, "Schadensbeschreibung")
        self.desc_text = focus_ring(ctk.CTkTextbox(
            b, height=80, wrap="word", corner_radius=8, fg_color=FIELD, border_color=BORDER_STRONG,
            border_width=1, text_color=FG, font=F(13)))
        self.desc_text.pack(fill="x")
        field_label(b, "Bearbeiter")
        entry(b, self.bearbeiter_var).pack(fill="x")
        button(b, " Neuer Fall  (F7)", self.new_case, icon="plus", kind="ghost").pack(fill="x", pady=(14, 0))
        self._update_case_label()

    def _build_measure_card(self, side):
        card = Card(side, "Messen & Beschriftung", "ruler")
        card.pack(fill="x", pady=(0, 12))
        b = card.body
        field_label(b, "Vergrößerung / Kalibrierung")
        self.calib_menu = option(b, [NOT_CALIBRATED], self._select_calibration, dynamic_resizing=False)
        self.calib_menu.pack(fill="x")
        self.calib_info = ctk.CTkLabel(b, text="", text_color=MUTED, font=F(12), anchor="w", justify="left",
                                       wraplength=340)
        self.calib_info.pack(fill="x", pady=(6, 0))
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))
        button(row, " Kalibrieren", self.start_calibration, icon="ruler").pack(side="left", fill="x", expand=True)
        button(row, " Löschen", self.delete_calibration, icon="trash", kind="ghost", width=100).pack(side="left", padx=(8, 0))
        self._refresh_calibrations()

        self.annotate_var = tk.BooleanVar(value=self.cfg["annotate_after"])
        self.info_var = tk.BooleanVar(value=self.cfg["overlay_info"])
        self.scale_var = tk.BooleanVar(value=self.cfg["overlay_scale"])
        for text, var, pad in (("Nach Aufnahme markieren (Pfeile, Kreise, Text)", self.annotate_var, 16),
                               ("Info-Leiste ins Foto schreiben", self.info_var, 10),
                               ("Maßstab ins Foto einblenden", self.scale_var, 10)):
            switch(b, text, var).pack(anchor="w", pady=(pad, 0))

    def _build_camera_card(self, side):
        card = Card(side, "Kamera / Capture-Card", "video")
        card.pack(fill="x", pady=(0, 12))
        b = card.body
        field_label(b, "Gerät")
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x")
        self.cam_menu = option(row, ["—"], lambda _: self.start_camera(), dynamic_resizing=False)
        self.cam_menu.pack(side="left", fill="x", expand=True)
        button(row, "", self.refresh_cameras, icon="refresh", width=36).pack(side="left", padx=(8, 0))
        grid = ctk.CTkFrame(b, fg_color="transparent")
        grid.pack(fill="x")
        grid.grid_columnconfigure((0, 1), weight=1, uniform="c")
        for col, (text, attr, values) in enumerate((("Auflösung", "res_menu", RESOLUTIONS),
                                                    ("Videomodus", "mode_menu", [MODE_AUTO] + list(MODES)))):
            cell = ctk.CTkFrame(grid, fg_color="transparent")
            cell.grid(row=0, column=col, sticky="ew", padx=(0, 6) if col == 0 else (6, 0))
            field_label(cell, text)
            menu = option(cell, values, lambda _: self.start_camera(), dynamic_resizing=False)
            menu.pack(fill="x")
            setattr(self, attr, menu)
        self.res_menu.set(self.cfg["resolution"])
        self.mode_menu.set(self.cfg.get("video_mode", MODE_AUTO)
                           if self.cfg.get("video_mode") in MODES else MODE_AUTO)

    def _build_storage_card(self, side):
        card = Card(side, "Speicherort", "folder")
        card.pack(fill="x", pady=(0, 12))
        b = card.body
        field_label(b, "Zielordner")
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x")
        self.dir_var = tk.StringVar(value=self.cfg["save_dir"])
        ent = entry(row, self.dir_var)
        ent.pack(side="left", fill="x", expand=True)
        ent.bind("<FocusOut>", lambda e: self._dir_changed(), add="+")
        button(row, "", self.choose_dir, icon="folder_open", width=36).pack(side="left", padx=(8, 0))
        self.server_info = ctk.CTkLabel(b, text="", image=None, compound="left", text_color=MUTED,
                                        font=F(12), anchor="w", justify="left", wraplength=340)
        self.server_info.pack(fill="x", pady=(6, 0))
        button(b, " Am Server anmelden (Laufwerk verbinden)", self.server_login, icon="folder",
               kind="ghost").pack(fill="x", pady=(8, 0))

        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x", pady=(14, 0))
        ctk.CTkLabel(row, text="Bildformat", text_color=MUTED, font=F(12, "bold")).pack(side="left")
        self.fmt_seg = ctk.CTkSegmentedButton(
            row, values=["PNG", "JPG"], width=140, height=32, corner_radius=8, font=F(12, "bold"),
            fg_color=(SECONDARY[0], FIELD[1]), unselected_color=(SECONDARY[0], FIELD[1]),
            unselected_hover_color=SECONDARY_HOVER, selected_color=("#FFFFFF", "#475569"),
            selected_hover_color=("#FFFFFF", "#475569"), text_color=FG)
        self.fmt_seg.set(self.cfg["image_format"].upper())
        self.fmt_seg.pack(side="right")

        self.excel_var = tk.BooleanVar(value=self.cfg["excel_enabled"])
        self.thumb_var = tk.BooleanVar(value=self.cfg["excel_thumbnail"])
        switch(b, "In Excel-Liste eintragen", self.excel_var).pack(anchor="w", pady=(16, 0))
        switch(b, "Vorschaubild in Excel einfügen", self.thumb_var).pack(anchor="w", pady=(10, 0))
        field_label(b, "Excel-Datei")
        self.excel_name_var = tk.StringVar(value=self.cfg["excel_name"])
        entry(b, self.excel_name_var).pack(fill="x")

    def _update_theme_button(self):
        dark = ctk.get_appearance_mode() == "Dark"
        self.theme_btn.configure(text="  Hell" if dark else "  Dunkel",
                                 image=ctk_icon("sun" if dark else "moon", 18, FG))

    def toggle_appearance(self):
        mode = "Light" if ctk.get_appearance_mode() == "Dark" else "Dark"
        ctk.set_appearance_mode(mode)
        self.cfg["appearance"] = mode
        self.canvas.configure(bg=mode_color(PREVIEW_BG))
        self._update_theme_button()
        save_config(self.cfg)

    def set_status(self, text, color=MUTED):
        self.status.configure(text=text, text_color=FG if color == MUTED else color)
        self.status_dot.configure(fg_color=color)

    def toast(self, text, color=OK_GREEN, ms=3500, ok=True):
        """Kurze Einblendung unten im Vorschaubild (verschwindet automatisch)."""
        self._toast = (text, color, time.time() + ms / 1000, ok)

    # ================= Tastatur / Shuttle Pro =================
    def _bind_keys(self):
        r = self.root
        keys = {
            "<F9>": self.capture, "<F8>": self.undo_last, "<F7>": self.new_case,
            "<F6>": lambda: self.cycle_fehlerart(1), "<Shift-F6>": lambda: self.cycle_fehlerart(-1),
            "<F3>": self.toggle_crosshair, "<F11>": self.toggle_fullscreen, "<F1>": self.show_help,
            "<Control-plus>": lambda: self.zoom_by(1.25), "<Control-equal>": lambda: self.zoom_by(1.25),
            "<Control-KP_Add>": lambda: self.zoom_by(1.25), "<Control-minus>": lambda: self.zoom_by(1 / 1.25),
            "<Control-KP_Subtract>": lambda: self.zoom_by(1 / 1.25), "<Control-0>": self.zoom_reset,
            "<Control-KP_0>": self.zoom_reset, "<Control-KP_Insert>": self.zoom_reset,
            "<Control-Left>": lambda: self.pan_by(-1, 0), "<Control-Right>": lambda: self.pan_by(1, 0),
            "<Control-Up>": lambda: self.pan_by(0, -1), "<Control-Down>": lambda: self.pan_by(0, 1),
            "<Escape>": self._escape,
        }
        for seq, fn in keys.items():
            r.bind_all(seq, lambda e, f=fn: None if self._busy else (f(), "break")[1])
        r.bind("<space>", self._space_capture)

    def _space_capture(self, event):
        # Leertaste nur auslösen, wenn nicht gerade in ein Textfeld geschrieben wird
        if not self._busy and not isinstance(event.widget, (tk.Entry, tk.Text)):
            self.capture()

    def _escape(self):
        if self._calib_mode:
            self._calib_mode = False
            self._measure = None
            self.set_status("Kalibrierung abgebrochen.")
        elif self._fullscreen:
            self.toggle_fullscreen()
        else:
            self._measure = None

    def show_help(self):
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Tastenbelegung")
        dlg.configure(fg_color=CARD)
        dlg.resizable(False, False)
        dlg.transient(self.root)
        body = ctk.CTkFrame(dlg, fg_color="transparent")
        body.pack(padx=26, pady=22)
        ctk.CTkLabel(body, text="Tastenbelegung", text_color=FG, font=F(18, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w")
        ctk.CTkLabel(body, text="Diese Tasten im Contour-ShuttlePro-Programm auf die Shuttle-Tasten legen.",
                     text_color=MUTED, font=F(12)).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 12))
        rows = [
            ("F9", "Aufnahme", "große Taste"),
            ("F8", "Letzte Aufnahme rückgängig", "Taste"),
            ("F7", "Neuer Fall", "Taste"),
            ("F6  /  Umschalt+F6", "Fehlerart vor / zurück", "Taste"),
            ("Strg +  /  Strg −", "Zoom rein / raus", "Jog-Rad rechts / links"),
            ("Strg 0", "Zoom zurücksetzen", "Taste"),
            ("Strg + Pfeiltasten", "Ausschnitt verschieben", "Shuttle-Ring"),
            ("F3", "Fadenkreuz ein/aus", "Taste"),
            ("F11  /  Esc", "Vollbild ein / aus", "Taste"),
            ("F1", "Diese Übersicht", ""),
            ("", "", ""),
            ("Mausrad", "Zoom an der Mausposition", ""),
            ("Linke Maus ziehen", "Strecke im Live-Bild messen", ""),
            ("Rechte Maus ziehen", "Ausschnitt verschieben", ""),
            ("Doppelklick", "Zoom zurücksetzen", ""),
            ("", "", ""),
            ("Enter / Esc", "Markier-Fenster: speichern / ohne Markierung", ""),
            ("Strg+Z  ·  1–5", "Markier-Fenster: rückgängig · Werkzeug wählen", ""),
        ]
        ctk.CTkLabel(body, text="TASTE", text_color=MUTED, font=F(11, "bold")).grid(row=2, column=0, sticky="w")
        ctk.CTkLabel(body, text="FUNKTION", text_color=MUTED, font=F(11, "bold")).grid(row=2, column=1, sticky="w", padx=16)
        ctk.CTkLabel(body, text="SHUTTLE PRO V2 (Vorschlag)", text_color=MUTED, font=F(11, "bold")).grid(
            row=2, column=2, sticky="w")
        for i, (k, f, s) in enumerate(rows, start=3):
            if not k:
                ctk.CTkFrame(body, height=1, fg_color=BORDER).grid(row=i, column=0, columnspan=3, sticky="ew", pady=6)
                continue
            keycap(body, k).grid(row=i, column=0, sticky="w", pady=3)
            ctk.CTkLabel(body, text=f, text_color=FG, font=F(13)).grid(row=i, column=1, sticky="w", padx=16)
            ctk.CTkLabel(body, text=s, text_color=MUTED, font=F(12)).grid(row=i, column=2, sticky="w")
        button(body, "Schließen", dlg.destroy, kind="primary", width=120).grid(
            row=len(rows) + 4, column=2, sticky="e", pady=(18, 0))
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        dlg.after(80, lambda: (dlg.lift(), dlg.focus_force()))

    # ================= Fall / Fehlerart / Pflichtfelder =================
    def _update_case_label(self):
        self.case_label.configure(text=f"Fall {self.case_id}  ·  Bild {self.case_img}")

    def new_case(self):
        self.case_id = new_case_id()
        self.case_img = 0
        self.artikel_var.set("")
        self.serie_var.set("")
        self.desc_text.delete("1.0", "end")
        self.fehler_menu.set(NO_FEHLERART)
        for var, widget, lbl, _ in self._required:
            set_error(widget, lbl, None)
        self._update_case_label()
        self.set_status(f"Neuer Fall {self.case_id} angelegt – Hersteller und Bearbeiter wurden übernommen.")
        self.toast(f"Neuer Fall {self.case_id}", OK_GREEN)

    def cycle_fehlerart(self, step):
        values = [NO_FEHLERART] + self.cfg["fehlerarten"]
        cur = self.fehler_menu.get()
        i = values.index(cur) if cur in values else 0
        new = values[(i + step) % len(values)]
        self.fehler_menu.set(new)
        self.toast(f"Fehlerart: {new}", OK_GREEN, ms=1800)

    def fehlerart(self):
        v = self.fehler_menu.get()
        return "" if v == NO_FEHLERART else v

    def _check_required(self):
        """False, wenn Pflichtfelder fehlen (erneutes Auslösen innerhalb kurzer Zeit speichert trotzdem)."""
        missing = [(w, l, name) for v, w, l, name in self._required if not v.get().strip()]
        if not missing:
            return True
        if time.time() < self._override_until:
            self._override_until = 0
            return True
        for w, l, name in missing:
            set_error(w, l, f"{name} ist ein Pflichtfeld")
        names = " und ".join(n for _, _, n in missing)
        verb = "fehlen" if len(missing) > 1 else "fehlt"
        self._override_until = time.time() + OVERRIDE_S
        self.set_status(f"{names} {verb} – bitte ausfüllen. Erneut F9 innerhalb von "
                        f"{OVERRIDE_S:.0f} s speichert trotzdem.", WARN)
        self.toast(f"{names} {verb}  ·  nochmal F9 = trotzdem speichern", WARN, ms=OVERRIDE_S * 1000, ok=False)
        return False

    # ================= Kamera =================
    def refresh_cameras(self):
        self.cameras = list_cameras()
        labels = [f"{i}: {n}" for i, n in self.cameras]
        if not labels:
            self.cam_menu.configure(values=["Keine Kamera gefunden"])
            self.cam_menu.set("Keine Kamera gefunden")
            self.set_status("Keine Kamera / Capture-Card gefunden!", ERR)
            return
        self.cam_menu.configure(values=labels)
        by_name = next((l for (i, n), l in zip(self.cameras, labels) if n == self.cfg.get("camera_name")), None)
        by_idx = next((l for (i, _), l in zip(self.cameras, labels) if i == self.cfg["camera_index"]), None)
        self.cam_menu.set(by_name or by_idx or labels[0])

    def selected_index(self):
        v = self.cam_menu.get()
        return int(v.split(":")[0]) if v and v[0].isdigit() else None

    def selected_name(self):
        v = self.cam_menu.get()
        return v.split(": ", 1)[1] if v and v[0].isdigit() and ": " in v else ""

    def stop_camera(self, wait=True):
        if self.cam:
            self.cam.stop()
            if wait:
                self.cam.join(timeout=1.5)
            self.cam = None

    def start_camera(self):
        self._gen += 1   # bricht laufende Auto-Suche / Neuverbindung ab
        self._reconnecting = False
        self.stop_camera()
        if self.selected_index() is None:
            return
        mode = self.mode_menu.get()
        if mode == MODE_AUTO:
            self._auto_try(list(MODES), self._gen)
        elif self._open_mode(mode):
            self.set_status(f"Kamera läuft ({mode}) – bereit für Aufnahmen.")

    def _open_mode(self, mode, idx=None):
        idx = self.selected_index() if idx is None else idx
        w, h = map(int, self.res_menu.get().split("x"))
        backend, fourcc = MODES[mode]
        self.set_status(f"Starte Kamera ({mode})…")
        self.root.update_idletasks()
        cam = CameraThread(idx, w, h, backend, fourcc)
        cam.mode_name = mode
        if not cam.running:
            self.set_status(f"Kamera {idx} konnte mit {mode} nicht geöffnet werden.", ERR)
            return False
        cam.start()
        self.cam = cam
        return True

    def _auto_try(self, queue, gen):
        """Probiert die Videomodi nacheinander, bis ein echtes (nicht schwarzes) Bild kommt."""
        if gen != self._gen:
            return
        self.stop_camera()
        if not queue:
            self._open_mode(next(iter(MODES)))
            self.set_status("Kein Bildsignal in allen Videomodi – HDMI-Quelle, Kabel und "
                            "Auflösung des Mikroskops prüfen (siehe Diagnose).", WARN)
            return
        mode, rest = queue[0], queue[1:]
        if not self._open_mode(mode):
            self.root.after(10, lambda: self._auto_try(rest, gen))
            return

        def check():
            if gen != self._gen or not self.cam:
                return
            if self.cam.got_signal:
                self.set_status(f"Kamera läuft (Auto → {mode}) – bereit für Aufnahmen.", OK_GREEN)
            else:
                self._auto_try(rest, gen)
        self.root.after(AUTO_CHECK_MS, check)

    # ---- automatische Neuverbindung ----
    def _watchdog(self):
        cam = self.cam
        if cam and not self._reconnecting and cam.got_signal and time.time() - cam.last_ok > LOST_AFTER_S:
            self._begin_reconnect()
        self.root.after(1000, self._watchdog)

    def _begin_reconnect(self):
        self._reconnecting = True
        self._reconnect_mode = self.cam.mode_name or next(iter(MODES))
        self._reconnect_name = self.selected_name()
        self._gen += 1
        self.stop_camera(wait=False)   # read() kann bei abgezogenem USB hängen -> nicht warten
        self.set_status("Verbindung zur Kamera unterbrochen – verbinde automatisch neu…", WARN)
        self.toast("Kamera getrennt – verbinde neu…", WARN, ok=False, ms=4000)
        gen = self._gen
        self.root.after(1500, lambda: self._try_reconnect(gen, 1))

    def _try_reconnect(self, gen, attempt):
        if gen != self._gen:
            return
        cams = list_cameras()
        idx = next((i for i, n in cams if n == self._reconnect_name), None)
        if idx is not None:
            self.cameras = cams
            labels = [f"{i}: {n}" for i, n in cams]
            self.cam_menu.configure(values=labels)
            self.cam_menu.set(f"{idx}: {self._reconnect_name}")
            if self._open_mode(self._reconnect_mode, idx):
                def check():
                    if gen != self._gen:
                        return
                    if self.cam and self.cam.got_signal:
                        self._reconnecting = False
                        self.set_status("Kamera wieder verbunden – bereit für Aufnahmen.", OK_GREEN)
                        self.toast("Kamera wieder verbunden", OK_GREEN)
                    else:
                        self.stop_camera(wait=False)
                        self.root.after(2000, lambda: self._try_reconnect(gen, attempt + 1))
                self.root.after(4000, check)
                return
        self.set_status(f"Kamera „{self._reconnect_name}“ nicht gefunden – neuer Versuch ({attempt}) …", WARN)
        self.root.after(2000, lambda: self._try_reconnect(gen, attempt + 1))

    # ================= Live-Ansicht: Zoom, Messen, Fadenkreuz =================
    def _bind_canvas(self):
        c = self.canvas
        c.bind("<MouseWheel>", self._wheel)
        c.bind("<ButtonPress-1>", self._m_press)
        c.bind("<B1-Motion>", self._m_move)
        c.bind("<ButtonRelease-1>", self._m_release)
        c.bind("<Double-Button-1>", lambda e: self.zoom_reset())
        for b in (2, 3):
            c.bind(f"<ButtonPress-{b}>", self._pan_press)
            c.bind(f"<B{b}-Motion>", self._pan_move)

    def to_frame(self, x, y):
        if not self._view:
            return None
        x0, y0, s, ox, oy, fw, fh = self._view
        return (min(max(x0 + (x - ox) / s, 0), fw - 1), min(max(y0 + (y - oy) / s, 0), fh - 1))

    def to_canvas(self, p):
        x0, y0, s, ox, oy, _, _ = self._view
        return ox + (p[0] - x0) * s, oy + (p[1] - y0) * s

    def zoom_by(self, factor, anchor=None):
        if not self._view:
            return
        _, _, _, _, _, fw, fh = self._view
        old = self.zoom
        self.zoom = min(max(self.zoom * factor, 1.0), 12.0)
        if self.zoom == 1.0:
            self.center = None
        else:
            cx, cy = self.center or (fw / 2, fh / 2)
            if anchor:   # Punkt unter der Maus bleibt stehen
                cx = anchor[0] + (cx - anchor[0]) * old / self.zoom
                cy = anchor[1] + (cy - anchor[1]) * old / self.zoom
            self.center = (cx, cy)
        self.zoom_btn.configure(text=f"{self.zoom * 100:.0f} %")

    def zoom_reset(self):
        self.zoom, self.center = 1.0, None
        self.zoom_btn.configure(text="100 %")

    def pan_by(self, dx, dy):
        if self.zoom > 1 and self._view:
            _, _, _, _, _, fw, fh = self._view
            cx, cy = self.center or (fw / 2, fh / 2)
            step = 0.1 * fw / self.zoom
            self.center = (cx + dx * step, cy + dy * step)

    def _wheel(self, e):
        p = self.to_frame(e.x, e.y)
        self.zoom_by(1.2 if e.delta > 0 else 1 / 1.2, p)

    def _pan_press(self, e):
        self._pan_start = (e.x, e.y, self.center)

    def _pan_move(self, e):
        if not (self._pan_start and self._view and self.zoom > 1):
            return
        x, y, center = self._pan_start
        _, _, s, _, _, fw, fh = self._view
        cx, cy = center or (fw / 2, fh / 2)
        self.center = (cx - (e.x - x) / s, cy - (e.y - y) / s)

    def _m_press(self, e):
        p = self.to_frame(e.x, e.y)
        self._measure = [p, p] if p else None
        self._press_xy = (e.x, e.y)

    def _m_move(self, e):
        if self._measure:
            self._measure[1] = self.to_frame(e.x, e.y)

    def _m_release(self, e):
        if not self._measure:
            return
        if math.dist(self._press_xy, (e.x, e.y)) < 4:   # Klick ohne Ziehen -> Messung löschen
            self._measure = None
            return
        self._measure[1] = self.to_frame(e.x, e.y)
        if self._calib_mode:
            self._finish_calibration(math.dist(*self._measure))

    def toggle_crosshair(self):
        self.cfg["crosshair"] = not self.cfg["crosshair"]
        set_toggle(self.cross_btn, self.cfg["crosshair"])

    def toggle_live_scale(self):
        self.cfg["live_scale"] = not self.cfg["live_scale"]
        set_toggle(self.scale_btn, self.cfg["live_scale"])

    def toggle_fullscreen(self):
        self._fullscreen = not self._fullscreen
        widgets = (self.header, self.side, self.cap_frame, self.status_bar, self.strip_card, self.bottom_pad)
        if self._fullscreen:
            self.banner.grid_remove()
            for w in widgets:
                w.grid_remove()
            self.left.grid_configure(row=0, rowspan=4, columnspan=2, padx=0, pady=0)
            self.root.attributes("-fullscreen", True)
            self.toast("Vollbild  ·  F11 oder Esc zum Beenden  ·  F9 Aufnahme", OK_GREEN, ms=3000)
        else:
            self.root.attributes("-fullscreen", False)
            self.left.grid_configure(row=1, rowspan=1, columnspan=1, padx=(20, 10), pady=(4, 0))
            for w in widgets:
                w.grid()
            if self._server_ok is False:
                self.banner.grid()
        set_toggle(self.full_btn, self._fullscreen)

    # ---- Kalibrierung ----
    def active_ppm(self, frame_w):
        """Pixel pro mm für ein Bild der Breite frame_w (None = nicht kalibriert)."""
        cal = self.cfg["calibrations"].get(self.cfg.get("calibration", ""))
        if not cal:
            return None
        return cal["ppm"] * frame_w / cal["ref_w"]

    def _refresh_calibrations(self):
        names = list(self.cfg["calibrations"])
        self.calib_menu.configure(values=names or [NOT_CALIBRATED])
        cur = self.cfg.get("calibration", "")
        if cur not in names:
            cur = names[0] if names else ""
            self.cfg["calibration"] = cur
        self.calib_menu.set(cur or NOT_CALIBRATED)
        cal = self.cfg["calibrations"].get(cur)
        if cal:
            self.calib_info.configure(
                text=f"1 mm = {cal['ppm']:.1f} px (bei {cal['ref_w']} px Bildbreite).\n"
                     "Beim Wechsel der Vergrößerung am Mikroskop hier mit umstellen!",
                text_color=MUTED)
        else:
            self.calib_info.configure(text="Noch nicht kalibriert – Messwerte werden in Pixel angezeigt.",
                                      text_color=WARN)

    def _select_calibration(self, name):
        if name in self.cfg["calibrations"]:
            self.cfg["calibration"] = name
            self._refresh_calibrations()
            save_config(self.cfg)
            self.toast(f"Vergrößerung: {name}", OK_GREEN, ms=1800)

    def start_calibration(self):
        if not (self.cam and self.cam.get_frame() is not None):
            self.set_status("Für die Kalibrierung wird ein Live-Bild benötigt.", ERR)
            return
        self._calib_mode = True
        self._measure = None
        self.set_status("Kalibrieren: Lineal/Kalibriernormal unter das Mikroskop legen und im Live-Bild "
                        "eine Linie über eine bekannte Länge ziehen (Esc = abbrechen). Tipp: vorher zoomen.", WARN)

    def _finish_calibration(self, px):
        self._calib_mode = False
        fw = self._view[5]
        cur = self.cfg.get("calibration") or "Vergrößerung 1"
        vals = ask_form(self.root, "Kalibrierung speichern",
                        [("Länge der gezogenen Strecke in mm", "1"), ("Name der Vergrößerung", cur)],
                        ok_text="Speichern",
                        message=f"Gezogene Strecke: {px:.1f} Pixel. Wie lang ist sie in Wirklichkeit? "
                                "Den Namen so wählen, wie die Zoomstufe am Mikroskop heißt (z. B. „Zoom 4x“).")
        self._measure = None
        if not vals:
            self.set_status("Kalibrierung abgebrochen.")
            return
        try:
            mm = float(vals[0].replace(",", "."))
            if mm <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror(APP_NAME, "Bitte eine gültige Länge in mm eingeben (z. B. 1 oder 0,5).")
            return
        name = vals[1] or cur
        self.cfg["calibrations"][name] = {"ppm": px / mm, "ref_w": fw}
        self.cfg["calibration"] = name
        save_config(self.cfg)
        self._refresh_calibrations()
        self.set_status(f"Kalibriert: „{name}“ – 1 mm = {px / mm:.1f} px.", OK_GREEN)
        self.toast(f"Kalibrierung „{name}“ gespeichert", OK_GREEN)

    def delete_calibration(self):
        name = self.cfg.get("calibration")
        if not name or name not in self.cfg["calibrations"]:
            return
        if messagebox.askyesno(APP_NAME, f"Kalibrierung „{name}“ löschen?"):
            del self.cfg["calibrations"][name]
            self.cfg["calibration"] = ""
            save_config(self.cfg)
            self._refresh_calibrations()

    # ---- Zeichnen der Live-Ansicht ----
    def _canvas_icon(self, name, size, color):
        key = (name, size, color)
        if key not in self._canvas_icons:
            self._canvas_icons[key] = ImageTk.PhotoImage(pil_icon(name, size, color, 1.5))
        return self._canvas_icons[key]

    def update_preview(self):
        c = self.canvas
        cw, ch = max(c.winfo_width(), 10), max(c.winfo_height(), 10)
        c.delete("all")
        frame = self.cam.get_frame() if (self.cam and not self._reconnecting) else None
        if frame is not None:
            fh, fw = frame.shape[:2]
            vw, vh = fw / self.zoom, fh / self.zoom
            cx, cy = self.center or (fw / 2, fh / 2)
            cx = min(max(cx, vw / 2), fw - vw / 2)
            cy = min(max(cy, vh / 2), fh - vh / 2)
            if self.zoom > 1:
                self.center = (cx, cy)
            x0, y0 = int(round(cx - vw / 2)), int(round(cy - vh / 2))
            crop = frame[y0:y0 + max(int(vh), 1), x0:x0 + max(int(vw), 1)]
            s = min(cw / crop.shape[1], ch / crop.shape[0])
            dw, dh = max(int(crop.shape[1] * s), 1), max(int(crop.shape[0] * s), 1)
            ox, oy = (cw - dw) // 2, (ch - dh) // 2
            self._view = (x0, y0, s, ox, oy, fw, fh)
            small = cv2.resize(crop, (dw, dh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
            if time.time() < self._flash_until:   # Blitz-Effekt nach Aufnahme
                small = cv2.addWeighted(small, 0.35, np.full_like(small, 255), 0.65, 0)
            self._photo = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB)))
            c.create_image(ox, oy, image=self._photo, anchor="nw")

            ppm = self.active_ppm(fw)
            if self.cfg["crosshair"]:
                self._draw_crosshair(ox, oy, dw, dh)
            if self.cfg["live_scale"] and ppm:
                self._draw_live_scale(ox, oy, dh, ppm * s)
            if self._measure:
                self._draw_measure(ppm)

            aw, ah = self.cam.actual_size
            x = self._badge(16, 16, "LIVE", "#DC2626", dot=True)
            info = f"{aw}×{ah}  ·  {self.cam.fps:4.1f} fps  ·  {self.cam.mode_name}"
            x = self._badge(x + 10, 16, info, "#0F172A", mono=True)
            if self.zoom > 1:
                x = self._badge(x + 10, 16, f"Zoom {self.zoom:.1f}×", "#047857", mono=True)
            cal = self.cfg.get("calibration")
            if cal:
                self._badge(x + 10, 16, cal, "#334155")
            if self._calib_mode:
                self._banner_on_canvas(cw // 2, 70, "Kalibrieren: Linie über eine bekannte Länge ziehen  ·  Esc = abbrechen")
        else:
            self._view = None
            muted = mode_color(MUTED)
            if self._reconnecting:
                title, sub, icon = ("Verbindung zur Kamera unterbrochen",
                                    "Die Neuverbindung läuft automatisch – USB-Kabel prüfen", "warning")
            elif self.cam is not None:
                title, sub, icon = "Warte auf Bildsignal…", "Videomodus wird automatisch gesucht", "video"
            else:
                title, sub, icon = ("Keine Kamera aktiv",
                                    "Capture-Card rechts unter „Kamera“ auswählen und Liste aktualisieren", "video")
            c.create_image(cw // 2, ch // 2 - 46, image=self._canvas_icon(icon, 56, muted))
            c.create_text(cw // 2, ch // 2 + 8, fill=mode_color(FG), font=(ui.FONT_UI, 15, "bold"), text=title)
            c.create_text(cw // 2, ch // 2 + 36, fill=muted, font=(ui.FONT_UI, 11), justify="center", text=sub)

        toast = getattr(self, "_toast", None)
        if toast and time.time() < toast[2]:
            self._toast_box(cw // 2, ch - 44, toast[0], mode_color(toast[1]), toast[3])
        self.root.after(33, self.update_preview)

    def _draw_crosshair(self, ox, oy, dw, dh):
        c = self.canvas
        cx, cy = ox + dw / 2, oy + dh / 2
        r = min(dw, dh) * 0.06
        for col, w in (("#0F172A", 3), ("#22C55E", 1)):
            c.create_line(ox, cy, cx - r, cy, fill=col, width=w)
            c.create_line(cx + r, cy, ox + dw, cy, fill=col, width=w)
            c.create_line(cx, oy, cx, cy - r, fill=col, width=w)
            c.create_line(cx, cy + r, cx, oy + dh, fill=col, width=w)
            c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=col, width=w)
            c.create_oval(cx - 2, cy - 2, cx + 2, cy + 2, fill=col, outline="")

    def _draw_live_scale(self, ox, oy, dh, screen_ppm):
        c = self.canvas
        mm = overlay.nice_scale_mm(160 / screen_ppm)
        if not mm:
            return
        bar = mm * screen_ppm
        x1, y = ox + 20, oy + dh - 22
        label = overlay.scale_label(mm)
        t = c.create_text(x1 + bar / 2, y - 12, text=label, fill="#F8FAFC", font=(ui.FONT_UI, 10, "bold"))
        bx1, by1, bx2, by2 = c.bbox(t)
        box = self._round_rect(min(x1, bx1) - 10, by1 - 6, max(x1 + bar, bx2) + 10, y + 10, 8, fill="#0F172A", outline="")
        c.tag_lower(box, t)
        c.create_rectangle(x1, y - 2, x1 + bar, y + 2, fill="#F8FAFC", outline="")
        for x in (x1, x1 + bar):
            c.create_rectangle(x - 1, y - 6, x + 1, y + 6, fill="#F8FAFC", outline="")

    def _draw_measure(self, ppm):
        c = self.canvas
        p1, p2 = self._measure
        (x1, y1), (x2, y2) = self.to_canvas(p1), self.to_canvas(p2)
        col = "#FACC15" if not self._calib_mode else "#22D3EE"
        for w, cl in ((5, "#0F172A"), (2, col)):
            c.create_line(x1, y1, x2, y2, fill=cl, width=w, capstyle="round")
            for x, y in ((x1, y1), (x2, y2)):
                c.create_oval(x - 4, y - 4, x + 4, y + 4, outline=cl, width=w - 1 if w > 2 else 2)
        px = math.dist(p1, p2)
        text = overlay.length_label(px, None if self._calib_mode else ppm)
        if not self._calib_mode and ppm:
            text += f"   ({px:.0f} px)"
        self._badge((x1 + x2) / 2 + 12, (y1 + y2) / 2 + 8, text, "#0F172A", mono=True)

    def _banner_on_canvas(self, cx, cy, text):
        c = self.canvas
        t = c.create_text(cx, cy, text=text, fill="#0F172A", font=(ui.FONT_UI, 12, "bold"))
        x1, y1, x2, y2 = c.bbox(t)
        box = self._round_rect(x1 - 16, y1 - 10, x2 + 16, y2 + 10, 10, fill="#22D3EE", outline="")
        c.tag_lower(box, t)

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
               x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def _badge(self, x, y, text, color, dot=False, mono=False):
        c = self.canvas
        pad = 10
        tx = x + pad + (14 if dot else 0)
        t = c.create_text(tx, y + 6, text=text, fill="#F8FAFC", anchor="nw",
                          font=(ui.FONT_MONO if mono else ui.FONT_UI, 10, "bold"))
        _, y1, x2, y2 = c.bbox(t)
        box = self._round_rect(x, y, x2 + pad, y2 + 6, 8, fill=color, outline="")
        c.tag_lower(box, t)
        if dot:
            cy = (y + y2 + 6) / 2
            c.create_oval(x + pad, cy - 4, x + pad + 8, cy + 4, fill="#FFFFFF", outline="")
        return x2 + pad

    def _toast_box(self, cx, cy, text, color, ok):
        c = self.canvas
        t = c.create_text(cx + 14, cy, text=text, fill="#F8FAFC", font=(ui.FONT_UI, 12, "bold"))
        x1, y1, x2, y2 = c.bbox(t)
        box = self._round_rect(x1 - 42, y1 - 12, x2 + 18, y2 + 12, 12, fill="#1E293B", outline=color, width=2)
        c.tag_lower(box, t)
        c.create_image(x1 - 20, cy, image=self._canvas_icon("check" if ok else "warning", 20, color))

    # ================= Speicherort prüfen =================
    def _dir_changed(self):
        if os.path.normcase(self.dir_var.get().strip()) != os.path.normcase(self.cfg["save_dir"]):
            self.persist()
            self.check_server()

    def check_server(self):
        d = Path(self.dir_var.get().strip())
        self.server_info.configure(text="  Prüfe Schreibzugriff…", image=ctk_icon("refresh", 14, MUTED),
                                   text_color=MUTED)

        def work():
            try:
                d.mkdir(parents=True, exist_ok=True)
                probe = d / f".schreibtest_{os.environ.get('COMPUTERNAME', 'pc')}.tmp"
                probe.write_bytes(b"ok")
                probe.unlink()
                err = None
            except Exception as e:
                err = str(e)
            self.root.after(0, lambda: self._server_result(d, err))

        threading.Thread(target=work, daemon=True).start()

    def _server_result(self, d, err):
        self._server_ok = err is None
        if err is None:
            self.server_info.configure(text="  Schreibzugriff OK", image=ctk_icon("check", 14, OK_GREEN),
                                       text_color=OK_GREEN)
            self.banner.grid_remove()
            self.load_recent()
        else:
            self.server_info.configure(text="  Kein Schreibzugriff – Details oben", image=ctk_icon("warning", 14, ERR),
                                       text_color=ERR)
            self.banner_text.configure(
                text=f"Speicherort nicht beschreibbar – Bilder können so nicht gespeichert werden.\n"
                     f"{d}\nGrund: {err}\nNetzwerk/VPN, Laufwerk N: und Schreibrechte des Windows-Benutzers prüfen.")
            if not self._fullscreen:
                self.banner.grid(row=0, column=0, sticky="ew", pady=(0, 10))
            self.set_status("Speicherort nicht beschreibbar – siehe Hinweis oben.", ERR)

    def _share_for(self, path):
        return netdrive.share_root(str(path)) or netdrive.share_root(SERVER_DIR)

    def server_login(self):
        """Wie das Skript 'net use N: /delete' + 'net use /persistent:no N: \\\\server\\share pw /user:x'."""
        share = self._share_for(self.dir_var.get().strip())
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Am Server anmelden")
        dlg.configure(fg_color=CARD)
        dlg.resizable(False, False)
        dlg.transient(self.root)
        body = ctk.CTkFrame(dlg, fg_color="transparent")
        body.pack(padx=26, pady=22)
        ctk.CTkLabel(body, text="Am Server anmelden", text_color=FG, font=F(18, "bold"), anchor="w").pack(fill="x")
        ctk.CTkLabel(body, text=f"Freigabe: {share}", text_color=MUTED, font=F(12, family=ui.FONT_MONO),
                     anchor="w").pack(fill="x", pady=(4, 0))

        user_var = tk.StringVar(value=self.cfg.get("server_user", ""))
        pw_var = tk.StringVar()
        drive_var = tk.StringVar(value=self.cfg.get("server_drive", "N:"))
        save_var = tk.BooleanVar(value=True)
        field_label(body, "Benutzer")
        entry(body, user_var, width=380).pack(fill="x")
        field_label(body, "Passwort")
        pw_entry = entry(body, pw_var, show="•")
        pw_entry.pack(fill="x")
        field_label(body, "Laufwerksbuchstabe (leer = nur anmelden, kein Laufwerk)")
        entry(body, drive_var).pack(fill="x")
        switch(body, "Anmeldung in Windows speichern (auch nach Neustart)", save_var).pack(anchor="w", pady=(14, 0))
        ctk.CTkLabel(body, text="Das Passwort wird nicht im Programm gespeichert – nur verschlüsselt in der\n"
                                "Windows-Anmeldeinformationsverwaltung, wenn der Schalter aktiv ist.",
                     text_color=MUTED, font=F(12), justify="left", anchor="w").pack(fill="x", pady=(6, 0))
        result = ctk.CTkLabel(body, text="", font=F(12), anchor="w", justify="left", wraplength=400)
        result.pack(fill="x", pady=(10, 0))

        def do_login(_=None):
            user, pw = user_var.get().strip(), pw_var.get()
            if not user or not pw:
                result.configure(text="Benutzer und Passwort eingeben.", text_color=ERR)
                return
            result.configure(text="Verbinde…", text_color=MUTED)
            dlg.update_idletasks()
            ok, msg = netdrive.connect(share, user, pw, drive_var.get())
            if ok and save_var.get():
                ok2, msg2 = netdrive.save_credential(netdrive.server_name(share), user, pw)
                msg += f"\n{msg2}" if ok2 else f"\nSpeichern in Windows fehlgeschlagen: {msg2}"
            if not ok:
                result.configure(text=msg, text_color=ERR)
                return
            self.cfg["server_user"] = user
            self.cfg["server_drive"] = drive_var.get().strip()
            save_config(self.cfg)
            dlg.destroy()
            self.set_status(msg.replace("\n", "  ·  "), OK_GREEN)
            self.toast("Mit Server verbunden", OK_GREEN)
            self.check_server()

        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x", pady=(16, 0))
        button(row, "Verbinden", do_login, kind="primary", width=130).pack(side="right")
        button(row, "Abbrechen", dlg.destroy, kind="ghost", width=110).pack(side="right", padx=(0, 8))
        dlg.bind("<Return>", do_login)
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        dlg.after(80, lambda: (dlg.lift(), dlg.focus_force(), pw_entry.focus_set()))
        dlg.grab_set()

    # ================= Letzte Aufnahmen =================
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
            self.strip_empty.pack(pady=34)
            return
        self.strip_empty.pack_forget()
        for path, img in items:
            ci = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self._strip_images.append(ci)
            ctk.CTkButton(self.strip, image=ci, text="", width=img.size[0] + 10, height=img.size[1] + 10,
                          fg_color=FIELD, hover_color=ACCENT, border_width=1, border_color=BORDER,
                          corner_radius=8, border_spacing=4, cursor="hand2",
                          command=lambda p=path: os.startfile(p)).pack(side="left", padx=(0, 8))

    def add_to_strip(self, path, img_bgr):
        h, w = img_bgr.shape[:2]
        small = cv2.resize(img_bgr, (int(w * 90 / h), 90), interpolation=cv2.INTER_AREA)
        img = Image.fromarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
        img.thumbnail((160, 90))
        self._fill_strip([(path, img)] + self._strip_items[:STRIP_COUNT - 1])

    # ================= Speichern =================
    def choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.dir_var.get() or None, title="Zielordner wählen")
        if d:
            self.dir_var.set(os.path.normpath(d))
            self.persist()
            self.check_server()

    def open_dir(self):
        d = self.dir_var.get()
        if os.path.isdir(d):
            os.startfile(d)

    def persist(self):
        self.cfg.update(
            camera_index=self.selected_index() or 0,
            camera_name=self.selected_name() or self.cfg.get("camera_name", ""),
            resolution=self.res_menu.get(),
            video_mode=self.mode_menu.get(),
            save_dir=self.dir_var.get(),
            excel_name=self.excel_name_var.get().strip() or DEFAULT_CONFIG["excel_name"],
            excel_enabled=self.excel_var.get(),
            excel_thumbnail=self.thumb_var.get(),
            image_format=self.fmt_seg.get().lower(),
            bearbeiter=self.bearbeiter_var.get(),
            annotate_after=self.annotate_var.get(),
            overlay_info=self.info_var.get(),
            overlay_scale=self.scale_var.get(),
        )
        h = self.hersteller_var.get().strip()
        if h:
            hist = [h] + [x for x in self.cfg["hersteller_history"] if x != h]
            self.cfg["hersteller_history"] = hist[:30]
            self.hersteller_combo.configure(values=self.cfg["hersteller_history"])
        save_config(self.cfg)

    def capture(self):
        """Aufnahme mit sichtbarem Ladezustand (Netzlaufwerk kann kurz dauern)."""
        if self._busy:
            return
        self._busy = True
        self.cap_btn.configure(state="disabled", text="  Speichere…")
        self.root.update_idletasks()
        try:
            self._capture()
        finally:
            self._busy = False
            self.cap_btn.configure(state="normal", text="  Aufnehmen")

    def _info_items(self, now, img_no):
        cal = self.cfg.get("calibration") if self.active_ppm(1) else ""
        return [now.strftime("%d.%m.%Y  %H:%M"),
                f"Fall {self.case_id} / Bild {img_no}",
                self.hersteller_var.get().strip(),
                f"Art.-Nr. {self.artikel_var.get().strip()}" if self.artikel_var.get().strip() else "",
                f"S/N {self.serie_var.get().strip()}" if self.serie_var.get().strip() else "",
                self.fehlerart(),
                cal,
                self.bearbeiter_var.get().strip()]

    def _capture(self):
        if not self.cam:
            self.set_status("Keine Kamera aktiv.", ERR)
            return
        frame = self.cam.get_frame()
        if frame is None:
            self.set_status("Noch kein Bild von der Kamera.", ERR)
            return
        if not self._check_required():
            return
        self._override_until = 0
        self._flash_until = time.time() + 0.15

        now = datetime.now()
        img_no = self.case_img + 1
        ppm = self.active_ppm(frame.shape[1])

        # Markieren direkt nach der Aufnahme
        shapes = []
        if self.annotate_var.get():
            sub = f"Fall {self.case_id} · Bild {img_no} · {self.hersteller_var.get().strip()} " \
                  f"{self.artikel_var.get().strip()}".strip()
            dlg = AnnotateDialog(self.root, frame, ppm, subtitle=sub)
            self.root.wait_window(dlg)
            action, shapes = dlg.result
            if action == "discard":
                self.set_status("Aufnahme verworfen.")
                self.toast("Aufnahme verworfen", WARN, ok=False)
                return

        img = overlay.render_annotations(frame, shapes, ppm)
        if self.scale_var.get() and ppm:
            img = overlay.draw_scale_bar(img, ppm)
        if self.info_var.get():
            img = overlay.add_info_bar(img, self._info_items(now, img_no))

        save_dir = Path(self.dir_var.get().strip())
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Zielordner nicht erreichbar:\n{save_dir}\n\n{e}")
            return

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
        ok, buf = cv2.imencode(f".{ext}", img, params)
        try:
            if not ok:
                raise RuntimeError("Bild konnte nicht kodiert werden")
            path.write_bytes(buf.tobytes())  # funktioniert auch mit Umlauten / UNC-Pfaden
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Bild konnte nicht gespeichert werden:\n{e}")
            return

        self.case_img = img_no
        self._update_case_label()
        self.persist()
        self.session_count += 1
        self._update_count()
        undo = {"path": path, "case_id": self.case_id, "xlsx": None}
        msg, color, toast, ok = f"Gespeichert: {path.name}", OK_GREEN, f"Bild {img_no} gespeichert", True
        if shapes:
            toast += f"  ·  {len(shapes)} Markierung{'en' if len(shapes) != 1 else ''}"

        if self.excel_var.get():
            try:
                undo["xlsx"] = self.append_excel(save_dir, path, now, img, img_no)
                msg += "   ·   Excel aktualisiert"
                toast += "  ·  Excel aktualisiert"
            except PermissionError:
                msg += "   ·   Excel GESPERRT"
                toast, color, ok = "Bild gespeichert – Excel gesperrt", WARN, False
                messagebox.showwarning(
                    APP_NAME,
                    "Das Bild wurde gespeichert, aber die Excel-Liste ist gerade geöffnet "
                    "(evtl. von einem Kollegen) und konnte nicht beschrieben werden.\n\n"
                    "Bitte Excel schließen und die Aufnahme ggf. nachtragen.")
            except Exception as e:
                msg += "   ·   Excel-Fehler"
                toast, color, ok = "Bild gespeichert – Excel-Fehler", WARN, False
                messagebox.showwarning(APP_NAME, f"Excel-Eintrag fehlgeschlagen:\n{e}")
        self.undo_stack.append(undo)
        self.set_status(msg + "   ·   F8 = rückgängig", color)
        self.toast(toast, color, ok=ok)
        self.add_to_strip(path, img)

    def _update_count(self):
        n = self.session_count
        self.count_label.configure(text=f"  {n} Aufnahme{'n' if n != 1 else ''}")

    # ================= Rückgängig =================
    def undo_last(self):
        if self._busy:
            return
        if not self.undo_stack:
            self.set_status("Nichts rückgängig zu machen – in dieser Sitzung wurde noch nichts aufgenommen.")
            self.toast("Nichts rückgängig zu machen", WARN, ok=False, ms=2000)
            return
        item = self.undo_stack.pop()
        path = item["path"]
        warn = None
        try:
            if path.exists():   # nicht endgültig löschen, sondern in versteckten Papierkorb-Ordner
                trash = path.parent / ".papierkorb"
                if not trash.exists():
                    trash.mkdir()
                    hide_dir(trash)
                os.replace(path, trash / path.name)
            (path.parent / ".thumbs" / (path.stem + ".jpg")).unlink(missing_ok=True)
        except Exception as e:
            warn = f"Bild konnte nicht entfernt werden: {e}"
        if item["xlsx"]:
            try:
                self.remove_excel_row(item["xlsx"], path.name)
            except PermissionError:
                warn = "Excel-Liste ist geöffnet – Zeile bitte von Hand löschen."
            except Exception as e:
                warn = f"Excel-Zeile konnte nicht entfernt werden: {e}"

        if item["case_id"] == self.case_id and self.case_img > 0:
            self.case_img -= 1
            self._update_case_label()
        self.session_count = max(0, self.session_count - 1)
        self._update_count()
        self._fill_strip([(p, i) for p, i in self._strip_items if p != path])
        if warn:
            self.set_status(f"Rückgängig: {path.name} – {warn}", WARN)
            self.toast("Rückgängig – mit Warnung", WARN, ok=False)
        else:
            self.set_status(f"Rückgängig: {path.name} in den Ordner „.papierkorb“ verschoben"
                            f"{' und aus Excel entfernt' if item['xlsx'] else ''}.", OK_GREEN)
            self.toast("Letzte Aufnahme rückgängig gemacht", OK_GREEN)

    # ================= Excel =================
    def _open_sheet(self, xlsx):
        """Öffnet/erstellt die Liste; liefert (wb, ws, {Überschrift: Spalte})."""
        if xlsx.exists():
            wb = load_workbook(xlsx)
            ws = wb.active
        else:
            wb = Workbook()
            ws = wb.active
            ws.title = "Schäden"
            ws.freeze_panes = "A2"
        cols = {c.value: c.column for c in ws[1] if c.value}
        for name, width in EXCEL_COLUMNS:
            if name not in cols:
                col = max(cols.values(), default=0) + 1
                cell = ws.cell(row=1, column=col, value=name)
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="305496")
                ws.column_dimensions[get_column_letter(col)].width = width
                cols[name] = col
        return wb, ws, cols

    def _place_thumbs(self, ws, cols, thumb_dir):
        # openpyxl verliert beim Laden vorhandene Bilder -> alle Vorschaubilder neu einfügen
        ws._images = []
        if not thumb_dir.is_dir():
            return
        name_col, thumb_col = cols["Dateiname"], get_column_letter(cols["Vorschau"])
        for row in range(2, ws.max_row + 2):
            name = ws.cell(row=row, column=name_col).value
            t = thumb_dir / (Path(str(name)).stem + ".jpg") if name else None
            if t and t.exists():
                ws.add_image(XLImage(str(t)), f"{thumb_col}{row}")
                ws.row_dimensions[row].height = THUMB_HEIGHT * 0.78
            elif row in ws.row_dimensions:
                ws.row_dimensions[row].height = None

    def append_excel(self, save_dir, img_path, now, img, img_no):
        if Workbook is None:
            raise RuntimeError("openpyxl ist nicht installiert (pip install openpyxl)")
        xlsx = save_dir / (self.excel_name_var.get().strip() or DEFAULT_CONFIG["excel_name"])
        wb, ws, cols = self._open_sheet(xlsx)
        r = ws.max_row + 1
        cal = self.cfg.get("calibration") if self.active_ppm(1) else ""
        values = {
            "Datum": now.strftime("%d.%m.%Y"), "Uhrzeit": now.strftime("%H:%M:%S"),
            "Fall-Nr.": self.case_id, "Bild-Nr.": img_no,
            "Hersteller": self.hersteller_var.get().strip(),
            "Leiterplatte / Artikel-Nr.": self.artikel_var.get().strip(),
            "Serien-/Auftrags-Nr.": self.serie_var.get().strip(),
            "Fehlerart": self.fehlerart(),
            "Schadensbeschreibung": self.desc_text.get("1.0", "end").strip(),
            "Vergrößerung": cal,
            "Bearbeiter": self.bearbeiter_var.get().strip(),
            "Dateiname": img_path.name,
        }
        for name, v in values.items():
            cell = ws.cell(row=r, column=cols[name], value=v)
            cell.alignment = Alignment(vertical="top", wrap_text=(name == "Schadensbeschreibung"))
        link = ws.cell(row=r, column=cols["Link"], value="Bild öffnen")
        link.hyperlink = str(img_path)
        link.font = Font(color="0563C1", underline="single")
        link.alignment = Alignment(vertical="top")

        thumb_dir = save_dir / ".thumbs"
        if self.thumb_var.get():
            if not thumb_dir.exists():
                thumb_dir.mkdir()
                hide_dir(thumb_dir)
            h, w = img.shape[:2]
            tw = int(w * THUMB_HEIGHT / h)
            cv2.imencode(".jpg", cv2.resize(img, (tw, THUMB_HEIGHT), interpolation=cv2.INTER_AREA),
                         [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tofile(str(thumb_dir / (img_path.stem + ".jpg")))
        self._place_thumbs(ws, cols, thumb_dir)
        wb.save(xlsx)
        return xlsx

    def remove_excel_row(self, xlsx, filename):
        wb, ws, cols = self._open_sheet(xlsx)
        for row in range(ws.max_row, 1, -1):
            if ws.cell(row=row, column=cols["Dateiname"]).value == filename:
                ws.delete_rows(row)
                break
        self._place_thumbs(ws, cols, xlsx.parent / ".thumbs")
        wb.save(xlsx)

    def on_close(self):
        self.persist()
        self._gen = -1
        self.stop_camera()
        self.root.destroy()


if __name__ == "__main__":
    cfg = load_config()
    ctk.set_appearance_mode(cfg.get("appearance", "Dark"))
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    App(root)
    root.mainloop()

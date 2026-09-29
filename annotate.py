"""
Markier-Fenster: öffnet sich nach der Aufnahme. Pfeile, Kreise, Rechtecke, Text und
Messlinien einzeichnen, dann speichern. Die Markierungen werden erst beim Speichern in
voller Auflösung ins Foto gerendert (overlay.render_annotations).
"""

import math
import tkinter as tk

import cv2
import customtkinter as ctk
from PIL import Image, ImageTk

import ui
from overlay import length_label, text_size

TOOLS = [("arrow", "Pfeil"), ("circle", "Kreis"), ("square", "Rechteck"), ("text", "Text"), ("ruler", "Messen")]
COLORS = [("#EF4444", "Rot"), ("#FACC15", "Gelb"), ("#22D3EE", "Cyan"), ("#FFFFFF", "Weiß")]


class AnnotateDialog(ctk.CTkToplevel):
    """result: ("save", shapes) | ("plain", []) | ("discard", None)"""

    def __init__(self, master, frame, ppm=None, subtitle=""):
        super().__init__(master)
        self.title("Aufnahme markieren")
        self.configure(fg_color=ui.BG)
        self.frame = frame
        self.ppm = ppm
        self.shapes = []
        self.tool = "arrow"
        self.color = COLORS[0][0]
        self.result = ("plain", [])
        self._drag = None
        self._view = (1.0, 0, 0)
        self._photo = None
        self._img_rgb = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

        top = master.winfo_toplevel()
        self.geometry(f"{top.winfo_width()}x{top.winfo_height()}+{top.winfo_rootx()}+{top.winfo_rooty()}")
        self.minsize(900, 600)
        self._build(subtitle)
        self.transient(top)
        self.protocol("WM_DELETE_WINDOW", self.save_plain)
        for seq, fn in (("<Return>", lambda e: self.save()), ("<KP_Enter>", lambda e: self.save()),
                        ("<Escape>", lambda e: self.save_plain()), ("<Control-z>", lambda e: self.undo()),
                        ("<F8>", lambda e: self.undo())):
            self.bind(seq, fn)
        for i, (key, _) in enumerate(TOOLS, start=1):
            self.bind(str(i), lambda e, k=key: self.set_tool(k))
        self.after(60, self._activate)

    def _activate(self):
        try:
            self.state("zoomed")
        except tk.TclError:
            pass
        self.lift()
        self.focus_force()
        self.grab_set()

    # ---------------- UI ----------------
    def _build(self, subtitle):
        bar = ctk.CTkFrame(self, fg_color=ui.CARD, corner_radius=0, border_width=0)
        bar.pack(fill="x")
        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=10)

        self.tool_btns = {}
        for i, (key, label) in enumerate(TOOLS, start=1):
            b = ui.button(inner, f" {label}", lambda k=key: self.set_tool(k), icon=key, width=96)
            b.pack(side="left", padx=(0, 6))
            self.tool_btns[key] = b

        ctk.CTkFrame(inner, width=1, height=28, fg_color=ui.BORDER_STRONG).pack(side="left", padx=10)
        self.color_btns = {}
        for col, name in COLORS:
            b = ctk.CTkButton(inner, text="", width=30, height=30, corner_radius=15, fg_color=col,
                              hover_color=col, border_width=3, border_color=ui.CARD, cursor="hand2",
                              command=lambda c=col: self.set_color(c))
            b.pack(side="left", padx=3)
            self.color_btns[col] = b

        ctk.CTkFrame(inner, width=1, height=28, fg_color=ui.BORDER_STRONG).pack(side="left", padx=10)
        ui.button(inner, " Rückgängig", self.undo, icon="undo", width=120).pack(side="left", padx=(0, 6))
        ui.button(inner, " Alle löschen", self.clear, icon="trash", kind="ghost", width=120).pack(side="left")

        ui.button(inner, "  Speichern", self.save, icon="check", kind="primary", width=140).pack(side="right")
        ui.button(inner, "Ohne Markierung", self.save_plain, width=150).pack(side="right", padx=(0, 8))
        ui.button(inner, " Verwerfen", self.discard, icon="trash", kind="danger", width=120).pack(side="right", padx=(0, 8))

        wrap = ctk.CTkFrame(self, fg_color=ui.PREVIEW_BG, corner_radius=12)
        wrap.pack(fill="both", expand=True, padx=16, pady=(12, 0))
        self.canvas = tk.Canvas(wrap, highlightthickness=0, bd=0, bg=ui.mode_color(ui.PREVIEW_BG), cursor="crosshair")
        self.canvas.pack(fill="both", expand=True, padx=6, pady=6)
        self.canvas.bind("<Configure>", lambda e: self._render_image())
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=20, pady=10)
        hint = ("Pfeil: von außen zur Schadstelle ziehen  ·  Kreis/Rechteck: aufziehen  ·  Text: klicken  ·  "
                "Messen: Linie ziehen")
        ctk.CTkLabel(foot, text=hint, text_color=ui.MUTED, font=ui.F(12)).pack(side="left")
        keys = ctk.CTkFrame(foot, fg_color="transparent")
        keys.pack(side="right")
        for k, t in (("Enter", "speichern"), ("Esc", "ohne Markierung"), ("Strg+Z", "rückgängig"), ("1–5", "Werkzeug")):
            ui.keycap(keys, k).pack(side="left", padx=(10, 4))
            ctk.CTkLabel(keys, text=t, text_color=ui.MUTED, font=ui.F(12)).pack(side="left")
        if subtitle or not self.ppm:
            info = subtitle + ("" if self.ppm else "   ·   Nicht kalibriert: Messwerte in Pixel")
            ctk.CTkLabel(foot, text=info.strip(" ·"), text_color=ui.MUTED, font=ui.F(12)).pack(side="left", padx=20)

        self.set_tool("arrow")
        self.set_color(self.color)

    def set_tool(self, key):
        self.tool = key
        for k, b in self.tool_btns.items():
            ui.set_toggle(b, k == key)

    def set_color(self, col):
        self.color = col
        for c, b in self.color_btns.items():
            b.configure(border_color=ui.ACCENT if c == col else ui.CARD)

    # ---------------- Koordinaten ----------------
    def _render_image(self):
        c = self.canvas
        cw, ch = max(c.winfo_width(), 10), max(c.winfo_height(), 10)
        iw, ih = self._img_rgb.size
        s = min(cw / iw, ch / ih)
        dw, dh = max(int(iw * s), 1), max(int(ih * s), 1)
        ox, oy = (cw - dw) // 2, (ch - dh) // 2
        self._view = (s, ox, oy)
        self._photo = ImageTk.PhotoImage(self._img_rgb.resize((dw, dh), Image.LANCZOS))
        c.delete("all")
        c.create_image(ox, oy, image=self._photo, anchor="nw", tags="img")
        self._redraw()

    def to_img(self, x, y):
        s, ox, oy = self._view
        h, w = self.frame.shape[:2]
        return min(max((x - ox) / s, 0), w - 1), min(max((y - oy) / s, 0), h - 1)

    def to_canvas(self, p):
        s, ox, oy = self._view
        return ox + p[0] * s, oy + p[1] * s

    # ---------------- Zeichnen ----------------
    def _press(self, e):
        p = self.to_img(e.x, e.y)
        if self.tool == "text":
            vals = ui.ask_form(self, "Text einfügen", [("Beschriftung", "")], ok_text="Einfügen")
            if vals and vals[0]:
                self.shapes.append({"type": "text", "p1": p, "text": vals[0], "color": self.color})
                self._redraw()
            return
        self._drag = {"type": self.tool, "p1": p, "p2": p, "color": self.color}

    def _motion(self, e):
        if self._drag:
            self._drag["p2"] = self.to_img(e.x, e.y)
            self._redraw()

    def _release(self, e):
        if not self._drag:
            return
        self._drag["p2"] = self.to_img(e.x, e.y)
        a, b = self.to_canvas(self._drag["p1"]), self.to_canvas(self._drag["p2"])
        if math.dist(a, b) > 6:
            self.shapes.append(self._drag)
        self._drag = None
        self._redraw()

    def _redraw(self):
        c = self.canvas
        c.delete("shape")
        s = self._view[0]
        lw = max(2, round(max(2, round(min(self.frame.shape[:2]) / 320)) * s))
        for sh in self.shapes + ([self._drag] if self._drag else []):
            self._draw_shape(sh, lw, s)

    def _draw_shape(self, sh, lw, s):
        c = self.canvas
        col = sh["color"]
        x1, y1 = self.to_canvas(sh["p1"])
        x2, y2 = self.to_canvas(sh.get("p2", sh["p1"]))
        kw = dict(fill=col, width=lw, tags="shape")
        if sh["type"] == "arrow":
            head = lw * 6
            c.create_line(x1, y1, x2, y2, arrow="last", arrowshape=(head, head * 1.2, head * 0.45),
                          capstyle="round", **kw)
        elif sh["type"] == "circle":
            c.create_oval(x1, y1, x2, y2, outline=col, width=lw, tags="shape")
        elif sh["type"] == "square":
            c.create_rectangle(x1, y1, x2, y2, outline=col, width=lw, tags="shape")
        elif sh["type"] == "ruler":
            c.create_line(x1, y1, x2, y2, **kw)
            d = max(math.hypot(x2 - x1, y2 - y1), 1)
            nx, ny = -(y2 - y1) / d * lw * 4, (x2 - x1) / d * lw * 4
            for x, y in ((x1, y1), (x2, y2)):
                c.create_line(x + nx, y + ny, x - nx, y - ny, **kw)
            px = math.dist(sh["p1"], sh["p2"])
            self._label((x1 + x2) / 2 - nx * 2.2, (y1 + y2) / 2 - ny * 2.2, length_label(px, self.ppm), col, s)
        elif sh["type"] == "text":
            self._label(x1, y1, sh["text"], col, s, anchor="w")

    def _label(self, x, y, text, col, s, anchor="center"):
        size = max(10, round(text_size(self.frame) * s * 0.75))
        font = (ui.FONT_UI, size, "bold")
        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1), (0, 2), (2, 0), (-2, 0), (0, -2)):
            self.canvas.create_text(x + dx, y + dy, text=text, fill="#0F172A", font=font, anchor=anchor, tags="shape")
        self.canvas.create_text(x, y, text=text, fill=col, font=font, anchor=anchor, tags="shape")

    # ---------------- Aktionen ----------------
    def undo(self):
        if self.shapes:
            self.shapes.pop()
            self._redraw()

    def clear(self):
        self.shapes.clear()
        self._redraw()

    def save(self):
        self.result = ("save", list(self.shapes))
        self._close()

    def save_plain(self):
        self.result = ("plain", [])
        self._close()

    def discard(self):
        self.result = ("discard", None)
        self._close()

    def _close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

"""
Outline-Icons (angelehnt an Phosphor/Lucide, 24er-Raster), mit PIL gezeichnet.
Keine Emojis, keine externen Dateien - funktioniert auch in der gepackten .exe.
"""

import math
from functools import lru_cache

from PIL import Image, ImageDraw

SS = 4  # Supersampling für glatte Kanten


def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


class _Pen:
    def __init__(self, size, color, stroke):
        self.k = size * SS / 24
        self.img = Image.new("RGBA", (size * SS, size * SS), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)
        self.c = _hex(color)
        self.w = max(1, round(stroke * self.k))

    def p(self, *pts):
        return [(x * self.k, y * self.k) for x, y in pts]

    def line(self, *pts):
        self.d.line(self.p(*pts), fill=self.c, width=self.w, joint="curve")
        r = self.w / 2
        for x, y in (self.p(pts[0])[0], self.p(pts[-1])[0]):  # runde Linienenden
            self.d.ellipse((x - r, y - r, x + r, y + r), fill=self.c)

    def rect(self, x1, y1, x2, y2, r=2, fill=False):
        box = [x1 * self.k, y1 * self.k, x2 * self.k, y2 * self.k]
        if fill:
            self.d.rounded_rectangle(box, r * self.k, fill=self.c)
        else:
            self.d.rounded_rectangle(box, r * self.k, outline=self.c, width=self.w)

    def circle(self, cx, cy, r, fill=False):
        box = [(cx - r) * self.k, (cy - r) * self.k, (cx + r) * self.k, (cy + r) * self.k]
        if fill:
            self.d.ellipse(box, fill=self.c)
        else:
            self.d.ellipse(box, outline=self.c, width=self.w)

    def arc(self, cx, cy, r, start, end):
        box = [(cx - r) * self.k, (cy - r) * self.k, (cx + r) * self.k, (cy + r) * self.k]
        self.d.arc(box, start, end, fill=self.c, width=self.w)

    def poly(self, *pts):
        self.d.polygon(self.p(*pts), fill=self.c)

    def result(self, size):
        return self.img.resize((size, size), Image.LANCZOS)


def _camera(p):
    p.line((3, 7), (7.5, 7), (9, 4.5), (15, 4.5), (16.5, 7), (21, 7))
    p.rect(3, 7, 21, 19.5, 2.5)
    p.circle(12, 13, 3.6)


def _video(p):
    p.rect(2.5, 6, 15.5, 18, 2.5)
    p.line((15.5, 10.5), (21.5, 7), (21.5, 17), (15.5, 13.5))


def _refresh(p):
    r, end = 7.5, 250                     # Bogen im Uhrzeigersinn von 330° bis 250°
    p.arc(12, 12, r, -30, end)
    a = math.radians(end)
    ex, ey = 12 + r * math.cos(a), 12 + r * math.sin(a)
    tx, ty = -math.sin(a), math.cos(a)    # Tangente (Laufrichtung)
    nx, ny = -ty, tx
    L = 3.6
    p.line((ex - L * tx + L * 0.8 * nx, ey - L * ty + L * 0.8 * ny), (ex + 0.6 * tx, ey + 0.6 * ty),
           (ex - L * tx - L * 0.8 * nx, ey - L * ty - L * 0.8 * ny))


def _folder(p):
    p.line((3, 18.5), (3, 6.5), (3.8, 5.5), (9, 5.5), (11, 7.8), (20.2, 7.8), (21, 8.8),
           (21, 18.5), (20, 19.5), (4, 19.5), (3, 18.5))


def _folder_open(p):
    p.line((3, 18.5), (3, 6.5), (3.8, 5.5), (9, 5.5), (11, 7.8), (18, 7.8), (18.8, 8.6), (18.8, 10.5))
    p.line((3, 18.5), (6, 11), (21.5, 11), (18.5, 19.5), (4, 19.5), (3, 18.5))


def _image(p):
    p.rect(3, 4.5, 21, 19.5, 2.5)
    p.circle(8.8, 9.6, 1.5)
    p.line((3.5, 17.5), (9, 12.5), (13.5, 16.5), (16.5, 13.8), (20.5, 17.5))


def _clipboard(p):  # Dokument mit Textzeilen (Formular)
    p.line((14, 3), (6.5, 3), (5, 4.5), (5, 19.5), (6.5, 21), (17.5, 21), (19, 19.5), (19, 8), (14, 3))
    p.line((14, 3), (14, 8), (19, 8))
    p.line((8.5, 12.5), (15.5, 12.5))
    p.line((8.5, 16.5), (13, 16.5))


def _sliders(p):
    p.line((4, 7.5), (20, 7.5))
    p.line((4, 16.5), (20, 16.5))
    p.circle(9, 7.5, 2.4, fill=True)
    p.circle(15, 16.5, 2.4, fill=True)


def _sun(p):
    p.circle(12, 12, 4)
    for i in range(8):
        a = math.radians(i * 45)
        p.line((12 + 7 * math.cos(a), 12 + 7 * math.sin(a)), (12 + 9.3 * math.cos(a), 12 + 9.3 * math.sin(a)))


def _moon(p):
    # gefüllte Sichel: Kreis minus versetzter Kreis
    k = p.k
    mask = Image.new("L", p.img.size, 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([3.5 * k, 3.5 * k, 20.5 * k, 20.5 * k], fill=255)
    md.ellipse([9 * k, 0.5 * k, 24 * k, 15.5 * k], fill=0)
    p.img.paste(Image.new("RGBA", p.img.size, p.c), (0, 0), mask)


def _microscope(p):
    p.line((8.5, 3.5), (13.5, 12))            # Tubus
    p.line((6.8, 4.5), (10.2, 2.5))           # Okular
    p.line((13.5, 12), (11, 13.5))
    p.line((8, 16.5), (15, 16.5))             # Objekttisch
    p.arc(12, 12.5, 7.5, -70, 80)             # Stativbogen
    p.line((4, 20.5), (20, 20.5))             # Fuß


def _eraser(p):
    p.line((20.5, 20.5), (8, 20.5))
    p.line((9.5, 20.5), (3.8, 14.8), (3.8, 13.2), (13.2, 3.8), (14.8, 3.8), (20.2, 9.2),
           (20.2, 10.8), (10.5, 20.5))
    p.line((8.2, 8.8), (15.2, 15.8))


def _file_xls(p):
    p.line((14, 3), (6.5, 3), (5, 4.5), (5, 19.5), (6.5, 21), (17.5, 21), (19, 19.5), (19, 8), (14, 3))
    p.line((14, 3), (14, 8), (19, 8))
    p.line((9, 12), (15, 18))
    p.line((15, 12), (9, 18))


def _check(p):
    p.line((5, 12.5), (10, 17.5), (19.5, 7))


def _warning(p):
    p.line((12, 3.5), (21.5, 20), (2.5, 20), (12, 3.5))
    p.line((12, 9.5), (12, 14))
    p.circle(12, 17, 0.9, fill=True)


def _keyboard(p):
    p.rect(2.5, 6, 21.5, 18, 2.5)
    for x in (6.5, 10, 13.5, 17):
        p.circle(x, 10, 0.8, fill=True)
    p.line((7.5, 14.2), (16.5, 14.2))


def _zoom_in(p):
    p.circle(10.5, 10.5, 6.8)
    p.line((15.5, 15.5), (20.5, 20.5))
    p.line((7.5, 10.5), (13.5, 10.5))
    p.line((10.5, 7.5), (10.5, 13.5))


def _zoom_out(p):
    p.circle(10.5, 10.5, 6.8)
    p.line((15.5, 15.5), (20.5, 20.5))
    p.line((7.5, 10.5), (13.5, 10.5))


def _search(p):
    p.circle(10.5, 10.5, 6.8)
    p.line((15.5, 15.5), (20.5, 20.5))


def _database(p):
    p.d.ellipse([5 * p.k, 3 * p.k, 19 * p.k, 8 * p.k], outline=p.c, width=p.w)
    p.line((5, 5.5), (5, 18.5))
    p.line((19, 5.5), (19, 18.5))
    for y in (12, 18.5):
        p.d.arc([5 * p.k, (y - 2.5) * p.k, 19 * p.k, (y + 2.5) * p.k], 0, 180, fill=p.c, width=p.w)


def _chevron_down(p):
    p.line((6, 9.5), (12, 15.5), (18, 9.5))


def _chevron_right(p):
    p.line((9.5, 6), (15.5, 12), (9.5, 18))


def _crosshair(p):
    p.circle(12, 12, 6.5)
    p.line((12, 2.5), (12, 8))
    p.line((12, 16), (12, 21.5))
    p.line((2.5, 12), (8, 12))
    p.line((16, 12), (21.5, 12))


def _ruler(p):
    p.rect(2.5, 8, 21.5, 16.5, 1.8)
    for i, x in enumerate((6.5, 10, 13.5, 17)):
        p.line((x, 8), (x, 12.5 if i % 2 else 11))


def _fullscreen(p):
    p.line((3.5, 8.5), (3.5, 3.5), (8.5, 3.5))
    p.line((15.5, 3.5), (20.5, 3.5), (20.5, 8.5))
    p.line((20.5, 15.5), (20.5, 20.5), (15.5, 20.5))
    p.line((8.5, 20.5), (3.5, 20.5), (3.5, 15.5))


def _help(p):
    p.circle(12, 12, 9)
    p.arc(12, 9.8, 2.9, 185, 400)
    p.line((14.2, 11.7), (12.4, 13.2), (12.2, 14.4))
    p.circle(12.2, 17.2, 1.0, fill=True)


def _arrow(p):
    p.line((5, 19), (18.5, 5.5))
    p.line((10.5, 5.5), (18.5, 5.5), (18.5, 13.5))


def _circle(p):
    p.circle(12, 12, 8.5)


def _square(p):
    p.rect(4, 4, 20, 20, 2)


def _text(p):
    p.line((5, 5.5), (19, 5.5))
    p.line((12, 5.5), (12, 19.5))
    p.line((9.5, 19.5), (14.5, 19.5))


def _undo(p):
    p.line((8.5, 4.8), (4.2, 9.2), (8.5, 13.6))
    p.line((4.2, 9.2), (14.5, 9.2))
    p.arc(14.5, 14.1, 4.9, 270, 450)
    p.line((14.5, 19), (8, 19))


def _trash(p):
    p.line((4, 6.5), (20, 6.5))
    p.line((9.5, 6.5), (9.5, 4), (14.5, 4), (14.5, 6.5))
    p.line((6, 6.5), (7, 20), (17, 20), (18, 6.5))
    p.line((10, 10.5), (10, 16))
    p.line((14, 10.5), (14, 16))


def _plus(p):
    p.line((12, 5), (12, 19))
    p.line((5, 12), (19, 12))


def _x(p):
    p.line((6.5, 6.5), (17.5, 17.5))
    p.line((17.5, 6.5), (6.5, 17.5))


def _tag(p):  # Etikett (Fehlerart)
    p.line((3.5, 11.5), (3.5, 4.5), (4.5, 3.5), (11.5, 3.5), (20.5, 12.5), (12.5, 20.5), (3.5, 11.5))
    p.circle(8, 8, 1.4, fill=True)


_ICONS = {
    "camera": _camera, "video": _video, "refresh": _refresh, "folder": _folder,
    "folder_open": _folder_open, "image": _image, "clipboard": _clipboard, "sliders": _sliders,
    "sun": _sun, "moon": _moon, "microscope": _microscope, "eraser": _eraser, "excel": _file_xls,
    "check": _check, "warning": _warning, "keyboard": _keyboard,
    "zoom_in": _zoom_in, "zoom_out": _zoom_out, "crosshair": _crosshair, "ruler": _ruler,
    "fullscreen": _fullscreen, "help": _help, "arrow": _arrow, "circle": _circle, "square": _square,
    "text": _text, "undo": _undo, "trash": _trash, "plus": _plus, "x": _x, "tag": _tag,
    "search": _search, "database": _database, "chevron_down": _chevron_down, "chevron_right": _chevron_right,
}


@lru_cache(maxsize=None)
def pil_icon(name, size=20, color="#F8FAFC", stroke=1.75):
    p = _Pen(size, color, stroke)
    _ICONS[name](p)
    return p.result(size)


def ctk_icon(name, size=20, color=("#0F172A", "#F8FAFC"), stroke=1.75):
    """CTkImage mit eigener Farbe für hellen und dunklen Modus."""
    import customtkinter as ctk
    light, dark = (color, color) if isinstance(color, str) else color
    # in doppelter Auflösung erzeugen - CTkImage skaliert für HiDPI selbst
    return ctk.CTkImage(light_image=pil_icon(name, size * 2, light, stroke),
                        dark_image=pil_icon(name, size * 2, dark, stroke), size=(size, size))

"""
Einblendungen, die fest ins gespeicherte Foto geschrieben werden:
Markierungen (Pfeil, Kreis, Rechteck, Text, Messlinie), Maßstabsbalken und Info-Leiste.
Alle Funktionen arbeiten auf BGR-Bildern (OpenCV) in voller Auflösung.
"""

import math

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

_FONT_FILES = {False: ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"),
               True: ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf")}
_font_cache = {}

INFO_BG = (15, 23, 42)       # #0F172A
OUTLINE = (15, 23, 42)


def pil_font(size, bold=False):
    key = (int(size), bold)
    if key not in _font_cache:
        font = None
        for name in _FONT_FILES[bold]:
            try:
                font = ImageFont.truetype(name, key[0])  # sucht unter Windows auch in C:\Windows\Fonts
                break
            except OSError:
                continue
        _font_cache[key] = font or ImageFont.load_default(size=key[0])
    return _font_cache[key]


def hex_rgb(color):
    c = color.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def hex_bgr(color):
    r, g, b = hex_rgb(color)
    return b, g, r


# ---------------- Längen / Maßstab ----------------
def format_length(mm):
    if mm < 1:
        return f"{mm * 1000:.0f} µm"
    if mm < 10:
        return f"{mm:.2f} mm".replace(".", ",")
    return f"{mm:.1f} mm".replace(".", ",")


def length_label(px, ppm):
    """Beschriftung für eine Strecke von px Pixeln (ppm = Pixel pro mm, None = unkalibriert)."""
    return format_length(px / ppm) if ppm else f"{px:.0f} px"


def nice_scale_mm(max_mm):
    """Größte 'runde' Länge (1-2-5-Reihe) <= max_mm."""
    if max_mm <= 0:
        return 0
    e = math.floor(math.log10(max_mm))
    for m in (5, 2, 1):
        v = m * 10 ** e
        if v <= max_mm:
            return v
    return 10 ** (e - 1)


def scale_label(mm):
    if mm < 1:
        return f"{mm * 1000:g} µm"
    return f"{mm:g} mm".replace(".", ",")


# ---------------- Markierungen ----------------
def stroke_width(img):
    return max(2, round(min(img.shape[:2]) / 320))


def text_size(img):
    return max(16, round(min(img.shape[:2]) / 28))


def render_annotations(bgr, shapes, ppm=None):
    """Zeichnet die Markierungen (Liste von dicts in Bildkoordinaten) ins Bild."""
    if not shapes:
        return bgr
    img = bgr.copy()
    t = stroke_width(img)
    o = t + max(2, t // 2)          # dunkle Kontur für Lesbarkeit auf grüner Leiterplatte
    labels = []                     # (x, y, text, color_hex, anchor)
    for s in shapes:
        col = hex_bgr(s["color"])
        kind = s["type"]
        p1 = tuple(int(round(v)) for v in s["p1"])
        p2 = tuple(int(round(v)) for v in s.get("p2", s["p1"]))
        if kind == "arrow":
            length = max(math.dist(p1, p2), 1)
            tip = min(0.45, (t * 7) / length)
            # Pfeil zeigt vom Startpunkt zur Schadstelle (Spitze am Ende)
            cv2.arrowedLine(img, p1, p2, OUTLINE, o, cv2.LINE_AA, tipLength=tip)
            cv2.arrowedLine(img, p1, p2, col, t, cv2.LINE_AA, tipLength=tip)
        elif kind in ("circle", "square"):
            x1, y1 = min(p1[0], p2[0]), min(p1[1], p2[1])
            x2, y2 = max(p1[0], p2[0]), max(p1[1], p2[1])
            for c, w in ((OUTLINE, o), (col, t)):
                if kind == "circle":
                    cv2.ellipse(img, ((x1 + x2) // 2, (y1 + y2) // 2), ((x2 - x1) // 2, (y2 - y1) // 2),
                                0, 0, 360, c, w, cv2.LINE_AA)
                else:
                    cv2.rectangle(img, (x1, y1), (x2, y2), c, w, cv2.LINE_AA)
        elif kind == "ruler":
            dx, dy = p2[0] - p1[0], p2[1] - p1[1]
            length = max(math.hypot(dx, dy), 1)
            nx, ny = -dy / length * t * 4, dx / length * t * 4
            segs = [(p1, p2),
                    ((int(p1[0] + nx), int(p1[1] + ny)), (int(p1[0] - nx), int(p1[1] - ny))),
                    ((int(p2[0] + nx), int(p2[1] + ny)), (int(p2[0] - nx), int(p2[1] - ny)))]
            for c, w in ((OUTLINE, o), (col, t)):
                for a, b in segs:
                    cv2.line(img, a, b, c, w, cv2.LINE_AA)
            mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
            labels.append((mx - nx * 2.2, my - ny * 2.2, length_label(length, ppm), s["color"], "mm"))
        elif kind == "text":
            labels.append((p1[0], p1[1], s["text"], s["color"], "lm"))

    if labels:
        pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        d = ImageDraw.Draw(pil)
        size = text_size(img)
        font = pil_font(size, bold=True)
        for x, y, text, color, anchor in labels:
            d.text((x, y), text, font=font, fill=hex_rgb(color), anchor=anchor,
                   stroke_width=max(2, size // 10), stroke_fill=OUTLINE)
        img = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
    return img


# ---------------- Maßstabsbalken ----------------
def draw_scale_bar(bgr, ppm):
    """Maßstabsbalken unten rechts ins Bild (ppm = Pixel pro mm in diesem Bild)."""
    if not ppm:
        return bgr
    h, w = bgr.shape[:2]
    mm = nice_scale_mm(0.18 * w / ppm)
    if not mm:
        return bgr
    bar = mm * ppm
    margin = round(w * 0.02)
    size = max(14, round(h / 50))
    font = pil_font(size, bold=True)
    label = scale_label(mm)
    thick = max(4, round(h / 200))

    pil = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)).convert("RGBA")
    layer = Image.new("RGBA", pil.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    tw = d.textlength(label, font=font)
    box_w = max(bar, tw) + 2 * size * 0.8
    box_h = size * 1.4 + thick + size * 1.2
    x2, y2 = w - margin, h - margin
    x1, y1 = x2 - box_w, y2 - box_h
    d.rounded_rectangle((x1, y1, x2, y2), radius=size * 0.4, fill=INFO_BG + (190,))
    cx = (x1 + x2) / 2
    by = y2 - size * 0.7 - thick
    d.rectangle((cx - bar / 2, by, cx + bar / 2, by + thick), fill=(255, 255, 255, 255))
    for x in (cx - bar / 2, cx + bar / 2):     # Endstriche
        d.rectangle((x - thick / 4, by - thick, x + thick / 4, by + thick), fill=(255, 255, 255, 255))
    d.text((cx, by - size * 0.35), label, font=font, fill=(255, 255, 255, 255), anchor="md")
    out = Image.alpha_composite(pil, layer).convert("RGB")
    return cv2.cvtColor(np.asarray(out), cv2.COLOR_RGB2BGR)


# ---------------- Info-Leiste ----------------
def add_info_bar(bgr, items):
    """Hängt unter dem Bild eine Leiste mit den Angaben an (Bildinhalt wird nicht verdeckt)."""
    items = [i for i in items if i]
    if not items:
        return bgr
    h, w = bgr.shape[:2]
    bar_h = max(30, round(h * 0.04))
    out = np.empty((h + bar_h, w, 3), dtype=bgr.dtype)
    out[:h] = bgr
    out[h:] = INFO_BG[::-1]  # RGB -> BGR

    pil = Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    text = "   ·   ".join(items)
    pad = round(bar_h * 0.5)
    size = round(bar_h * 0.46)
    font = pil_font(size)
    while size > 10 and d.textlength(text, font=font) > w - 2 * pad:
        size -= 1
        font = pil_font(size)
    d.text((pad, h + bar_h / 2), text, font=font, fill=(248, 250, 252), anchor="lm")
    return cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)

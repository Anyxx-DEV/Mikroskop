"""
PDF-Bericht pro Fertigungsauftrag (A4, mit Pillow erzeugt - kein zusätzliches Paket nötig).

Gestaltung nach den SE-Elektronic Brand Guidelines (Mini Guide 2026.01 R1):
- Logo: Standardversion (vierfarbig, rot mit schwarzem Text auf Weiß), unverändert, min. 30 mm breit
- Farben: SE-Rot #DD042D (sparsamer Akzent), Schwarz, Weiß, Grau #808080, Dunkelgrau #333333
- Schrift: Source Sans Pro (Unternehmensschrift); falls nicht vorhanden Calibri (für Dokumente zugelassen)
"""

import os
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DPI = 150
W, H = 1240, 1754            # A4 bei 150 dpi
MM = DPI / 25.4              # Pixel pro mm
M = int(20 * MM)             # Seitenrand 20 mm

# Markenfarben
SE_RED = (221, 4, 45)        # #DD042D
BLACK = (0, 0, 0)
DARK = (51, 51, 51)          # #333333
GREY = (128, 128, 128)       # #808080
WHITE = (255, 255, 255)
ROW_TINT = (242, 242, 242)   # sehr helle Abstufung des Grau für Tabellenzeilen

_BASES = [Path(getattr(sys, "_MEIPASS", "")), Path(sys.executable).parent, Path(__file__).parent]
_FONT_FILES = {
    # Gewicht: (Source Sans Pro / Source Sans 3, Calibri-Ersatz)
    "light": (["SourceSansPro-Light.ttf", "SourceSans3-Light.ttf", "SourceSansPro-Light.otf"], "calibril.ttf"),
    "regular": (["SourceSansPro-Regular.ttf", "SourceSans3-Regular.ttf", "SourceSansPro-Regular.otf"], "calibri.ttf"),
    "bold": (["SourceSansPro-Bold.ttf", "SourceSans3-Bold.ttf", "SourceSansPro-Bold.otf"], "calibrib.ttf"),
}
_font_cache = {}


def _resource(*parts):
    for base in _BASES:
        p = base.joinpath(*parts)
        if p.exists():
            return p
    return None


def _font_dirs():
    dirs = [b / "fonts" for b in _BASES]
    dirs.append(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
    dirs.append(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts")
    return [d for d in dirs if d.is_dir()]


def font(size, weight="regular"):
    key = (int(size), weight)
    if key not in _font_cache:
        brand, fallback = _FONT_FILES[weight]
        f = None
        for d in _font_dirs():
            for name in brand:
                if (d / name).exists():
                    f = ImageFont.truetype(str(d / name), key[0])
                    break
            if f:
                break
        if f is None:
            try:
                f = ImageFont.truetype(fallback, key[0])
            except OSError:
                f = ImageFont.load_default(size=key[0])
        _font_cache[key] = f
    return _font_cache[key]


def brand_font_name():
    """Name der verwendeten Schrift (für Hinweise im Programm)."""
    f = font(20)
    try:
        return f.getname()[0]
    except Exception:
        return "?"


def _logo():
    p = _resource("assets", "se_logo.png")
    if not p:
        return None
    logo = Image.open(p).convert("RGBA")
    w = int(45 * MM)                                   # 45 mm breit (Guideline: mindestens 30 mm)
    return logo.resize((w, int(logo.height * w / logo.width)), Image.LANCZOS)


# ---------------- Hilfsfunktionen ----------------
def _wrap(draw, text, fnt, width):
    lines = []
    for para in str(text).splitlines() or [""]:
        cur = ""
        for word in para.split(" "):
            test = f"{cur} {word}".strip()
            if draw.textlength(test, font=fnt) <= width:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


def _fit(draw, text, fnt, width):
    text = str(text)
    while text and draw.textlength(text, font=fnt) > width:
        text = text[:-2] + "…" if len(text) > 2 else ""
    return text


def _v(f, key):
    return str(f.get(key, "") or "").strip()


def _section(d, y, title):
    """Rubrik im Stil der Guidelines: Großbuchstaben, darunter dünne graue Linie."""
    d.text((M, y), title.upper(), font=font(19, "bold"), fill=BLACK)
    d.line((M, y + 32, W - M, y + 32), fill=GREY, width=1)
    return y + 50


def _new_page(logo, label):
    page = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(page)
    d.text((M, int(12 * MM)), label.upper(), font=font(17), fill=BLACK)      # Seitenrubrik oben links
    if logo is not None:
        page.paste(logo, (W - M - logo.width, int(10 * MM)), logo)
    return page, d


def _footer(d, fa, n, total):
    y = H - int(14 * MM)
    d.line((M, y, W - M, y), fill=GREY, width=1)
    d.text((M, y + 14), f"SE-Elektronic GmbH  ·  Schadensbericht Fertigungsauftrag {fa}  ·  "
                        f"erstellt {datetime.now():%d.%m.%Y %H:%M}", font=font(16), fill=GREY)
    d.text((W - M, y + 14), f"Seite {n} von {total}", font=font(16), fill=GREY, anchor="ra")


# ---------------- Bericht ----------------
def make_report(fa, findings, out_path, author=""):
    """findings: Liste von dicts (Excel-Spalten + "_image"). Schreibt die PDF und gibt den Pfad zurück."""
    logo = _logo()
    content_top = int(10 * MM) + (logo.height if logo is not None else 40) + int(8 * MM)
    footer_top = H - int(14 * MM) - 20
    pages = []

    # ---------- Seite 1: Titel, Kenndaten, Übersicht ----------
    page, d = _new_page(logo, "Qualitätssicherung · SMD")
    y = content_top
    d.text((M, y), "Schadensbericht", font=font(64, "light"), fill=BLACK)
    y += 82
    d.text((M, y), f"Fertigungsauftrag {fa}", font=font(32, "regular"), fill=SE_RED)
    y += 70

    first = findings[0] if findings else {}
    dates = sorted({_v(f, "Datum") for f in findings if _v(f, "Datum")},
                   key=lambda s: datetime.strptime(s, "%d.%m.%Y") if len(s) == 10 else datetime.min)
    meta = [("Platinen-Nr.", _v(first, "Platinen-Nr.")),
            ("Bezeichnung", _v(first, "Artikelbezeichnung")),
            ("Zeitraum", f"{dates[0]} – {dates[-1]}" if len(dates) > 1 else (dates[0] if dates else "")),
            ("Anzahl Befunde", str(len(findings))),
            ("Erstellt von", author)]
    y = _section(d, y, "Kenndaten")
    for k, v in meta:
        if v:
            d.text((M, y), k, font=font(23), fill=GREY)
            d.text((M + 260, y), v, font=font(23, "bold"), fill=BLACK)
            y += 38
    y += 30

    y = _section(d, y, "Übersicht der Befunde")
    cols = [("Nr.", 70), ("Datum", 200), ("Fehlerart", 300), ("Bauteil", W - 2 * M - 570)]
    fh, fr = font(20, "bold"), font(20)
    x = M
    for name, w in cols:
        d.text((x + 8, y), name, font=fh, fill=BLACK)
        x += w
    y += 34
    d.line((M, y, W - M, y), fill=BLACK, width=2)
    y += 10
    for i, f in enumerate(findings, 1):
        if y > footer_top - 60:
            d.text((M + 8, y), f"… weitere {len(findings) - i + 1} Befunde auf den folgenden Seiten",
                   font=fr, fill=GREY)
            y += 36
            break
        if i % 2 == 0:
            d.rectangle((M, y - 6, W - M, y + 30), fill=ROW_TINT)
        vals = [str(i), f"{_v(f, 'Datum')} {_v(f, 'Uhrzeit')[:5]}", _v(f, "Fehlerart") or "–",
                _v(f, "Bauteil").replace("  ·  ", ", ") or "–"]
        x = M
        for (name, w), val in zip(cols, vals):
            d.text((x + 8, y), _fit(d, val, fr, w - 16), font=fr, fill=BLACK)
            x += w
        y += 36
    d.line((M, y - 4, W - M, y - 4), fill=GREY, width=1)

    # ersten Befund noch auf Seite 1 unterbringen, wenn unter der Tabelle Platz ist
    slot_h = (footer_top - content_top) // 2
    start = 0
    if findings and y + 50 + slot_h < footer_top:
        _draw_finding(page, d, findings[0], 1, y + 50, slot_h)
        start = 1
    pages.append(page)

    # ---------- weitere Befunde: zwei pro Seite ----------
    for i in range(start, len(findings), 2):
        page, d = _new_page(logo, f"Schadensbericht · Fertigungsauftrag {fa}")
        for j, f in enumerate(findings[i:i + 2]):
            _draw_finding(page, d, f, i + j + 1, content_top + j * slot_h, slot_h)
        pages.append(page)

    total = len(pages)
    for n, p in enumerate(pages, 1):
        _footer(ImageDraw.Draw(p), fa, n, total)
    pages[0].save(out_path, save_all=True, append_images=pages[1:], resolution=DPI,
                  title=f"Schadensbericht Fertigungsauftrag {fa}", author=author or "SE-Elektronic GmbH",
                  subject="Schadensdokumentation Leiterplatten", creator="Mikroskop-Capture")
    return out_path


def _draw_finding(page, d, f, nr, top, slot_h):
    # Überschrift mit kleinem roten Akzent
    d.rectangle((M, top + 10, M + 8, top + 42), fill=SE_RED)
    d.text((M + 22, top), f"Befund {nr}", font=font(36, "light"), fill=BLACK)
    sub = f"{_v(f, 'Datum')} {_v(f, 'Uhrzeit')}  ·  Fall {_v(f, 'Fall-Nr.')} / Bild {_v(f, 'Bild-Nr.')}"
    d.text((W - M, top + 14), sub, font=font(19), fill=GREY, anchor="ra")
    y = top + 60

    # Bild (Seitenverhältnis beibehalten, zentriert, dünner grauer Rahmen)
    img_h_max = int(slot_h * 0.60)
    box_w = W - 2 * M
    path = f.get("_image")
    if path:
        try:
            with Image.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail((box_w, img_h_max), Image.LANCZOS)
                x0 = M + (box_w - im.width) // 2
                page.paste(im, (x0, y))
                d.rectangle((x0 - 1, y - 1, x0 + im.width, y + im.height), outline=GREY, width=1)
                y += im.height + 20
        except Exception:
            path = None
    if not path:
        d.rectangle((M, y, W - M, y + 120), outline=GREY, width=1)
        d.text((W // 2, y + 60), f"Bild nicht gefunden: {_v(f, 'Dateiname')}", font=font(20),
               fill=GREY, anchor="mm")
        y += 140

    # Angaben
    fl, fv = font(21), font(21, "bold")
    bauteil = _v(f, "Bauteil").replace("  ·  ", ", ")
    details = ", ".join(x for x in (_v(f, "Position"), _v(f, "Gehäuse"), _v(f, "Technologie"),
                                    _v(f, "Bauteiltyp")) if x and x not in bauteil)
    rows = [("Fehlerart", _v(f, "Fehlerart")),
            ("Bauteil", bauteil + (f"  ({details})" if details else "")),
            ("Vergrößerung", _v(f, "Vergrößerung")),
            ("Bearbeiter", _v(f, "Bearbeiter"))]
    for k, v in rows:
        if v:
            d.text((M, y), k, font=fl, fill=GREY)
            d.text((M + 200, y), _fit(d, v, fv, W - 2 * M - 200), font=fv, fill=BLACK)
            y += 32
    desc = _v(f, "Schadensbeschreibung")
    if desc:
        d.text((M, y), "Beschreibung", font=fl, fill=GREY)
        limit = top + slot_h - 20
        for line in _wrap(d, desc, font(21), W - 2 * M - 200):
            if y > limit - 30:
                d.text((M + 200, y), "…", font=font(21), fill=BLACK)
                break
            d.text((M + 200, y), line, font=font(21), fill=BLACK)
            y += 30

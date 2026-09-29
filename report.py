"""
PDF-Bericht pro Fertigungsauftrag (A4, mit Pillow erzeugt - kein zusätzliches Paket nötig).
Seite 1: Kopf mit Auftrag/Platine + Übersichtstabelle; danach je Seite zwei Befunde mit Bild und Angaben.
"""

from datetime import datetime

from PIL import Image, ImageDraw

from overlay import pil_font

DPI = 150
W, H = 1240, 1754            # A4 bei 150 dpi
M = 80                       # Rand
ACCENT = (4, 120, 87)        # #047857
TEXT = (15, 23, 42)
MUTED = (71, 85, 105)
LINE = (203, 213, 225)
ROW_BG = (241, 245, 249)


def _wrap(draw, text, font, width):
    """Zeilenumbruch nach Pixelbreite."""
    lines = []
    for para in str(text).splitlines() or [""]:
        cur = ""
        for word in para.split(" "):
            test = f"{cur} {word}".strip()
            if draw.textlength(test, font=font) <= width:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


def _fit(draw, text, font, width):
    text = str(text)
    while text and draw.textlength(text, font=font) > width:
        text = text[:-2] + "…" if len(text) > 2 else ""
    return text


def _new_page():
    page = Image.new("RGB", (W, H), "white")
    return page, ImageDraw.Draw(page)


def _footer(d, fa, n, total):
    f = pil_font(18)
    d.line((M, H - 70, W - M, H - 70), fill=LINE, width=2)
    d.text((M, H - 50), f"Schadensbericht FA {fa}  ·  erstellt {datetime.now():%d.%m.%Y %H:%M}", font=f, fill=MUTED)
    d.text((W - M, H - 50), f"Seite {n} / {total}", font=f, fill=MUTED, anchor="ra")


def _v(f, key):
    return str(f.get(key, "") or "").strip()


def make_report(fa, findings, out_path, author=""):
    """findings: Liste von dicts (Excel-Spalten + "_image"). Schreibt die PDF und gibt den Pfad zurück."""
    pages = []

    # ---------- Seite 1: Kopf + Übersicht ----------
    page, d = _new_page()
    d.rectangle((0, 0, W, 14), fill=ACCENT)
    d.text((M, 60), "Schadensbericht", font=pil_font(56, True), fill=TEXT)
    d.text((M, 135), f"Fertigungsauftrag {fa}", font=pil_font(34, True), fill=ACCENT)
    first = findings[0] if findings else {}
    dates = sorted({_v(f, "Datum") for f in findings if _v(f, "Datum")},
                   key=lambda s: datetime.strptime(s, "%d.%m.%Y") if len(s) == 10 else datetime.min)
    meta = [("Platinen-Nr.", _v(first, "Platinen-Nr.")),
            ("Bezeichnung", _v(first, "Artikelbezeichnung")),
            ("Zeitraum", f"{dates[0]} – {dates[-1]}" if len(dates) > 1 else (dates[0] if dates else "")),
            ("Befunde", str(len(findings))),
            ("Erstellt von", author)]
    y = 210
    for k, v in meta:
        if v:
            d.text((M, y), k, font=pil_font(24), fill=MUTED)
            d.text((M + 230, y), v, font=pil_font(24, True), fill=TEXT)
            y += 40

    # Übersichtstabelle
    y += 30
    d.text((M, y), "Übersicht", font=pil_font(30, True), fill=TEXT)
    y += 55
    cols = [("Nr.", 70), ("Datum", 190), ("Fehlerart", 300), ("Bauteil", 520)]
    fh = pil_font(20, True)
    fr = pil_font(20)
    x = M
    d.rectangle((M, y - 8, W - M, y + 34), fill=ACCENT)
    for name, w in cols:
        d.text((x + 10, y), name, font=fh, fill="white")
        x += w
    y += 42
    for i, f in enumerate(findings, 1):
        if y > H - 140:
            d.text((M, y), f"… weitere {len(findings) - i + 1} Befunde auf den folgenden Seiten",
                   font=fr, fill=MUTED)
            break
        if i % 2 == 0:
            d.rectangle((M, y - 6, W - M, y + 30), fill=ROW_BG)
        vals = [str(i), f"{_v(f, 'Datum')} {_v(f, 'Uhrzeit')[:5]}", _v(f, "Fehlerart") or "–",
                _v(f, "Bauteil").replace("  ·  ", ", ") or "–"]
        x = M
        for (name, w), val in zip(cols, vals):
            d.text((x + 10, y), _fit(d, val, fr, w - 20), font=fr, fill=TEXT)
            x += w
        y += 36

    # ersten Befund noch auf Seite 1 unterbringen, wenn unter der Tabelle Platz ist
    slot_h = (H - 2 * M - 70) // 2
    start = 0
    if findings and y + 50 + slot_h < H - 70:
        _draw_finding(page, d, findings[0], 1, y + 50, slot_h)
        start = 1
    pages.append(page)

    # ---------- weitere Befunde: zwei pro Seite ----------
    for i in range(start, len(findings), 2):
        page, d = _new_page()
        d.rectangle((0, 0, W, 8), fill=ACCENT)
        for j, f in enumerate(findings[i:i + 2]):
            _draw_finding(page, d, f, i + j + 1, M + j * slot_h, slot_h)
        pages.append(page)

    total = len(pages)
    for n, p in enumerate(pages, 1):
        _footer(ImageDraw.Draw(p), fa, n, total)
    pages[0].save(out_path, save_all=True, append_images=pages[1:], resolution=DPI,
                  title=f"Schadensbericht FA {fa}", author=author or "Mikroskop-Capture")
    return out_path


def _draw_finding(page, d, f, nr, top, slot_h):
    title = f"Befund {nr}"
    sub = f"{_v(f, 'Datum')} {_v(f, 'Uhrzeit')}  ·  Fall {_v(f, 'Fall-Nr.')} / Bild {_v(f, 'Bild-Nr.')}"
    d.text((M, top), title, font=pil_font(30, True), fill=TEXT)
    d.text((M + 170, top + 8), sub, font=pil_font(20), fill=MUTED)
    y = top + 50

    # Bild (Seitenverhältnis beibehalten)
    img_h_max = int(slot_h * 0.62)
    box_w = W - 2 * M
    path = f.get("_image")
    if path:
        try:
            with Image.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail((box_w, img_h_max), Image.LANCZOS)
                page.paste(im, (M + (box_w - im.width) // 2, y))
                d.rectangle((M + (box_w - im.width) // 2 - 1, y - 1, M + (box_w + im.width) // 2, y + im.height),
                            outline=LINE, width=2)
                y += im.height + 18
        except Exception:
            path = None
    if not path:
        d.rectangle((M, y, W - M, y + 120), outline=LINE, width=2)
        d.text((W // 2, y + 60), f"Bild nicht gefunden: {_v(f, 'Dateiname')}", font=pil_font(20),
               fill=MUTED, anchor="mm")
        y += 140

    # Angaben
    fl, fv = pil_font(21), pil_font(21, True)
    bauteil = _v(f, "Bauteil").replace("  ·  ", ", ")
    details = ", ".join(x for x in (_v(f, "Gehäuse"), _v(f, "Technologie"), _v(f, "Bauteiltyp"))
                        if x and x not in bauteil)          # nichts doppelt anzeigen
    rows = [("Fehlerart", _v(f, "Fehlerart")),
            ("Bauteil", bauteil + (f"  ({details})" if details else "")),
            ("Vergrößerung", _v(f, "Vergrößerung")),
            ("Bearbeiter", _v(f, "Bearbeiter"))]
    for k, v in rows:
        if v:
            d.text((M, y), k, font=fl, fill=MUTED)
            d.text((M + 190, y), _fit(d, v, fv, W - 2 * M - 190), font=fv, fill=TEXT)
            y += 32
    desc = _v(f, "Schadensbeschreibung")
    if desc:
        d.text((M, y), "Beschreibung", font=fl, fill=MUTED)
        limit = top + slot_h - 20
        for line in _wrap(d, desc, pil_font(21), W - 2 * M - 190):
            if y > limit - 30:
                d.text((M + 190, y), "…", font=pil_font(21), fill=TEXT)
                break
            d.text((M + 190, y), line, font=pil_font(21), fill=TEXT)
            y += 30

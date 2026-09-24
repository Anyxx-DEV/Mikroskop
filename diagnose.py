"""
Diagnose für die Capture-Card: probiert alle Videomodi und Auflösungen durch,
misst Bildrate und Helligkeit und speichert von jedem Versuch ein Testbild.
Ergebnis: Ordner "diagnose" neben diesem Skript (diagnose.txt + Bilder).
"""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")
import cv2

OUT = (Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent) / "diagnose"
OUT.mkdir(exist_ok=True)
LOG = open(OUT / "diagnose.txt", "w", encoding="utf-8")


def log(text=""):
    print(text, flush=True)
    LOG.write(text + "\n")
    LOG.flush()


def fourcc_str(v):
    v = int(v)
    s = "".join(chr((v >> 8 * i) & 0xFF) for i in range(4))
    return s if s.isalnum() else "-"


try:
    from pygrabber.dshow_graph import FilterGraph
    devices = FilterGraph().get_input_devices()
except Exception as e:
    devices = []
    log(f"pygrabber nicht verfügbar: {e}")

log(f"OpenCV {cv2.__version__}")
log("Gefundene Videogeräte:")
for i, n in enumerate(devices):
    log(f"  {i}: {n}")
if not devices:
    log("  (keine)")

idx = next((i for i, n in enumerate(devices) if "ugreen" in n.lower() or "capture" in n.lower()), None)
if len(sys.argv) > 1:
    idx = int(sys.argv[1])
if idx is None:
    idx = int(input("Nummer der Capture-Card eingeben: ") or 0)
log(f"\nTeste Gerät {idx}: {devices[idx] if idx < len(devices) else '?'}\n")

BACKENDS = [("DSHOW", cv2.CAP_DSHOW), ("MSMF", cv2.CAP_MSMF)]
FOURCCS = ["MJPG", "YUY2", "NV12", None]
RESOLUTIONS = [(3840, 2160), (1920, 1080), (1280, 720)]

log(f"{'Backend':6} {'Format':6} {'angefragt':>10} -> {'erhalten':>10} {'FourCC':6} {'fps':>5} {'Hell.':>6} {'Kontr.':>6}  Ergebnis")
log("-" * 90)
best = None
for bname, backend in BACKENDS:
    for fcc in FOURCCS:
        if backend == cv2.CAP_MSMF and fcc not in (None, "MJPG", "NV12"):
            continue
        for w, h in RESOLUTIONS:
            cap = cv2.VideoCapture(idx, backend)
            if not cap.isOpened():
                log(f"{bname:6} {fcc or 'Std':6} {w}x{h:<5}    Gerät lässt sich nicht öffnen")
                continue
            if fcc:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fcc))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            cap.set(cv2.CAP_PROP_FPS, 30)
            aw, ah = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            got_fcc = fourcc_str(cap.get(cv2.CAP_PROP_FOURCC))

            frames, last, t0 = 0, None, time.time()
            while time.time() - t0 < 4:
                ok, f = cap.read()
                if ok:
                    frames += 1
                    last = f
            fps = frames / (time.time() - t0)
            cap.release()

            if last is None:
                log(f"{bname:6} {fcc or 'Std':6} {w}x{h:<5} -> {aw}x{ah:<5} {got_fcc:6} {fps:5.1f}      -      -  KEINE BILDER")
                continue
            mean, std = float(last.mean()), float(last.std())
            black = mean < 6 or std < 2
            name = f"{bname}_{fcc or 'Std'}_{w}x{h}.png"
            cv2.imencode(".png", last)[1].tofile(str(OUT / name))
            res = "schwarz / kein Signal" if black else "BILD OK"
            log(f"{bname:6} {fcc or 'Std':6} {w}x{h:<5} -> {last.shape[1]}x{last.shape[0]:<5} {got_fcc:6} "
                f"{fps:5.1f} {mean:6.1f} {std:6.1f}  {res}")
            if not black and (best is None or (last.shape[1], fps) > (best[1], best[2])):
                mode = "Media Foundation" if backend == cv2.CAP_MSMF else f"DirectShow · {fcc or 'Standard'}"
                best = (mode, last.shape[1], fps, name, f"{w}x{h}")

log("")
if best:
    log(f"EMPFEHLUNG im Programm:  Videomodus '{best[0]}'  +  Auflösung {best[4]}")
    log(f"            ({best[1]} px breit, {best[2]:.1f} fps)  -> Testbild {best[3]}")
else:
    log("In KEINEM Modus kam ein echtes Bild an. Die Capture-Card bekommt kein (verwertbares) HDMI-Signal:")
    log("  - Zeigt der Monitor am Mikroskop ein Bild? Hängt die Karte am richtigen HDMI-Ausgang?")
    log("  - HDMI-Kabel direkt Mikroskop -> Capture-Card IN (nicht OUT) testen")
    log("  - Mikroskop-Ausgabe evtl. auf 1080p/30 Hz oder 60 Hz stellen (4K/60 wird nicht von jeder Karte angenommen)")
    log("  - HDCP im Mikroskop-Menü deaktivieren, falls vorhanden")
    log("  - USB-3-Port (blau) direkt am PC verwenden")
log(f"\nErgebnisse gespeichert in: {OUT}")
LOG.close()
try:
    input("\nEnter drücken zum Beenden…")
except EOFError:
    pass

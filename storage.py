"""
Ablage der Aufnahmen:
- Bild in den Unterordner des Fertigungsauftrags  (…\Bilder Mikroskop\81678\…)
- Vorschaubild in …\Bilder Mikroskop\.thumbs\ (für Programm-Ansichten)
- Befund als Datensatz in die SQL-Tabelle dbo.SMD_Mikroskop_Befunde (ersetzt die frühere Excel-Liste)
- Offline-Puffer: Ist der Server/die Datenbank nicht erreichbar, wird lokal zwischengespeichert
  (%LOCALAPPDATA%\MikroskopCapture\puffer) und später automatisch nachgetragen.
- Rückgängig (Bild -> .papierkorb, Datensatz als gelöscht markieren), Log-Datei.
"""

import json
import logging
import logging.handlers
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import sqldb

log = logging.getLogger("mikroskop")
THUMB_WIDTH = 480          # Vorschaubild (Liste "Befunde", Leiste "Letzte Aufnahmen")


# ---------------- Hilfsfunktionen ----------------
def local_data_dir():
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    d = base / "MikroskopCapture"
    d.mkdir(parents=True, exist_ok=True)
    return d


def setup_logging(app_dir):
    """Log-Datei neben dem Programm (Fallback: %LOCALAPPDATA%), rotierend 5 x 1 MB."""
    for d in (Path(app_dir) / "logs", local_data_dir() / "logs"):
        try:
            d.mkdir(parents=True, exist_ok=True)
            path = d / "mikroskop.log"
            handler = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5,
                                                           encoding="utf-8")
            break
        except OSError:
            continue
    else:
        return None
    handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.setLevel(logging.INFO)
    log.handlers[:] = [handler]
    log.propagate = False
    return path


def safe_name(text):
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(text).strip())
    return re.sub(r"\s+", "_", text)[:40]


def fa_folder(fa):
    return safe_name(fa) or "ohne_FA"


def hide_dir(path):
    try:
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)
    except Exception:
        pass


def _hidden_subdir(root, name):
    d = Path(root) / name
    if not d.exists():
        d.mkdir(parents=True)
        hide_dir(d)
    return d


def thumb_path(image_path):
    """Vorschaubild zu einem Foto: <Hauptordner>\\.thumbs\\<Name>.jpg (Hauptordner = über dem FA-Ordner)."""
    p = Path(image_path)
    for root in (p.parent.parent, p.parent):
        t = root / ".thumbs" / (p.stem + ".jpg")
        if t.exists():
            return t
    return None


def _short_err(e):
    msg = str(e)
    m = re.search(r"\]([^\[\]]+)\(\d+\)", msg)          # ODBC-Meldung kürzen
    return (m.group(1).strip() if m else msg)[:300]


class Store:
    """
    rec (Aufnahme-Datensatz): id, root, fa_folder, filename, sql (Server/DB/Benutzer, ohne Passwort),
    data (Spalten für dbo.SMD_Mikroskop_Befunde), db_id (nach dem Eintragen), stage (im Puffer)
    """

    def __init__(self, on_change=None):
        self.lock = threading.RLock()
        self.buffer_dir = local_data_dir() / "puffer"
        self.buffer_dir.mkdir(parents=True, exist_ok=True)
        self.on_change = on_change or (lambda n: None)

    # ---- Pfade ----
    @staticmethod
    def image_path(rec):
        return Path(rec["root"]) / rec["fa_folder"] / rec["filename"]

    @staticmethod
    def new_record(root, fa, filename, sql_cfg, data):
        return {"id": uuid.uuid4().hex, "root": str(root), "fa_folder": fa_folder(fa), "filename": filename,
                "sql": {k: sql_cfg.get(k, "") for k in ("server", "database", "user")},
                "data": data, "db_id": None, "created": time.time()}

    # ---- Speichern ----
    def save(self, rec, img_bytes, thumb_bytes):
        """Speichert Bild + Datensatz. Ergebnis: ("ok"|"db_buffered"|"buffered", Grund)."""
        with self.lock:
            try:
                self._write_image(rec, img_bytes, thumb_bytes)
            except Exception as e:
                log.warning("Bild nicht speicherbar (%s) -> Puffer: %s", e, rec["filename"])
                self._buffer(rec, "image", img_bytes, thumb_bytes)
                return "buffered", str(e)
            log.info("Bild gespeichert: %s", self.image_path(rec))
            try:
                self._db_insert(rec)
            except Exception as e:
                reason = _short_err(e)
                log.warning("Datenbank-Eintrag nicht möglich (%s) -> Puffer: %s", reason, rec["filename"])
                self._buffer(rec, "db", None, None)
                return "db_buffered", reason
            return "ok", None

    def _write_image(self, rec, img_bytes, thumb_bytes):
        d = Path(rec["root"]) / rec["fa_folder"]
        d.mkdir(parents=True, exist_ok=True)
        base, ext = os.path.splitext(rec["filename"])
        path, n = d / rec["filename"], 2
        while path.exists():                     # Namenskonflikt -> _2, _3 …
            path = d / f"{base}_{n}{ext}"
            n += 1
        path.write_bytes(img_bytes)              # funktioniert auch mit Umlauten / UNC-Pfaden
        rec["filename"] = path.name
        rec["data"]["Dateiname"] = path.name
        rec["data"]["Bildpfad"] = str(path)
        if thumb_bytes:
            try:
                (_hidden_subdir(rec["root"], ".thumbs") / (path.stem + ".jpg")).write_bytes(thumb_bytes)
            except OSError as e:
                log.warning("Vorschaubild nicht speicherbar: %s", e)

    def _db_insert(self, rec):
        rec["db_id"] = sqldb.insert_befund(rec["sql"], rec["data"])
        log.info("Datenbank: Befund %s eingetragen (FA %s, %s)", rec["db_id"], rec["data"].get("Bel_Nr"),
                 rec["filename"])

    # ---- Offline-Puffer ----
    def _buffer(self, rec, stage, img_bytes, thumb_bytes):
        rec["stage"] = stage
        b = self.buffer_dir
        if img_bytes is not None:
            (b / f"{rec['id']}.img").write_bytes(img_bytes)
        if thumb_bytes:
            (b / f"{rec['id']}.thumb").write_bytes(thumb_bytes)
        (b / f"{rec['id']}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=str),
                                             encoding="utf-8")
        self.on_change(self.pending_count())

    def pending_count(self):
        return len(list(self.buffer_dir.glob("*.json")))

    def is_buffered(self, rec):
        return (self.buffer_dir / f"{rec['id']}.json").exists()

    def _drop(self, rid):
        for ext in ("json", "img", "thumb"):
            (self.buffer_dir / f"{rid}.{ext}").unlink(missing_ok=True)

    def retry(self):
        """Puffer nachtragen. Liefert (erledigt, verbleibend, letzter Fehler)."""
        done, err = 0, None
        with self.lock:
            for j in sorted(self.buffer_dir.glob("*.json"), key=lambda p: p.stat().st_mtime):
                try:
                    rec = json.loads(j.read_text(encoding="utf-8"))
                    rid = rec["id"]
                    if "data" not in rec:              # Eintrag aus der früheren Excel-Version -> verwerfen
                        log.warning("Puffer: alter Excel-Eintrag übersprungen: %s", rec.get("filename"))
                        self._drop(rid)
                        continue
                    if rec.get("stage") == "image":
                        thumb = self.buffer_dir / f"{rid}.thumb"
                        self._write_image(rec, (self.buffer_dir / f"{rid}.img").read_bytes(),
                                          thumb.read_bytes() if thumb.exists() else None)
                        rec["stage"] = "db"
                        j.write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
                        (self.buffer_dir / f"{rid}.img").unlink(missing_ok=True)
                        log.info("Puffer: Bild nachgetragen: %s", self.image_path(rec))
                    self._db_insert(rec)
                    self._drop(rid)
                    done += 1
                except Exception as e:
                    err = _short_err(e)
                    log.info("Puffer: Nachtragen noch nicht möglich (%s)", err)
                    break          # Server/Datenbank noch nicht bereit -> später erneut
        n = self.pending_count()
        if done:
            self.on_change(n)
        return done, n, err

    # ---- Rückgängig ----
    def undo(self, rec, user=""):
        """Bild -> .papierkorb, Datensatz als gelöscht markieren. Liefert Warntext oder None."""
        with self.lock:
            if self.is_buffered(rec):
                stage = rec.get("stage", "image")
                self._drop(rec["id"])
                self.on_change(self.pending_count())
                if stage == "image":
                    log.info("Rückgängig (aus Puffer): %s", rec["filename"])
                    return None
            warn = None
            root, path = Path(rec["root"]), self.image_path(rec)
            try:
                if path.exists():   # nicht endgültig löschen, sondern in versteckten Papierkorb-Ordner
                    os.replace(path, _hidden_subdir(root, ".papierkorb") / path.name)
                (root / ".thumbs" / (path.stem + ".jpg")).unlink(missing_ok=True)
            except Exception as e:
                warn = f"Bild konnte nicht entfernt werden: {e}"
            if rec.get("db_id"):
                try:
                    sqldb.mark_deleted(rec["sql"], rec["db_id"], user)
                except Exception as e:
                    warn = f"Datenbank-Eintrag {rec['db_id']} konnte nicht als gelöscht markiert werden: {_short_err(e)}"
            log.info("Rückgängig: %s (DB-ID %s)%s", rec["filename"], rec.get("db_id"),
                     f" – Warnung: {warn}" if warn else "")
            return warn


# ---------------- Befunde für den PDF-Bericht ----------------
def findings_from_db(sql_cfg, fa):
    """Befunde eines Fertigungsauftrags aus der Datenbank, im Format für report.make_report."""
    out = []
    for r in sqldb.befunde_for_fa(sql_cfg, fa):
        erf = r.get("Erfasst")
        bauteil = "  ·  ".join(x for x in (r.get("Bauteil_ArtikelNr") or "", r.get("Bauteil_Bez") or "") if x)
        img = Path(r["Bildpfad"]) if r.get("Bildpfad") else None
        out.append({
            "Datum": erf.strftime("%d.%m.%Y") if isinstance(erf, datetime) else "",
            "Uhrzeit": erf.strftime("%H:%M:%S") if isinstance(erf, datetime) else "",
            "Fall-Nr.": r.get("Fall_Nr") or "", "Bild-Nr.": r.get("Bild_Nr") or "",
            "Platinen-Nr.": r.get("Platinen_Nr") or "", "Artikelbezeichnung": r.get("Artikelbezeichnung") or "",
            "Fehlerart": r.get("Fehlerart") or "", "Bauteil": bauteil, "Position": r.get("Position") or "",
            "Gehäuse": r.get("Gehaeuse") or "", "Technologie": r.get("Technologie") or "",
            "Bauteiltyp": r.get("Bauteiltyp") or "", "Schadensbeschreibung": r.get("Beschreibung") or "",
            "Vergrößerung": r.get("Vergroesserung") or "", "Bearbeiter": r.get("Bearbeiter") or "",
            "Dateiname": r.get("Dateiname") or "",
            "_image": img if img and img.exists() else None,
        })
    return out

"""
Anbindung an den SQL Server (nur lesend).
Nachschlagen eines Fertigungsauftrags (Bel_Nr) in SE_Tools.dbo.SMD_FA_Fehler
-> Platinen-Nr. (Bel_Pos_ArtikelNr) und Artikelbezeichnung der Leiterplatte.

Das Passwort des SQL-Benutzers liegt nicht im Programm, sondern in der
Windows-Anmeldeinformationsverwaltung (Ziel: "MikroskopCapture/SQL/<Server>").
"""

import ctypes
import re
import threading
from ctypes import wintypes

try:
    import pyodbc
except ImportError:
    pyodbc = None

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
DRIVER_PREFERENCE = ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server", "SQL Server")

# Abfrage für den Fertigungsauftrag - Tabellen-/Spaltennamen wie im SQL Server Management Studio
QUERY_AUFTRAG = """
SELECT DISTINCT [Bel_Nr], [Bel_Pos_ArtikelNr], [Bel_Pos_Artikeltext]
FROM [dbo].[SMD_FA_Fehler]
WHERE [Bel_Nr] = ?
ORDER BY [Bel_Pos_ArtikelNr]
"""


class CREDENTIAL(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_char)), ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


def _target(server):
    return f"MikroskopCapture/SQL/{server.strip().lower()}"


def store_password(server, user, password):
    """Passwort in der Windows-Anmeldeinformationsverwaltung ablegen."""
    blob = password.encode("utf-16-le")
    buf = ctypes.create_string_buffer(blob, len(blob))
    cred = CREDENTIAL(Type=CRED_TYPE_GENERIC, TargetName=_target(server), UserName=user,
                      CredentialBlobSize=len(blob), Persist=CRED_PERSIST_LOCAL_MACHINE,
                      CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    if not adv.CredWriteW(ctypes.byref(cred), 0):
        raise OSError(ctypes.FormatError(ctypes.get_last_error()))


def _load_password(server):
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    ptr = ctypes.POINTER(CREDENTIAL)()
    if not adv.CredReadW(_target(server), CRED_TYPE_GENERIC, 0, ctypes.byref(ptr)):
        return None
    try:
        c = ptr.contents
        return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-16-le")
    finally:
        adv.CredFree(ptr)


def has_password(server):
    return bool(server) and _load_password(server) is not None


def pick_driver():
    if pyodbc is None:
        return None
    installed = set(pyodbc.drivers())
    return next((d for d in DRIVER_PREFERENCE if d in installed), None)


def connect(server, database, user, password=None, timeout=5):
    if pyodbc is None:
        raise RuntimeError("Python-Paket 'pyodbc' fehlt (pip install pyodbc).")
    driver = pick_driver()
    if not driver:
        raise RuntimeError("Kein SQL-Server-ODBC-Treiber installiert.")
    if password is None:
        password = _load_password(server)
    if password is None:
        raise RuntimeError("Für diesen Server ist noch kein Passwort hinterlegt – Verbindung einrichten.")
    parts = [f"DRIVER={{{driver}}}", f"SERVER={server}", f"DATABASE={database}", f"UID={user}", f"PWD={password}"]
    if driver.startswith("ODBC Driver 1"):
        # wie im SQL Server Management Studio: Verschlüsseln = Optional, Serverzertifikat vertrauen
        parts += ["Encrypt=Optional" if driver.startswith("ODBC Driver 18") else "Encrypt=no",
                  "TrustServerCertificate=yes"]
    return pyodbc.connect(";".join(parts), timeout=timeout, readonly=True)


def lookup_auftrag(cfg, bel_nr):
    """Liefert [(Bel_Nr, ArtikelNr, Artikeltext), ...] für den Fertigungsauftrag."""
    with connect(cfg["server"], cfg["database"], cfg["user"]) as conn:
        rows = conn.cursor().execute(QUERY_AUFTRAG, bel_nr.strip()).fetchall()
    return [(str(r[0]).strip(), str(r[1] or "").strip(), str(r[2] or "").strip()) for r in rows]


# ---------------- Vorschläge beim Tippen ----------------
# Exakter Treffer zuerst, danach die neuesten Aufträge (höchste Bel_Nr) - die sind am wahrscheinlichsten gemeint.
QUERY_SUGGEST = """
SELECT TOP {limit} [Bel_Nr], [Bel_Pos_ArtikelNr], [Bel_Pos_Artikeltext]
FROM [dbo].[SMD_FA_Fehler]
WHERE CAST([Bel_Nr] AS nvarchar(50)) LIKE ?
GROUP BY [Bel_Nr], [Bel_Pos_ArtikelNr], [Bel_Pos_Artikeltext]
ORDER BY CASE WHEN CAST([Bel_Nr] AS nvarchar(50)) = ? THEN 0 ELSE 1 END, [Bel_Nr] DESC
"""

_conn = None
_conn_key = None
_conn_lock = threading.Lock()


def _cached_connection(cfg):
    """Offene Verbindung wiederverwenden, damit Vorschläge ohne Verbindungsaufbau kommen."""
    global _conn, _conn_key
    key = (cfg["server"], cfg["database"], cfg["user"])
    if _conn is None or _conn_key != key:
        _conn = connect(*key)
        _conn_key = key
    return _conn


def suggest_auftraege(cfg, prefix, limit=8):
    """Fertigungsaufträge, deren Bel_Nr mit prefix beginnt: [(Bel_Nr, ArtikelNr, Artikeltext), ...]."""
    global _conn
    prefix = prefix.strip()
    like = re.sub(r"([\[%_])", r"[\1]", prefix) + "%"      # Platzhalterzeichen maskieren
    sql = QUERY_SUGGEST.format(limit=int(limit))
    with _conn_lock:
        for attempt in (1, 2):
            try:
                rows = _cached_connection(cfg).cursor().execute(sql, like, prefix).fetchall()
                break
            except Exception:
                _conn = None            # Verbindung verloren -> einmal neu aufbauen
                if attempt == 2:
                    raise
    return [(str(r[0]).strip(), str(r[1] or "").strip(), str(r[2] or "").strip()) for r in rows]


# ---------------- Stückliste (Bauteile einer Leiterplatte) ----------------
BOM_TABLE = "[dbo].[tmp_ProduktionsStücklisten_Rekursion]"
# Suchmuster für die automatische Spaltenerkennung, in dieser Reihenfolge bevorzugt.
# Kopf- und Baugruppen-Spalten (= Leiterplatte bzw. übergeordnete Baugruppe) werden ausgeschlossen,
# damit im Dropdown die Artikel-Nr. + Bezeichnung des BAUTEILS steht.
# Überschreibbar in config.json unter "sql" -> "bom_columns":
#   {"pos": ..., "artnr": ..., "bez": ..., "package": ..., "technology": ..., "type": ...}
BOM_EXCLUDE = r"^(kopf|baugruppe|bg_|ober|parent|eltern|haupt)|baugruppe"
BOM_PATTERNS = {
    "pos": [r"refdes", r"referenz", r"designator", r"best(ü|ue)ck", r"einbau", r"position$", r"^pos(ition)?$"],
    "artnr": [r"^artikelnr$", r"^artikel_?nr$", r"^pos\w*artikelnr$", r"^(komp|bauteil|unter|child)\w*artikelnr$",
              r"artikelnr$"],
    "bez": [r"^artikelbez(eichnung)?$", r"^artikeltext$", r"^pos\w*artikel(bez|text)",
            r"^(komp|bauteil|unter|child)\w*artikel(bez|text)", r"artikelbez$", r"artikeltext$", r"bezeichnung$"],
    "package": [r"^package$", r"geh(ä|ae)use"],
    "technology": [r"^technology$", r"technolog"],
    "type": [r"^type_of_component$", r"bauteiltyp", r"component_?type"],
}
DETAIL_KEYS = ("package", "technology", "type")
# Fest vorgegebene Spalten (vom Anwender festgelegt) - werden vor der automatischen Erkennung verwendet
BOM_DEFAULT_COLUMNS = {"artnr": "SMD_ArtikelNR", "bez": "SMD_Art_Bez"}


def bom_columns(conn, cfg):
    """Ermittelt die Spalten der Stückliste: {"pos", "artnr", "bez", "extra", "all"}."""
    cols = [d[0] for d in conn.cursor().execute(f"SELECT TOP 0 * FROM {BOM_TABLE}").description]
    by_lower = {c.lower(): c for c in cols}          # Spaltennamen ohne Groß/Klein vergleichen
    override = {**BOM_DEFAULT_COLUMNS, **(cfg.get("bom_columns") or {})}
    found = {"all": cols}
    for key, patterns in BOM_PATTERNS.items():
        col = by_lower.get(str(override.get(key) or "").lower())
        if not col:
            candidates = [c for c in cols if not re.search(BOM_EXCLUDE, c, re.I)
                          and not re.search(r"nrbez$", c, re.I)]     # kombinierte "Nr (Bez)"-Spalten
            col = next((c for p in patterns for c in candidates if re.search(p, c, re.I)), None)
        found[key] = col if col in cols else None
    if not (found["artnr"] or found["bez"]):
        raise RuntimeError("Bauteil-Spalten in der Stückliste nicht erkannt. Vorhandene Spalten: " + ", ".join(cols))
    return found


def lookup_bauteile(cfg, kopf_artikel_nr):
    """
    Bauteile der Leiterplatte: ([(Anzeigetext, Details), ...], erkannte Spalten).
    Details = {"pos", "artnr", "bez", "package", "technology", "type"} (fehlende Spalten -> "").
    """
    keys = ("pos", "artnr", "bez") + DETAIL_KEYS
    with connect(cfg["server"], cfg["database"], cfg["user"]) as conn:
        m = bom_columns(conn, cfg)
        used = [k for k in keys if m.get(k)]
        sql = (f"SELECT DISTINCT {', '.join(f'[{m[k]}]' for k in used)} FROM {BOM_TABLE} "
               f"WHERE [KopfArtikelNr] = ?")
        rows = conn.cursor().execute(sql, kopf_artikel_nr.strip()).fetchall()
    items = {}
    for r in rows:
        det = {k: "" for k in keys}
        det.update({k: (str(v).strip() if v is not None else "") for k, v in zip(used, r)})
        label = "  ·  ".join(det[k] for k in ("artnr", "bez") if det[k])     # Artikel-Nr. · Bezeichnung
        if not label:
            continue
        if label in items:            # gleiches Bauteil an mehreren Positionen -> Positionen sammeln
            known = items[label]["pos"].split(", ") if items[label]["pos"] else []
            if det["pos"] and det["pos"] not in known:
                items[label]["pos"] = ", ".join(known + [det["pos"]])
        else:
            items[label] = det
    natural = lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]
    return [(lbl, items[lbl]) for lbl in sorted(items, key=natural)], m


def test_connection(server, database, user, password=None):
    with connect(server, database, user, password) as conn:
        n = conn.cursor().execute("SELECT COUNT(*) FROM [dbo].[SMD_FA_Fehler]").fetchone()[0]
    return n

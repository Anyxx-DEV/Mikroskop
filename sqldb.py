"""
Anbindung an den SQL Server.
- lesend: Fertigungsauftrag (SMD_FA_Fehler), Stückliste (tmp_ProduktionsStücklisten_Rekursion)
- schreibend: Befunde in die eigene Tabelle dbo.SMD_Mikroskop_Befunde (ersetzt die Excel-Liste)

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


def connect(server, database, user, password=None, timeout=5, write=False):
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
    return pyodbc.connect(";".join(parts), timeout=timeout, readonly=not write)


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


def all_bauteile(cfg):
    """
    Alle Bauteile aus allen Stücklisten (je Artikel-Nr. einmal) für die Suche über die ganze Datenbank:
    [(Anzeigetext, Details), ...]. Wird einmal geladen und im Programm zwischengespeichert.
    """
    with connect(cfg["server"], cfg["database"], cfg["user"], timeout=10) as conn:
        m = bom_columns(conn, cfg)
        if not m.get("artnr"):
            return []
        extra = [k for k in ("bez",) + DETAIL_KEYS if m.get(k)]
        sel = ", ".join([f"[{m['artnr']}]"] + [f"MAX([{m[k]}])" for k in extra])
        sql = (f"SELECT {sel} FROM {BOM_TABLE} WHERE [{m['artnr']}] IS NOT NULL "
               f"AND LTRIM(RTRIM([{m['artnr']}])) <> '' GROUP BY [{m['artnr']}]")
        rows = conn.cursor().execute(sql).fetchall()
    out = []
    for r in rows:
        det = {"pos": "", "artnr": str(r[0]).strip(), "bez": "", "package": "", "technology": "", "type": ""}
        det.update({k: (str(v).strip() if v is not None else "") for k, v in zip(extra, r[1:])})
        label = "  ·  ".join(det[k] for k in ("artnr", "bez") if det[k])
        out.append((label, det))
    out.sort(key=lambda x: x[0])
    return out


# ================= Befunde-Tabelle (ersetzt die Excel-Liste) =================
BEFUNDE_TABLE = "dbo.SMD_Mikroskop_Befunde"
BEFUNDE_VIEW = "dbo.SMD_Mikroskop_Befunde_View"   # Sicht im SSMS-Ordner "Sichten" (neben SMD_FA_Fehler)
OLD_VIEW = "dbo.v_SMD_Mikroskop_Befunde"           # frühere Bezeichnung (wird vom Skript entfernt)
# Spalten, die das Programm schreibt (Reihenfolge = Reihenfolge im INSERT)
BEFUND_FIELDS = ["Erfasst", "Bel_Nr", "Fall_Nr", "Bild_Nr", "Platinen_Nr", "Artikelbezeichnung", "Fehlerart",
                 "Bauteil_ArtikelNr", "Bauteil_Bez", "Position", "Gehaeuse", "Technologie", "Bauteiltyp",
                 "Beschreibung", "Vergroesserung", "Bearbeiter", "Bildpfad", "Dateiname", "Arbeitsplatz"]


def create_table_sql(grant_user=""):
    """SQL-Skript zum Anlegen der Tabelle (Batches mit GO getrennt)."""
    # Rechte nur vergeben, wenn es einen eigenen DB-Benutzer gibt (ist er Besitzer/dbo, hat er sie schon)
    grant = (f"\nGO\nIF EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'{grant_user}' "
             f"AND type IN ('S', 'U', 'G') AND name <> USER_NAME())\n"
             f"BEGIN\n"
             f"    EXEC(N'GRANT SELECT, INSERT, UPDATE ON {BEFUNDE_TABLE} TO [{grant_user}]');\n"
             f"    EXEC(N'GRANT SELECT ON {BEFUNDE_VIEW} TO [{grant_user}]');\n"
             f"END\n"
             f"ELSE\n    PRINT N'Hinweis: kein eigener Benutzer {grant_user} in dieser Datenbank "
             f"(z. B. Besitzer/dbo) - keine Rechtevergabe noetig.';" if grant_user else "")
    return f"""-- Tabelle fuer die Mikroskop-Befunde (Leiterplatten-Schadensdokumentation)
-- Wird vom Programm Mikroskop-Capture beschrieben. Loeschen = nur markieren (Geloescht = 1).
IF OBJECT_ID(N'{BEFUNDE_TABLE}', N'U') IS NULL
BEGIN
    CREATE TABLE {BEFUNDE_TABLE} (
        ID                  int IDENTITY(1,1) NOT NULL CONSTRAINT PK_SMD_Mikroskop_Befunde PRIMARY KEY,
        Erfasst             datetime2(0)   NOT NULL,             -- Datum + Uhrzeit der Aufnahme
        Bel_Nr              nvarchar(30)   NOT NULL,             -- Fertigungsauftrag
        Fall_Nr             nvarchar(20)   NULL,
        Bild_Nr             int            NULL,
        Platinen_Nr         nvarchar(50)   NULL,
        Artikelbezeichnung  nvarchar(200)  NULL,
        Fehlerart           nvarchar(100)  NULL,
        Bauteil_ArtikelNr   nvarchar(50)   NULL,                 -- SMD_ArtikelNR aus der Stueckliste
        Bauteil_Bez         nvarchar(200)  NULL,                 -- SMD_Art_Bez (oder Freitext)
        Position            nvarchar(400)  NULL,                 -- Bestueckposition(en)
        Gehaeuse            nvarchar(50)   NULL,
        Technologie         nvarchar(50)   NULL,
        Bauteiltyp          nvarchar(100)  NULL,
        Beschreibung        nvarchar(max)  NULL,
        Vergroesserung      nvarchar(50)   NULL,
        Bearbeiter          nvarchar(100)  NULL,
        Bildpfad            nvarchar(400)  NOT NULL,             -- vollstaendiger Pfad zum Foto
        Dateiname           nvarchar(200)  NOT NULL,
        Arbeitsplatz        nvarchar(50)   NULL,                 -- PC-Name
        Angelegt_am         datetime2(0)   NOT NULL CONSTRAINT DF_SMD_Mikroskop_Befunde_Angelegt DEFAULT SYSDATETIME(),
        Geloescht           bit            NOT NULL CONSTRAINT DF_SMD_Mikroskop_Befunde_Geloescht DEFAULT 0,
        Geloescht_am        datetime2(0)   NULL,
        Geloescht_von       nvarchar(100)  NULL
    );
    CREATE UNIQUE INDEX UX_SMD_Mikroskop_Befunde_Bildpfad ON {BEFUNDE_TABLE} (Bildpfad);
    CREATE INDEX IX_SMD_Mikroskop_Befunde_BelNr ON {BEFUNDE_TABLE} (Bel_Nr, Erfasst);
    CREATE INDEX IX_SMD_Mikroskop_Befunde_Erfasst ON {BEFUNDE_TABLE} (Erfasst DESC);
END
GO
-- fruehere Sicht-Bezeichnung entfernen (nur die Sicht - die Daten liegen in der Tabelle)
DROP VIEW IF EXISTS {OLD_VIEW};
GO
-- Sicht (Ordner "Sichten"): nur gueltige Befunde, lesbar aufbereitet - fuer Abfragen, Excel, Power BI
CREATE OR ALTER VIEW {BEFUNDE_VIEW} AS
SELECT  ID,
        CAST(Erfasst AS date)                     AS Datum,
        CONVERT(char(5), Erfasst, 108)            AS Uhrzeit,
        Bel_Nr                                    AS Fertigungsauftrag,
        Fall_Nr, Bild_Nr,
        Platinen_Nr, Artikelbezeichnung,
        Fehlerart,
        CONCAT_WS(N' - ', Bauteil_ArtikelNr, Bauteil_Bez) AS Bauteil,
        Bauteil_ArtikelNr, Bauteil_Bez, Position, Gehaeuse, Technologie, Bauteiltyp,
        Beschreibung, Vergroesserung, Bearbeiter, Arbeitsplatz,
        Bildpfad, Dateiname, Erfasst
FROM    {BEFUNDE_TABLE}
WHERE   Geloescht = 0;{grant}
"""


def _write_conn(cfg, timeout=8):
    return connect(cfg["server"], cfg["database"], cfg["user"], timeout=timeout, write=True)


def table_exists(cfg):
    with connect(cfg["server"], cfg["database"], cfg["user"]) as conn:
        return conn.cursor().execute("SELECT OBJECT_ID(?, 'U')", BEFUNDE_TABLE).fetchone()[0] is not None


def create_table(cfg):
    """Legt die Tabelle an (braucht CREATE TABLE-Rechte für den angemeldeten Benutzer)."""
    with _write_conn(cfg) as conn:
        cur = conn.cursor()
        for batch in re.split(r"^\s*GO\s*$", create_table_sql(), flags=re.M | re.I):
            if batch.strip():
                cur.execute(batch)
        conn.commit()


def insert_befund(cfg, data):
    """Schreibt einen Befund, liefert die ID. Bereits vorhanden (gleicher Bildpfad) -> vorhandene ID."""
    from datetime import datetime
    values = []
    for f in BEFUND_FIELDS:
        v = data.get(f)
        if f == "Erfasst" and isinstance(v, str):
            v = datetime.fromisoformat(v)
        if f == "Bild_Nr":
            v = int(v) if v not in (None, "") else None
        elif isinstance(v, str):
            v = v.strip() or None
        values.append(v)
    cols = ", ".join(f"[{f}]" for f in BEFUND_FIELDS)
    marks = ", ".join("?" for _ in BEFUND_FIELDS)
    with _write_conn(cfg) as conn:
        cur = conn.cursor()
        try:
            new_id = cur.execute(f"INSERT INTO {BEFUNDE_TABLE} ({cols}) OUTPUT INSERTED.ID VALUES ({marks})",
                                 *values).fetchone()[0]
            conn.commit()
            return int(new_id)
        except pyodbc.IntegrityError:          # schon eingetragen (z. B. Puffer erneut nachgetragen)
            conn.rollback()
            row = cur.execute(f"SELECT ID FROM {BEFUNDE_TABLE} WHERE Bildpfad = ?", data["Bildpfad"]).fetchone()
            if row:
                return int(row[0])
            raise


def mark_deleted(cfg, befund_id, user):
    """Rückgängig: Eintrag als gelöscht markieren (bleibt zur Nachvollziehbarkeit in der Tabelle)."""
    with _write_conn(cfg) as conn:
        conn.cursor().execute(f"UPDATE {BEFUNDE_TABLE} SET Geloescht = 1, Geloescht_am = SYSDATETIME(), "
                              f"Geloescht_von = ? WHERE ID = ?", user or None, int(befund_id))
        conn.commit()


BEFUND_COLUMNS = ["ID", "Erfasst"] + BEFUND_FIELDS[1:]


def query_befunde(cfg, text="", fa="", fehlerart="", date_from=None, date_to=None, limit=1000):
    """Befunde für die Listenansicht (neueste zuerst): ([dict, ...], Gesamtanzahl der Treffer)."""
    where, params = ["Geloescht = 0"], []
    if fa:
        where.append("Bel_Nr LIKE ?")
        params.append(fa.strip() + "%")
    if fehlerart:
        where.append("Fehlerart = ?")
        params.append(fehlerart)
    if date_from:
        where.append("Erfasst >= ?")
        params.append(date_from)
    if date_to:
        where.append("Erfasst < ?")
        params.append(date_to)
    if text:
        like = "%" + re.sub(r"([\[%_])", r"[\1]", text.strip()) + "%"
        cols = ["Bel_Nr", "Platinen_Nr", "Artikelbezeichnung", "Fehlerart", "Bauteil_ArtikelNr", "Bauteil_Bez",
                "Position", "Beschreibung", "Bearbeiter", "Fall_Nr"]
        where.append("(" + " OR ".join(f"{c} LIKE ?" for c in cols) + ")")
        params += [like] * len(cols)
    w = " AND ".join(where)
    sel = ", ".join(f"[{c}]" for c in BEFUND_COLUMNS)
    with connect(cfg["server"], cfg["database"], cfg["user"], timeout=10) as conn:
        cur = conn.cursor()
        total = cur.execute(f"SELECT COUNT(*) FROM {BEFUNDE_TABLE} WHERE {w}", *params).fetchone()[0]
        rows = cur.execute(f"SELECT TOP {int(limit)} {sel} FROM {BEFUNDE_TABLE} WHERE {w} "
                           f"ORDER BY Erfasst DESC, ID DESC", *params).fetchall()
    return [dict(zip(BEFUND_COLUMNS, r)) for r in rows], int(total)


def befunde_for_fa(cfg, fa):
    """Alle Befunde eines Fertigungsauftrags (älteste zuerst) - für den PDF-Bericht."""
    sel = ", ".join(f"[{c}]" for c in BEFUND_COLUMNS)
    with connect(cfg["server"], cfg["database"], cfg["user"], timeout=10) as conn:
        rows = conn.cursor().execute(f"SELECT {sel} FROM {BEFUNDE_TABLE} WHERE Geloescht = 0 AND Bel_Nr = ? "
                                     f"ORDER BY Erfasst, ID", fa.strip()).fetchall()
    return [dict(zip(BEFUND_COLUMNS, r)) for r in rows]


def test_connection(server, database, user, password=None):
    with connect(server, database, user, password) as conn:
        n = conn.cursor().execute("SELECT COUNT(*) FROM [dbo].[SMD_FA_Fehler]").fetchone()[0]
    return n

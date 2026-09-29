"""
Anmeldung an der Server-Freigabe über die Windows-API (entspricht "net use"),
ohne Konsolenfenster und ohne dass das Passwort in einer Befehlszeile auftaucht.
Optional wird die Anmeldung in der Windows-Anmeldeinformationsverwaltung gespeichert
(wie "cmdkey /add"), damit der Zugriff auch nach einem Neustart automatisch klappt.
Das Passwort wird NICHT in config.json gespeichert.
"""

import ctypes
from ctypes import wintypes

RESOURCETYPE_DISK = 1
CRED_TYPE_DOMAIN_PASSWORD = 2
CRED_PERSIST_LOCAL_MACHINE = 2

ERROR_HINTS = {
    5: "Zugriff verweigert – Benutzer hat keine Rechte auf die Freigabe.",
    53: "Server nicht gefunden – Netzwerkverbindung/VPN prüfen.",
    67: "Freigabename nicht gefunden – Pfad prüfen.",
    85: "Der Laufwerksbuchstabe ist bereits belegt.",
    86: "Falsches Passwort.",
    1219: ("Es besteht bereits eine Verbindung zu diesem Server mit anderen Anmeldedaten. "
           "Vorhandene Verbindungen trennen (Explorer: Netzlaufwerk trennen) oder Windows neu anmelden."),
    1326: "Benutzername oder Passwort falsch.",
    1327: "Konto-Einschränkung (z. B. leeres Passwort nicht erlaubt).",
    1909: "Das Benutzerkonto ist gesperrt.",
}


class NETRESOURCE(ctypes.Structure):
    _fields_ = [("dwScope", wintypes.DWORD), ("dwType", wintypes.DWORD),
                ("dwDisplayType", wintypes.DWORD), ("dwUsage", wintypes.DWORD),
                ("lpLocalName", wintypes.LPWSTR), ("lpRemoteName", wintypes.LPWSTR),
                ("lpComment", wintypes.LPWSTR), ("lpProvider", wintypes.LPWSTR)]


class CREDENTIAL(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_char)), ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


def share_root(path):
    r"""'\\server\share\a\b' -> '\\server\share' (sonst None)."""
    p = str(path).replace("/", "\\")
    if not p.startswith("\\\\"):
        return None
    parts = [x for x in p[2:].split("\\") if x]
    return f"\\\\{parts[0]}\\{parts[1]}" if len(parts) >= 2 else None


def server_name(share):
    return share.lstrip("\\").split("\\")[0]


def error_text(code):
    hint = ERROR_HINTS.get(code)
    base = ctypes.FormatError(code).strip()
    return f"{hint} ({base}, Code {code})" if hint else f"{base} (Code {code})"


def connect(share, user, password, drive=None):
    """
    Verbindet die Freigabe (wie: net use N: /delete  +  net use /persistent:no N: \\server\share pw /user:x).
    Liefert (True, Meldung) oder (False, Fehlertext).
    """
    mpr = ctypes.WinDLL("mpr")
    drive = (drive or "").strip().upper().rstrip("\\") or None
    if drive and not drive.endswith(":"):
        drive += ":"
    if drive:
        mpr.WNetCancelConnection2W(drive, 0, True)       # vorhandenes Laufwerk trennen (erzwungen)
    nr = NETRESOURCE(dwType=RESOURCETYPE_DISK, lpLocalName=drive, lpRemoteName=share)
    rc = mpr.WNetAddConnection2W(ctypes.byref(nr), password, user, 0)   # 0 = nicht dauerhaft
    if rc == 1219:
        # alte Verbindung genau zu dieser Freigabe trennen und einmal neu versuchen
        mpr.WNetCancelConnection2W(share, 0, True)
        rc = mpr.WNetAddConnection2W(ctypes.byref(nr), password, user, 0)
    if rc == 0:
        return True, f"Verbunden: {drive + ' → ' if drive else ''}{share} (Benutzer {user})"
    return False, error_text(rc)


def save_credential(server, user, password):
    """Speichert die Anmeldung in der Windows-Anmeldeinformationsverwaltung (wie cmdkey /add)."""
    blob = password.encode("utf-16-le")
    buf = ctypes.create_string_buffer(blob, len(blob))
    cred = CREDENTIAL(Type=CRED_TYPE_DOMAIN_PASSWORD, TargetName=server, UserName=user,
                      CredentialBlobSize=len(blob), Persist=CRED_PERSIST_LOCAL_MACHINE,
                      CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    ok = ctypes.WinDLL("advapi32", use_last_error=True).CredWriteW(ctypes.byref(cred), 0)
    return (True, "Anmeldung in Windows gespeichert") if ok else (False, error_text(ctypes.get_last_error()))

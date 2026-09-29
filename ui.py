"""
Design-Tokens und wiederverwendbare UI-Bausteine (CustomTkinter).
Farben nach ui-ux-pro-max: Slate-Palette mit grünem Aktions-Akzent, hell + dunkel.
"""

import tkinter as tk

import customtkinter as ctk

from icons import ctk_icon

# ---- Farben (hell, dunkel) ----
BG = ("#F1F5F9", "#0F172A")
CARD = ("#FFFFFF", "#1B2336")
FIELD = ("#F8FAFC", "#272F42")
BORDER = ("#E2E8F0", "#2A3446")
BORDER_STRONG = ("#CBD5E1", "#475569")
FG = ("#0F172A", "#F8FAFC")
MUTED = ("#475569", "#94A3B8")
SECONDARY = ("#E2E8F0", "#334155")
SECONDARY_HOVER = ("#CBD5E1", "#475569")
ACCENT = ("#047857", "#22C55E")
ACCENT_HOVER = ("#065F46", "#16A34A")
ON_ACCENT = ("#FFFFFF", "#0F172A")
ACCENT_SOFT = ("#D1FAE5", "#14532D")      # aktiver Umschalter
PREVIEW_BG = ("#E2E8F0", "#020617")
OK_GREEN = ("#047857", "#22C55E")
WARN = ("#B45309", "#F59E0B")
ERR = ("#B91C1C", "#EF4444")

# ---- Schriften (werden in init_fonts je nach System gewählt) ----
FONT_UI = "Segoe UI"
FONT_DISPLAY = "Segoe UI"
FONT_MONO = "Consolas"


def pick_font(*names):
    import tkinter.font as tkfont
    fams = set(tkfont.families())
    return next((n for n in names if n in fams), names[-1])


def init_fonts():
    """Nach dem Erzeugen des Hauptfensters aufrufen."""
    global FONT_UI, FONT_DISPLAY, FONT_MONO
    FONT_UI = pick_font("Segoe UI Variable Text", "Segoe UI")
    FONT_DISPLAY = pick_font("Segoe UI Variable Display", "Segoe UI")
    FONT_MONO = pick_font("Cascadia Mono", "Consolas")
    ctk.ThemeManager.theme["CTkFont"]["family"] = FONT_UI


def mode_color(pair):
    return pair[1] if ctk.get_appearance_mode() == "Dark" else pair[0]


def F(size=13, weight="normal", family=None):
    return ctk.CTkFont(family=family or FONT_UI, size=size, weight=weight)


# ---- Bausteine ----
def focus_ring(widget):
    """Sichtbarer Fokus-Rahmen; berücksichtigt einen evtl. gesetzten Fehlerzustand."""
    def _in(_):
        widget.configure(border_color=ERR if getattr(widget, "_err", False) else ACCENT, border_width=2)

    def _out(_):
        err = getattr(widget, "_err", False)
        widget.configure(border_color=ERR if err else BORDER_STRONG, border_width=2 if err else 1)

    widget.bind("<FocusIn>", _in, add="+")
    widget.bind("<FocusOut>", _out, add="+")
    return widget


def set_error(widget, label, message):
    """Feld rot markieren und Hinweis darunter anzeigen (message=None -> zurücksetzen)."""
    widget._err = bool(message)
    widget.configure(border_color=ERR if message else BORDER_STRONG, border_width=2 if message else 1)
    if message:
        label.configure(text=message)
        label.pack(fill="x", pady=(4, 0), after=widget)
    else:
        label.pack_forget()


def error_label(master):
    return ctk.CTkLabel(master, text="", text_color=ERR, anchor="w", font=F(12), height=18)


def button(master, text, command, icon=None, kind="secondary", height=36, icon_size=18, **kw):
    styles = {
        "primary": dict(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ON_ACCENT),
        "secondary": dict(fg_color=SECONDARY, hover_color=SECONDARY_HOVER, text_color=FG),
        "ghost": dict(fg_color="transparent", hover_color=SECONDARY, text_color=FG,
                      border_width=1, border_color=BORDER_STRONG),
        "danger": dict(fg_color="transparent", hover_color=SECONDARY, text_color=ERR,
                       border_width=1, border_color=BORDER_STRONG),
    }
    icon_color = {"primary": ON_ACCENT, "danger": ERR}.get(kind, FG)
    img = ctk_icon(icon, icon_size, icon_color) if icon else None
    opts = dict(text=text, command=command, image=img, height=height, corner_radius=8,
                cursor="hand2", font=F(13, "bold" if kind == "primary" else "normal"))
    opts.update(styles[kind])
    opts.update(kw)
    return ctk.CTkButton(master, **opts)


def set_toggle(btn, active):
    """Umschalt-Button optisch als aktiv/inaktiv darstellen."""
    btn.configure(fg_color=ACCENT_SOFT if active else SECONDARY,
                  border_width=1 if active else 0, border_color=ACCENT)


def entry(master, var, **kw):
    e = ctk.CTkEntry(master, textvariable=var, height=36, corner_radius=8, fg_color=FIELD,
                     border_color=BORDER_STRONG, border_width=1, text_color=FG, font=F(13), **kw)
    return focus_ring(e)


def option(master, values, command, **kw):
    return ctk.CTkOptionMenu(master, values=values, command=command, height=36, corner_radius=8,
                             fg_color=SECONDARY, button_color=SECONDARY, button_hover_color=SECONDARY_HOVER,
                             text_color=FG, dropdown_fg_color=CARD, dropdown_hover_color=SECONDARY,
                             dropdown_text_color=FG, font=F(13), dropdown_font=F(13), cursor="hand2", **kw)


def switch(master, text, var):
    return ctk.CTkSwitch(master, text=text, variable=var, progress_color=ACCENT, fg_color=BORDER_STRONG,
                         button_color=("#FFFFFF", "#F8FAFC"), button_hover_color=("#F1F5F9", "#E2E8F0"),
                         text_color=FG, font=F(13), cursor="hand2")


def field_label(master, text, required=False):
    lbl = ctk.CTkLabel(master, text=text + ("  *" if required else ""), text_color=MUTED, anchor="w",
                       font=F(12, "bold"))
    lbl.pack(fill="x", pady=(10, 4))
    return lbl


def keycap(master, text):
    return ctk.CTkLabel(master, text=f" {text} ", fg_color=SECONDARY, corner_radius=6, height=24,
                        text_color=FG, font=F(12, family=FONT_MONO))


class Card(ctk.CTkFrame):
    """Karte mit Icon-Überschrift; rechts in .head können weitere Elemente ergänzt werden."""

    def __init__(self, master, title, icon):
        super().__init__(master, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDER)
        self.head = ctk.CTkFrame(self, fg_color="transparent")
        self.head.pack(fill="x", padx=16, pady=(14, 2))
        ctk.CTkLabel(self.head, text="", image=ctk_icon(icon, 18, MUTED), width=20).pack(side="left")
        ctk.CTkLabel(self.head, text=title, anchor="w", text_color=FG,
                     font=F(14, "bold")).pack(side="left", padx=(8, 0))
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="x", padx=16, pady=(0, 16))


def ask_form(master, title, fields, ok_text="OK", message=None):
    """
    Kleiner modaler Dialog mit Eingabefeldern.
    fields = [(Beschriftung, Vorgabewert), ...]  ->  Liste der Eingaben oder None (Abbruch)
    """
    prev_grab = master.grab_current()
    dlg = ctk.CTkToplevel(master)
    dlg.title(title)
    dlg.configure(fg_color=CARD)
    dlg.resizable(False, False)
    dlg.transient(master.winfo_toplevel())
    result = {"values": None}

    body = ctk.CTkFrame(dlg, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=24, pady=20)
    ctk.CTkLabel(body, text=title, text_color=FG, font=F(16, "bold"), anchor="w").pack(fill="x")
    if message:
        ctk.CTkLabel(body, text=message, text_color=MUTED, font=F(12), anchor="w", justify="left",
                     wraplength=380).pack(fill="x", pady=(6, 0))
    vars_, entries = [], []
    for label, default in fields:
        field_label(body, label)
        v = tk.StringVar(value=default)
        e = entry(body, v, width=380)
        e.pack(fill="x")
        vars_.append(v)
        entries.append(e)

    def ok(_=None):
        result["values"] = [v.get().strip() for v in vars_]
        dlg.destroy()

    def cancel(_=None):
        dlg.destroy()

    row = ctk.CTkFrame(body, fg_color="transparent")
    row.pack(fill="x", pady=(18, 0))
    button(row, ok_text, ok, kind="primary", width=120).pack(side="right")
    button(row, "Abbrechen", cancel, kind="ghost", width=110).pack(side="right", padx=(0, 8))
    dlg.bind("<Return>", ok)
    dlg.bind("<Escape>", cancel)
    dlg.protocol("WM_DELETE_WINDOW", cancel)

    dlg.update_idletasks()
    top = master.winfo_toplevel()
    x = top.winfo_rootx() + (top.winfo_width() - dlg.winfo_reqwidth()) // 2
    y = top.winfo_rooty() + (top.winfo_height() - dlg.winfo_reqheight()) // 3
    dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    dlg.after(80, lambda: (dlg.lift(), dlg.focus_force(), entries[0].focus_set() if entries else None,
                           entries[0].select_range(0, "end") if entries else None))
    dlg.grab_set()
    master.wait_window(dlg)
    if prev_grab is not None:
        try:
            prev_grab.grab_set()
        except tk.TclError:
            pass
    return result["values"]

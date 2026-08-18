"""Zugangsschutz und Geheimnisverwaltung.

Geheimnisse kommen wahlweise aus Umgebungsvariablen (so setzt Hugging Face
Spaces seine Secrets) oder aus `.streamlit/secrets.toml` (lokal). Ist kein
Passwort hinterlegt, läuft die App ungeschützt – das ist für localhost
gewollt, wird online aber deutlich angemahnt.
"""

from __future__ import annotations

import hmac
import os

import streamlit as st

PASSWORT_SCHLUESSEL = "APP_PASSWORT"
TOKEN_SCHLUESSEL = "REPLICATE_API_TOKEN"


def geheimnis(name: str) -> str | None:
    """Liest ein Geheimnis: erst Umgebung, dann secrets.toml."""
    aus_umgebung = os.environ.get(name, "").strip()
    if aus_umgebung:
        return aus_umgebung
    try:
        aus_datei = st.secrets.get(name) or st.secrets.get(name.lower())
    except Exception:
        return None
    aus_datei = str(aus_datei).strip() if aus_datei else ""
    return aus_datei or None


def passwortschutz_aktiv() -> bool:
    return geheimnis(PASSWORT_SCHLUESSEL) is not None


def angemeldet() -> bool:
    """Zeigt bei Bedarf die Anmeldemaske. True = Zugriff erlaubt."""
    erwartet = geheimnis(PASSWORT_SCHLUESSEL)
    if erwartet is None:
        return True
    if st.session_state.get("_angemeldet"):
        return True

    _anmeldemaske(erwartet)
    return bool(st.session_state.get("_angemeldet"))


def _anmeldemaske(erwartet: str) -> None:
    links, mitte, rechts = st.columns([1, 2, 1])
    with mitte:
        st.title("🔥 Kachelofen-Konfigurator")
        st.caption("Interner Zugang – bitte Team-Passwort eingeben.")
        with st.form("anmeldung"):
            eingabe = st.text_input("Passwort", type="password")
            gesendet = st.form_submit_button("Anmelden",
                                             use_container_width=True)
        if gesendet:
            if hmac.compare_digest(eingabe.strip(), erwartet):
                st.session_state["_angemeldet"] = True
                st.rerun()
            else:
                st.error("Passwort stimmt nicht.")


def abmelden_knopf() -> None:
    if passwortschutz_aktiv() and st.session_state.get("_angemeldet"):
        if st.sidebar.button("Abmelden", use_container_width=True):
            st.session_state["_angemeldet"] = False
            st.rerun()


def zentraler_token() -> str | None:
    """Der serverseitig hinterlegte Replicate-Token, falls vorhanden."""
    return geheimnis(TOKEN_SCHLUESSEL)


def laeuft_online() -> bool:
    """Grobe Erkennung einer gehosteten Umgebung (für Warnhinweise)."""
    return any(os.environ.get(v) for v in
               ("SPACE_ID", "SPACE_HOST", "HOSTNAME_STREAMLIT",
                "STREAMLIT_SERVER_HEADLESS_CLOUD", "DYNO", "RENDER"))

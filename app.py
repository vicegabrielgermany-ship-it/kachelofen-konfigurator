"""Modul 4 – Streamlit-Oberfläche des Kachelofen-Konfigurators.

Start:  streamlit run app.py
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
from PIL import Image

from assets import (
    AssetBibliothek, als_png_bytes, auf_alpha_zuschneiden, drehen,
    exif_korrigiert_laden, freistellen, platzhalter_fixture,
    platzhalter_kachel, rembg_verfuegbar,
)
from config import Fixture, OfenKonfiguration, Sektion
from depthmap import (
    SzenenProfil, TiefenProfil, rendere_depthmap, rendere_kantenkarte,
    rendere_maske, rendere_szenen_depthmap,
)
import vorlage as vl
from geometry import baue_geometrie, stueckliste
from layout import auf_hintergrund, rendere_composite
import auth, kontingent
import render as ki

st.set_page_config(page_title="Kachelofen-Konfigurator",
                   page_icon="🔥", layout="wide")

KACHEL_SLOTS = {
    "flaeche": "Flächenkachel (Pflicht)",
    "halb": "Passkachel / Halbkachel (optional)",
    "sockel": "Sockelkachel (optional)",
    "gesims": "Gesims- / Kronenkachel (optional)",
    "ecke": "Eckkachel (optional)",
}


# --------------------------------------------------------------------------
# Session-State
# --------------------------------------------------------------------------

def zustand() -> None:
    if "konfiguration" not in st.session_state:
        st.session_state.konfiguration = OfenKonfiguration()
    if "bibliothek" not in st.session_state:
        st.session_state.bibliothek = AssetBibliothek()
    if "renderergebnisse" not in st.session_state:
        st.session_state.renderergebnisse = []
    if "vorlagenfoto" not in st.session_state:
        st.session_state.vorlagenfoto = None
    if "vorlagenname" not in st.session_state:
        st.session_state.vorlagenname = ""
    if "befund" not in st.session_state:
        st.session_state.befund = None


@st.cache_data(show_spinner=False, max_entries=32)
def _aufbereiten(rohdaten: bytes, entfernen: bool, winkel: float,
                 zuschneiden: bool) -> bytes:
    bild = exif_korrigiert_laden(io.BytesIO(rohdaten))
    if entfernen:
        bild = freistellen(bild)
    if winkel:
        bild = drehen(bild, winkel)
    if zuschneiden:
        bild = auf_alpha_zuschneiden(bild)
    return als_png_bytes(bild)


def aufbereiten(datei, entfernen: bool, winkel: float,
                zuschneiden: bool) -> Image.Image:
    return Image.open(io.BytesIO(
        _aufbereiten(datei.getvalue(), entfernen, winkel, zuschneiden)
    )).convert("RGBA")


# --------------------------------------------------------------------------
# Seitenleiste – Grundmaße
# --------------------------------------------------------------------------

def seitenleiste() -> None:
    k: OfenKonfiguration = st.session_state.konfiguration
    with st.sidebar:
        st.header("Grundmaße")
        k.name = st.text_input("Projektname", k.name)
        k.kachelbreite_mm = st.number_input(
            "Kachelbreite (mm)", 60.0, 600.0, float(k.kachelbreite_mm), 5.0,
            help="Gemessene Breite einer Flächenkachel inkl. halber Fuge.")
        k.spalten = st.slider("Anzahl Spalten", 1, 12, int(k.spalten),
                              help="Volle Kacheln je Zeile in der Ofenfront.")
        k.fuge_mm = st.slider("Fugenbreite (mm)", 0.0, 12.0,
                              float(k.fuge_mm), 0.5)
        k.fugenfarbe = st.color_picker("Fugenfarbe", k.fugenfarbe)
        k.px_pro_mm = st.slider("Auflösung (Pixel je mm)", 0.4, 3.0,
                                float(k.px_pro_mm), 0.1)

        st.divider()
        st.header("Ofenmaß")
        st.metric("Breite", f"{k.max_breite_mm():.0f} mm")
        st.metric("Höhe", f"{k.hoehe_mm:.0f} mm")
        st.caption(f"{sum(s.zeilen for s in k.sektionen)} Kachelreihen · "
                   "nur volle Zeilen, kein vertikaler Zuschnitt")

        st.divider()
        st.header("Projekt")
        st.download_button("Konfiguration speichern (.json)",
                           k.to_json().encode("utf-8"),
                           file_name=f"{_dateiname(k.name)}.json",
                           mime="application/json", width='stretch')
        hochgeladen = st.file_uploader("Konfiguration laden", type=["json"],
                                       key="konfig_upload")
        if hochgeladen is not None and st.button("Laden", width='stretch'):
            try:
                st.session_state.konfiguration = OfenKonfiguration.from_dict(
                    json.load(hochgeladen))
                st.success("Konfiguration übernommen.")
                st.rerun()
            except Exception as fehler:
                st.error(f"Datei konnte nicht gelesen werden: {fehler}")

        if auth.laeuft_online() and not auth.passwortschutz_aktiv():
            st.divider()
            st.error("Diese Instanz ist öffentlich erreichbar und **nicht** "
                     "passwortgeschützt. Bitte das Secret `APP_PASSWORT` "
                     "setzen.")
    auth.abmelden_knopf()


def _dateiname(name: str) -> str:
    erlaubt = "".join(c if c.isalnum() or c in "-_ " else "-" for c in name)
    return erlaubt.strip().replace(" ", "-").lower() or "kachelofen"



# --------------------------------------------------------------------------
# Reiter 0 – Vorlage aus Foto
# --------------------------------------------------------------------------

BLOCKNAMEN = ["Sockel", "Korpus", "Gesims", "Aufsatz", "Mittelteil"]


@st.cache_data(show_spinner=False, max_entries=8)
def _foto_laden(rohdaten: bytes) -> bytes:
    bild = exif_korrigiert_laden(io.BytesIO(rohdaten))
    if max(bild.size) > 2000:
        faktor = 2000 / max(bild.size)
        bild = bild.resize((int(bild.width * faktor), int(bild.height * faktor)),
                           Image.LANCZOS)
    return als_png_bytes(bild)


def _auswahl_bauen(x: float, y: float, breite: float, hoehe: float,
                   keystone_h: float, keystone_v: float,
                   drehung: float) -> vl.Auswahl:
    """Rechteck plus zwei Entzerrungsregler plus Drehung."""
    x1, y1 = x + breite, y + hoehe
    kh, kv = keystone_h * breite, keystone_v * hoehe
    ecken = [
        (x + kh, y + kv),
        (x1 - kh, y - kv),
        (x1 + kh, y1 - kv),
        (x - kh, y1 + kv),
    ]
    if abs(drehung) > 1e-6:
        import math
        mx, my = x + breite / 2, y + hoehe / 2
        bogen = math.radians(drehung)
        cos_w, sin_w = math.cos(bogen), math.sin(bogen)
        ecken = [
            (mx + (px - mx) * cos_w - (py - my) * sin_w,
             my + (px - mx) * sin_w + (py - my) * cos_w)
            for px, py in ecken
        ]
    return vl.Auswahl([(float(np.clip(px, -0.2, 1.2)),
                        float(np.clip(py, -0.2, 1.2))) for px, py in ecken])


def reiter_vorlage() -> None:
    k: OfenKonfiguration = st.session_state.konfiguration
    bibliothek: AssetBibliothek = st.session_state.bibliothek

    st.subheader("Foto des ausgelegten Ofens")
    st.caption("Das Foto, auf dem die Kacheln flach nebeneinander liegen. "
               "Daraus werden Reihen, Spalten, Verband und die Kachelbilder "
               "übernommen – Block für Block.")

    hochgeladen = st.file_uploader(
        "Auslege-Foto", type=["png", "jpg", "jpeg", "webp", "heic"],
        key="vorlage_upload")
    if hochgeladen is not None:
        st.session_state.vorlagenfoto = _foto_laden(hochgeladen.getvalue())
        st.session_state.vorlagenname = hochgeladen.name

    if not st.session_state.vorlagenfoto:
        st.info("Noch kein Foto geladen. Ohne Foto lässt sich der Ofen auch "
                "im Reiter „Aufbau“ von Hand zusammenstellen.")
        return

    foto = Image.open(io.BytesIO(st.session_state.vorlagenfoto)).convert("RGB")

    st.divider()
    st.subheader("Block auswählen")
    st.caption("Ein Block ist ein zusammenhängendes Kachelfeld – etwa der "
               "Korpus oder der Aufsatz. Rahmen darüberlegen, dann die "
               "Reihen zählen.")

    r1, r2 = st.columns(2)
    x = r1.slider("Linke Kante", 0.0, 0.95, 0.10, 0.01, key="v_x")
    breite = r1.slider("Breite", 0.05, 1.0, 0.80, 0.01, key="v_b")
    y = r2.slider("Obere Kante", 0.0, 0.95, 0.10, 0.01, key="v_y")
    hoehe = r2.slider("Höhe", 0.05, 1.0, 0.35, 0.01, key="v_h")

    with st.expander("Schräg fotografiert? Hier geradeziehen"):
        e1, e2, e3 = st.columns(3)
        keystone_h = e1.slider("Oben schmaler / breiter", -0.25, 0.25, 0.0,
                               0.005, key="v_kh")
        keystone_v = e2.slider("Links höher / tiefer", -0.25, 0.25, 0.0,
                               0.005, key="v_kv")
        drehung = e3.slider("Drehung (°)", -15.0, 15.0, 0.0, 0.5, key="v_dreh")

    auswahl = _auswahl_bauen(x, y, breite, hoehe, keystone_h, keystone_v,
                             drehung)

    try:
        entzerrt = vl.ausschnitt(foto, auswahl)
    except ValueError as fehler:
        st.error(str(fehler))
        return

    st.divider()
    b1, b2, b3 = st.columns(3)
    name = b1.selectbox("Blockname", BLOCKNAMEN, index=1, key="v_name")
    zeilen = b2.number_input("Reihen (bitte zählen)", 1, 30, 4, 1, key="v_zeilen",
                             help="Die Reihenzahl ist mit bloßem Auge in "
                                  "Sekunden gezählt und sicherer als jede "
                                  "automatische Erkennung.")
    automatik = b3.checkbox("Spalten automatisch erkennen", True, key="v_auto")
    spalten_manuell = None
    if not automatik:
        spalten_manuell = b3.number_input("Spalten", 1, 20, 6, 1, key="v_spalten")

    befund = vl.block_auswerten(
        entzerrt, name, zeilen=int(zeilen),
        spalten=int(spalten_manuell) if spalten_manuell else None)
    versatz = st.checkbox(
        "Halbverband (Reihen um eine halbe Kachel versetzt)",
        value=befund.versatz, key="v_versatz",
        help="Automatisch vorgeschlagen. Bei Jugendstilöfen liegen die "
             "Kacheln oft Fuge auf Fuge – dann diesen Haken entfernen.")
    befund.versatz = versatz
    st.session_state.befund = befund

    v1, v2 = st.columns(2)
    v1.image(vl.markiere(foto, auswahl), caption="Auswahl im Foto",
             width='stretch')
    v2.image(vl.raster_zeichnen(entzerrt, befund.spalten, befund.zeilen,
                                befund.versatz),
             caption="Entzerrt mit vermutetem Raster", width='stretch')
    st.caption(befund.hinweis())

    st.divider()
    st.subheader("Übernehmen")

    u1, u2 = st.columns(2)
    with u1:
        st.markdown("**Sektion in den Aufbau übernehmen**")
        kachelbreite = st.number_input(
            "Gemessene Kachelbreite (mm)", 40.0, 600.0,
            float(k.kachelbreite_mm), 5.0, key="v_kb",
            help="Einmal am echten Stück nachmessen – daraus ergibt sich "
                 "der gesamte Maßstab des Ofens.")
        tiefe = st.number_input("Vorsprung gegenüber der Front (mm)",
                                -60.0, 150.0, 0.0, 2.0, key="v_tiefe")
        zeilenhoehe = befund.zeilenhoehe_mm(kachelbreite)
        st.caption(f"Daraus folgt eine Zeilenhöhe von **{zeilenhoehe:.0f} mm** "
                   f"und eine Blockbreite von "
                   f"**{befund.spalten * kachelbreite:.0f} mm**.")
        if st.button("Als Sektion anlegen", type="primary",
                     width='stretch'):
            k.kachelbreite_mm = kachelbreite
            if not k.sektionen or all(s.name != befund.name for s in k.sektionen):
                k.spalten = max(k.spalten, befund.spalten)
            k.sektionen.append(befund.sektion(
                kachelbreite_mm=kachelbreite,
                kachel_key=_freier_kachelschluessel(bibliothek, befund.name),
                global_spalten=k.spalten, tiefe_mm=tiefe))
            st.success(f"Sektion „{befund.name}“ angelegt.")

    with u2:
        st.markdown("**Bilder aus dem Foto übernehmen**")
        z1, z2 = st.columns(2)
        spalte = z1.number_input("Spalte", 0, max(befund.spalten - 1, 0), 0, 1,
                                 key="v_zell_s")
        zeile = z2.number_input("Reihe von unten", 0, max(befund.zeilen - 1, 0),
                                0, 1, key="v_zell_z")
        try:
            zelle = vl.zelle_ausschneiden(entzerrt, befund.spalten, befund.zeilen,
                                          int(spalte), int(zeile), befund.versatz)
            st.image(zelle, width=180, caption="Diese Kachel")
        except ValueError as fehler:
            st.warning(str(fehler))
            zelle = None

        ziel = st.selectbox("Als welchen Kacheltyp?", list(KACHEL_SLOTS.keys()),
                            key="v_ziel")
        if zelle is not None and st.button("Kachel übernehmen",
                                           width='stretch'):
            bibliothek.setze_kachel(ziel, zelle, st.session_state.vorlagenname)
            st.success(f"Kachel als „{ziel}“ gespeichert.")

        st.markdown("---")
        st.caption("Türen und Gitter liegen meist außerhalb des Rasters – "
                   "dafür den Rahmen oben direkt um das Bauteil legen.")
        fixture_name = st.text_input("Bezeichnung", "Schürtür", key="v_fx_name")
        if st.button("Auswahl als Eisenware übernehmen",
                     width='stretch'):
            schluessel = _dateiname(fixture_name) or "eisenware"
            bibliothek.setze_fixture(schluessel, entzerrt,
                                     st.session_state.vorlagenname)
            st.success(f"„{fixture_name}“ als Eisenware gespeichert – im "
                       "Reiter „Aufbau“ positionieren.")

    if k.sektionen:
        st.divider()
        st.caption("Bereits angelegt: "
                   + " · ".join(f"{s.name} ({s.zeilen}×"
                                f"{s.wirksame_spalten(k.spalten)})"
                                for s in k.sektionen))


def _freier_kachelschluessel(bibliothek: AssetBibliothek, blockname: str) -> str:
    """Wählt einen sinnvollen Kacheltyp für einen erkannten Block."""
    zuordnung = {"Sockel": "sockel", "Gesims": "gesims", "Aufsatz": "flaeche",
                 "Korpus": "flaeche", "Mittelteil": "flaeche"}
    schluessel = zuordnung.get(blockname, "flaeche")
    return schluessel if schluessel in bibliothek.kacheln else "flaeche"


# --------------------------------------------------------------------------
# Reiter 1 – Assets
# --------------------------------------------------------------------------

def reiter_assets() -> None:
    bibliothek: AssetBibliothek = st.session_state.bibliothek

    if not rembg_verfuegbar():
        st.info("Automatisches Freistellen ist nicht installiert. "
                "Mit `pip install rembg onnxruntime` nachrüsten – bis dahin "
                "bitte bereits freigestellte PNGs hochladen.")

    st.subheader("Kacheltypen")
    st.caption("Ein möglichst frontales Foto je Typ. Die Passkachel wird "
               "automatisch aus der Flächenkachel halbiert, wenn kein "
               "eigenes Foto vorliegt.")

    for key, beschriftung in KACHEL_SLOTS.items():
        with st.expander(beschriftung,
                         expanded=(key == "flaeche" and key not in bibliothek.kacheln)):
            _asset_block(bibliothek, key, fixture=False)

    st.divider()
    st.subheader("Eisenwaren")
    st.caption("Schürtür, Aschetür, Wärmefach, Lüftungsgitter … "
               "Der Dateiname wird zum Kennzeichen im Aufbau-Reiter.")

    dateien = st.file_uploader(
        "Eisenwaren hochladen", type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True, key="fixture_upload")
    spalte_a, spalte_b = st.columns(2)
    entfernen = spalte_a.checkbox("Hintergrund entfernen", True,
                                  key="fx_rembg", disabled=not rembg_verfuegbar())
    zuschneiden = spalte_b.checkbox("Auf Motiv zuschneiden", True, key="fx_crop")

    for datei in dateien or []:
        key = Path(datei.name).stem.lower().replace(" ", "-")
        bild = aufbereiten(datei, entfernen, 0.0, zuschneiden)
        bibliothek.setze_fixture(key, bild, datei.name)

    if bibliothek.fixtures:
        spalten = st.columns(min(len(bibliothek.fixtures), 5))
        for spalte, (key, asset) in zip(spalten * 5, bibliothek.fixtures.items()):
            spalte.image(asset.bild, caption=key, width='stretch')
        if st.button("Alle Eisenwaren entfernen"):
            bibliothek.fixtures.clear()
            st.rerun()


def _asset_block(bibliothek: AssetBibliothek, key: str, fixture: bool) -> None:
    datei = st.file_uploader("Foto", type=["png", "jpg", "jpeg", "webp"],
                             key=f"up_{key}", label_visibility="collapsed")
    s1, s2, s3 = st.columns([1, 1, 2])
    entfernen = s1.checkbox("Freistellen", True, key=f"rb_{key}",
                            disabled=not rembg_verfuegbar())
    zuschneiden = s2.checkbox("Zuschneiden", True, key=f"cr_{key}")
    winkel = s3.slider("Drehung (°)", -10.0, 10.0, 0.0, 0.1, key=f"rot_{key}")

    if datei is not None:
        bild = aufbereiten(datei, entfernen, winkel, zuschneiden)
        bibliothek.setze_kachel(key, bild, datei.name)

    asset = bibliothek.kacheln.get(key)
    if asset is not None:
        vorschau, hinweis = st.columns([1, 2])
        vorschau.image(asset.bild, width='stretch')
        hinweis.write(f"**{asset.bild.width} × {asset.bild.height} px**  \n"
                      f"Seitenverhältnis {asset.seitenverhaeltnis:.2f}  \n"
                      f"Quelle: {asset.quelle or '–'}")
        hinweis.download_button("PNG herunterladen", als_png_bytes(asset.bild),
                                file_name=f"kachel-{key}.png", mime="image/png",
                                key=f"dl_{key}")
        if hinweis.button("Entfernen", key=f"rm_{key}"):
            bibliothek.kacheln.pop(key, None)
            st.rerun()
    elif key == "flaeche":
        st.caption("Ohne Foto wird eine synthetische Musterkachel verwendet.")


# --------------------------------------------------------------------------
# Reiter 2 – Aufbau
# --------------------------------------------------------------------------

def reiter_aufbau() -> None:
    k: OfenKonfiguration = st.session_state.konfiguration
    bibliothek: AssetBibliothek = st.session_state.bibliothek

    st.subheader("Sektionen")
    st.caption("Von unten nach oben. Jede Sektion besteht aus vollen "
               "Kachelreihen – halbe Zeilen gibt es handwerklich nicht.")

    tabelle = pd.DataFrame([{
        "Sektion": s.name,
        "Zeilen": s.zeilen,
        "Zeilenhöhe (mm)": s.zeilenhoehe_mm,
        "Kacheltyp": s.kachel_key,
        "Halbverband": s.versatz,
        "Spalten (0 = global)": s.spalten or 0,
        "Vorsprung (mm)": s.tiefe_mm,
    } for s in k.sektionen])
    bearbeitet = st.data_editor(
        tabelle, num_rows="dynamic", width='stretch', key="sektionen",
        column_config={
            "Zeilen": st.column_config.NumberColumn(min_value=1, max_value=40, step=1),
            "Zeilenhöhe (mm)": st.column_config.NumberColumn(
                min_value=40.0, max_value=600.0, step=5.0),
            "Kacheltyp": st.column_config.SelectboxColumn(
                options=list(KACHEL_SLOTS.keys())),
            "Halbverband": st.column_config.CheckboxColumn(
                help="50 % Fugenversatz; an den Rändern automatisch Passkacheln."),
            "Spalten (0 = global)": st.column_config.NumberColumn(
                min_value=0, max_value=16, step=1,
                help="0 übernimmt die globale Spaltenzahl aus der "
                     "Seitenleiste. Höhere Werte für auskragende Gesimse "
                     "oder breitere Sockel."),
            "Vorsprung (mm)": st.column_config.NumberColumn(
                min_value=-60.0, max_value=150.0, step=2.0,
                help="Wirkt nur auf die Tiefenkarte."),
        },
    )
    k.sektionen = _sektionen_aus_tabelle(bearbeitet, k.sektionen)

    st.divider()
    st.subheader("Eisenwaren im Raster")
    st.caption("Position in Kachelbreiten ab der linken Ofenkante bzw. in "
               "Zeilen ab der Unterkante der Sektion. 0,5 setzt das Element "
               "auf den Fugenversatz.")

    if not k.sektionen:
        st.warning("Erst eine Sektion anlegen.")
        return

    schluessel = sorted(bibliothek.fixtures.keys()) or ["platzhalter"]
    namen = [s.name for s in k.sektionen]

    fx_tabelle = pd.DataFrame([{
        "Bezeichnung": f.name,
        "Bild": f.asset_key,
        "Sektion": f.sektion if f.sektion in namen else namen[0],
        "Spalte": f.spalte,
        "Zeile": f.zeile,
        "Breite (Kacheln)": f.breite_kacheln,
        "Höhe (Zeilen)": f.hoehe_zeilen,
        "Tiefe (mm)": f.tiefe_mm,
        "Randfuge (mm)": f.randfuge_mm,
    } for f in k.fixtures])
    if fx_tabelle.empty:
        fx_tabelle = pd.DataFrame(columns=[
            "Bezeichnung", "Bild", "Sektion", "Spalte", "Zeile",
            "Breite (Kacheln)", "Höhe (Zeilen)", "Tiefe (mm)", "Randfuge (mm)"])

    fx_bearbeitet = st.data_editor(
        fx_tabelle, num_rows="dynamic", width='stretch', key="fixtures",
        column_config={
            "Bild": st.column_config.SelectboxColumn(options=schluessel),
            "Sektion": st.column_config.SelectboxColumn(options=namen),
            "Spalte": st.column_config.NumberColumn(
                min_value=0.0, max_value=16.0, step=0.5),
            "Zeile": st.column_config.NumberColumn(
                min_value=0.0, max_value=40.0, step=1.0),
            "Breite (Kacheln)": st.column_config.NumberColumn(
                min_value=0.5, max_value=16.0, step=0.5),
            "Höhe (Zeilen)": st.column_config.NumberColumn(
                min_value=0.5, max_value=20.0, step=0.5),
            "Tiefe (mm)": st.column_config.NumberColumn(
                min_value=-120.0, max_value=80.0, step=2.0),
            "Randfuge (mm)": st.column_config.NumberColumn(
                min_value=0.0, max_value=30.0, step=1.0),
        },
    )
    k.fixtures = _fixtures_aus_tabelle(fx_bearbeitet, namen, schluessel[0])

    st.caption("Schnelleinbau – die Elemente werden mittig und übereinander "
               "gesetzt und lassen sich danach in der Tabelle verschieben.")
    s1, s2, s3 = st.columns(3)
    if s1.button("Schürtür einsetzen", width='stretch'):
        _standard_fixture(k, "Schürtür", schluessel[0], 2.0, 2.0, -28.0)
        st.rerun()
    if s2.button("Aschetür einsetzen", width='stretch'):
        _standard_fixture(k, "Aschetür", schluessel[0], 1.0, 1.0, -20.0,
                          unterste_sektion=True)
        st.rerun()
    if s3.button("Wärmefach einsetzen", width='stretch'):
        _standard_fixture(k, "Wärmefach", schluessel[0], 2.0, 1.0, -35.0)
        st.rerun()


def _sektionen_aus_tabelle(tabelle: pd.DataFrame,
                           vorher: list[Sektion]) -> list[Sektion]:
    sektionen: list[Sektion] = []
    for i, zeile in tabelle.iterrows():
        name = str(zeile.get("Sektion") or f"Sektion {i + 1}").strip()
        if not name:
            continue
        spalten = zeile.get("Spalten (0 = global)")
        sektionen.append(Sektion(
            name=name,
            zeilen=max(1, int(zeile.get("Zeilen") or 1)),
            zeilenhoehe_mm=float(zeile.get("Zeilenhöhe (mm)") or 220.0),
            kachel_key=str(zeile.get("Kacheltyp") or "flaeche"),
            versatz=bool(zeile.get("Halbverband", True)),
            spalten=None if pd.isna(spalten) or int(spalten) < 1
                    else int(spalten),
            tiefe_mm=float(zeile.get("Vorsprung (mm)") or 0.0),
        ))
    return sektionen or vorher


def _fixtures_aus_tabelle(tabelle: pd.DataFrame, sektionen: list[str],
                          ersatzbild: str) -> list[Fixture]:
    fixtures: list[Fixture] = []
    for i, zeile in tabelle.iterrows():
        name = str(zeile.get("Bezeichnung") or "").strip()
        if not name:
            continue
        sektion = str(zeile.get("Sektion") or sektionen[0])
        fixtures.append(Fixture(
            name=name,
            asset_key=str(zeile.get("Bild") or ersatzbild),
            sektion=sektion if sektion in sektionen else sektionen[0],
            spalte=float(zeile.get("Spalte") or 0.0),
            zeile=float(zeile.get("Zeile") or 0.0),
            breite_kacheln=float(zeile.get("Breite (Kacheln)") or 1.0),
            hoehe_zeilen=float(zeile.get("Höhe (Zeilen)") or 1.0),
            tiefe_mm=float(zeile.get("Tiefe (mm)") or -25.0),
            randfuge_mm=float(zeile.get("Randfuge (mm)") or 3.0),
        ))
    return fixtures


def _standard_fixture(k: OfenKonfiguration, name: str, bild: str,
                      breite: float, hoehe: float, tiefe: float,
                      unterste_sektion: bool = False) -> None:
    """Setzt ein Element mittig und oberhalb der bereits belegten Zeilen."""
    index = 0 if unterste_sektion else min(1, len(k.sektionen) - 1)
    sektion = k.sektionen[index]
    spalten = sektion.wirksame_spalten(k.spalten)
    spalte = max(0.0, (spalten - breite) / 2.0)

    belegt = [f.zeile + f.hoehe_zeilen
              for f in k.fixtures if f.sektion == sektion.name]
    zeile = max(belegt) if belegt else 0.0
    if zeile + hoehe > sektion.zeilen:
        zeile = 0.0

    k.fixtures.append(Fixture(
        name=_eindeutig(name, {f.name for f in k.fixtures}),
        asset_key=bild, sektion=sektion.name,
        spalte=round(spalte * 2) / 2, zeile=zeile,
        breite_kacheln=breite, hoehe_zeilen=hoehe, tiefe_mm=tiefe,
    ))


def _eindeutig(name: str, vergeben: set[str]) -> str:
    if name not in vergeben:
        return name
    i = 2
    while f"{name} {i}" in vergeben:
        i += 1
    return f"{name} {i}"


# --------------------------------------------------------------------------
# Reiter 3 – Vorschau und Export
# --------------------------------------------------------------------------

def reiter_vorschau() -> None:
    k: OfenKonfiguration = st.session_state.konfiguration
    bibliothek: AssetBibliothek = st.session_state.bibliothek

    if not k.sektionen:
        st.warning("Der Ofen hat noch keine Sektion.")
        return

    profil, szene = _tiefenprofil_regler()
    geo = baue_geometrie(k)
    for warnung in geo.warnungen():
        st.warning(warnung)

    composite = rendere_composite(k, bibliothek, geo)
    depth = rendere_szenen_depthmap(k, geo, profil=profil, szene=szene)
    kanten = rendere_kantenkarte(k, geo)
    maske = rendere_maske(k, geo)
    st.session_state["letzte_depthmap"] = depth
    st.session_state["letzte_szene"] = szene

    s1, s2, s3 = st.columns(3)
    s1.image(auf_hintergrund(composite), caption="2D-Composite",
             width='stretch')
    s2.image(depth, caption="Tiefenkarte mit Wand und Boden (hell = nah)",
             width='stretch')
    s3.image(kanten, caption="Fugen- und Kantenkarte", width='stretch')

    st.divider()
    links, rechts = st.columns([2, 1])

    with links:
        st.subheader("Downloads")
        d1, d2, d3, d4 = st.columns(4)
        basis = _dateiname(k.name)
        d1.download_button("Composite (PNG)", als_png_bytes(composite),
                           f"{basis}-composite.png", "image/png",
                           width='stretch')
        d2.download_button("Tiefenkarte (PNG)", als_png_bytes(depth),
                           f"{basis}-depth.png", "image/png",
                           width='stretch')
        d3.download_button("Kantenkarte (PNG)", als_png_bytes(kanten),
                           f"{basis}-kanten.png", "image/png",
                           width='stretch')
        d4.download_button("Maske (PNG)", als_png_bytes(maske),
                           f"{basis}-maske.png", "image/png",
                           width='stretch')

    with rechts:
        st.subheader("Materialbedarf")
        liste = stueckliste(geo)
        st.dataframe(
            pd.DataFrame({"Position": list(liste.keys()),
                          "Stück": list(liste.values())}),
            hide_index=True, width='stretch')
        st.caption(f"{len(geo.zellen)} Rasterplätze gesamt · "
                   f"{len(geo.boxen)} Eisenwaren · "
                   f"{geo.breite_mm:.0f} × {geo.hoehe_mm:.0f} mm")


def _tiefenprofil_regler() -> tuple[TiefenProfil, SzenenProfil]:
    with st.expander("Tiefenkarte und Szene feinjustieren"):
        st.caption("Die Tiefenkarte steuert das KI-Rendering. Wand und Boden "
                   "gehören mit hinein – ohne sie steht der Ofen für die "
                   "Bild-KI im Nichts und wirft keinen Schatten.")
        s1, s2, s3 = st.columns(3)
        woelbung = s1.slider("Kachelwölbung (mm)", 0.0, 25.0, 7.0, 0.5)
        fugentiefe = s2.slider("Fugentiefe (mm)", 0.0, 20.0, 5.0, 0.5)
        weich = s3.slider("Weichzeichnen (px)", 0.0, 6.0, 1.2, 0.2)

        st.markdown("---")
        t1, t2, t3 = st.columns(3)
        mit_szene = t1.checkbox("Wand und Boden", True,
                                help="Abschalten liefert den freigestellten "
                                     "Ofen auf Schwarz.")
        blickwinkel = t2.slider(
            "Dreiviertelansicht", 0.0, 0.5, 0.0, 0.05,
            help="0 = streng frontal. Höhere Werte deuten die rechte "
                 "Seitenwange an; die Front bleibt maßhaltig.")
        wandabstand = t3.slider("Wandabstand (mm)", 100, 900, 350, 25)
    return (TiefenProfil(kachel_woelbung_mm=woelbung,
                         fugen_tiefe_mm=fugentiefe,
                         weichzeichnen_px=weich),
            SzenenProfil(aktiv=mit_szene, blickwinkel=blickwinkel,
                         wand_abstand_mm=float(wandabstand)))


# --------------------------------------------------------------------------
# Reiter 4 – KI-Rendering
# --------------------------------------------------------------------------

def reiter_ki() -> None:
    k: OfenKonfiguration = st.session_state.konfiguration
    depth: Image.Image | None = st.session_state.get("letzte_depthmap")

    if depth is None:
        st.info("Bitte zuerst den Reiter „Vorschau & Export“ öffnen – dort "
                "entsteht die Tiefenkarte, die das Rendering steuert.")
        return

    links, rechts = st.columns([1, 2])

    with links:
        st.image(depth, caption="Steuerbild", width='stretch')
        preset_key = st.selectbox(
            "Modell", list(ki.PRESETS.keys()),
            format_func=lambda x: ki.PRESETS[x].beschriftung)
        st.caption(ki.PRESETS[preset_key].hinweis)
        zentral = auth.zentraler_token()
        if zentral:
            token = None
            st.caption("Zugang zum Bildmodell ist hinterlegt – "
                       "es muss kein Token eingetragen werden.")
        else:
            token = st.text_input(
                "Replicate-Token", type="password",
                placeholder="r8_… (oder REPLICATE_API_TOKEN setzen)")

        rest = kontingent.verbleibend() if zentral else None
        if rest is not None:
            st.progress(min(rest / max(kontingent.limit(), 1), 1.0),
                        text=f"Heute noch {rest} von {kontingent.limit()} "
                             "Renderings frei")

        seed_setzen = st.checkbox("Seed festlegen (reproduzierbar)")
        seed = st.number_input("Seed", 0, 2**31 - 1, 42, 1) if seed_setzen else None

    with rechts:
        kachelbeschreibung = st.text_input(
            "Kachelbeschreibung",
            "smaragdgrün glasierte Reliefkacheln mit feiner Craquelé-Struktur",
            help="Farbe, Glasur, Dekor – das ist der wichtigste Hebel für "
                 "die Materialtreue.")
        raum = st.selectbox("Hintergrund", list(ki.RAUM_BAUSTEINE.keys()))

        l1, l2 = st.columns(2)
        lichtrichtung = l1.selectbox("Lichteinfall",
                                     list(ki.LICHT_RICHTUNGEN.keys()), index=2)
        lichtstimmung = l2.selectbox("Lichtstimmung",
                                     list(ki.LICHT_STIMMUNGEN.keys()))
        mit_schatten = st.checkbox(
            "Schlag- und Kontaktschatten anfordern", True,
            help="Beschreibt der KI ausdrücklich Wandschatten und "
                 "Bodenkontakt – zusammen mit der Wandebene in der "
                 "Tiefenkarte der Hebel für ein realistisches Bild.")

        zusatz = st.text_area("Zusätzliche Bildwünsche", "", height=70)
        szene = st.session_state.get("letzte_szene")
        prompt = ki.baue_prompt(
            raum=raum, zusatz=zusatz, kachelbeschreibung=kachelbeschreibung,
            lichtrichtung=lichtrichtung, lichtstimmung=lichtstimmung,
            mit_schatten=mit_schatten,
            dreiviertel=bool(szene and szene.blickwinkel > 0.01),
        )
        prompt = st.text_area("Prompt (bearbeitbar)", prompt, height=160)
        negativ = st.text_area("Negativ-Prompt", ki.NEGATIV_PROMPT, height=70)

        gesperrt = bool(zentral) and kontingent.erschoepft()
        if gesperrt:
            st.warning("Das Tageskontingent für Renderings ist aufgebraucht. "
                       "Morgen geht es weiter – oder Gabriel hebt das Limit an.")

        if st.button("Bild erzeugen", type="primary",
                     width='stretch', disabled=gesperrt):
            with st.spinner("Replicate rendert – das dauert 20–90 Sekunden …"):
                try:
                    ergebnis = ki.rendere(
                        depthmap=depth, prompt=prompt, preset_key=preset_key,
                        token=token or zentral, negativ_prompt=negativ,
                        seed=seed,
                    )
                    st.session_state.renderergebnisse = ergebnis
                    if zentral:
                        kontingent.verbuchen(len(ergebnis) or 1)
                except ki.RenderFehler as fehler:
                    st.error(str(fehler))
                except Exception as fehler:  # Netzwerk, Zeitüberschreitung …
                    st.error(f"Unerwarteter Fehler: {fehler}")

    ergebnisse = st.session_state.get("renderergebnisse") or []
    if ergebnisse:
        st.divider()
        st.subheader("Ergebnisse")
        for i, bild in enumerate(ergebnisse, start=1):
            s1, s2 = st.columns([3, 1])
            s1.image(bild, width='stretch')
            s2.download_button(
                f"Bild {i} herunterladen", als_png_bytes(bild),
                f"{_dateiname(k.name)}-render-{i}.png", "image/png",
                width='stretch', key=f"dl_render_{i}")


# --------------------------------------------------------------------------

def main() -> None:
    if not auth.angemeldet():
        return

    zustand()
    st.title("🔥 Kachelofen-Konfigurator")
    st.caption("Aus Einzelfotos historischer Kacheln und Eisenwaren wird ein "
               "maßstabsgetreues Raster, eine Tiefenkarte und daraus ein "
               "fotorealistisches Verkaufsbild.")
    seitenleiste()

    r0, r1, r2, r3, r4 = st.tabs(
        ["1 · Vorlage aus Foto", "2 · Kacheln & Eisenwaren", "3 · Aufbau",
         "4 · Vorschau & Export", "5 · KI-Rendering"])
    with r0:
        reiter_vorlage()
    with r1:
        reiter_assets()
    with r2:
        reiter_aufbau()
    with r3:
        reiter_vorschau()
    with r4:
        reiter_ki()


if __name__ == "__main__":
    main()
